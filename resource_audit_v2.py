import os
import re
import json
import math
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


# PURPOSE:
# Converts measured Greensboro PV and EV data into reproducible five-minute flexibility profiles for the final data-center MILP.
# WHY:
# Existing PV/EV effects are already embedded in breaker SCADA, so the model must derive incremental PV availability and shiftable EV load without double-counting historical resources.
# DEPENDENCIES:
# Queries PV/EV assets and 2024 kWh time series directly from the EDM; reads the current candidate context and the existing greensboro_candidate_resource_inputs.xlsx used by the MILP.
# OUTPUT:
# Writes greensboro_candidate_pv_profiles_2024.csv.gz, greensboro_substation_ev_flex_profiles_2024.csv.gz, greensboro_pv_availability_shape_2024.csv.gz, and greensboro_resource_flexibility_profiles_audit.xlsx, then prints a complete terminal handoff.


# ======================================================================================
# SETTINGS
# ======================================================================================

YEAR = 2024

CANDIDATE_AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
CANDIDATE_AUDIT_SHEET = "MILP_Readiness"
THERMAL_RESULTS_FILE = "greensboro_final_thermal_hosting_results.xlsx"
THERMAL_RESULTS_SHEET = "Candidate_Summary"

RESOURCE_INPUT_FILE = "greensboro_candidate_resource_inputs.xlsx"
RESOURCE_INPUT_SHEET = "Candidate_Resources"

PV_OUTPUT_FILE = "greensboro_candidate_pv_profiles_2024.csv.gz"
PV_SHAPE_OUTPUT_FILE = "greensboro_pv_availability_shape_2024.csv.gz"
EV_OUTPUT_FILE = "greensboro_substation_ev_flex_profiles_2024.csv.gz"
AUDIT_OUTPUT_FILE = "greensboro_resource_flexibility_profiles_audit.xlsx"

# Existing PV is used only to learn an empirical Greensboro production SHAPE.
# New PV credited to a candidate equals ProposedPVCapacityMW * that normalized shape.
# ProposedPVCapacityMW is read from the existing MILP resource-input workbook.
PV_CAPACITY_INPUT_COLUMN = "ProposedPVCapacityMW"

# Require roughly a full year before a PV asset contributes to the normalized profile.
MIN_PV_PROFILE_SPAN_DAYS = 300.0
MIN_PV_PROFILE_OBSERVATIONS = 24 * 250

# EV flexibility is a scenario assumption, not an observed program enrollment rate.
# 0.50 means at most 50% of observed EV charging can be deferred at a timestamp,
# and at most 50% of validated unused charger capacity can be used for rebound charging.
EV_FLEX_FRACTION = 0.50

# Rebound is only credited for chargers that have a validated charging-power rating.
# The script infers the units of active_charging_power from observed 2024 load.
MIN_EV_PROFILE_SPAN_DAYS = 300.0

# Power-factor is irrelevant here because PV/EV time series are real-energy measurements.
FIVE_MINUTES_PER_HOUR = 12
EXPECTED_5MIN_INTERVALS = 366 * 24 * FIVE_MINUTES_PER_HOUR if YEAR % 4 == 0 else 365 * 24 * FIVE_MINUTES_PER_HOUR
EXPECTED_HOURLY_INTERVALS = 366 * 24 if YEAR % 4 == 0 else 365 * 24

# Unit-inference candidates. Metadata values in the current EDM appear numerically
# consistent with watts, but the script verifies that relationship against observed data.
POWER_UNIT_SCALES_TO_MW = {
    "W": 1e-6,
    "kW": 1e-3,
    "MW": 1.0
}

# Conservative validation thresholds.
MAX_REASONABLE_OBSERVED_TO_RATING_RATIO = 1.35
MAX_ALLOWED_RATING_VIOLATION_PCT = 20.0
PV_PROFILE_CLIP_PU = 1.10

load_dotenv()
DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")


# ======================================================================================
# BASIC HELPERS
# ======================================================================================

def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def connect_to_edm():
    missing = [name for name, value in {"EDM_HOST": DB_HOST, "EDM_USER": DB_USER, "EDM_PASSWORD": DB_PASSWORD, "EDM_DATABASE": DB_NAME}.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME)


def query_dataframe(conn, query, params=None):
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def safe_query_dataframe(conn, query, params=None, savepoint_name="resource_profiles"):
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SAVEPOINT {savepoint_name}")
        df = query_dataframe(conn, query, params)
        with conn.cursor() as cursor:
            cursor.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        return df, None
    except Exception as exc:
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint_name}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint_name}")
        except Exception:
            conn.rollback()
        return pd.DataFrame(), str(exc)


def is_missing(value):
    if value is None:
        return True
    try:
        result = pd.isna(value)
        if isinstance(result, (bool, np.bool_)):
            return bool(result)
    except Exception:
        pass
    return False


def safe_float(value):
    if is_missing(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def truthy(value):
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if is_missing(value):
        return False
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def meta_get(meta, key):
    if not isinstance(meta, dict):
        return None
    for existing_key, value in meta.items():
        if str(existing_key).lower() == str(key).lower():
            return value
    return None


def make_excel_safe(df):
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    for column in df.columns:
        if isinstance(df[column].dtype, pd.DatetimeTZDtype):
            df[column] = df[column].dt.tz_localize(None)
        elif pd.api.types.is_datetime64_any_dtype(df[column]):
            try:
                df[column] = pd.to_datetime(df[column]).dt.tz_localize(None)
            except Exception:
                pass
        elif df[column].dtype == "object":
            df[column] = df[column].map(lambda value: json.dumps(value, default=str) if isinstance(value, (dict, list, tuple, set)) else value)
    return df


def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_format = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})
    decimal_format = workbook.add_format({"num_format": "0.000000"})
    integer_format = workbook.add_format({"num_format": "0"})
    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)
        values = dataframe[column_name].tolist() if not dataframe.empty else []
        max_length = max((len(str(value)) for value in values if value is not None), default=0)
        width = min(max(max_length, len(str(column_name))) + 2, 52)
        if pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, decimal_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)
    worksheet.freeze_panes(1, 0)
    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


# ======================================================================================
# CANDIDATES AND EXISTING MILP RESOURCE INPUTS
# ======================================================================================

def load_candidate_context():
    if Path(CANDIDATE_AUDIT_FILE).exists():
        df = pd.read_excel(CANDIDATE_AUDIT_FILE, sheet_name=CANDIDATE_AUDIT_SHEET)
        required = {"GridID", "SubstationID", "CandidateLineID"}
        if required.issubset(df.columns):
            if "ReadyForFeederBoundMILP" in df.columns:
                ready = df["ReadyForFeederBoundMILP"].map(truthy)
                if ready.any():
                    df = df[ready].copy()
            if "CandidateKey" not in df.columns:
                df["CandidateKey"] = df["GridID"].astype(str) + "|" + df["CandidateLineID"].astype(str)
            keep = [column for column in ["CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ReadyForFeederBoundMILP"] if column in df.columns]
            df = df[keep].copy()
            df["CandidateContextSource"] = f"{CANDIDATE_AUDIT_FILE}:{CANDIDATE_AUDIT_SHEET}"
            return df.drop_duplicates(subset=["CandidateKey"]).reset_index(drop=True)

    if Path(THERMAL_RESULTS_FILE).exists():
        df = pd.read_excel(THERMAL_RESULTS_FILE, sheet_name=THERMAL_RESULTS_SHEET)
        required = {"GridID", "SubstationID", "CandidateLineID"}
        if required.issubset(df.columns):
            if "CandidateKey" not in df.columns:
                df["CandidateKey"] = df["GridID"].astype(str) + "|" + df["CandidateLineID"].astype(str)
            keep = [column for column in ["CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ModelStatus"] if column in df.columns]
            df = df[keep].copy()
            df["CandidateContextSource"] = f"{THERMAL_RESULTS_FILE}:{THERMAL_RESULTS_SHEET}"
            return df.drop_duplicates(subset=["CandidateKey"]).reset_index(drop=True)

    raise FileNotFoundError("Could not load current candidate context.")


def ensure_resource_input_workbook(candidates):
    path = Path(RESOURCE_INPUT_FILE)

    default_columns = {
        "BatteryMaxPowerMW": np.nan,
        "BatteryMaxEnergyMWh": np.nan,
        "BatteryChargeEfficiency": np.nan,
        "BatteryDischargeEfficiency": np.nan,
        "BatteryInitialSOCFraction": np.nan,
        "BatteryMinSOCFraction": np.nan,
        "FlexibleComputeMaxReductionFraction": np.nan,
        "FlexibleComputeMaxEnergyFraction": np.nan,
        PV_CAPACITY_INPUT_COLUMN: 0.0,
        "ResourceSource": "Review",
        "Notes": ""
    }

    if path.exists():
        try:
            existing = pd.read_excel(path, sheet_name=RESOURCE_INPUT_SHEET)
        except Exception:
            existing = pd.DataFrame()
    else:
        existing = pd.DataFrame()

    base = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].drop_duplicates().copy()

    if not existing.empty and "CandidateKey" in existing.columns:
        existing["CandidateKey"] = existing["CandidateKey"].astype(str)
        keep_existing = [column for column in existing.columns if column not in {"GridID", "SubstationID", "CandidateLineID"}]
        base = base.merge(existing[keep_existing], on="CandidateKey", how="left")

    for column, default in default_columns.items():
        if column not in base.columns:
            base[column] = default

    base[PV_CAPACITY_INPUT_COLUMN] = pd.to_numeric(base[PV_CAPACITY_INPUT_COLUMN], errors="coerce").fillna(0.0).clip(lower=0.0)

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        safe = make_excel_safe(base)
        safe.to_excel(writer, sheet_name=RESOURCE_INPUT_SHEET, index=False)
        format_excel(writer, RESOURCE_INPUT_SHEET, safe)

    return base


# ======================================================================================
# FEEDER / SUBSTATION FOOTPRINT
# ======================================================================================

def get_breaker_substation_map(conn):
    return query_dataframe(conn, """
        SELECT b.grid_id AS "GridID",
               b.grid_element_id AS "BreakerID",
               b.meta ->> 'enclosure_id' AS "SubstationID"
        FROM grid_element b
        WHERE b.grid_id ~ '^GSO_[0-9]+$'
          AND LOWER(COALESCE(b.type, '')) ~ 'breaker'
          AND b.meta ? 'enclosure_id'
          AND NULLIF(TRIM(b.meta ->> 'enclosure_id'), '') IS NOT NULL
        ORDER BY b.grid_id;
    """)


def build_relevant_feeders(candidates, breaker_map):
    substations = sorted(candidates["SubstationID"].dropna().astype(str).unique().tolist())
    relevant = breaker_map[breaker_map["SubstationID"].astype(str).isin(substations)].copy()
    candidate_feeders = set(candidates["GridID"].astype(str))
    relevant["IsCandidateFeeder"] = relevant["GridID"].astype(str).isin(candidate_feeders)
    return relevant.sort_values(["SubstationID", "GridID"]).reset_index(drop=True)


# ======================================================================================
# PV / EV ASSET INVENTORY
# ======================================================================================

def get_resource_assets(conn, feeders):
    return query_dataframe(conn, """
        SELECT grid_id AS "GridID",
               grid_element_id AS "ElementID",
               type AS "ElementType",
               customer_type AS "CustomerType",
               is_producer AS "IsProducer",
               is_consumer AS "IsConsumer",
               meta AS "Meta"
        FROM grid_element
        WHERE grid_id = ANY(%s)
          AND (
              LOWER(COALESCE(type, '')) ~ '(photovoltaic|solar|^pv$|evcharger|electric.?vehicle|ev.?charger)'
              OR LOWER(COALESCE(customer_type, '')) ~ '(photovoltaic|solar|^pv$|ev_charger|electric.?vehicle|ev.?charger)'
          )
        ORDER BY grid_id, grid_element_id;
    """, (feeders,))


def classify_resource(row):
    text = f"{row.get('ElementType', '')} {row.get('CustomerType', '')}".lower()
    if re.search(r"photovoltaic|solar|\bpv\b", text):
        return "PV"
    if re.search(r"evcharger|electric.?vehicle|ev.?charger|\bev\b", text):
        return "EV"
    return None


def get_kwh_sources(conn, feeders):
    return query_dataframe(conn, """
        SELECT ds.grid_id AS "GridID",
               ds.grid_element_id AS "ElementID",
               ds.grid_element_data_source_id::text AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.direction AS "Direction",
               ds.valid AS "ValidRange"
        FROM grid_element_data_source ds
        WHERE ds.grid_id = ANY(%s)
          AND 'kWh' = ANY(ds.metrics)
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """, (feeders,))


def load_kwh_source(conn, data_source_id):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    df, error = safe_query_dataframe(conn, """
        SELECT timestamp, value
        FROM ts_data_source_select(
            %s::uuid,
            'kWh',
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        )
        ORDER BY timestamp;
    """, (str(data_source_id), start, end), savepoint_name="profile_ts")

    if error or df.empty:
        return pd.Series(dtype=float), error or "No data"

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["timestamp", "value"]).sort_values("timestamp")

    if df.empty:
        return pd.Series(dtype=float), "No valid timestamp/value rows"

    series = pd.Series(df["value"].to_numpy(dtype=float), index=df["timestamp"])
    series = series[~series.index.duplicated(keep="last")]
    return series, None


# ======================================================================================
# UNIT INFERENCE
# ======================================================================================

def infer_rating_scale(records, raw_column, observed_peak_column, resource_label, preferred_target_ratio=0.80):
    rows = []

    for unit_name, scale in POWER_UNIT_SCALES_TO_MW.items():
        valid = records[[raw_column, observed_peak_column]].copy()
        valid[raw_column] = pd.to_numeric(valid[raw_column], errors="coerce")
        valid[observed_peak_column] = pd.to_numeric(valid[observed_peak_column], errors="coerce")
        valid = valid[(valid[raw_column] > 0) & (valid[observed_peak_column] >= 0)].copy()

        if valid.empty:
            rows.append({
                "Resource": resource_label,
                "RawField": raw_column,
                "AssumedUnit": unit_name,
                "ScaleToMW": scale,
                "ValidAssets": 0,
                "MedianObservedToRatingRatio": np.nan,
                "P95ObservedToRatingRatio": np.nan,
                "RatingViolationPct": np.nan,
                "Score": np.inf
            })
            continue

        rating = valid[raw_column] * scale
        ratio = valid[observed_peak_column] / rating
        ratio = ratio.replace([np.inf, -np.inf], np.nan).dropna()

        if ratio.empty:
            score = np.inf
            median_ratio = np.nan
            p95_ratio = np.nan
            violation_pct = np.nan
        else:
            median_ratio = float(ratio.median())
            p95_ratio = float(ratio.quantile(0.95))
            violation_pct = float((ratio > MAX_REASONABLE_OBSERVED_TO_RATING_RATIO).mean() * 100.0)
            positive_ratio = max(median_ratio, 1e-9)
            score = abs(math.log(positive_ratio / preferred_target_ratio)) + 3.0 * (violation_pct / 100.0)

        rows.append({
            "Resource": resource_label,
            "RawField": raw_column,
            "AssumedUnit": unit_name,
            "ScaleToMW": scale,
            "ValidAssets": len(valid),
            "MedianObservedToRatingRatio": median_ratio,
            "P95ObservedToRatingRatio": p95_ratio,
            "RatingViolationPct": violation_pct,
            "Score": score
        })

    result = pd.DataFrame(rows).sort_values("Score", ascending=True).reset_index(drop=True)
    best = result.iloc[0].to_dict() if not result.empty else None

    if best is None or not np.isfinite(best["Score"]):
        return None, result

    if pd.notna(best["RatingViolationPct"]) and best["RatingViolationPct"] > MAX_ALLOWED_RATING_VIOLATION_PCT:
        return None, result

    return best, result


def infer_best_pv_capacity_field(pv_asset_summary):
    candidate_fields = ["ac_size", "generation_capacity", "dc_size"]
    all_results = []
    field_penalty = {"ac_size": 0.0, "generation_capacity": 0.05, "dc_size": 0.10}

    for field in candidate_fields:
        if field not in pv_asset_summary.columns:
            continue
        best, trials = infer_rating_scale(pv_asset_summary, field, "ObservedPeakMW", "PV", preferred_target_ratio=0.75)
        if not trials.empty:
            trials["FieldPenalty"] = field_penalty[field]
            trials["AdjustedScore"] = trials["Score"] + trials["FieldPenalty"]
            all_results.append(trials)

    if not all_results:
        return None, pd.DataFrame()

    comparison = pd.concat(all_results, ignore_index=True).sort_values("AdjustedScore").reset_index(drop=True)
    best_row = comparison.iloc[0].to_dict()

    if not np.isfinite(best_row["AdjustedScore"]):
        return None, comparison

    if pd.notna(best_row["RatingViolationPct"]) and best_row["RatingViolationPct"] > MAX_ALLOWED_RATING_VIOLATION_PCT:
        return None, comparison

    return best_row, comparison


# ======================================================================================
# PV PROFILE
# ======================================================================================

def build_pv_asset_profiles(conn, pv_assets, sources):
    full_hourly_index = pd.date_range(
        start=f"{YEAR}-01-01 00:00:00+00",
        end=f"{YEAR + 1}-01-01 00:00:00+00",
        freq="1h",
        inclusive="left"
    )

    asset_rows = []
    profile_columns = {}

    for position, (_, asset) in enumerate(pv_assets.iterrows(), start=1):
        grid_id = str(asset["GridID"])
        element_id = str(asset["ElementID"])
        asset_sources = sources[(sources["GridID"].astype(str) == grid_id) & (sources["ElementID"].astype(str) == element_id)]

        if position == 1 or position % 10 == 0 or position == len(pv_assets):
            print(f"  PV asset {position}/{len(pv_assets)}: {grid_id} | {element_id}")

        source_series = []
        errors = []

        for _, source in asset_sources.iterrows():
            series, error = load_kwh_source(conn, source["DataSourceID"])
            if error:
                errors.append(error)
                continue
            if not series.empty:
                source_series.append(series)

        if source_series:
            combined = pd.concat(source_series, axis=1).sum(axis=1, min_count=1).sort_index()
            # PV may be stored as positive production or negative export depending on source direction.
            # Use production magnitude only; the historical net effect is NOT added to SCADA later.
            combined = combined.abs()
            hourly_kwh = combined.resample("1h").sum(min_count=1).reindex(full_hourly_index)
            hourly_mw = hourly_kwh / 1000.0
            observed_peak_mw = float(hourly_mw.max()) if hourly_mw.notna().any() else np.nan
            first = hourly_mw.dropna().index.min() if hourly_mw.notna().any() else pd.NaT
            last = hourly_mw.dropna().index.max() if hourly_mw.notna().any() else pd.NaT
            span_days = (last - first).total_seconds() / 86400.0 if pd.notna(first) and pd.notna(last) else 0.0
            observations = int(hourly_mw.notna().sum())
        else:
            hourly_mw = pd.Series(index=full_hourly_index, dtype=float)
            observed_peak_mw = np.nan
            span_days = 0.0
            observations = 0

        meta = asset.get("Meta")
        row = {
            "GridID": grid_id,
            "SubstationID": asset.get("SubstationID"),
            "ElementID": element_id,
            "DataSourceCount": len(asset_sources),
            "ObservedHourlyIntervals": observations,
            "SpanDays": span_days,
            "ObservedPeakMW": observed_peak_mw,
            "ac_size": safe_float(meta_get(meta, "ac_size")),
            "dc_size": safe_float(meta_get(meta, "dc_size")),
            "generation_capacity": safe_float(meta_get(meta, "generation_capacity")),
            "panel_rated_power": safe_float(meta_get(meta, "panel_rated_power")),
            "Errors": " | ".join(errors)
        }
        asset_rows.append(row)
        profile_columns[f"{grid_id}|{element_id}"] = hourly_mw

    return pd.DataFrame(asset_rows), pd.DataFrame(profile_columns, index=full_hourly_index)


def build_normalized_pv_shape(pv_asset_summary, pv_hourly_profiles, best_capacity_rule):
    if best_capacity_rule is None or pv_asset_summary.empty or pv_hourly_profiles.empty:
        return pd.DataFrame(), pd.DataFrame()

    capacity_field = best_capacity_rule["RawField"]
    scale = float(best_capacity_rule["ScaleToMW"])

    asset_summary = pv_asset_summary.copy()
    asset_summary["ValidatedCapacityMW"] = pd.to_numeric(asset_summary[capacity_field], errors="coerce") * scale
    asset_summary["ObservedToCapacityRatio"] = asset_summary["ObservedPeakMW"] / asset_summary["ValidatedCapacityMW"]
    asset_summary["PVProfileEligible"] = (
        (asset_summary["SpanDays"] >= MIN_PV_PROFILE_SPAN_DAYS)
        & (asset_summary["ObservedHourlyIntervals"] >= MIN_PV_PROFILE_OBSERVATIONS)
        & (asset_summary["ValidatedCapacityMW"] > 0)
        & (asset_summary["ObservedToCapacityRatio"] >= 0)
        & (asset_summary["ObservedToCapacityRatio"] <= MAX_REASONABLE_OBSERVED_TO_RATING_RATIO)
    )

    eligible = asset_summary[asset_summary["PVProfileEligible"]].copy()

    if eligible.empty:
        return pd.DataFrame(), asset_summary

    normalized = pd.DataFrame(index=pv_hourly_profiles.index)

    for _, row in eligible.iterrows():
        key = f"{row['GridID']}|{row['ElementID']}"
        if key not in pv_hourly_profiles.columns:
            continue
        normalized[key] = (pv_hourly_profiles[key] / row["ValidatedCapacityMW"]).clip(lower=0.0, upper=PV_PROFILE_CLIP_PU)

    if normalized.empty:
        return pd.DataFrame(), asset_summary

    # System-wide shape is used as fallback. Substation shapes are used where at least one
    # eligible PV source exists. Existing assets contribute only a normalized production pattern.
    hourly_shape = pd.DataFrame(index=normalized.index)
    hourly_shape["SystemPVAvailabilityPU"] = normalized.mean(axis=1, skipna=True)
    hourly_shape["SystemPVAssetsContributing"] = normalized.notna().sum(axis=1)

    for substation_id, group in eligible.groupby("SubstationID"):
        keys = [f"{row['GridID']}|{row['ElementID']}" for _, row in group.iterrows()]
        keys = [key for key in keys if key in normalized.columns]
        if not keys:
            continue
        hourly_shape[f"PVAvailabilityPU__{substation_id}"] = normalized[keys].mean(axis=1, skipna=True)
        hourly_shape[f"PVAssetsContributing__{substation_id}"] = normalized[keys].notna().sum(axis=1)

    hourly_shape.index.name = "TimestampUTC"
    return hourly_shape.reset_index(), asset_summary


def expand_pv_shape_to_5min(pv_shape_hourly, candidates, resource_inputs):
    five_minute_index = pd.date_range(
        start=f"{YEAR}-01-01 00:00:00+00",
        end=f"{YEAR + 1}-01-01 00:00:00+00",
        freq="5min",
        inclusive="left"
    )

    if pv_shape_hourly.empty:
        shape_5min = pd.DataFrame({
            "TimestampUTC": five_minute_index,
            "SystemPVAvailabilityPU": 0.0,
            "SystemPVAssetsContributing": 0
        })
    else:
        hourly = pv_shape_hourly.copy()
        hourly["TimestampUTC"] = pd.to_datetime(hourly["TimestampUTC"], utc=True, errors="coerce")
        hourly = hourly.dropna(subset=["TimestampUTC"]).set_index("TimestampUTC").sort_index()
        shape_5min = hourly.reindex(five_minute_index, method="ffill", tolerance=pd.Timedelta(minutes=55)).reset_index().rename(columns={"index": "TimestampUTC"})
        shape_5min["SystemPVAvailabilityPU"] = pd.to_numeric(shape_5min["SystemPVAvailabilityPU"], errors="coerce").fillna(0.0).clip(lower=0.0, upper=PV_PROFILE_CLIP_PU)
        shape_5min["SystemPVAssetsContributing"] = pd.to_numeric(shape_5min["SystemPVAssetsContributing"], errors="coerce").fillna(0).astype(int)

    resource_lookup = resource_inputs.set_index("CandidateKey").to_dict("index")
    profile_frames = []
    candidate_summary_rows = []

    for _, candidate in candidates.iterrows():
        candidate_key = str(candidate["CandidateKey"])
        substation_id = str(candidate["SubstationID"])
        proposed_capacity = safe_float(resource_lookup.get(candidate_key, {}).get(PV_CAPACITY_INPUT_COLUMN))
        proposed_capacity = 0.0 if pd.isna(proposed_capacity) else max(proposed_capacity, 0.0)

        local_shape_column = f"PVAvailabilityPU__{substation_id}"
        local_count_column = f"PVAssetsContributing__{substation_id}"

        if local_shape_column in shape_5min.columns:
            availability = pd.to_numeric(shape_5min[local_shape_column], errors="coerce")
            local_count = pd.to_numeric(shape_5min[local_count_column], errors="coerce").fillna(0) if local_count_column in shape_5min.columns else pd.Series(0, index=shape_5min.index)
            use_local = availability.notna() & (local_count > 0)
            availability = availability.where(use_local, shape_5min["SystemPVAvailabilityPU"])
            profile_source = "Substation empirical shape with system fallback"
        else:
            availability = shape_5min["SystemPVAvailabilityPU"]
            profile_source = "System-wide empirical Greensboro PV shape"

        availability = pd.to_numeric(availability, errors="coerce").fillna(0.0).clip(lower=0.0, upper=PV_PROFILE_CLIP_PU)

        frame = pd.DataFrame({
            "TimestampUTC": shape_5min["TimestampUTC"],
            "CandidateKey": candidate_key,
            "AvailablePVMW": availability.to_numpy(dtype=float) * proposed_capacity
        })
        profile_frames.append(frame)

        candidate_summary_rows.append({
            "CandidateKey": candidate_key,
            "GridID": candidate["GridID"],
            "SubstationID": substation_id,
            "ProposedPVCapacityMW": proposed_capacity,
            "MaximumAvailablePVMW": float(frame["AvailablePVMW"].max()) if not frame.empty else 0.0,
            "AnnualMeanAvailablePVMW": float(frame["AvailablePVMW"].mean()) if not frame.empty else 0.0,
            "PVProfileSource": profile_source,
            "PVActiveForMILP": proposed_capacity > 0 and float(frame["AvailablePVMW"].max()) > 0
        })

    pv_profile = pd.concat(profile_frames, ignore_index=True) if profile_frames else pd.DataFrame(columns=["TimestampUTC", "CandidateKey", "AvailablePVMW"])
    return pv_profile, shape_5min, pd.DataFrame(candidate_summary_rows)


# ======================================================================================
# EV PROFILE
# ======================================================================================

def build_ev_asset_profiles(conn, ev_assets, sources):
    five_minute_index = pd.date_range(
        start=f"{YEAR}-01-01 00:00:00+00",
        end=f"{YEAR + 1}-01-01 00:00:00+00",
        freq="5min",
        inclusive="left"
    )

    asset_rows = []
    profiles = {}

    for position, (_, asset) in enumerate(ev_assets.iterrows(), start=1):
        grid_id = str(asset["GridID"])
        element_id = str(asset["ElementID"])
        asset_sources = sources[(sources["GridID"].astype(str) == grid_id) & (sources["ElementID"].astype(str) == element_id)]

        if position == 1 or position % 10 == 0 or position == len(ev_assets):
            print(f"  EV asset {position}/{len(ev_assets)}: {grid_id} | {element_id}")

        source_series = []
        errors = []

        for _, source in asset_sources.iterrows():
            series, error = load_kwh_source(conn, source["DataSourceID"])
            if error:
                errors.append(error)
                continue
            if not series.empty:
                source_series.append(series)

        if source_series:
            combined = pd.concat(source_series, axis=1).sum(axis=1, min_count=1).sort_index()
            combined = combined.clip(lower=0.0)
            five_min_kwh = combined.resample("5min").sum(min_count=1).reindex(five_minute_index)
            five_min_mw = five_min_kwh * FIVE_MINUTES_PER_HOUR / 1000.0

            # Also evaluate the native-resolution peak to validate active_charging_power units.
            native_diffs = combined.index.to_series().diff().dropna().dt.total_seconds() / 60.0
            median_interval_minutes = float(native_diffs.median()) if not native_diffs.empty else 1.0
            median_interval_minutes = max(median_interval_minutes, 1e-6)
            native_power_mw = combined / (median_interval_minutes / 60.0) / 1000.0
            observed_peak_mw = float(native_power_mw.quantile(0.999)) if native_power_mw.notna().any() else np.nan

            first = combined.index.min()
            last = combined.index.max()
            span_days = (last - first).total_seconds() / 86400.0 if pd.notna(first) and pd.notna(last) else 0.0
            observations = len(combined)
        else:
            five_min_mw = pd.Series(index=five_minute_index, dtype=float)
            observed_peak_mw = np.nan
            span_days = 0.0
            observations = 0
            median_interval_minutes = np.nan

        meta = asset.get("Meta")
        asset_rows.append({
            "GridID": grid_id,
            "SubstationID": asset.get("SubstationID"),
            "ElementID": element_id,
            "DataSourceCount": len(asset_sources),
            "NativeObservations": observations,
            "SpanDays": span_days,
            "NativeMedianIntervalMinutes": median_interval_minutes,
            "ObservedPeakMW": observed_peak_mw,
            "active_charging_power": safe_float(meta_get(meta, "active_charging_power")),
            "active_generation_power": safe_float(meta_get(meta, "active_generation_power")),
            "is_dc_charger": meta_get(meta, "is_dc_charger"),
            "Errors": " | ".join(errors)
        })
        profiles[f"{grid_id}|{element_id}"] = five_min_mw

    return pd.DataFrame(asset_rows), pd.DataFrame(profiles, index=five_minute_index)


def build_ev_station_profiles(ev_asset_summary, ev_profiles, best_ev_scale):
    if ev_asset_summary.empty or ev_profiles.empty or best_ev_scale is None:
        return pd.DataFrame(), ev_asset_summary

    summary = ev_asset_summary.copy()
    scale = float(best_ev_scale["ScaleToMW"])
    summary["ValidatedChargingPowerMW"] = pd.to_numeric(summary["active_charging_power"], errors="coerce") * scale
    summary["ObservedToRatingRatio"] = summary["ObservedPeakMW"] / summary["ValidatedChargingPowerMW"]
    summary["EVRatingValidated"] = (
        (summary["SpanDays"] >= MIN_EV_PROFILE_SPAN_DAYS)
        & (summary["ValidatedChargingPowerMW"] > 0)
        & (summary["ObservedToRatingRatio"] >= 0)
        & (summary["ObservedToRatingRatio"] <= MAX_REASONABLE_OBSERVED_TO_RATING_RATIO)
    )

    station_frames = []

    for substation_id, group in summary.groupby("SubstationID"):
        observed_total = pd.Series(0.0, index=ev_profiles.index)
        reducible_total = pd.Series(0.0, index=ev_profiles.index)
        rebound_total = pd.Series(0.0, index=ev_profiles.index)
        contributing = pd.Series(0, index=ev_profiles.index, dtype=int)
        rating_validated_assets = 0

        for _, asset in group.iterrows():
            key = f"{asset['GridID']}|{asset['ElementID']}"
            if key not in ev_profiles.columns:
                continue

            observed = pd.to_numeric(ev_profiles[key], errors="coerce")
            observed_nonnegative = observed.clip(lower=0.0)
            observed_total = observed_total.add(observed_nonnegative.fillna(0.0), fill_value=0.0)
            reducible_total = reducible_total.add(observed_nonnegative.fillna(0.0) * EV_FLEX_FRACTION, fill_value=0.0)
            contributing = contributing.add(observed.notna().astype(int), fill_value=0).astype(int)

            if truthy(asset["EVRatingValidated"]):
                rating_validated_assets += 1
                rating_mw = float(asset["ValidatedChargingPowerMW"])
                spare = (rating_mw - observed_nonnegative).clip(lower=0.0)
                rebound_total = rebound_total.add(spare.fillna(0.0) * EV_FLEX_FRACTION, fill_value=0.0)

        frame = pd.DataFrame({
            "TimestampUTC": ev_profiles.index,
            "SubstationID": str(substation_id),
            "ObservedEVLoadMW": observed_total.to_numpy(dtype=float),
            "AvailableEVReductionMW": reducible_total.to_numpy(dtype=float),
            "AvailableEVReboundMW": rebound_total.to_numpy(dtype=float),
            "EVAssetsContributing": contributing.to_numpy(dtype=int),
            "EVRatingValidatedAssets": rating_validated_assets,
            "EVFlexFraction": EV_FLEX_FRACTION
        })
        station_frames.append(frame)

    output = pd.concat(station_frames, ignore_index=True) if station_frames else pd.DataFrame()
    return output, summary


# ======================================================================================
# AUDIT SUMMARIES
# ======================================================================================

def build_ev_substation_summary(ev_station_profiles):
    if ev_station_profiles.empty:
        return pd.DataFrame()
    return ev_station_profiles.groupby("SubstationID", as_index=False).agg(
        PeakObservedEVLoadMW=("ObservedEVLoadMW", "max"),
        P95ObservedEVLoadMW=("ObservedEVLoadMW", lambda x: x.quantile(0.95)),
        PeakAvailableEVReductionMW=("AvailableEVReductionMW", "max"),
        P95AvailableEVReductionMW=("AvailableEVReductionMW", lambda x: x.quantile(0.95)),
        PeakAvailableEVReboundMW=("AvailableEVReboundMW", "max"),
        MedianAvailableEVReboundMW=("AvailableEVReboundMW", "median"),
        MaxEVAssetsContributing=("EVAssetsContributing", "max"),
        EVRatingValidatedAssets=("EVRatingValidatedAssets", "max")
    )


def build_assumptions():
    return pd.DataFrame([
        {"Item": "Existing PV", "Assumption": "Existing PV generation is already embedded in historical breaker SCADA and is not added to the MILP a second time."},
        {"Item": "Incremental PV", "Assumption": f"Existing PV time series are normalized into an empirical production shape. New PV credited to each candidate equals {PV_CAPACITY_INPUT_COLUMN} from {RESOURCE_INPUT_FILE} multiplied by that shape."},
        {"Item": "PV capacity metadata", "Assumption": "The script empirically compares ac_size, generation_capacity, and dc_size under W/kW/MW interpretations against measured 2024 PV output and selects the most physically consistent field/unit combination."},
        {"Item": "PV time resolution", "Assumption": "Hourly measured PV production is carried as an hourly-average power value across its twelve five-minute intervals. No synthetic intra-hour solar shape is invented."},
        {"Item": "EV historical load", "Assumption": "Existing EV charging is already embedded in breaker/substation SCADA; the profile only describes how much of that existing charging could potentially be deferred and later restored."},
        {"Item": "EV flexibility fraction", "Assumption": f"EV_FLEX_FRACTION={EV_FLEX_FRACTION:.2f} is an explicit scenario assumption, not an observed enrollment rate."},
        {"Item": "EV charging rating", "Assumption": "active_charging_power units are inferred empirically by comparing W/kW/MW interpretations against measured native-resolution 2024 EV charging peaks."},
        {"Item": "EV reduction", "Assumption": "AvailableEVReductionMW equals EV_FLEX_FRACTION times observed EV charging load at each five-minute interval."},
        {"Item": "EV rebound", "Assumption": "AvailableEVReboundMW equals EV_FLEX_FRACTION times validated unused charger power. Plug/connection state is unavailable, so this is a technical rebound envelope rather than a guaranteed behavioral response."},
        {"Item": "Battery", "Assumption": "Battery/storage remains a proposed design variable in the MILP; no historical battery asset is required by this profile script."},
        {"Item": "DR", "Assumption": "No explicit DR profile is generated because the resource audit found no direct DR/flexible-load data source in the relevant EDM footprint."},
        {"Item": "Reproducibility", "Assumption": "This script queries the EDM directly and does not require greensboro_resource_flexibility_audit.xlsx to run."}
    ])


# ======================================================================================
# TERMINAL HANDOFF
# ======================================================================================

def print_handoff(candidates, resource_inputs, pv_rule, pv_unit_trials, pv_assets, pv_candidate_summary, ev_rule, ev_unit_trials, ev_assets, ev_substation_summary):
    heading("PV / EV PROFILE HANDOFF - COPY THIS SECTION BACK INTO CHATGPT")

    print(f"Candidates profiled: {len(candidates)}")
    print(f"Candidate substations: {candidates['SubstationID'].nunique()}")
    print(f"EV flexibility scenario fraction: {EV_FLEX_FRACTION:.2f}")

    print("\nPV CAPACITY / UNIT INFERENCE:")
    if pv_rule is None:
        print("PV capacity field/unit could not be validated. Incremental PV profile should not be credited until reviewed.")
    else:
        print(f"Selected metadata field: {pv_rule['RawField']}")
        print(f"Selected assumed unit: {pv_rule['AssumedUnit']}")
        print(f"Scale to MW: {pv_rule['ScaleToMW']}")
        print(f"Median observed/rating ratio: {pv_rule['MedianObservedToRatingRatio']:.4f}")
        print(f"P95 observed/rating ratio: {pv_rule['P95ObservedToRatingRatio']:.4f}")
        print(f"Rating violation pct: {pv_rule['RatingViolationPct']:.2f}%")
        print(f"Eligible PV assets used for shape: {int(pv_assets['PVProfileEligible'].sum()) if 'PVProfileEligible' in pv_assets.columns else 0}/{len(pv_assets)}")

    print("\nPV CANDIDATE INPUT / OUTPUT SUMMARY:")
    pv_display = [column for column in ["CandidateKey", "GridID", "SubstationID", "ProposedPVCapacityMW", "MaximumAvailablePVMW", "AnnualMeanAvailablePVMW", "PVActiveForMILP", "PVProfileSource"] if column in pv_candidate_summary.columns]
    print(pv_candidate_summary[pv_display].to_string(index=False))

    if (pv_candidate_summary["ProposedPVCapacityMW"] <= 0).all():
        print(f"\nIMPORTANT: every {PV_CAPACITY_INPUT_COLUMN} is currently 0.0. The PV shape is ready, but the MILP will continue to give incremental PV zero credit until defensible proposed PV capacities are entered in {RESOURCE_INPUT_FILE}.")

    print("\nEV CHARGER POWER / UNIT INFERENCE:")
    if ev_rule is None:
        print("active_charging_power units could not be validated. EV rebound capacity should not be credited until reviewed.")
    else:
        print(f"Selected assumed unit: {ev_rule['AssumedUnit']}")
        print(f"Scale to MW: {ev_rule['ScaleToMW']}")
        print(f"Median observed/rating ratio: {ev_rule['MedianObservedToRatingRatio']:.4f}")
        print(f"P95 observed/rating ratio: {ev_rule['P95ObservedToRatingRatio']:.4f}")
        print(f"Rating violation pct: {ev_rule['RatingViolationPct']:.2f}%")
        print(f"EV assets with validated charger rating: {int(ev_assets['EVRatingValidated'].sum()) if 'EVRatingValidated' in ev_assets.columns else 0}/{len(ev_assets)}")

    print("\nEV FLEXIBILITY BY CANDIDATE SUBSTATION:")
    if ev_substation_summary.empty:
        print("No EV flexibility profiles produced.")
    else:
        print(ev_substation_summary.to_string(index=False))

    print("\nMILP-READY OUTPUTS:")
    print(f"PV candidate profile: {Path(PV_OUTPUT_FILE).resolve()}")
    print(f"EV substation flexibility profile: {Path(EV_OUTPUT_FILE).resolve()}")
    print(f"Normalized PV shape audit: {Path(PV_SHAPE_OUTPUT_FILE).resolve()}")
    print(f"Detailed profile audit: {Path(AUDIT_OUTPUT_FILE).resolve()}")
    print(f"Candidate resource inputs: {Path(RESOURCE_INPUT_FILE).resolve()}")

    print("\nINTERPRETATION:")
    print("1. Existing PV is not double-counted. It only supplies the normalized Greensboro solar production shape.")
    print("2. ProposedPVCapacityMW controls the amount of NEW PV available to each candidate. Zero means no incremental PV credit.")
    print("3. EV reduction is tied to actual observed charging at each five-minute interval; EV rebound is limited by validated spare charger power.")
    print("4. EV_FLEX_FRACTION is a scenario assumption and should be reported as such in the final model.")
    print("5. The final MILP should eventually enforce EV repayment over a realistic bounded shift horizon (for example same-day or several-hour windows), rather than allowing annual energy neutrality across an entire continuous year.")
    print("6. Paste this handoff back into ChatGPT before the final MILP rerun so the inferred units and resulting MW envelopes can be checked.")


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading("GREENSBORO PV / EV FLEXIBILITY PROFILE BUILDER")
    print("This script converts the resource audit findings into the exact PV and EV files consumed by the integrated MILP.")
    print("It queries the EDM directly so the prior resource audit workbook is not a runtime dependency.")

    candidates = load_candidate_context()
    resource_inputs = ensure_resource_input_workbook(candidates)

    print(f"\nCandidate context source: {candidates['CandidateContextSource'].iloc[0]}")
    print(f"Candidates loaded: {len(candidates)}")
    print(f"Candidate substations: {candidates['SubstationID'].nunique()}")
    print(f"Resource-input workbook preserved/refreshed: {Path(RESOURCE_INPUT_FILE).resolve()}")

    conn = connect_to_edm()

    try:
        heading("1. BUILDING RELEVANT FEEDER FOOTPRINT")
        breaker_map = get_breaker_substation_map(conn)
        relevant_feeders = build_relevant_feeders(candidates, breaker_map)
        feeder_ids = sorted(relevant_feeders["GridID"].astype(str).unique().tolist())

        print(f"Relevant feeders behind candidate substations: {len(feeder_ids)}")
        print(relevant_feeders[["SubstationID", "GridID", "BreakerID", "IsCandidateFeeder"]].to_string(index=False))

        heading("2. LOADING PV / EV ASSETS AND DATA SOURCES")
        assets = get_resource_assets(conn, feeder_ids)
        assets["ResourceType"] = assets.apply(classify_resource, axis=1)
        assets = assets.merge(relevant_feeders[["GridID", "SubstationID", "IsCandidateFeeder"]], on="GridID", how="left")
        sources = get_kwh_sources(conn, feeder_ids)

        pv_assets = assets[assets["ResourceType"] == "PV"].copy().reset_index(drop=True)
        ev_assets = assets[assets["ResourceType"] == "EV"].copy().reset_index(drop=True)

        print(f"PV assets found: {len(pv_assets)}")
        print(f"EV assets found: {len(ev_assets)}")

        heading("3. BUILDING EMPIRICAL PV PRODUCTION SHAPE")
        pv_asset_raw, pv_hourly = build_pv_asset_profiles(conn, pv_assets, sources)
        pv_rule, pv_unit_trials = infer_best_pv_capacity_field(pv_asset_raw)
        pv_shape_hourly, pv_asset_validated = build_normalized_pv_shape(pv_asset_raw, pv_hourly, pv_rule)
        pv_profile, pv_shape_5min, pv_candidate_summary = expand_pv_shape_to_5min(pv_shape_hourly, candidates, resource_inputs)

        pv_profile.to_csv(PV_OUTPUT_FILE, index=False, compression="gzip")
        pv_shape_5min.to_csv(PV_SHAPE_OUTPUT_FILE, index=False, compression="gzip")

        if pv_rule is None:
            print("PV metadata capacity/unit validation: UNRESOLVED")
        else:
            print(f"PV metadata capacity/unit validation: {pv_rule['RawField']} interpreted as {pv_rule['AssumedUnit']}")
            print(f"Eligible PV assets used for normalized profile: {int(pv_asset_validated['PVProfileEligible'].sum())}/{len(pv_asset_validated)}")

        print(f"PV MILP profile rows written: {len(pv_profile):,}")

        heading("4. BUILDING FIVE-MINUTE EV FLEXIBILITY PROFILE")
        ev_asset_raw, ev_5min = build_ev_asset_profiles(conn, ev_assets, sources)
        ev_rule, ev_unit_trials = infer_rating_scale(ev_asset_raw, "active_charging_power", "ObservedPeakMW", "EV", preferred_target_ratio=0.80)
        ev_station_profile, ev_asset_validated = build_ev_station_profiles(ev_asset_raw, ev_5min, ev_rule)
        ev_substation_summary = build_ev_substation_summary(ev_station_profile)

        required_ev_columns = ["TimestampUTC", "SubstationID", "AvailableEVReductionMW", "AvailableEVReboundMW"]
        if ev_station_profile.empty:
            pd.DataFrame(columns=required_ev_columns).to_csv(EV_OUTPUT_FILE, index=False, compression="gzip")
        else:
            ev_station_profile[required_ev_columns].to_csv(EV_OUTPUT_FILE, index=False, compression="gzip")

        if ev_rule is None:
            print("EV active_charging_power unit validation: UNRESOLVED")
        else:
            print(f"EV active_charging_power validation: interpreted as {ev_rule['AssumedUnit']}")
            print(f"Validated EV charger ratings: {int(ev_asset_validated['EVRatingValidated'].sum())}/{len(ev_asset_validated)}")

        if not ev_substation_summary.empty:
            print("\nEV flexibility summary:")
            print(ev_substation_summary.to_string(index=False))

        print(f"\nEV MILP profile rows written: {len(ev_station_profile):,}")

        heading("5. WRITING PROFILE AUDIT WORKBOOK")
        workbook_frames = {
            "PV_Candidate_Summary": pv_candidate_summary,
            "PV_Asset_Validation": pv_asset_validated,
            "PV_Unit_Trials": pv_unit_trials,
            "EV_Substation_Summary": ev_substation_summary,
            "EV_Asset_Validation": ev_asset_validated,
            "EV_Unit_Trials": ev_unit_trials,
            "Relevant_Feeder_Map": relevant_feeders,
            "Candidate_Context": candidates,
            "Candidate_Resources": resource_inputs,
            "Assumptions": build_assumptions()
        }

        with pd.ExcelWriter(AUDIT_OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe = make_excel_safe(frame)
                safe.to_excel(writer, sheet_name=sheet_name[:31], index=False)
                format_excel(writer, sheet_name[:31], safe)

        print(f"Profile audit workbook: {Path(AUDIT_OUTPUT_FILE).resolve()}")

        print_handoff(
            candidates=candidates,
            resource_inputs=resource_inputs,
            pv_rule=pv_rule,
            pv_unit_trials=pv_unit_trials,
            pv_assets=pv_asset_validated,
            pv_candidate_summary=pv_candidate_summary,
            ev_rule=ev_rule,
            ev_unit_trials=ev_unit_trials,
            ev_assets=ev_asset_validated,
            ev_substation_summary=ev_substation_summary
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
