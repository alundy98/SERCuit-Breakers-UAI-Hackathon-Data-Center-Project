import os
import json
import math
import uuid
import decimal
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2
import networkx as nx
from dotenv import load_dotenv

RANKING_FILE = "greensboro_substation_constrained_location_rankings.xlsx"
RANKING_SHEET = "Final_Location_Ranking"

OUTPUT_FILE = "greensboro_final_thermal_hosting_results.xlsx"
INTERVAL_OUTPUT_FILE = "greensboro_5min_candidate_hosting_2024.csv.gz"

YEAR = 2024
BASE_POWER_FACTOR = 0.95
POWER_FACTOR_SENSITIVITY = [0.90, 0.95, 1.00]

TOP_FEEDERS = 10
CANDIDATES_PER_FEEDER = 1
TEST_DC_LEVELS_MW = [5.0, 7.5, 10.0, 12.5, 15.0]

BREAKER_INTERVAL_MINUTES = 5
MAX_BREAKER_INTERPOLATION_GAP_INTERVALS = 12
MAX_METER_INTERPOLATION_GAP_HOURS = 6
MIN_METER_TOTAL_KWH_FOR_DIRECT_WEIGHTS = 1e-9

PRIMARY_ONLY = True
MIN_TOPOLOGY_MAPPING_PCT_FOR_READY = 95.0
MAX_ACCEPTABLE_METER_BREAKER_NMAE_PCT = 20.0
MIN_ACCEPTABLE_METER_BREAKER_CORRELATION = 0.90

SERIES_CLASSES = {"Line", "Transformer", "Switch", "Recloser", "Fuse", "Regulator"}

load_dotenv()
DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")

# PURPOSE:
# Calculates five-minute thermal hosting capacity for shortlisted data-center candidate locations using feeder topology, breaker SCADA, meter load shares, and equipment ratings.
# WHY: It converts the earlier location screening into a time-series physical feasibility result, showing how much additional load each site can support before a breaker, line, or transformer reaches its modeled rating.
# DEPENDENCIES: Reads greensboro_substation_constrained_location_rankings.xlsx, queries feeder assets and time-series data from the EDM using .env credentials, and uses the selected candidate locations from the prior screening/ranking stage.
# OUTPUT: Writes greensboro_final_thermal_hosting_results.xlsx and greensboro_5min_candidate_hosting_2024.csv.gz, which feed the validation/audit and MILP stages.

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


def safe_query_dataframe(conn, query, params=None):
    savepoint = "thermal_query"
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SAVEPOINT {savepoint}")
        df = query_dataframe(conn, query, params)
        with conn.cursor() as cursor:
            cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        return df, None
    except Exception as exc:
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
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
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except Exception:
        return np.nan


def truthy(value):
    if is_missing(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def parse_meta(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def flatten_dict(data, prefix=""):
    output = {}
    if not isinstance(data, dict):
        return output
    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)
        if isinstance(value, dict):
            output.update(flatten_dict(value, full_key))
        else:
            output[full_key] = value
    return output


def normalize_key(value):
    return "".join(character for character in str(value).lower() if character.isalnum())


def meta_find(meta, candidate_keys):
    flattened = flatten_dict(parse_meta(meta))
    normalized_candidates = [normalize_key(key) for key in candidate_keys]
    for key, value in flattened.items():
        normalized_key = normalize_key(key)
        for candidate in normalized_candidates:
            if normalized_key.endswith(candidate):
                return value
    return None


def normalize_voltage_kv(value):
    value = safe_float(value)
    if np.isnan(value) or value <= 0:
        return np.nan
    return value / 1000.0 if value > 100 else value


def phase_count(phases):
    if is_missing(phases):
        return 3
    text = str(phases).upper()
    present = {phase for phase in ["A", "B", "C"] if phase in text}
    return len(present) if present else 3


def classify_element(element_type, is_switchable=False):
    text = str(element_type).lower()
    if "breaker" in text:
        return "Breaker"
    if "transformer" in text:
        return "Transformer"
    if "recloser" in text:
        return "Recloser"
    if "fuse" in text:
        return "Fuse"
    if "regulator" in text:
        return "Regulator"
    if "line" in text:
        return "Line"
    if "switch" in text or truthy(is_switchable):
        return "Switch"
    if "meter" in text:
        return "Meter"
    if "solar" in text or "photovoltaic" in text or text == "pv":
        return "PV"
    if "ev" in text:
        return "EV"
    return "Other"


def is_series_element(row):
    return classify_element(row.get("type"), row.get("is_switchable")) in SERIES_CLASSES


def line_capacity_from_values(rating_a, voltage_kv, phases, power_factor):
    rating_a = safe_float(rating_a)
    voltage_kv = safe_float(voltage_kv)
    phases = int(phases) if not is_missing(phases) else 3

    if np.isnan(rating_a) or np.isnan(voltage_kv) or rating_a <= 0 or voltage_kv <= 0:
        return np.nan

    if phases >= 3:
        apparent_mva = math.sqrt(3) * voltage_kv * rating_a / 1000.0
    elif phases == 2:
        apparent_mva = voltage_kv * rating_a / 1000.0
    else:
        apparent_mva = (voltage_kv / math.sqrt(3)) * rating_a / 1000.0

    return apparent_mva * power_factor


def line_capacity_record(row, power_factor=BASE_POWER_FACTOR):
    rating_a = safe_float(meta_find(row.get("meta"), ["rating_a", "rated_a", "ampacity", "current_rating"]))
    voltage_kv = normalize_voltage_kv(meta_find(row.get("meta"), ["ph_ph_voltage", "phase_phase_voltage", "line_voltage"]))
    phases = phase_count(row.get("phases"))
    capacity_mw = line_capacity_from_values(rating_a, voltage_kv, phases, power_factor)

    if np.isnan(capacity_mw):
        method = "MissingRatingOrVoltage"
    elif phases >= 3:
        method = "3Phase_sqrt3_VLL_I_x_PF"
    elif phases == 2:
        method = "2Phase_VLL_I_x_PF"
    else:
        method = "1Phase_VLN_I_x_PF"

    return {
        "CapacityMW": capacity_mw,
        "RatingA": rating_a,
        "VoltageKV": voltage_kv,
        "RatingKVA": np.nan,
        "PhaseCount": phases,
        "CapacityMethod": method
    }


def transformer_capacity_record(row, power_factor=BASE_POWER_FACTOR):
    rating_kva = safe_float(meta_find(row.get("meta"), ["rating_kva", "rated_kva", "kva_rating"]))
    capacity_mw = rating_kva / 1000.0 * power_factor if not np.isnan(rating_kva) and rating_kva > 0 else np.nan
    return {
        "CapacityMW": capacity_mw,
        "RatingA": np.nan,
        "VoltageKV": normalize_voltage_kv(meta_find(row.get("meta"), ["primary_voltage", "secondary_voltage"])),
        "RatingKVA": rating_kva,
        "PhaseCount": phase_count(row.get("phases")),
        "CapacityMethod": "Transformer_kVA_x_PF" if not np.isnan(capacity_mw) else "MissingTransformerRating"
    }


def breaker_capacity_record(row, power_factor=BASE_POWER_FACTOR):
    rating_kva = safe_float(meta_find(row.get("meta"), ["rating_kva", "rated_kva", "kva_rating"]))
    capacity_mw = rating_kva / 1000.0 * power_factor if not np.isnan(rating_kva) and rating_kva > 0 else np.nan
    return {
        "CapacityMW": capacity_mw,
        "RatingA": safe_float(meta_find(row.get("meta"), ["rating_a", "rated_a"])),
        "VoltageKV": normalize_voltage_kv(meta_find(row.get("meta"), ["ph_ph_voltage", "voltage", "primary_voltage"])),
        "RatingKVA": rating_kva,
        "PhaseCount": phase_count(row.get("phases")),
        "CapacityMethod": "Breaker_kVA_x_PF" if not np.isnan(capacity_mw) else "MissingBreakerRating"
    }


def element_capacity_record(row, power_factor=BASE_POWER_FACTOR):
    element_class = classify_element(row.get("type"), row.get("is_switchable"))
    if element_class == "Line":
        return line_capacity_record(row, power_factor)
    if element_class == "Transformer":
        return transformer_capacity_record(row, power_factor)
    if element_class == "Breaker":
        return breaker_capacity_record(row, power_factor)
    return {
        "CapacityMW": np.nan,
        "RatingA": np.nan,
        "VoltageKV": np.nan,
        "RatingKVA": np.nan,
        "PhaseCount": phase_count(row.get("phases")),
        "CapacityMethod": "ConnectivityOnly"
    }


def capacity_at_power_factor(record, power_factor):
    element_class = record.get("ElementClass")
    if element_class == "Line":
        return line_capacity_from_values(record.get("RatingA"), record.get("VoltageKV"), record.get("PhaseCount"), power_factor)
    if element_class in {"Transformer", "Breaker"}:
        rating_kva = safe_float(record.get("RatingKVA"))
        return rating_kva / 1000.0 * power_factor if not np.isnan(rating_kva) and rating_kva > 0 else np.nan
    return np.nan


def load_candidates():
    path = Path(RANKING_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {RANKING_FILE}. Place the current ranking workbook beside this script.")

    df = pd.read_excel(path, sheet_name=RANKING_SHEET)

    required = {"GridID", "CandidateLineID"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{RANKING_FILE} is missing required columns: {sorted(missing)}")

    for column in ["FinalBaseHostingMW", "FeederPathHostingMW", "EstimatedBaseHostingMW", "GlobalRank", "RankWithinFeeder"]:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["ScreeningHostingMW"] = df["FinalBaseHostingMW"].combine_first(df["FeederPathHostingMW"]).combine_first(df["EstimatedBaseHostingMW"])
    df = df[df["ScreeningHostingMW"].notna()].copy()

    if df.empty:
        raise RuntimeError("No candidate rows with a usable hosting estimate were found.")

    for column in ["SubstationID", "CandidateNode", "CandidateConfidence"]:
        if column not in df.columns:
            df[column] = None

    feeder_best = df.sort_values(["GridID", "ScreeningHostingMW", "GlobalRank"], ascending=[True, False, True], na_position="last").groupby("GridID", as_index=False).head(1)
    target_feeders = feeder_best.sort_values(["ScreeningHostingMW", "GlobalRank"], ascending=[False, True], na_position="last").head(TOP_FEEDERS)["GridID"].astype(str).tolist()

    selected = df[df["GridID"].astype(str).isin(target_feeders)].copy()
    selected = selected.sort_values(["GridID", "ScreeningHostingMW", "GlobalRank"], ascending=[True, False, True], na_position="last").groupby("GridID", as_index=False).head(CANDIDATES_PER_FEEDER)
    selected["CandidateKey"] = selected["GridID"].astype(str) + "|" + selected["CandidateLineID"].astype(str)

    return selected.reset_index(drop=True), target_feeders


def get_grid_elements(conn, grid_ids):
    return query_dataframe(conn, """
        SELECT grid_id, grid_element_id, type, customer_type, phases, is_underground, is_producer, is_consumer,
               is_switchable, switch_is_open, terminal1_cn, terminal2_cn, power_flow_direction,
               upstream_grid_element_id, meta
        FROM grid_element
        WHERE grid_id = ANY(%s)
        ORDER BY grid_id, grid_element_id;
    """, (grid_ids,))


def get_kwh_sources(conn, grid_id, element_type_pattern):
    return query_dataframe(conn, """
        SELECT ge.grid_element_id AS "GridElementID",
               ds.grid_element_data_source_id AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.phases AS "Phase",
               ds.direction AS "Direction",
               ds.valid AS "ValidRange"
        FROM grid_element ge
        JOIN grid_element_data_source ds
          ON ds.grid_id = ge.grid_id
         AND ds.grid_element_id = ge.grid_element_id
        WHERE ge.grid_id = %s
          AND LOWER(ge.type) LIKE %s
          AND 'kWh' = ANY(ds.metrics)
        ORDER BY ge.grid_element_id, ds.grid_element_data_source_id;
    """, (grid_id, f"%{element_type_pattern.lower()}%"))


def load_raw_kwh_source(conn, source_id):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    df = query_dataframe(conn, """
        SELECT timestamp, value
        FROM ts_data_source_select(%s::uuid, 'kWh', tstzrange(%s::timestamptz, %s::timestamptz, '[)'))
        ORDER BY timestamp;
    """, (str(source_id), start, end))

    if df.empty:
        return pd.Series(dtype=float)

    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True, errors="coerce")
    df["value"] = pd.to_numeric(df["value"], errors="coerce")
    df = df.dropna(subset=["timestamp", "value"]).sort_values("timestamp")
    return pd.Series(df["value"].to_numpy(dtype=float), index=df["timestamp"])


def load_breaker_5min_profile(conn, grid_id):
    sources = get_kwh_sources(conn, grid_id, "breaker")
    if sources.empty:
        raise RuntimeError(f"{grid_id}: no breaker kWh sources found.")

    source_series = []
    raw_interval_minutes = []
    total_sources = len(sources)

    for _, source in sources.iterrows():
        raw = load_raw_kwh_source(conn, source["DataSourceID"])
        if raw.empty:
            continue

        diffs = raw.index.to_series().diff().dt.total_seconds().div(60)
        valid_diffs = diffs[(diffs > 0) & (diffs <= 60)]
        if not valid_diffs.empty:
            raw_interval_minutes.append(float(valid_diffs.median()))

        five_minute = raw.groupby(raw.index.floor(f"{BREAKER_INTERVAL_MINUTES}min")).sum()
        source_series.append(five_minute.rename(str(source["DataSourceID"])))

    if not source_series:
        raise RuntimeError(f"{grid_id}: breaker kWh sources were found but returned no {YEAR} data.")

    source_frame = pd.concat(source_series, axis=1).sort_index()
    full_index = pd.date_range(source_frame.index.min(), source_frame.index.max(), freq=f"{BREAKER_INTERVAL_MINUTES}min", tz="UTC")
    source_frame = source_frame.reindex(full_index)

    missing_before = int(source_frame.isna().sum().sum())
    source_frame = source_frame.interpolate(method="time", limit=MAX_BREAKER_INTERPOLATION_GAP_INTERVALS, limit_direction="both")
    missing_after_fill = int(source_frame.isna().sum().sum())

    valid_rows = source_frame.notna().all(axis=1)
    accepted = source_frame.loc[valid_rows].copy()

    if accepted.empty:
        raise RuntimeError(f"{grid_id}: no complete breaker intervals remained after limited interpolation.")

    breaker_kwh = accepted.sum(axis=1)
    interval_hours = BREAKER_INTERVAL_MINUTES / 60.0
    breaker_mw = breaker_kwh / 1000.0 / interval_hours

    diagnostics = {
        "BreakerSourceCount": total_sources,
        "BreakerMedianRawIntervalMinutes": float(np.median(raw_interval_minutes)) if raw_interval_minutes else np.nan,
        "BreakerExpected5MinIntervals": len(full_index),
        "BreakerAccepted5MinIntervals": len(accepted),
        "Breaker5MinCoveragePct": len(accepted) / len(full_index) * 100.0 if len(full_index) else np.nan,
        "BreakerMissingSourceCellsBeforeFill": missing_before,
        "BreakerMissingSourceCellsAfterLimitedFill": missing_after_fill,
        "BreakerPeakMW": float(np.abs(breaker_mw).max()),
        "BreakerP95AbsMW": float(np.abs(breaker_mw).quantile(0.95))
    }

    return breaker_mw, breaker_kwh, sources, diagnostics


def load_meter_hourly_matrix(conn, grid_id, meter_ids, required_hours):
    sources = get_kwh_sources(conn, grid_id, "meter")
    meter_id_set = {str(value) for value in meter_ids}
    sources = sources[sources["GridElementID"].astype(str).isin(meter_id_set)].copy()

    if sources.empty:
        raise RuntimeError(f"{grid_id}: no meter kWh sources found.")

    meter_series = {}
    source_counts = sources.groupby("GridElementID")["DataSourceID"].nunique().to_dict()
    available_meter_ids = set(sources["GridElementID"].astype(str))
    missing_source_meters = [meter_id for meter_id in meter_ids if str(meter_id) not in available_meter_ids]
    total_sources = len(sources)

    for source_number, (_, source) in enumerate(sources.iterrows(), start=1):
        meter_id = str(source["GridElementID"])
        raw = load_raw_kwh_source(conn, source["DataSourceID"])

        if raw.empty:
            continue

        hourly = raw.groupby(raw.index.floor("h")).sum().reindex(required_hours)

        if meter_id not in meter_series:
            meter_series[meter_id] = hourly.copy()
        else:
            meter_series[meter_id] = meter_series[meter_id].add(hourly, fill_value=0.0)

        if source_number == 1 or source_number % 50 == 0 or source_number == total_sources:
            print(f"    Loaded {source_number:,}/{total_sources:,} meter kWh sources")

    matrix = pd.DataFrame(index=required_hours)

    for meter_id in meter_ids:
        meter_id = str(meter_id)
        matrix[meter_id] = meter_series[meter_id].reindex(required_hours) if meter_id in meter_series else np.nan

    total_cells = int(matrix.shape[0] * matrix.shape[1])
    missing_before = int(matrix.isna().sum().sum())

    matrix = matrix.interpolate(method="time", limit=MAX_METER_INTERPOLATION_GAP_HOURS, limit_direction="both")
    column_medians = matrix.median(axis=0, skipna=True)
    matrix = matrix.fillna(column_medians)

    all_nan_columns = matrix.columns[matrix.isna().any()].tolist()
    if all_nan_columns:
        matrix[all_nan_columns] = matrix[all_nan_columns].fillna(0.0)

    missing_after = int(matrix.isna().sum().sum())
    negative_cells = int((matrix < 0).sum().sum())

    diagnostics = {
        "MeterCount": len(meter_ids),
        "MeterSourceCount": total_sources,
        "MetersWithoutKWhSource": len(missing_source_meters),
        "MetersWithMultipleKWhSources": int(sum(1 for count in source_counts.values() if count > 1)),
        "TotalMeterHourCells": total_cells,
        "MissingMeterHourCellsBeforeFill": missing_before,
        "MissingMeterHourCellsAfterFill": missing_after,
        "MeterHourCellsFilled": missing_before - missing_after,
        "MeterHourFillPct": (missing_before - missing_after) / total_cells * 100.0 if total_cells else np.nan,
        "NegativeMeterHourCells": negative_cells,
        "NegativeMeterHourPct": negative_cells / total_cells * 100.0 if total_cells else np.nan
    }

    return matrix.astype(float), sources, diagnostics


def hourly_breaker_kwh_from_5min(breaker_kwh_5min, required_hours):
    return breaker_kwh_5min.groupby(breaker_kwh_5min.index.floor("h")).sum().reindex(required_hours)


def meter_breaker_reconciliation(raw_meter_kwh, breaker_hourly_kwh):
    raw_meter_total = raw_meter_kwh.sum(axis=1)
    aligned = pd.concat([raw_meter_total.rename("MeterKWh"), breaker_hourly_kwh.rename("BreakerKWh")], axis=1).dropna()

    if aligned.empty:
        return {
            "MeterBreakerCorrelation": np.nan,
            "MeterBreakerNMAEPct": np.nan,
            "MeterBreakerEnergyRatio": np.nan,
            "MeterBreakerMatchedHours": 0
        }

    correlation = aligned["MeterKWh"].corr(aligned["BreakerKWh"])
    error = aligned["MeterKWh"] - aligned["BreakerKWh"]
    mae = error.abs().mean()
    mean_breaker = aligned["BreakerKWh"].abs().mean()
    nmae = mae / mean_breaker * 100.0 if mean_breaker > 0 else np.nan
    energy_ratio = aligned["MeterKWh"].sum() / aligned["BreakerKWh"].sum() if aligned["BreakerKWh"].sum() != 0 else np.nan

    return {
        "MeterBreakerCorrelation": float(correlation) if pd.notna(correlation) else np.nan,
        "MeterBreakerNMAEPct": float(nmae) if pd.notna(nmae) else np.nan,
        "MeterBreakerEnergyRatio": float(energy_ratio) if pd.notna(energy_ratio) else np.nan,
        "MeterBreakerMatchedHours": len(aligned)
    }


def build_hourly_meter_weights(raw_meter_kwh):
    raw_total = raw_meter_kwh.sum(axis=1)
    historical_abs = raw_meter_kwh.abs().mean(axis=0)
    fallback_weights = historical_abs / historical_abs.sum() if historical_abs.sum() > 0 else pd.Series(1.0 / len(raw_meter_kwh.columns), index=raw_meter_kwh.columns)

    weights = pd.DataFrame(index=raw_meter_kwh.index, columns=raw_meter_kwh.columns, dtype=float)
    direct_hours = 0
    fallback_hours = 0
    extreme_scale_hours = 0
    implied_scale_factors = pd.Series(index=raw_meter_kwh.index, dtype=float)

    for timestamp in raw_meter_kwh.index:
        total = raw_total.loc[timestamp]

        if pd.notna(total) and abs(total) > MIN_METER_TOTAL_KWH_FOR_DIRECT_WEIGHTS:
            weights.loc[timestamp, :] = raw_meter_kwh.loc[timestamp, :] / total
            direct_hours += 1
        else:
            weights.loc[timestamp, :] = fallback_weights
            fallback_hours += 1

    row_sums = weights.sum(axis=1)
    max_weight_sum_error = float((row_sums - 1.0).abs().max()) if len(row_sums) else np.nan

    return weights.astype(float), {
        "DirectMeterWeightHours": direct_hours,
        "FallbackMeterWeightHours": fallback_hours,
        "MaxHourlyMeterWeightSumError": max_weight_sum_error,
        "ExtremeCalibrationHours": extreme_scale_hours
    }


def identify_breaker(feeder_elements):
    breakers = feeder_elements[feeder_elements["type"].astype(str).str.contains("breaker", case=False, na=False)].copy()
    if breakers.empty:
        return None
    breakers["ProducerSort"] = breakers["is_producer"].fillna(False).astype(bool)
    return breakers.sort_values(["ProducerSort", "grid_element_id"], ascending=[False, True]).iloc[0]


def get_element_record(row, power_factor=BASE_POWER_FACTOR):
    element_class = classify_element(row.get("type"), row.get("is_switchable"))
    capacity = element_capacity_record(row, power_factor)
    return {
        "ElementID": str(row["grid_element_id"]),
        "ElementType": row.get("type"),
        "ElementClass": element_class,
        "CapacityMW": capacity["CapacityMW"],
        "RatingA": capacity["RatingA"],
        "VoltageKV": capacity["VoltageKV"],
        "RatingKVA": capacity["RatingKVA"],
        "PhaseCount": capacity["PhaseCount"],
        "CapacityMethod": capacity["CapacityMethod"]
    }


def choose_conservative_parallel_record(records):
    constrained = [record for record in records if record["ElementClass"] in {"Line", "Transformer"} and pd.notna(record["CapacityMW"])]
    if constrained:
        return min(constrained, key=lambda record: record["CapacityMW"])
    return records[0]


def build_network_graph(feeder_elements, breaker_id):
    graph = nx.Graph()
    edge_lookup = {}
    pair_records = defaultdict(list)
    skipped_open_switches = 0
    skipped_missing_terminals = 0

    for _, row in feeder_elements.iterrows():
        element_id = str(row["grid_element_id"])

        if element_id == str(breaker_id) or not is_series_element(row):
            continue

        if truthy(row.get("is_switchable")) and truthy(row.get("switch_is_open")):
            skipped_open_switches += 1
            continue

        terminal1 = row.get("terminal1_cn")
        terminal2 = row.get("terminal2_cn")

        if is_missing(terminal1) or is_missing(terminal2):
            skipped_missing_terminals += 1
            continue

        terminal1 = str(terminal1)
        terminal2 = str(terminal2)

        if terminal1 == terminal2:
            continue

        record = get_element_record(row, BASE_POWER_FACTOR)
        pair_key = tuple(sorted((terminal1, terminal2)))
        pair_records[pair_key].append(record)
        edge_lookup[element_id] = (terminal1, terminal2)

    parallel_pair_count = 0

    for pair_key, records in pair_records.items():
        terminal1, terminal2 = pair_key
        representative = choose_conservative_parallel_record(records)
        parallel_count = len(records)

        if parallel_count > 1:
            parallel_pair_count += 1

        graph.add_edge(
            terminal1,
            terminal2,
            element_id=representative["ElementID"],
            element_type=representative["ElementType"],
            element_class=representative["ElementClass"],
            capacity_mw=representative["CapacityMW"],
            rating_a=representative["RatingA"],
            voltage_kv=representative["VoltageKV"],
            rating_kva=representative["RatingKVA"],
            phase_count=representative["PhaseCount"],
            capacity_method=representative["CapacityMethod"],
            parallel_element_count=parallel_count,
            parallel_element_ids=" | ".join(record["ElementID"] for record in records),
            parallel_records=records
        )

    diagnostics = {
        "ParallelNodePairCount": parallel_pair_count,
        "SkippedOpenSwitches": skipped_open_switches,
        "SkippedMissingTerminalElements": skipped_missing_terminals
    }

    return graph, edge_lookup, pair_records, diagnostics


def choose_root_node(graph, breaker_row):
    terminals = [str(value) for value in [breaker_row.get("terminal1_cn"), breaker_row.get("terminal2_cn")] if not is_missing(value)]
    component_sizes = {}

    for terminal in terminals:
        component_sizes[terminal] = len(nx.node_connected_component(graph, terminal)) if terminal in graph else 0

    if not component_sizes:
        return None, {}

    root_node = max(component_sizes, key=component_sizes.get)
    return (root_node if component_sizes[root_node] > 0 else None), component_sizes


def build_shortest_path_tree(graph, root_node):
    connected_nodes = nx.node_connected_component(graph, root_node)
    connected_graph = graph.subgraph(connected_nodes).copy()

    cycle_rank = connected_graph.number_of_edges() - connected_graph.number_of_nodes() + nx.number_connected_components(connected_graph)
    is_radial = cycle_rank == 0

    paths = nx.single_source_shortest_path(connected_graph, root_node)
    depths = {node: len(path) - 1 for node, path in paths.items()}
    parents = {}
    parent_edges = {}

    for node, path in paths.items():
        if node == root_node or len(path) < 2:
            continue
        parent = path[-2]
        parents[node] = parent
        parent_edges[node] = dict(connected_graph[parent][node])

    return connected_graph, paths, depths, parents, parent_edges, cycle_rank, is_radial


def get_meter_rows(feeder_elements):
    return feeder_elements[feeder_elements["type"].astype(str).str.contains("meter", case=False, na=False)].copy()


def choose_meter_node(row, paths, depths, edge_lookup):
    reachable_terminals = []

    for value in [row.get("terminal1_cn"), row.get("terminal2_cn")]:
        if is_missing(value):
            continue
        node = str(value)
        if node in paths:
            reachable_terminals.append(node)

    if reachable_terminals:
        return max(reachable_terminals, key=lambda node: depths.get(node, -1)), "DirectTerminal"

    upstream_id = row.get("upstream_grid_element_id")

    if not is_missing(upstream_id):
        upstream_id = str(upstream_id)

        if upstream_id in edge_lookup:
            node_a, node_b = edge_lookup[upstream_id]
            reachable = [node for node in [node_a, node_b] if node in depths]

            if reachable:
                return max(reachable, key=lambda node: depths[node]), "UpstreamElement"

    return None, "Unresolved"


def build_meter_mapping(feeder_elements, meter_ids, paths, depths, edge_lookup, root_node):
    meter_rows = get_meter_rows(feeder_elements).copy()
    meter_rows["GridElementIDString"] = meter_rows["grid_element_id"].astype(str)
    meter_lookup = meter_rows.set_index("GridElementIDString").to_dict("index")
    rows = []

    for meter_id in meter_ids:
        meter_id = str(meter_id)
        row = meter_lookup.get(meter_id)

        if row is None:
            rows.append({"MeterID": meter_id, "LoadNode": root_node, "MappingMethod": "MissingElementRow_RootFallback", "Resolved": False, "Depth": 0})
            continue

        node, method = choose_meter_node(row, paths, depths, edge_lookup)

        if node is None:
            rows.append({"MeterID": meter_id, "LoadNode": root_node, "MappingMethod": "RootFallback", "Resolved": False, "Depth": 0})
        else:
            rows.append({"MeterID": meter_id, "LoadNode": node, "MappingMethod": method, "Resolved": True, "Depth": depths.get(node, 0)})

    return pd.DataFrame(rows)


def aggregate_hourly_meter_weights_to_tree(hourly_weights, meter_mapping, root_node, depths, parents, parent_edges):
    node_shares = {}
    mapping_lookup = meter_mapping.set_index("MeterID")["LoadNode"].to_dict()

    for meter_id in hourly_weights.columns:
        node = mapping_lookup.get(str(meter_id), root_node)
        values = hourly_weights[meter_id].to_numpy(dtype=np.float64)

        if node not in node_shares:
            node_shares[node] = values.copy()
        else:
            node_shares[node] += values

    element_hourly_shares = {}
    nodes_descending = sorted(depths.keys(), key=lambda node: depths[node], reverse=True)

    for node in nodes_descending:
        if node == root_node or node not in node_shares:
            continue

        values = node_shares[node]
        parent = parents.get(node)

        if parent is None:
            continue

        edge = parent_edges[node]
        element_id = str(edge.get("element_id"))
        element_hourly_shares[element_id] = values.copy()

        if parent not in node_shares:
            node_shares[parent] = values.copy()
        else:
            node_shares[parent] += values

    root_share = node_shares.get(root_node, np.zeros(len(hourly_weights.index), dtype=float))
    return element_hourly_shares, root_share


def build_hour_position_index(hourly_index, five_minute_index):
    hourly_lookup = pd.Series(np.arange(len(hourly_index), dtype=int), index=hourly_index)
    five_minute_hours = five_minute_index.floor("h")
    positions = hourly_lookup.reindex(five_minute_hours).to_numpy()

    if pd.isna(positions).any():
        missing_count = int(pd.isna(positions).sum())
        raise RuntimeError(f"{missing_count:,} breaker intervals could not be matched to an hourly meter-weight period.")

    return positions.astype(int)


def element_5min_existing_flow(element_id, element_hourly_shares, hour_positions, breaker_mw_values):
    hourly_share = element_hourly_shares.get(str(element_id))

    if hourly_share is None:
        return np.zeros(len(breaker_mw_values), dtype=float)

    five_minute_share = hourly_share[hour_positions]
    return five_minute_share * breaker_mw_values


def candidate_path(graph, candidate_row, paths, depths, edge_lookup):
    candidate_line_id = str(candidate_row["CandidateLineID"])
    requested_node = candidate_row.get("CandidateNode")

    if not is_missing(requested_node) and str(requested_node) in paths:
        downstream_node = str(requested_node)
    elif candidate_line_id in edge_lookup:
        node_a, node_b = edge_lookup[candidate_line_id]
        reachable = [node for node in [node_a, node_b] if node in depths]

        if not reachable:
            return None, [], "CandidateLineNotReachable"

        downstream_node = max(reachable, key=lambda node: depths[node])
    else:
        return None, [], "CandidateLineNotInGraph"

    node_path = paths.get(downstream_node)

    if not node_path:
        return None, [], "CandidateNodeNotReachable"

    records = []
    candidate_pair_seen = False

    for index in range(len(node_path) - 1):
        node_a = node_path[index]
        node_b = node_path[index + 1]
        edge = dict(graph[node_a][node_b])
        parallel_ids = str(edge.get("parallel_element_ids", "")).split(" | ")

        if candidate_line_id in parallel_ids:
            candidate_pair_seen = True

        records.append({
            "FromNode": node_a,
            "ToNode": node_b,
            "ElementID": str(edge.get("element_id")),
            "ElementType": edge.get("element_type"),
            "ElementClass": edge.get("element_class"),
            "CapacityMW": safe_float(edge.get("capacity_mw")),
            "RatingA": safe_float(edge.get("rating_a")),
            "VoltageKV": safe_float(edge.get("voltage_kv")),
            "RatingKVA": safe_float(edge.get("rating_kva")),
            "PhaseCount": edge.get("phase_count"),
            "CapacityMethod": edge.get("capacity_method"),
            "ParallelElementCount": int(edge.get("parallel_element_count", 1)),
            "ParallelElementIDs": edge.get("parallel_element_ids")
        })

    if not candidate_pair_seen:
        return downstream_node, records, "CandidateLineNotOnChosenTreePath"

    if PRIMARY_ONLY and any(record["ElementClass"] == "Transformer" for record in records):
        return downstream_node, records, "CandidateDownstreamOfTransformer"

    return downstream_node, records, "Ready"


def build_element_load_audit(grid_id, graph, element_hourly_shares, hour_positions, breaker_mw, selected_candidate_path_ids):
    rows = []
    breaker_values = breaker_mw.to_numpy(dtype=float)

    for _, _, edge in graph.edges(data=True):
        element_class = edge.get("element_class")

        if element_class not in {"Line", "Transformer"}:
            continue

        element_id = str(edge.get("element_id"))
        record = {
            "ElementID": element_id,
            "ElementClass": element_class,
            "RatingA": safe_float(edge.get("rating_a")),
            "VoltageKV": safe_float(edge.get("voltage_kv")),
            "RatingKVA": safe_float(edge.get("rating_kva")),
            "PhaseCount": edge.get("phase_count")
        }

        capacity_mw = capacity_at_power_factor(record, BASE_POWER_FACTOR)
        existing = element_5min_existing_flow(element_id, element_hourly_shares, hour_positions, breaker_values)
        absolute_existing = np.abs(existing)

        if len(existing) == 0:
            continue

        peak_position = int(np.argmax(absolute_existing))
        rating_exceedance = absolute_existing > capacity_mw if pd.notna(capacity_mw) and capacity_mw > 0 else np.zeros(len(existing), dtype=bool)

        rows.append({
            "GridID": grid_id,
            "GridElementID": element_id,
            "ElementType": edge.get("element_type"),
            "ElementClass": element_class,
            "CapacityMW_BasePF": capacity_mw,
            "PeakExistingAbsMW": float(absolute_existing[peak_position]),
            "PeakExistingSignedMW": float(existing[peak_position]),
            "P95ExistingAbsMW": float(np.quantile(absolute_existing, 0.95)),
            "PeakUtilizationPct": float(absolute_existing[peak_position] / capacity_mw * 100.0) if pd.notna(capacity_mw) and capacity_mw > 0 else np.nan,
            "RatingExceedanceIntervals": int(np.sum(rating_exceedance)),
            "RatingExceedanceHoursEquivalent": float(np.sum(rating_exceedance) * BREAKER_INTERVAL_MINUTES / 60.0),
            "RatingExceedancePctIntervals": float(np.mean(rating_exceedance) * 100.0),
            "PeakTimestampUTC": breaker_mw.index[peak_position],
            "OnSelectedCandidatePath": element_id in selected_candidate_path_ids,
            "ParallelElementCount": int(edge.get("parallel_element_count", 1)),
            "ParallelElementIDs": edge.get("parallel_element_ids"),
            "CapacityMethod": edge.get("capacity_method"),
            "AuditInterpretation": "Modeled existing absolute flow exceeds PF-adjusted equipment rating" if np.any(rating_exceedance) else "No modeled rating exceedance"
        })

    return pd.DataFrame(rows)


def build_path_constraint_arrays(path_records, breaker_row, breaker_mw, element_hourly_shares, hour_positions, power_factor):
    breaker_values = breaker_mw.to_numpy(dtype=float)
    arrays = []
    records = []
    missing_capacity = []

    breaker_record = get_element_record(breaker_row, power_factor)
    breaker_record["ElementClass"] = "Breaker"
    breaker_record["ElementID"] = str(breaker_row["grid_element_id"])
    breaker_capacity = capacity_at_power_factor(breaker_record, power_factor)

    if np.isnan(breaker_capacity) or breaker_capacity <= 0:
        missing_capacity.append(breaker_record["ElementID"])
    else:
        existing = breaker_values.copy()
        baseline_violation = np.abs(existing) > breaker_capacity
        incremental_headroom = breaker_capacity - existing
        incremental_headroom = np.where(baseline_violation, 0.0, incremental_headroom)

        arrays.append(incremental_headroom)
        records.append({
            "ElementID": breaker_record["ElementID"],
            "ElementType": breaker_row.get("type"),
            "ElementClass": "Breaker",
            "CapacityMW": breaker_capacity,
            "ExistingSignedMW": existing,
            "BaselineViolation": baseline_violation,
            "IncrementalHeadroomMW": incremental_headroom,
            "RatingA": breaker_record["RatingA"],
            "VoltageKV": breaker_record["VoltageKV"],
            "RatingKVA": breaker_record["RatingKVA"],
            "PhaseCount": breaker_record["PhaseCount"],
            "CapacityMethod": breaker_record["CapacityMethod"],
            "ParallelElementCount": 1,
            "ParallelElementIDs": breaker_record["ElementID"]
        })

    for path_record in path_records:
        if path_record["ElementClass"] not in {"Line", "Transformer"}:
            continue

        capacity_mw = capacity_at_power_factor(path_record, power_factor)

        if np.isnan(capacity_mw) or capacity_mw <= 0:
            missing_capacity.append(path_record["ElementID"])
            continue

        existing = element_5min_existing_flow(path_record["ElementID"], element_hourly_shares, hour_positions, breaker_values)
        baseline_violation = np.abs(existing) > capacity_mw
        incremental_headroom = capacity_mw - existing
        incremental_headroom = np.where(baseline_violation, 0.0, incremental_headroom)

        arrays.append(incremental_headroom)
        records.append({
            **path_record,
            "CapacityMW": capacity_mw,
            "ExistingSignedMW": existing,
            "BaselineViolation": baseline_violation,
            "IncrementalHeadroomMW": incremental_headroom
        })

    return arrays, records, missing_capacity


def summarize_path_constraints(candidate_row, grid_id, substation_id, constraint_records, power_factor, timestamps):
    rows = []

    for record in constraint_records:
        existing = np.asarray(record["ExistingSignedMW"], dtype=float)
        absolute_existing = np.abs(existing)
        baseline_violation = np.asarray(record["BaselineViolation"], dtype=bool)
        headroom = np.asarray(record["IncrementalHeadroomMW"], dtype=float)
        peak_position = int(np.argmax(absolute_existing)) if len(existing) else None

        rows.append({
            "CandidateKey": candidate_row["CandidateKey"],
            "GridID": grid_id,
            "SubstationID": substation_id,
            "CandidateLineID": candidate_row["CandidateLineID"],
            "PowerFactor": power_factor,
            "ElementID": record["ElementID"],
            "ElementType": record.get("ElementType"),
            "ElementClass": record["ElementClass"],
            "CapacityMW": record["CapacityMW"],
            "PeakExistingAbsMW": float(np.max(absolute_existing)) if len(existing) else np.nan,
            "PeakExistingSignedMW": float(existing[peak_position]) if peak_position is not None else np.nan,
            "P95ExistingAbsMW": float(np.quantile(absolute_existing, 0.95)) if len(existing) else np.nan,
            "MinimumIncrementalHeadroomMW": float(np.min(headroom)) if len(headroom) else np.nan,
            "BaselineRatingExceedanceIntervals": int(np.sum(baseline_violation)),
            "BaselineRatingExceedancePct": float(np.mean(baseline_violation) * 100.0) if len(baseline_violation) else np.nan,
            "PeakTimestampUTC": timestamps[peak_position] if peak_position is not None else pd.NaT,
            "RatingA": record.get("RatingA"),
            "VoltageKV": record.get("VoltageKV"),
            "RatingKVA": record.get("RatingKVA"),
            "PhaseCount": record.get("PhaseCount"),
            "CapacityMethod": record.get("CapacityMethod"),
            "ParallelElementCount": record.get("ParallelElementCount", 1),
            "ParallelElementIDs": record.get("ParallelElementIDs")
        })

    return pd.DataFrame(rows)


def evaluate_candidate_at_power_factor(candidate_row, grid_id, substation_id, breaker_row, breaker_mw, path_records, element_hourly_shares, hour_positions, power_factor):
    arrays, constraint_records, missing_capacity = build_path_constraint_arrays(path_records, breaker_row, breaker_mw, element_hourly_shares, hour_positions, power_factor)

    if missing_capacity:
        return None, constraint_records, missing_capacity

    if not arrays:
        return None, constraint_records, ["NoUsableThermalConstraints"]

    stack = np.vstack(arrays)
    raw_hosting = np.min(stack, axis=0)
    hosting = np.maximum(raw_hosting, 0.0)
    limiting_index = np.argmin(stack, axis=0)
    limiting_ids = np.array([record["ElementID"] for record in constraint_records], dtype=object)[limiting_index]

    any_path_baseline_violation = np.any(np.vstack([record["BaselineViolation"] for record in constraint_records]), axis=0)
    unique_limiter, limiter_counts = np.unique(limiting_ids, return_counts=True)
    most_frequent_position = int(np.argmax(limiter_counts))
    most_frequent_limiter = unique_limiter[most_frequent_position]
    most_frequent_limiter_pct = limiter_counts[most_frequent_position] / len(limiting_ids) * 100.0
    firm_position = int(np.argmin(hosting))

    result = {
        "PowerFactor": power_factor,
        "ModeledIntervals": len(hosting),
        "ModeledHoursEquivalent": len(hosting) * BREAKER_INTERVAL_MINUTES / 60.0,
        "FirmHostingMW": float(np.min(hosting)),
        "HostingAvailable99PctIntervalsMW": float(np.quantile(hosting, 0.01)),
        "HostingAvailable95PctIntervalsMW": float(np.quantile(hosting, 0.05)),
        "MedianHostingMW": float(np.median(hosting)),
        "MeanHostingMW": float(np.mean(hosting)),
        "MaximumHostingMW": float(np.max(hosting)),
        "MinimumRawIncrementalHeadroomMW": float(np.min(raw_hosting)),
        "IntervalsWithNoAdditionalHosting": int(np.sum(hosting <= 1e-9)),
        "PctIntervalsWithNoAdditionalHosting": float(np.mean(hosting <= 1e-9) * 100.0),
        "IntervalsWithBaselinePathRatingExceedance": int(np.sum(any_path_baseline_violation)),
        "PctIntervalsWithBaselinePathRatingExceedance": float(np.mean(any_path_baseline_violation) * 100.0),
        "MostFrequentLimitingElementID": most_frequent_limiter,
        "MostFrequentLimitingElementPct": float(most_frequent_limiter_pct),
        "FirmLimitTimestampUTC": breaker_mw.index[firm_position],
        "FirmLimitElementID": limiting_ids[firm_position],
        "HostingArray": hosting,
        "RawHostingArray": raw_hosting,
        "LimitingIDs": limiting_ids,
        "PathBaselineViolationArray": any_path_baseline_violation,
        "ConstraintRecords": constraint_records
    }

    return result, constraint_records, []


def evaluate_test_dc_levels(candidate_row, base_result):
    hosting = base_result["HostingArray"]
    rows = []

    for dc_mw in TEST_DC_LEVELS_MW:
        feasible = hosting >= dc_mw
        constrained = ~feasible

        rows.append({
            "CandidateKey": candidate_row["CandidateKey"],
            "GridID": candidate_row["GridID"],
            "SubstationID": candidate_row.get("SubstationID"),
            "CandidateLineID": candidate_row["CandidateLineID"],
            "TestDCLoadMW": dc_mw,
            "FeasibleIntervals": int(np.sum(feasible)),
            "ConstrainedIntervals": int(np.sum(constrained)),
            "FeasiblePctIntervals": float(np.mean(feasible) * 100.0),
            "ConstrainedPctIntervals": float(np.mean(constrained) * 100.0),
            "ConstrainedHoursEquivalent": float(np.sum(constrained) * BREAKER_INTERVAL_MINUTES / 60.0)
        })

    return pd.DataFrame(rows)


def model_status(mapping_pct, reconciliation, cycle_rank, parallel_on_path, missing_capacity, path_baseline_exceedance_pct):
    flags = []

    if mapping_pct < MIN_TOPOLOGY_MAPPING_PCT_FOR_READY:
        flags.append(f"MeterTopologyMapping<{MIN_TOPOLOGY_MAPPING_PCT_FOR_READY:.0f}%")

    correlation = reconciliation.get("MeterBreakerCorrelation")
    nmae = reconciliation.get("MeterBreakerNMAEPct")

    if pd.isna(correlation) or correlation < MIN_ACCEPTABLE_METER_BREAKER_CORRELATION:
        flags.append("MeterBreakerCorrelationReview")

    if pd.isna(nmae) or nmae > MAX_ACCEPTABLE_METER_BREAKER_NMAE_PCT:
        flags.append("MeterBreakerErrorReview")

    if cycle_rank > 0:
        flags.append("MeshedTopologyUsesShortestPathTree")

    if parallel_on_path:
        flags.append("ParallelPathUsesConservativeLowestRating")

    if missing_capacity:
        flags.append("MissingThermalCapacityOnPath")

    if path_baseline_exceedance_pct > 0:
        flags.append("ExistingPathRatingExceedance")

    return ("Ready" if not flags else "ReadyWithReviewFlags"), " | ".join(flags)


def assumptions_table():
    return pd.DataFrame([
        {"Item": "Model purpose", "Assumption": "Time-series thermal hosting capacity model used as the physical feasibility engine before optimization and Monte Carlo."},
        {"Item": "Primary measured total", "Assumption": "Five-minute circuit-breaker SCADA is treated as feeder-level net real-power truth because it is the highest-frequency measured feeder total available."},
        {"Item": "Spatial load allocation", "Assumption": "Hourly meter kWh determines relative spatial shares within each hour. Those shares are applied to every five-minute breaker observation in that hour."},
        {"Item": "Why hourly meter data are not interpolated to artificial five-minute behavior", "Assumption": "The model does not invent intra-hour customer shapes. It holds each meter's observed hourly spatial share constant within the hour and lets measured five-minute breaker SCADA provide the actual intra-hour feeder magnitude."},
        {"Item": "Meter-breaker validation", "Assumption": "Meter totals are re-audited against hourly breaker energy using correlation, normalized MAE, and annual energy ratio. This validates source consistency; it is not used as a fitted predictive relationship."},
        {"Item": "Physical constraints vs statistical weights", "Assumption": "Line, transformer, and breaker ratings are hard engineering limits in the thermal model. They are not statistically weighted or selected based on correlation."},
        {"Item": "Topology", "Assumption": "Open switchable elements are excluded. If the active network is radial, the breaker-to-load path is unique. If cycles remain, a shortest-path tree is used and the feeder is explicitly flagged for review."},
        {"Item": "Parallel elements", "Assumption": "Without impedance, current sharing between truly parallel elements cannot be calculated. For a parallel node pair the lowest usable individual thermal rating is used conservatively and any candidate crossing that pair is flagged."},
        {"Item": "Meter mapping", "Assumption": "Meters are attached to reachable topology terminals, then upstream-element connectivity. Unresolved meters are retained at the feeder root and the mapping percentage is reported; they are not silently discarded."},
        {"Item": "Power factor base case", "Assumption": f"{BASE_POWER_FACTOR:.2f} is the base screening power factor used to translate apparent/current equipment ratings to real-power capacity."},
        {"Item": "Power factor sensitivity", "Assumption": f"Candidate results are recalculated at PF values {POWER_FACTOR_SENSITIVITY} so conclusions are not dependent on a single untested PF assumption."},
        {"Item": "Three-phase line capacity", "Assumption": "P_limit = sqrt(3) x V_LL x I_rating x PF."},
        {"Item": "Two-phase line capacity", "Assumption": "P_limit = V_LL x I_rating x PF."},
        {"Item": "Single-phase line capacity", "Assumption": "V_LN is approximated as V_LL / sqrt(3), then P_limit = V_LN x I_rating x PF."},
        {"Item": "Transformer capacity", "Assumption": "Real-power thermal limit = rating_kVA / 1000 x PF."},
        {"Item": "Breaker capacity", "Assumption": "Real-power thermal limit = rating_kVA / 1000 x PF."},
        {"Item": "Incremental data-center load", "Assumption": "A positive data-center load is added at the candidate point and therefore adds to every constrained element on its breaker path."},
        {"Item": "Existing reverse flow", "Assumption": "Existing branch flows retain their modeled sign. Incremental positive-load headroom is capacity minus signed existing flow, while any pre-existing absolute rating exceedance is set to zero available hosting for that interval."},
        {"Item": "Baseline rating exceedances", "Assumption": "Existing modeled rating exceedances are audited separately and are not automatically assumed to represent a real utility violation. Candidate-path exceedances are explicitly flagged."},
        {"Item": "Unrelated downstream exceedances", "Assumption": "An existing exceedance on a branch that the candidate data-center load does not traverse does not directly reduce that candidate's path headroom, but remains in the audit output."},
        {"Item": "Substation capacity", "Assumption": "Explicit substation available capacity is still unavailable, so no substation-capacity ceiling is enforced. Shared-substation constraints must be added when reliable capacity values are obtained."},
        {"Item": "Voltage and AC power flow", "Assumption": "Line R/X and transformer impedance are unavailable. The model therefore does not claim voltage-drop, reactive-power, losses, or AC power-flow validation."},
        {"Item": "PV and EV", "Assumption": "Existing net effects are already present in breaker SCADA. Their exact spatial electrical injections are not separately inferred unless measured element-level profiles are explicitly modeled later."},
        {"Item": "Interpretation", "Assumption": "Outputs are modeled five-minute thermal hosting capacity under the available EDM data, not utility-approved interconnection capacity."}
    ])


def excel_safe_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str)
    return str(value)


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
            df[column] = df[column].map(excel_safe_value)

    return df


def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return

    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_format = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})
    decimal_format = workbook.add_format({"num_format": "0.000"})
    integer_format = workbook.add_format({"num_format": "0"})

    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)
        max_length = max((len(str(value)) for value in dataframe[column_name].tolist() if value is not None), default=0) if not dataframe.empty else 0
        width = min(max(max_length, len(str(column_name))) + 2, 48)

        if pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, decimal_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


def main():
    heading("GREENSBORO FINAL FIVE-MINUTE THERMAL HOSTING MODEL")
    print("Five-minute breaker SCADA supplies the measured feeder magnitude.")
    print("Hourly meter kWh supplies spatial load shares without inventing five-minute customer behavior.")
    print("Line, transformer, and breaker ratings are enforced as physical thermal constraints rather than statistically weighted predictors.")

    candidates, target_feeders = load_candidates()

    print("\nSelected candidate locations:")
    print(candidates[["GridID", "CandidateLineID", "SubstationID", "ScreeningHostingMW"]].to_string(index=False))

    conn = connect_to_edm()

    candidate_summaries = []
    pf_sensitivity_frames = []
    path_constraint_frames = []
    test_dc_frames = []
    interval_frames = []
    feeder_diagnostics = []
    reconciliation_rows = []
    element_summary_frames = []
    exceedance_frames = []
    meter_mapping_frames = []
    error_rows = []

    try:
        all_elements = get_grid_elements(conn, target_feeders)

        for feeder_number, grid_id in enumerate(target_feeders, start=1):
            heading(f"[{feeder_number}/{len(target_feeders)}] {grid_id}")

            feeder_elements = all_elements[all_elements["grid_id"].astype(str) == str(grid_id)].copy()
            feeder_candidates = candidates[candidates["GridID"].astype(str) == str(grid_id)].copy()

            if feeder_elements.empty:
                error_rows.append({"GridID": grid_id, "Stage": "Elements", "Error": "No grid elements found"})
                print("No grid elements found. Skipping.")
                continue

            breaker_row = identify_breaker(feeder_elements)

            if breaker_row is None:
                error_rows.append({"GridID": grid_id, "Stage": "Breaker", "Error": "No circuit breaker found"})
                print("No circuit breaker found. Skipping.")
                continue

            breaker_id = str(breaker_row["grid_element_id"])
            graph, edge_lookup, pair_records, graph_diagnostics = build_network_graph(feeder_elements, breaker_id)
            root_node, breaker_terminal_component_sizes = choose_root_node(graph, breaker_row)

            if root_node is None:
                error_rows.append({"GridID": grid_id, "Stage": "Topology", "Error": "Unable to identify breaker-side root node"})
                print("Unable to identify breaker-side root node. Skipping.")
                continue

            connected_graph, paths, depths, parents, parent_edges, cycle_rank, is_radial = build_shortest_path_tree(graph, root_node)

            print(f"Elements: {len(feeder_elements):,}")
            print(f"Active connected graph: {connected_graph.number_of_nodes():,} nodes, {connected_graph.number_of_edges():,} edges")
            print(f"Root node: {root_node}")
            print(f"Radial after open-switch removal: {is_radial}")
            print(f"Cycle rank: {cycle_rank}")
            print(f"Parallel node pairs: {graph_diagnostics['ParallelNodePairCount']:,}")
            print(f"Open switchable elements skipped: {graph_diagnostics['SkippedOpenSwitches']:,}")

            print("\nLoading five-minute breaker SCADA...")
            breaker_mw, breaker_kwh_5min, breaker_sources, breaker_diagnostics = load_breaker_5min_profile(conn, grid_id)
            print(f"Accepted five-minute intervals: {len(breaker_mw):,}")
            print(f"Breaker five-minute coverage: {breaker_diagnostics['Breaker5MinCoveragePct']:.2f}%")
            print(f"Breaker peak: {breaker_diagnostics['BreakerPeakMW']:.3f} MW")

            required_hours = pd.DatetimeIndex(sorted(breaker_mw.index.floor("h").unique()))
            meter_rows = get_meter_rows(feeder_elements)
            meter_ids = meter_rows["grid_element_id"].astype(str).tolist()

            print(f"\nLoading {len(meter_ids):,} hourly meter profiles...")
            raw_meter_kwh, meter_sources, meter_diagnostics = load_meter_hourly_matrix(conn, grid_id, meter_ids, required_hours)
            breaker_hourly_kwh = hourly_breaker_kwh_from_5min(breaker_kwh_5min, required_hours)
            reconciliation = meter_breaker_reconciliation(raw_meter_kwh, breaker_hourly_kwh)
            reconciliation_rows.append({"GridID": grid_id, **reconciliation})

            print(f"Meter vs breaker correlation: {reconciliation['MeterBreakerCorrelation']:.6f}" if pd.notna(reconciliation["MeterBreakerCorrelation"]) else "Meter vs breaker correlation: NaN")
            print(f"Meter vs breaker normalized MAE: {reconciliation['MeterBreakerNMAEPct']:.3f}%" if pd.notna(reconciliation["MeterBreakerNMAEPct"]) else "Meter vs breaker normalized MAE: NaN")
            print(f"Annual meter/breaker energy ratio: {reconciliation['MeterBreakerEnergyRatio']:.4f}" if pd.notna(reconciliation["MeterBreakerEnergyRatio"]) else "Annual meter/breaker energy ratio: NaN")

            hourly_weights, weight_diagnostics = build_hourly_meter_weights(raw_meter_kwh)

            mapping = build_meter_mapping(feeder_elements, meter_ids, paths, depths, edge_lookup, root_node)
            mapping["GridID"] = grid_id
            meter_mapping_frames.append(mapping)

            mapping_pct = mapping["Resolved"].mean() * 100.0 if not mapping.empty else 0.0
            print(f"Meter topology mapping: {mapping_pct:.2f}%")

            element_hourly_shares, root_hourly_share = aggregate_hourly_meter_weights_to_tree(hourly_weights, mapping, root_node, depths, parents, parent_edges)
            root_share_error = float(np.max(np.abs(root_hourly_share - 1.0))) if len(root_hourly_share) else np.nan
            print(f"Maximum hourly root share-conservation error: {root_share_error:.10f}")

            hour_positions = build_hour_position_index(hourly_weights.index, breaker_mw.index)

            selected_candidate_path_ids = set()
            candidate_path_cache = {}

            for _, candidate_row in feeder_candidates.iterrows():
                downstream_node, path_records, path_status = candidate_path(connected_graph, candidate_row, paths, depths, edge_lookup)
                candidate_path_cache[candidate_row["CandidateKey"]] = (downstream_node, path_records, path_status)

                if path_status == "Ready":
                    selected_candidate_path_ids.update(record["ElementID"] for record in path_records)

            element_summary = build_element_load_audit(grid_id, connected_graph, element_hourly_shares, hour_positions, breaker_mw, selected_candidate_path_ids)
            element_summary_frames.append(element_summary)

            exceedances = element_summary[element_summary["RatingExceedanceIntervals"] > 0].copy() if not element_summary.empty else pd.DataFrame()
            if not exceedances.empty:
                exceedance_frames.append(exceedances)

            total_exceedance_elements = len(exceedances)
            candidate_path_exceedance_elements = int(exceedances["OnSelectedCandidatePath"].sum()) if not exceedances.empty else 0

            print(f"Modeled baseline rating-exceedance elements: {total_exceedance_elements:,}")
            print(f"Of those, elements on selected candidate paths: {candidate_path_exceedance_elements:,}")

            feeder_diagnostics.append({
                "GridID": grid_id,
                "BreakerID": breaker_id,
                "RootNode": root_node,
                "FeederIsRadialAfterOpenSwitchRemoval": is_radial,
                "CycleRank": cycle_rank,
                "GraphNodesConnectedToRoot": connected_graph.number_of_nodes(),
                "GraphEdgesConnectedToRoot": connected_graph.number_of_edges(),
                "MeterTopologyMappingPct": mapping_pct,
                "MaxHourlyRootShareConservationError": root_share_error,
                "BaselineRatingExceedanceElements": total_exceedance_elements,
                "BaselineRatingExceedanceElementsOnSelectedPaths": candidate_path_exceedance_elements,
                **graph_diagnostics,
                **breaker_diagnostics,
                **meter_diagnostics,
                **weight_diagnostics,
                **reconciliation
            })

            for _, candidate_row in feeder_candidates.iterrows():
                candidate_key = candidate_row["CandidateKey"]
                substation_id = candidate_row.get("SubstationID")
                downstream_node, path_records, path_status = candidate_path_cache[candidate_key]

                print(f"\nEvaluating candidate {candidate_row['CandidateLineID']}")

                if path_status != "Ready":
                    print(f"  Candidate skipped: {path_status}")
                    error_rows.append({"GridID": grid_id, "CandidateLineID": candidate_row["CandidateLineID"], "Stage": "CandidatePath", "Error": path_status})
                    continue

                parallel_on_path = any(record["ParallelElementCount"] > 1 for record in path_records)
                pf_rows = []
                base_result = None
                base_missing_capacity = []

                for power_factor in POWER_FACTOR_SENSITIVITY:
                    result, constraint_records, missing_capacity = evaluate_candidate_at_power_factor(
                        candidate_row, grid_id, substation_id, breaker_row, breaker_mw, path_records,
                        element_hourly_shares, hour_positions, power_factor
                    )

                    if missing_capacity:
                        if math.isclose(power_factor, BASE_POWER_FACTOR, rel_tol=0.0, abs_tol=1e-9):
                            base_missing_capacity = missing_capacity
                        pf_rows.append({
                            "CandidateKey": candidate_key,
                            "GridID": grid_id,
                            "SubstationID": substation_id,
                            "CandidateLineID": candidate_row["CandidateLineID"],
                            "PowerFactor": power_factor,
                            "Status": "MissingCapacity",
                            "MissingCapacityElements": " | ".join(missing_capacity)
                        })
                        continue

                    pf_rows.append({
                        "CandidateKey": candidate_key,
                        "GridID": grid_id,
                        "SubstationID": substation_id,
                        "CandidateLineID": candidate_row["CandidateLineID"],
                        "PowerFactor": power_factor,
                        "Status": "Ready",
                        "FirmHostingMW": result["FirmHostingMW"],
                        "HostingAvailable99PctIntervalsMW": result["HostingAvailable99PctIntervalsMW"],
                        "HostingAvailable95PctIntervalsMW": result["HostingAvailable95PctIntervalsMW"],
                        "MedianHostingMW": result["MedianHostingMW"],
                        "PctIntervalsWithBaselinePathRatingExceedance": result["PctIntervalsWithBaselinePathRatingExceedance"],
                        "MostFrequentLimitingElementID": result["MostFrequentLimitingElementID"]
                    })

                    constraint_df = summarize_path_constraints(candidate_row, grid_id, substation_id, constraint_records, power_factor, breaker_mw.index)
                    path_constraint_frames.append(constraint_df)

                    if math.isclose(power_factor, BASE_POWER_FACTOR, rel_tol=0.0, abs_tol=1e-9):
                        base_result = result

                pf_sensitivity_frames.append(pd.DataFrame(pf_rows))

                if base_result is None:
                    print("  Base-PF model could not be completed because at least one path constraint lacks usable capacity.")
                    error_rows.append({
                        "GridID": grid_id,
                        "CandidateLineID": candidate_row["CandidateLineID"],
                        "Stage": "BasePowerFactor",
                        "Error": "Missing capacity: " + " | ".join(base_missing_capacity)
                    })
                    continue

                status, review_flags = model_status(
                    mapping_pct=mapping_pct,
                    reconciliation=reconciliation,
                    cycle_rank=cycle_rank,
                    parallel_on_path=parallel_on_path,
                    missing_capacity=bool(base_missing_capacity),
                    path_baseline_exceedance_pct=base_result["PctIntervalsWithBaselinePathRatingExceedance"]
                )

                candidate_summary = {
                    "CandidateKey": candidate_key,
                    "GridID": grid_id,
                    "SubstationID": substation_id,
                    "CandidateLineID": candidate_row["CandidateLineID"],
                    "CandidateNode": downstream_node,
                    "OriginalScreeningHostingMW": candidate_row.get("ScreeningHostingMW"),
                    "CandidateConfidenceFromPriorScreen": candidate_row.get("CandidateConfidence"),
                    "ModelStatus": status,
                    "ReviewFlags": review_flags,
                    "Modeled5MinIntervals": base_result["ModeledIntervals"],
                    "ModeledHoursEquivalent": base_result["ModeledHoursEquivalent"],
                    "FirmHostingMW": base_result["FirmHostingMW"],
                    "HostingAvailable99PctIntervalsMW": base_result["HostingAvailable99PctIntervalsMW"],
                    "HostingAvailable95PctIntervalsMW": base_result["HostingAvailable95PctIntervalsMW"],
                    "MedianHostingMW": base_result["MedianHostingMW"],
                    "MeanHostingMW": base_result["MeanHostingMW"],
                    "MaximumHostingMW": base_result["MaximumHostingMW"],
                    "FirmToMedianGapMW": base_result["MedianHostingMW"] - base_result["FirmHostingMW"],
                    "IntervalsWithNoAdditionalHosting": base_result["IntervalsWithNoAdditionalHosting"],
                    "PctIntervalsWithNoAdditionalHosting": base_result["PctIntervalsWithNoAdditionalHosting"],
                    "BaselinePathRatingExceedanceIntervals": base_result["IntervalsWithBaselinePathRatingExceedance"],
                    "BaselinePathRatingExceedancePct": base_result["PctIntervalsWithBaselinePathRatingExceedance"],
                    "MostFrequentLimitingElementID": base_result["MostFrequentLimitingElementID"],
                    "MostFrequentLimitingElementPct": base_result["MostFrequentLimitingElementPct"],
                    "FirmLimitTimestampUTC": base_result["FirmLimitTimestampUTC"],
                    "FirmLimitElementID": base_result["FirmLimitElementID"],
                    "PathHops": len(path_records),
                    "PathLineCount": sum(1 for record in path_records if record["ElementClass"] == "Line"),
                    "PathTransformerCount": sum(1 for record in path_records if record["ElementClass"] == "Transformer"),
                    "ParallelAmbiguityOnPath": parallel_on_path,
                    "MeterTopologyMappingPct": mapping_pct,
                    "MeterBreakerCorrelation": reconciliation["MeterBreakerCorrelation"],
                    "MeterBreakerNMAEPct": reconciliation["MeterBreakerNMAEPct"],
                    "FeederIsRadial": is_radial,
                    "CycleRank": cycle_rank,
                    "SubstationCapacityConstraintApplied": False,
                    "VoltageConstraintApplied": False,
                    "BasePowerFactor": BASE_POWER_FACTOR
                }

                candidate_summaries.append(candidate_summary)
                test_dc_frames.append(evaluate_test_dc_levels(candidate_row, base_result))

                interval_frames.append(pd.DataFrame({
                    "TimestampUTC": breaker_mw.index,
                    "CandidateKey": candidate_key,
                    "GridID": grid_id,
                    "SubstationID": substation_id,
                    "CandidateLineID": candidate_row["CandidateLineID"],
                    "ExistingBreakerMW": breaker_mw.to_numpy(dtype=float),
                    "AvailableThermalHostingMW": base_result["HostingArray"],
                    "RawIncrementalHeadroomMW": base_result["RawHostingArray"],
                    "LimitingElementID": base_result["LimitingIDs"],
                    "BaselinePathRatingExceedance": base_result["PathBaselineViolationArray"]
                }))

                print(f"  Firm hosting at PF {BASE_POWER_FACTOR:.2f}: {base_result['FirmHostingMW']:.3f} MW")
                print(f"  Available in 99% of 5-minute intervals: {base_result['HostingAvailable99PctIntervalsMW']:.3f} MW")
                print(f"  Available in 95% of 5-minute intervals: {base_result['HostingAvailable95PctIntervalsMW']:.3f} MW")
                print(f"  Median hosting: {base_result['MedianHostingMW']:.3f} MW")
                print(f"  Most frequent limiter: {base_result['MostFrequentLimitingElementID']} ({base_result['MostFrequentLimitingElementPct']:.1f}% of intervals)")
                print(f"  Model status: {status}")
                if review_flags:
                    print(f"  Review flags: {review_flags}")

        candidate_summary_df = pd.DataFrame(candidate_summaries)
        pf_sensitivity_df = pd.concat(pf_sensitivity_frames, ignore_index=True) if pf_sensitivity_frames else pd.DataFrame()
        path_constraints_df = pd.concat(path_constraint_frames, ignore_index=True) if path_constraint_frames else pd.DataFrame()
        test_dc_df = pd.concat(test_dc_frames, ignore_index=True) if test_dc_frames else pd.DataFrame()
        interval_df = pd.concat(interval_frames, ignore_index=True) if interval_frames else pd.DataFrame()
        feeder_diagnostics_df = pd.DataFrame(feeder_diagnostics)
        reconciliation_df = pd.DataFrame(reconciliation_rows)
        element_summary_df = pd.concat(element_summary_frames, ignore_index=True) if element_summary_frames else pd.DataFrame()
        exceedance_df = pd.concat(exceedance_frames, ignore_index=True) if exceedance_frames else pd.DataFrame()
        meter_mapping_df = pd.concat(meter_mapping_frames, ignore_index=True) if meter_mapping_frames else pd.DataFrame()
        errors_df = pd.DataFrame(error_rows)
        assumptions_df = assumptions_table()

        if not candidate_summary_df.empty:
            candidate_summary_df = candidate_summary_df.sort_values(["FirmHostingMW", "HostingAvailable99PctIntervalsMW"], ascending=[False, False]).reset_index(drop=True)
            candidate_summary_df.insert(0, "ThermalRank", np.arange(1, len(candidate_summary_df) + 1))

        heading("FINAL THERMAL MODEL RESULTS")

        if candidate_summary_df.empty:
            print("No candidate completed successfully.")
        else:
            display_columns = [
                "ThermalRank", "GridID", "CandidateLineID", "FirmHostingMW",
                "HostingAvailable99PctIntervalsMW", "HostingAvailable95PctIntervalsMW",
                "MedianHostingMW", "MostFrequentLimitingElementID", "ModelStatus"
            ]
            print(candidate_summary_df[display_columns].to_string(index=False))

        if not exceedance_df.empty:
            print(f"\nModeled baseline rating-exceedance elements: {len(exceedance_df):,}")
            candidate_path_exceedances = exceedance_df[exceedance_df["OnSelectedCandidatePath"] == True]
            print(f"Rating-exceedance elements on selected candidate paths: {len(candidate_path_exceedances):,}")

        if not interval_df.empty:
            interval_df.to_csv(INTERVAL_OUTPUT_FILE, index=False, compression="gzip")
            print(f"\nFive-minute candidate hosting output saved to: {Path(INTERVAL_OUTPUT_FILE).resolve()}")

        workbook_frames = {
            "Candidate_Summary": candidate_summary_df,
            "PF_Sensitivity": pf_sensitivity_df,
            "Test_DC_Levels": test_dc_df,
            "Path_Constraints": path_constraints_df,
            "Feeder_Diagnostics": feeder_diagnostics_df,
            "Reconciliation_Audit": reconciliation_df,
            "Element_Load_Summary": element_summary_df,
            "Baseline_Exceedances": exceedance_df,
            "Meter_Mapping": meter_mapping_df,
            "Errors": errors_df,
            "Assumptions": assumptions_df
        }

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe_frame = make_excel_safe(frame)
                safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
                format_excel(writer, sheet_name, safe_frame)

        print(f"Summary workbook saved to: {Path(OUTPUT_FILE).resolve()}")

        heading("WHAT THE RESULTS MEAN")
        print("FirmHostingMW is the minimum additional positive data-center load that remains within all modeled candidate-path thermal ratings across accepted five-minute 2024 intervals.")
        print("HostingAvailable99PctIntervalsMW and HostingAvailable95PctIntervalsMW quantify flexible hosting that is available during at least 99% and 95% of modeled five-minute intervals.")
        print("Breaker SCADA supplies actual five-minute feeder magnitude; hourly meter data only determine where that measured feeder load is distributed during each hour.")
        print("Baseline rating exceedances are audit findings, not automatically declared real utility violations. Candidate-path exceedances are flagged directly in Candidate_Summary.")
        print("Power-factor sensitivity shows how strongly the result depends on the assumed conversion from apparent/current ratings to usable real power.")
        print("No explicit substation-capacity or voltage/power-flow constraint is claimed because those required source parameters remain unavailable.")
        print("The five-minute interval output is structured to feed the later optimization and Monte Carlo models directly.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
