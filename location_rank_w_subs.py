import os
import json
import re
import calendar
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

LOCATION_FILE = "greensboro_top15_location_hosting_screen.xlsx"
LOCATION_SHEET = "All_Candidates"

OUTPUT_FILE = "greensboro_substation_constrained_location_rankings.xlsx"
SUBSTATION_PROFILE_FILE = "greensboro_relevant_substation_2024_profiles.csv"

YEAR = 2024
POWER_FACTOR = 0.95

PREFERRED_SUBSTATION_LOADING_LOW = 0.80
PREFERRED_SUBSTATION_LOADING_HIGH = 0.90
HARD_SUBSTATION_LOADING_LIMIT = 1.00

MIN_FINAL_HOSTING_MW = 0.10
TOP_GLOBAL_LOCATIONS = 100
TOP_PER_FEEDER = 10
TOP_PER_SUBSTATION = 10

WITHIN_PEAK_PERCENTAGES = [5, 10, 20, 30, 50]

# PURPOSE:
# Builds the candidate-location ranking by combining feeder-path hosting capacity with shared substation loading and any resolved substation capacity limits.
# WHY: A location can look strong on its feeder but still be limited by the upstream substation, so the ranking must account for both local path capacity and shared station headroom.
# DEPENDENCIES: Reads greensboro_top15_location_hosting_screen.xlsx, queries breaker/substation mappings and 2024 SCADA from the EDM using .env credentials, and can optionally use explicit substation capacity values from a configured file or dictionary.
# OUTPUT: Writes greensboro_substation_constrained_location_rankings.xlsx and greensboro_relevant_substation_2024_profiles.csv, which provide the candidate set and substation context used by the later thermal model.

SUBSTATION_CAPACITY_MW = {}


SUBSTATION_CAPACITY_FILE = None
SUBSTATION_CAPACITY_SHEET = "Substation_Capacity"

# Base ranking does NOT allow overload above 100%.
# These are only here for a future separately documented sensitivity case.
ENABLE_OVERLOAD_SCENARIO = False
OVERLOAD_LIMIT_PCT = None
OVERLOAD_DURATION_HOURS = None

load_dotenv()

DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")


def connect_to_edm():
    missing = [name for name, value in {"EDM_HOST": DB_HOST, "EDM_USER": DB_USER, "EDM_PASSWORD": DB_PASSWORD, "EDM_DATABASE": DB_NAME}.items() if not value]

    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")

    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME)


def query_dataframe(conn, query, params=None):
    with conn.cursor() as cursor:
        cursor.execute(query, params if params is not None else None)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]

    return pd.DataFrame(rows, columns=columns)


def safe_float(value):
    if value is None:
        return np.nan

    try:
        if pd.isna(value):
            return np.nan
    except Exception:
        pass

    try:
        return float(str(value).replace(",", "").strip())
    except Exception:
        match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", str(value))
        return float(match.group()) if match else np.nan


def normalize_key(value):
    return "".join(character for character in str(value).lower() if character.isalnum())


def parse_meta(value):
    if value is None:
        return {}

    if isinstance(value, dict):
        return value

    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
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


def make_excel_safe(df):
    if df is None:
        return pd.DataFrame()

    df = df.copy()

    for column in df.columns:
        if isinstance(df[column].dtype, pd.DatetimeTZDtype):
            df[column] = df[column].dt.tz_localize(None)

        elif df[column].dtype == "object":
            df[column] = df[column].map(lambda value: json.dumps(value, default=str) if isinstance(value, (dict, list, tuple, set)) else value)

    return df


def load_location_candidates():
    path = Path(LOCATION_FILE)

    if not path.exists():
        raise FileNotFoundError(f"Could not find {LOCATION_FILE}: {path.resolve()}")

    candidates = pd.read_excel(path, sheet_name=LOCATION_SHEET)

    required = {"GridID", "CandidateLineID", "EstimatedBaseHostingMW"}
    missing = required - set(candidates.columns)

    if missing:
        raise RuntimeError(f"{LOCATION_FILE} / {LOCATION_SHEET} is missing required columns: {sorted(missing)}")

    return candidates


def get_breaker_substation_map(conn):
    return query_dataframe(conn, """
        SELECT
            b.grid_id AS "GridID",
            b.grid_element_id AS "BreakerID",
            b.meta ->> 'enclosure_id' AS "SubstationID",
            b.meta ->> 'rating_kva' AS "BreakerRatingKVA",
            b.meta ->> 'voltage_level' AS "BreakerVoltageLevel"
        FROM grid_element b
        JOIN grid_element s
          ON s.type = 'Substation'
         AND s.grid_element_id = b.meta ->> 'enclosure_id'
        WHERE b.type = 'CircuitBreaker'
          AND b.grid_id ~ '^GSO_[0-9]+$'
        ORDER BY b.grid_id;
    """)


def get_substations(conn, substation_ids):
    if not substation_ids:
        return pd.DataFrame()

    placeholders = ",".join(["%s"] * len(substation_ids))

    return query_dataframe(conn, f"""
        SELECT DISTINCT ON (grid_element_id)
            grid_id AS "SubstationGridID",
            grid_element_id AS "SubstationID",
            type AS "SubstationType",
            customer_type AS "CustomerType",
            is_producer AS "IsProducer",
            is_consumer AS "IsConsumer",
            upstream_grid_element_id AS "UpstreamElementID",
            terminal1_cn AS "Terminal1CN",
            terminal2_cn AS "Terminal2CN",
            meta AS "Meta"
        FROM grid_element
        WHERE type = 'Substation'
          AND grid_element_id IN ({placeholders})
        ORDER BY grid_element_id, grid_id;
    """, tuple(substation_ids))


def get_breaker_data_sources(conn, breaker_ids):
    if not breaker_ids:
        return pd.DataFrame()

    placeholders = ",".join(["%s"] * len(breaker_ids))

    return query_dataframe(conn, f"""
        SELECT
            ds.grid_id AS "GridID",
            ds.grid_element_id AS "BreakerID",
            ds.grid_element_data_source_id AS "DataSourceID",
            ds.type AS "DataSourceType",
            ds.provider AS "Provider",
            ds.metrics AS "Metrics",
            ds.friendly_id AS "FriendlyID",
            ds.phases AS "Phase",
            ds.direction AS "Direction",
            ds.valid AS "Valid"
        FROM grid_element_data_source ds
        WHERE ds.grid_element_id IN ({placeholders})
          AND ds.metrics::text ~* 'kWh'
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """, tuple(breaker_ids))


def get_scada_timeseries(conn, data_source_id):
    start_timestamp = f"{YEAR}-01-01 00:00:00+00"
    end_timestamp = f"{YEAR + 1}-01-01 00:00:00+00"

    df = query_dataframe(conn, """
        SELECT *
        FROM ts_data_source_select(
            %s::uuid,
            'kWh',
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        );
    """, (data_source_id, start_timestamp, end_timestamp))

    if df.empty:
        return pd.DataFrame(columns=["Timestamp", "IntervalKWh"])

    column_map = {str(column).lower(): column for column in df.columns}

    timestamp_candidates = ["timestamp", "time", "ts", "datetime", "date_time"]
    value_candidates = ["value", "metric_value", "double_value", "kwh"]

    timestamp_col = next((column_map[name] for name in timestamp_candidates if name in column_map), None)
    value_col = next((column_map[name] for name in value_candidates if name in column_map), None)

    if timestamp_col is None:
        for column in df.columns:
            if pd.api.types.is_datetime64_any_dtype(df[column]):
                timestamp_col = column
                break

        if timestamp_col is None:
            for column in df.columns:
                if pd.api.types.is_numeric_dtype(df[column]):
                    continue

                converted = pd.to_datetime(df[column], utc=True, errors="coerce")

                if len(converted) > 0 and converted.notna().mean() >= 0.90:
                    timestamp_col = column
                    break

    if value_col is None:
        for column in df.columns:
            if column == timestamp_col:
                continue

            if pd.api.types.is_numeric_dtype(df[column]):
                value_col = column
                break

        if value_col is None:
            for column in df.columns:
                if column == timestamp_col:
                    continue

                converted = pd.to_numeric(df[column], errors="coerce")

                if len(converted) > 0 and converted.notna().mean() >= 0.90:
                    value_col = column
                    break

    if timestamp_col is None or value_col is None:
        raise RuntimeError(f"Could not identify timestamp/value fields for source {data_source_id}. Returned columns: {list(df.columns)}")

    result = df[[timestamp_col, value_col]].copy()
    result.columns = ["Timestamp", "IntervalKWh"]

    result["Timestamp"] = pd.to_datetime(result["Timestamp"], utc=True, errors="coerce")
    result["IntervalKWh"] = pd.to_numeric(result["IntervalKWh"], errors="coerce")

    result = result.dropna(subset=["Timestamp", "IntervalKWh"])
    result = result.sort_values("Timestamp").drop_duplicates("Timestamp", keep="last").reset_index(drop=True)

    return result


def normalize_phase(value):
    if value is None:
        return None

    text = str(value).strip().upper()

    if text in {"A", "B", "C"}:
        return text

    for phase in ["A", "B", "C"]:
        if re.search(rf"(^|[^A-Z]){phase}([^A-Z]|$)", text):
            return phase

    return text if text else None


def load_breaker_profile(conn, grid_id, breaker_id, source_rows):
    loaded_sources = []
    audit_rows = []

    for _, row in source_rows.iterrows():
        source_id = row["DataSourceID"]
        phase = normalize_phase(row.get("Phase"))

        try:
            ts = get_scada_timeseries(conn, source_id)

            audit_rows.append({
                "GridID": grid_id,
                "BreakerID": breaker_id,
                "DataSourceID": str(source_id),
                "Phase": phase,
                "Provider": row.get("Provider"),
                "FriendlyID": row.get("FriendlyID"),
                "Observations": len(ts),
                "StartTimestamp": ts["Timestamp"].min() if not ts.empty else pd.NaT,
                "EndTimestamp": ts["Timestamp"].max() if not ts.empty else pd.NaT,
                "Status": "Loaded" if not ts.empty else "No observations"
            })

            if ts.empty:
                continue

            ts["DataSourceID"] = str(source_id)
            ts["Phase"] = phase
            loaded_sources.append(ts)

        except Exception as exc:
            audit_rows.append({
                "GridID": grid_id,
                "BreakerID": breaker_id,
                "DataSourceID": str(source_id),
                "Phase": phase,
                "Provider": row.get("Provider"),
                "FriendlyID": row.get("FriendlyID"),
                "Observations": 0,
                "StartTimestamp": pd.NaT,
                "EndTimestamp": pd.NaT,
                "Status": f"ERROR: {exc}"
            })

    if not loaded_sources:
        return pd.DataFrame(), pd.DataFrame(audit_rows)

    source_stats = pd.DataFrame([
        {
            "DataSourceID": frame["DataSourceID"].iloc[0],
            "Phase": frame["Phase"].iloc[0],
            "Observations": len(frame)
        }
        for frame in loaded_sources
    ])

    non_null_phases = source_stats["Phase"].dropna().nunique()

    if non_null_phases >= 2:
        selected_ids = []

        for _, group in source_stats.groupby("Phase", dropna=False):
            selected_ids.append(group.sort_values(["Observations", "DataSourceID"], ascending=[False, True]).iloc[0]["DataSourceID"])

    else:
        selected_ids = source_stats.sort_values(["Observations", "DataSourceID"], ascending=[False, True]).head(3)["DataSourceID"].tolist()

    selected_frames = [frame for frame in loaded_sources if frame["DataSourceID"].iloc[0] in selected_ids]

    combined = pd.concat(selected_frames, ignore_index=True)
    selected_count = combined["DataSourceID"].nunique()

    feeder = combined.groupby("Timestamp", as_index=False).agg(
        IntervalKWh=("IntervalKWh", "sum"),
        SourceCount=("DataSourceID", "nunique")
    )

    complete = feeder[feeder["SourceCount"] == selected_count].copy()

    if not complete.empty:
        feeder = complete

    feeder = feeder.sort_values("Timestamp").reset_index(drop=True)

    time_differences = feeder["Timestamp"].diff().dt.total_seconds().div(60)
    valid_differences = time_differences[(time_differences > 0) & (time_differences <= 60)]

    interval_minutes = float(valid_differences.median()) if not valid_differences.empty else 5.0
    interval_hours = interval_minutes / 60.0

    feeder["LoadMW"] = feeder["IntervalKWh"] / interval_hours / 1000.0
    feeder["IntervalMinutes"] = interval_minutes
    feeder["GridID"] = grid_id
    feeder["BreakerID"] = breaker_id

    audit = pd.DataFrame(audit_rows)
    audit["SelectedForFeederProfile"] = audit["DataSourceID"].isin(selected_ids)

    return feeder, audit


def summarize_feeder_profile(grid_id, substation_id, profile):
    if profile.empty:
        return None

    peak_index = profile["LoadMW"].idxmax()

    interval_minutes = float(profile["IntervalMinutes"].iloc[0])
    observed_hours = len(profile) * interval_minutes / 60.0
    calendar_hours = 8784.0 if calendar.isleap(YEAR) else 8760.0

    return {
        "GridID": grid_id,
        "SubstationID": substation_id,
        "AverageLoadMW": float(profile["LoadMW"].mean()),
        "P95LoadMW": float(profile["LoadMW"].quantile(0.95)),
        "PeakLoadMW": float(profile["LoadMW"].max()),
        "PeakTimestampUTC": profile.loc[peak_index, "Timestamp"],
        "SCADAIntervalMinutes": interval_minutes,
        "Observations": len(profile),
        "ObservedHours": observed_hours,
        "DataCoveragePct": observed_hours / calendar_hours * 100.0
    }


def build_substation_profile(substation_id, feeder_ids, feeder_profiles):
    available = {
        grid_id: feeder_profiles[grid_id]
        for grid_id in feeder_ids
        if grid_id in feeder_profiles and not feeder_profiles[grid_id].empty
    }

    if not available:
        return pd.DataFrame(), 0

    frames = []

    for grid_id, profile in available.items():
        temp = profile[["Timestamp", "LoadMW"]].copy()
        temp["GridID"] = grid_id
        frames.append(temp)

    combined = pd.concat(frames, ignore_index=True)

    grouped = combined.groupby("Timestamp", as_index=False).agg(
        SubstationLoadMW=("LoadMW", "sum"),
        FeederCountPresent=("GridID", "nunique")
    )

    loaded_count = len(available)

    complete = grouped[grouped["FeederCountPresent"] == loaded_count].copy()

    if complete.empty:
        return complete, loaded_count

    complete = complete.sort_values("Timestamp").reset_index(drop=True)

    time_differences = complete["Timestamp"].diff().dt.total_seconds().div(60)
    valid_differences = time_differences[(time_differences > 0) & (time_differences <= 60)]

    interval_minutes = float(valid_differences.median()) if not valid_differences.empty else 5.0

    complete["IntervalMinutes"] = interval_minutes
    complete["SubstationID"] = substation_id

    return complete, loaded_count


def summarize_substation_profile(substation_id, feeder_ids, profile, loaded_count, feeder_summary):
    summary = {
        "SubstationID": substation_id,
        "ExpectedFeederCount": len(feeder_ids),
        "LoadedFeederCount": loaded_count,
        "AllMappedFeedersLoaded": loaded_count == len(feeder_ids)
    }

    if profile.empty:
        summary["LoadProfileConfidence"] = "Low"
        return summary

    peak_mw = float(profile["SubstationLoadMW"].max())
    peak_timestamp = profile.loc[profile["SubstationLoadMW"].idxmax(), "Timestamp"]

    interval_minutes = float(profile["IntervalMinutes"].iloc[0])
    interval_hours = interval_minutes / 60.0

    observed_hours = len(profile) * interval_hours
    calendar_hours = 8784.0 if calendar.isleap(YEAR) else 8760.0

    individual_peaks = feeder_summary.loc[
        feeder_summary["GridID"].isin(feeder_ids),
        "PeakLoadMW"
    ].dropna()

    sum_individual_peaks = float(individual_peaks.sum()) if not individual_peaks.empty else np.nan

    coincidence_factor = peak_mw / sum_individual_peaks if sum_individual_peaks > 0 else np.nan

    summary.update({
        "AverageLoadMW": float(profile["SubstationLoadMW"].mean()),
        "P95LoadMW": float(profile["SubstationLoadMW"].quantile(0.95)),
        "PeakLoadMW": peak_mw,
        "PeakTimestampUTC": peak_timestamp,
        "SumIndividualFeederPeaksMW": sum_individual_peaks,
        "CoincidenceFactor": coincidence_factor,
        "DiversityFactor": 1.0 / coincidence_factor if coincidence_factor > 0 else np.nan,
        "LoadFactor_AvgDivPeak": float(profile["SubstationLoadMW"].mean()) / peak_mw if peak_mw > 0 else np.nan,
        "SCADAIntervalMinutes": interval_minutes,
        "Observations": len(profile),
        "ObservedHours": observed_hours,
        "DataCoveragePct": observed_hours / calendar_hours * 100.0
    })

    for within_pct in WITHIN_PEAK_PERCENTAGES:
        threshold_mw = peak_mw * (1.0 - within_pct / 100.0)

        qualifying_hours = int(
            (profile["SubstationLoadMW"] >= threshold_mw).sum()
        ) * interval_hours

        summary[f"HoursWithin{within_pct}PctOfPeak"] = qualifying_hours
        summary[f"PctTimeWithin{within_pct}PctOfPeak"] = qualifying_hours / observed_hours * 100.0 if observed_hours > 0 else np.nan

    if summary["AllMappedFeedersLoaded"] and summary["DataCoveragePct"] >= 95.0:
        summary["LoadProfileConfidence"] = "High"

    elif summary["AllMappedFeedersLoaded"]:
        summary["LoadProfileConfidence"] = "Medium"

    else:
        summary["LoadProfileConfidence"] = "Low"

    return summary


def build_peak_contributions(substation_summary, feeder_map, feeder_profiles):
    rows = []

    for _, substation in substation_summary.iterrows():
        substation_id = substation["SubstationID"]
        peak_timestamp = substation.get("PeakTimestampUTC")
        peak_mw = safe_float(substation.get("PeakLoadMW"))

        if pd.isna(peak_timestamp):
            continue

        feeder_ids = feeder_map.loc[
            feeder_map["SubstationID"] == substation_id,
            "GridID"
        ].tolist()

        for grid_id in feeder_ids:
            profile = feeder_profiles.get(grid_id)

            if profile is None or profile.empty:
                load_mw = np.nan

            else:
                match = profile[profile["Timestamp"] == peak_timestamp]
                load_mw = float(match.iloc[0]["LoadMW"]) if not match.empty else np.nan

            rows.append({
                "SubstationID": substation_id,
                "PeakTimestampUTC": peak_timestamp,
                "GridID": grid_id,
                "FeederLoadAtSubstationPeakMW": load_mw,
                "ContributionPct": load_mw / peak_mw * 100.0 if peak_mw > 0 and not np.isnan(load_mw) else np.nan
            })

    return pd.DataFrame(rows)


def build_substation_metadata_inventory(substations):
    rows = []

    for _, substation in substations.iterrows():
        flattened = flatten_dict(parse_meta(substation.get("Meta")))

        for key, value in flattened.items():
            rows.append({
                "SubstationID": substation["SubstationID"],
                "MetaKey": key,
                "MetaValue": value,
                "NormalizedKey": normalize_key(key),
                "NumericValue": safe_float(value)
            })

    return pd.DataFrame(rows)


def capacity_from_key_value(key, value):
    numeric = safe_float(value)

    if np.isnan(numeric) or numeric <= 0:
        return None

    normalized = normalize_key(key)

    mw_keys = [
        "availablecapacitymw",
        "totalavailablecapacitymw",
        "capacitymw",
        "totalcapacitymw",
        "ratingmw",
        "ratedmw",
        "nameplatemw",
        "substationcapacitymw"
    ]

    kw_keys = [
        "availablecapacitykw",
        "totalavailablecapacitykw",
        "capacitykw",
        "totalcapacitykw",
        "ratingkw",
        "ratedkw",
        "nameplatekw",
        "substationcapacitykw"
    ]

    mva_keys = [
        "availablecapacitymva",
        "totalavailablecapacitymva",
        "capacitymva",
        "totalcapacitymva",
        "ratingmva",
        "ratedmva",
        "nameplatemva",
        "substationcapacitymva"
    ]

    kva_keys = [
        "availablecapacitykva",
        "totalavailablecapacitykva",
        "capacitykva",
        "totalcapacitykva",
        "ratingkva",
        "ratedkva",
        "nameplatekva",
        "substationcapacitykva"
    ]

    if any(normalized.endswith(candidate) for candidate in mw_keys):
        return numeric, "MW"

    if any(normalized.endswith(candidate) for candidate in kw_keys):
        return numeric / 1000.0, "kW"

    if any(normalized.endswith(candidate) for candidate in mva_keys):
        return numeric * POWER_FACTOR, "MVA"

    if any(normalized.endswith(candidate) for candidate in kva_keys):
        return numeric / 1000.0 * POWER_FACTOR, "kVA"

    return None


def load_external_capacity_file():
    if not SUBSTATION_CAPACITY_FILE:
        return pd.DataFrame()

    path = Path(SUBSTATION_CAPACITY_FILE)

    if not path.exists():
        raise FileNotFoundError(f"Configured SUBSTATION_CAPACITY_FILE was not found: {path.resolve()}")

    if path.suffix.lower() == ".csv":
        df = pd.read_csv(path)

    else:
        df = pd.read_excel(path, sheet_name=SUBSTATION_CAPACITY_SHEET)

    required = {"SubstationID", "AvailableCapacityMW"}

    missing = required - set(df.columns)

    if missing:
        raise RuntimeError(f"Capacity file missing required columns: {sorted(missing)}")

    df = df[["SubstationID", "AvailableCapacityMW"]].copy()

    df["AvailableCapacityMW"] = pd.to_numeric(
        df["AvailableCapacityMW"],
        errors="coerce"
    )

    return df


def resolve_substation_capacity(substations, metadata_inventory):
    external = load_external_capacity_file()

    external_lookup = (
        external.set_index("SubstationID")["AvailableCapacityMW"].to_dict()
        if not external.empty
        else {}
    )

    rows = []

    for _, substation in substations.iterrows():
        substation_id = substation["SubstationID"]

        if substation_id in SUBSTATION_CAPACITY_MW:
            rows.append({
                "SubstationID": substation_id,
                "AvailableCapacityMW": safe_float(SUBSTATION_CAPACITY_MW[substation_id]),
                "CapacityResolutionStatus": "Resolved",
                "CapacitySource": "Manual dictionary",
                "CapacitySourceKey": None,
                "CapacitySourceUnit": "MW"
            })

            continue

        if substation_id in external_lookup:
            rows.append({
                "SubstationID": substation_id,
                "AvailableCapacityMW": safe_float(external_lookup[substation_id]),
                "CapacityResolutionStatus": "Resolved",
                "CapacitySource": "External capacity file",
                "CapacitySourceKey": None,
                "CapacitySourceUnit": "MW"
            })

            continue

        candidates = []

        if not metadata_inventory.empty:
            subset = metadata_inventory[
                metadata_inventory["SubstationID"] == substation_id
            ]

            for _, meta_row in subset.iterrows():
                converted = capacity_from_key_value(
                    meta_row["MetaKey"],
                    meta_row["MetaValue"]
                )

                if converted:
                    candidates.append({
                        "Key": meta_row["MetaKey"],
                        "CapacityMW": converted[0],
                        "Unit": converted[1]
                    })

        if not candidates:
            rows.append({
                "SubstationID": substation_id,
                "AvailableCapacityMW": np.nan,
                "CapacityResolutionStatus": "Unresolved",
                "CapacitySource": "No recognized explicit capacity value",
                "CapacitySourceKey": None,
                "CapacitySourceUnit": None
            })

            continue

        values = np.array(
            [candidate["CapacityMW"] for candidate in candidates],
            dtype=float
        )

        median_value = float(np.nanmedian(values))

        max_difference_pct = (
            float(
                np.nanmax(
                    np.abs(values - median_value) / median_value * 100.0
                )
            )
            if median_value > 0
            else np.inf
        )

        if len(values) == 1 or max_difference_pct <= 2.0:
            rows.append({
                "SubstationID": substation_id,
                "AvailableCapacityMW": median_value,
                "CapacityResolutionStatus": "Resolved",
                "CapacitySource": "Substation metadata",
                "CapacitySourceKey": ", ".join(sorted(set(candidate["Key"] for candidate in candidates))),
                "CapacitySourceUnit": ", ".join(sorted(set(candidate["Unit"] for candidate in candidates)))
            })

        else:
            rows.append({
                "SubstationID": substation_id,
                "AvailableCapacityMW": np.nan,
                "CapacityResolutionStatus": "Ambiguous",
                "CapacitySource": f"Conflicting metadata capacity values with {max_difference_pct:.1f}% maximum disagreement",
                "CapacitySourceKey": ", ".join(sorted(set(candidate["Key"] for candidate in candidates))),
                "CapacitySourceUnit": ", ".join(sorted(set(candidate["Unit"] for candidate in candidates)))
            })

    return pd.DataFrame(rows)


def apply_substation_constraints(substation_summary, capacity_resolution):
    result = substation_summary.merge(
        capacity_resolution,
        on="SubstationID",
        how="left"
    )

    result["Preferred80PctLimitMW"] = result["AvailableCapacityMW"] * PREFERRED_SUBSTATION_LOADING_LOW
    result["Preferred90PctLimitMW"] = result["AvailableCapacityMW"] * PREFERRED_SUBSTATION_LOADING_HIGH
    result["Hard100PctLimitMW"] = result["AvailableCapacityMW"] * HARD_SUBSTATION_LOADING_LIMIT

    result["Preferred80PctHeadroomMW"] = (
        result["Preferred80PctLimitMW"] - result["PeakLoadMW"]
    ).clip(lower=0)

    result["Preferred90PctHeadroomMW"] = (
        result["Preferred90PctLimitMW"] - result["PeakLoadMW"]
    ).clip(lower=0)

    result["Hard100PctHeadroomMW"] = (
        result["Hard100PctLimitMW"] - result["PeakLoadMW"]
    ).clip(lower=0)

    result["PeakSubstationUtilizationPct"] = (
        result["PeakLoadMW"]
        / result["AvailableCapacityMW"]
        * 100.0
    )

    if ENABLE_OVERLOAD_SCENARIO:
        if OVERLOAD_LIMIT_PCT is None or OVERLOAD_DURATION_HOURS is None:
            raise RuntimeError("Overload scenario requires OVERLOAD_LIMIT_PCT and OVERLOAD_DURATION_HOURS.")

        result["OverloadLimitPct"] = OVERLOAD_LIMIT_PCT
        result["OverloadDurationHours"] = OVERLOAD_DURATION_HOURS

        result["OverloadLimitMW"] = (
            result["AvailableCapacityMW"]
            * OVERLOAD_LIMIT_PCT
            / 100.0
        )

        result["OverloadHeadroomMW"] = (
            result["OverloadLimitMW"]
            - result["PeakLoadMW"]
        ).clip(lower=0)

    else:
        result["OverloadLimitPct"] = np.nan
        result["OverloadDurationHours"] = np.nan
        result["OverloadLimitMW"] = np.nan
        result["OverloadHeadroomMW"] = np.nan

    return result


def build_final_location_ranking(location_candidates, feeder_map, substation_summary):
    mapping = feeder_map[
        [
            "GridID",
            "BreakerID",
            "SubstationID",
            "BreakerRatingKVA"
        ]
    ].drop_duplicates("GridID")

    result = location_candidates.merge(
        mapping,
        on="GridID",
        how="left"
    )

    substation_columns = [
        "SubstationID",
        "ExpectedFeederCount",
        "LoadedFeederCount",
        "AllMappedFeedersLoaded",
        "AverageLoadMW",
        "P95LoadMW",
        "PeakLoadMW",
        "PeakTimestampUTC",
        "SumIndividualFeederPeaksMW",
        "CoincidenceFactor",
        "DiversityFactor",
        "DataCoveragePct",
        "LoadProfileConfidence",
        "AvailableCapacityMW",
        "CapacityResolutionStatus",
        "CapacitySource",
        "CapacitySourceKey",
        "CapacitySourceUnit",
        "Preferred80PctLimitMW",
        "Preferred90PctLimitMW",
        "Hard100PctLimitMW",
        "Preferred80PctHeadroomMW",
        "Preferred90PctHeadroomMW",
        "Hard100PctHeadroomMW",
        "PeakSubstationUtilizationPct",
        "OverloadLimitPct",
        "OverloadDurationHours",
        "OverloadLimitMW",
        "OverloadHeadroomMW"
    ]

    substation_columns = [
        column
        for column in substation_columns
        if column in substation_summary.columns
    ]

    substation_join = substation_summary[
        substation_columns
    ].copy()

    rename_columns = {
        column: f"Substation_{column}"
        for column in substation_join.columns
        if column != "SubstationID"
    }

    substation_join = substation_join.rename(
        columns=rename_columns
    )

    result = result.merge(
        substation_join,
        on="SubstationID",
        how="left"
    )

    result["FeederPathHostingMW"] = pd.to_numeric(
        result["EstimatedBaseHostingMW"],
        errors="coerce"
    )

    result["SubstationHardHeadroomMW"] = pd.to_numeric(
        result["Substation_Hard100PctHeadroomMW"],
        errors="coerce"
    )

    result["SubstationPreferred90HeadroomMW"] = pd.to_numeric(
        result["Substation_Preferred90PctHeadroomMW"],
        errors="coerce"
    )

    result["SubstationPreferred80HeadroomMW"] = pd.to_numeric(
        result["Substation_Preferred80PctHeadroomMW"],
        errors="coerce"
    )

    capacity_resolved = (
        result["Substation_CapacityResolutionStatus"] == "Resolved"
    ) & result["SubstationHardHeadroomMW"].notna()

    load_valid = result[
        "Substation_LoadProfileConfidence"
    ].isin(["High", "Medium"])

    result["FinalBaseHostingMW"] = np.nan

    result.loc[
        capacity_resolved,
        "FinalBaseHostingMW"
    ] = np.minimum(
        result.loc[
            capacity_resolved,
            "FeederPathHostingMW"
        ],
        result.loc[
            capacity_resolved,
            "SubstationHardHeadroomMW"
        ]
    )

    result["Preferred90HostingMW"] = np.nan

    result.loc[
        capacity_resolved,
        "Preferred90HostingMW"
    ] = np.minimum(
        result.loc[
            capacity_resolved,
            "FeederPathHostingMW"
        ],
        result.loc[
            capacity_resolved,
            "SubstationPreferred90HeadroomMW"
        ]
    )

    result["Preferred80HostingMW"] = np.nan

    result.loc[
        capacity_resolved,
        "Preferred80HostingMW"
    ] = np.minimum(
        result.loc[
            capacity_resolved,
            "FeederPathHostingMW"
        ],
        result.loc[
            capacity_resolved,
            "SubstationPreferred80HeadroomMW"
        ]
    )

    if "LimitingElementClass" in result.columns:
        result["FinalConstraintClass"] = result["LimitingElementClass"]
    else:
        result["FinalConstraintClass"] = "FeederPath"

    substation_limited = (
        capacity_resolved
        & (
            result["SubstationHardHeadroomMW"]
            < result["FeederPathHostingMW"] - 1e-9
        )
    )

    result.loc[
        substation_limited,
        "FinalConstraintClass"
    ] = "Substation"

    result.loc[
        ~capacity_resolved,
        "FinalConstraintClass"
    ] = "SubstationCapacityUnresolved"

    if "LimitingElementID" in result.columns:
        feeder_constraint_id = result["LimitingElementID"]

    else:
        feeder_constraint_id = result["CandidateLineID"]

    result["FinalConstraintID"] = np.where(
        result["FinalConstraintClass"] == "Substation",
        result["SubstationID"],
        feeder_constraint_id
    )

    result["FinalRankingStatus"] = "Ready"

    result.loc[
        ~capacity_resolved,
        "FinalRankingStatus"
    ] = "Substation capacity unresolved"

    result.loc[
        capacity_resolved & ~load_valid,
        "FinalRankingStatus"
    ] = "Substation load profile incomplete"

    result.loc[
        capacity_resolved
        & load_valid
        & (result["FinalBaseHostingMW"] < MIN_FINAL_HOSTING_MW),
        "FinalRankingStatus"
    ] = "Below minimum hosting threshold"

    ready_mask = (
        (result["FinalRankingStatus"] == "Ready")
        & result["FinalBaseHostingMW"].notna()
        & (result["FinalBaseHostingMW"] >= MIN_FINAL_HOSTING_MW)
    )

    result["FinalRank"] = pd.Series(
        pd.NA,
        index=result.index,
        dtype="Int64"
    )

    sort_columns = [
        "FinalBaseHostingMW",
        "Preferred90HostingMW"
    ]

    ascending = [
        False,
        False
    ]

    if "LoadPointReachabilityPct" in result.columns:
        sort_columns.append("LoadPointReachabilityPct")
        ascending.append(False)

    if "HoursWithin10PctOfPeak" in result.columns:
        sort_columns.append("HoursWithin10PctOfPeak")
        ascending.append(True)

    if "ElectricalDistanceHops" in result.columns:
        sort_columns.append("ElectricalDistanceHops")
        ascending.append(True)

    ready = result.loc[
        ready_mask
    ].sort_values(
        sort_columns,
        ascending=ascending
    ).copy()

    ready["FinalRank"] = np.arange(
        1,
        len(ready) + 1
    )

    result.loc[
        ready.index,
        "FinalRank"
    ] = ready["FinalRank"].astype("Int64")

    shared_counts = (
        result.groupby("SubstationID")["CandidateLineID"]
        .count()
        .rename("CandidateCountOnSharedSubstation")
    )

    result = result.merge(
        shared_counts,
        on="SubstationID",
        how="left"
    )

    return result.sort_values(
        [
            "FinalRank",
            "FinalBaseHostingMW",
            "GridID"
        ],
        ascending=[
            True,
            False,
            True
        ],
        na_position="last"
    ).reset_index(drop=True)


def build_shortlists(final_ranking):
    ready = final_ranking[
        final_ranking["FinalRankingStatus"] == "Ready"
    ].copy()

    global_top = (
        ready.sort_values("FinalRank")
        .head(TOP_GLOBAL_LOCATIONS)
        .copy()
    )

    best_by_feeder = (
        ready.sort_values(
            ["GridID", "FinalRank"]
        )
        .groupby(
            "GridID",
            group_keys=False
        )
        .head(TOP_PER_FEEDER)
        .copy()
    )

    best_by_substation = (
        ready.sort_values(
            ["SubstationID", "FinalRank"]
        )
        .groupby(
            "SubstationID",
            group_keys=False
        )
        .head(TOP_PER_SUBSTATION)
        .copy()
    )

    return global_top, best_by_feeder, best_by_substation


def build_constraint_audit(final_ranking):
    columns = [
        "FinalRank",
        "GridID",
        "CandidateLineID",
        "SubstationID",
        "FeederPathHostingMW",
        "SubstationPreferred80HeadroomMW",
        "SubstationPreferred90HeadroomMW",
        "SubstationHardHeadroomMW",
        "Preferred80HostingMW",
        "Preferred90HostingMW",
        "FinalBaseHostingMW",
        "FinalConstraintClass",
        "FinalConstraintID",
        "FinalRankingStatus",
        "CandidateConfidence",
        "LoadPointReachabilityPct",
        "Substation_PeakLoadMW",
        "Substation_AvailableCapacityMW",
        "Substation_PeakSubstationUtilizationPct",
        "Substation_CoincidenceFactor",
        "Substation_LoadProfileConfidence",
        "Substation_CapacityResolutionStatus"
    ]

    return final_ranking[
        [
            column
            for column in columns
            if column in final_ranking.columns
        ]
    ].copy()


def build_assumptions():
    rows = [
        ["Substation mapping", "Circuit breakers are mapped to substations through breaker meta enclosure_id"],
        ["Shared substation load", "Every feeder served by a relevant substation is included even if that feeder was not itself in the original top 15"],
        ["Coincident demand", "Substation demand is calculated by summing feeder breaker SCADA at matching timestamps"],
        ["Power conversion", "Interval kWh is converted to MW using interval energy divided by interval duration"],
        ["Capacity source", "Substation capacity must come from explicit metadata organizer supplied values or an external capacity file"],
        ["Breaker ratings", "Breaker ratings are not summed and treated as substation capacity"],
        ["100 percent limit", "100 percent of stated substation available capacity is the normal hard planning limit"],
        ["Preferred operating range", "Substation transformers are considered preferable around 80 to 90 percent loading"],
        ["Overload", "Capacity above 100 percent is excluded from the base ranking and may only be evaluated separately with a documented overload range and duration"],
        ["Service transformer assumption", "Organizer guidance notes service transformers are most effective around 30 percent loading"],
        ["Existing location hosting", "EstimatedBaseHostingMW already represents the smallest thermal residual on the feeder breaker to candidate path"],
        ["Final base hosting", "FinalBaseHostingMW equals the smaller of feeder path hosting and remaining substation capacity at the 100 percent limit"],
        ["Preferred hosting", "Preferred80HostingMW and Preferred90HostingMW show additional capacity while keeping the substation below those utilization levels"],
        ["Shared constraint", "Locations on feeders belonging to the same substation share the same upstream capacity and cannot be added together independently"],
        ["Ranking scope", "This ranking evaluates each candidate independently and does not yet optimize simultaneous deployment across multiple sites"],
        ["Next stage", "Highest ranked electrically distinct locations proceed to voltage and power flow validation"]
    ]

    if ENABLE_OVERLOAD_SCENARIO:
        rows.append([
            "Overload sensitivity",
            f"Overload scenario set to {OVERLOAD_LIMIT_PCT} percent for no more than {OVERLOAD_DURATION_HOURS} hours and is not used in the base ranking"
        ])

    return pd.DataFrame(
        rows,
        columns=["Item", "Assumption"]
    )


def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return

    workbook = writer.book
    worksheet = writer.sheets[sheet_name]

    header_format = workbook.add_format({
        "bold": True,
        "border": 1,
        "align": "center",
        "valign": "vcenter"
    })

    number_format = workbook.add_format({
        "num_format": "0.000"
    })

    integer_format = workbook.add_format({
        "num_format": "0"
    })

    percent_format = workbook.add_format({
        "num_format": "0.00"
    })

    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(
            0,
            column_index,
            str(column_name),
            header_format
        )

        values = dataframe[column_name].tolist()

        max_length = max(
            (
                len(str(value))
                for value in values
                if value is not None
                and not (
                    isinstance(value, float)
                    and np.isnan(value)
                )
            ),
            default=0
        )

        width = min(
            max(
                max_length,
                len(str(column_name))
            ) + 2,
            45
        )

        lower_name = str(column_name).lower()

        if "rank" in lower_name or "count" in lower_name:
            worksheet.set_column(
                column_index,
                column_index,
                width,
                integer_format
            )

        elif "pct" in lower_name or "percent" in lower_name or "factor" in lower_name:
            worksheet.set_column(
                column_index,
                column_index,
                width,
                percent_format
            )

        elif pd.api.types.is_float_dtype(
            dataframe[column_name]
        ):
            worksheet.set_column(
                column_index,
                column_index,
                width,
                number_format
            )

        else:
            worksheet.set_column(
                column_index,
                column_index,
                width
            )

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(
            0,
            0,
            len(dataframe),
            len(dataframe.columns) - 1
        )


def main():
    print("GREENSBORO SUBSTATION CONSTRAINED LOCATION RANKING")
    print("Adds shared substation loading and capacity constraints to the existing location hosting results\n")

    location_candidates = load_location_candidates()

    candidate_feeders = sorted(
        location_candidates["GridID"]
        .dropna()
        .unique()
        .tolist()
    )

    conn = connect_to_edm()

    try:
        breaker_map = get_breaker_substation_map(conn)

        candidate_mapping = breaker_map[
            breaker_map["GridID"].isin(
                candidate_feeders
            )
        ].copy()

        relevant_substations = sorted(
            candidate_mapping["SubstationID"]
            .dropna()
            .unique()
            .tolist()
        )

        if not relevant_substations:
            raise RuntimeError(
                "No substations were mapped to the existing location candidate feeders"
            )

        relevant_feeder_map = breaker_map[
            breaker_map["SubstationID"].isin(
                relevant_substations
            )
        ].copy()

        substations = get_substations(
            conn,
            relevant_substations
        )

        breaker_ids = (
            relevant_feeder_map["BreakerID"]
            .dropna()
            .tolist()
        )

        data_sources = get_breaker_data_sources(
            conn,
            breaker_ids
        )

        print(f"Candidate locations loaded: {len(location_candidates):,}")
        print(f"Candidate feeders: {len(candidate_feeders):,}")
        print(f"Relevant substations: {len(relevant_substations):,}")
        print(f"All feeders on those substations: {relevant_feeder_map['GridID'].nunique():,}")
        print(f"Breaker kWh data sources: {len(data_sources):,}\n")

        metadata_inventory = build_substation_metadata_inventory(
            substations
        )

        capacity_resolution = resolve_substation_capacity(
            substations,
            metadata_inventory
        )

        print("Substation capacity resolution")
        print(
            capacity_resolution[
                [
                    "SubstationID",
                    "AvailableCapacityMW",
                    "CapacityResolutionStatus",
                    "CapacitySource"
                ]
            ].to_string(index=False)
        )

        feeder_profiles = {}
        feeder_summary_rows = []
        source_audit_frames = []

        ordered_breakers = (
            relevant_feeder_map
            .sort_values(
                [
                    "SubstationID",
                    "GridID"
                ]
            )
            .reset_index(drop=True)
        )

        for index, breaker in ordered_breakers.iterrows():
            grid_id = breaker["GridID"]
            breaker_id = breaker["BreakerID"]
            substation_id = breaker["SubstationID"]

            source_rows = data_sources[
                data_sources["BreakerID"] == breaker_id
            ].copy()

            print(
                f"[{index + 1}/{len(ordered_breakers)}] "
                f"{substation_id} -> {grid_id} -> {breaker_id}"
            )

            if source_rows.empty:
                print("  WARNING no kWh SCADA sources")
                continue

            profile, source_audit = load_breaker_profile(
                conn,
                grid_id,
                breaker_id,
                source_rows
            )

            if not source_audit.empty:
                source_audit_frames.append(
                    source_audit
                )

            if profile.empty:
                print("  WARNING no usable 2024 SCADA profile")
                continue

            feeder_profiles[grid_id] = profile

            summary = summarize_feeder_profile(
                grid_id,
                substation_id,
                profile
            )

            if summary:
                feeder_summary_rows.append(
                    summary
                )

                print(
                    f"  peak {summary['PeakLoadMW']:.4f} MW | "
                    f"coverage {summary['DataCoveragePct']:.2f}%"
                )

        feeder_summary = pd.DataFrame(
            feeder_summary_rows
        )

        source_audit = (
            pd.concat(
                source_audit_frames,
                ignore_index=True
            )
            if source_audit_frames
            else pd.DataFrame()
        )

        substation_profiles = {}
        substation_summary_rows = []

        for substation_id in relevant_substations:
            feeder_ids = relevant_feeder_map.loc[
                relevant_feeder_map["SubstationID"] == substation_id,
                "GridID"
            ].tolist()

            profile, loaded_count = build_substation_profile(
                substation_id,
                feeder_ids,
                feeder_profiles
            )

            substation_profiles[substation_id] = profile

            summary = summarize_substation_profile(
                substation_id,
                feeder_ids,
                profile,
                loaded_count,
                feeder_summary
            )

            substation_summary_rows.append(
                summary
            )

            peak_text = (
                f"{summary['PeakLoadMW']:.4f} MW"
                if "PeakLoadMW" in summary
                and not pd.isna(summary["PeakLoadMW"])
                else "Unavailable"
            )

            print(
                f"\n{substation_id}: "
                f"coincident peak {peak_text} | "
                f"feeders loaded {loaded_count}/{len(feeder_ids)}"
            )

        substation_summary = pd.DataFrame(
            substation_summary_rows
        )

        constrained_substations = apply_substation_constraints(
            substation_summary,
            capacity_resolution
        )

        peak_contributions = build_peak_contributions(
            substation_summary,
            relevant_feeder_map,
            feeder_profiles
        )

        profile_frames = [
            profile
            for profile in substation_profiles.values()
            if not profile.empty
        ]

        if profile_frames:
            profile_output = pd.concat(
                profile_frames,
                ignore_index=True
            )

            csv_output = profile_output.copy()

            csv_output["Timestamp"] = (
                csv_output["Timestamp"]
                .astype(str)
            )

            csv_output.to_csv(
                SUBSTATION_PROFILE_FILE,
                index=False
            )

            print(
                f"\nSubstation profile CSV: "
                f"{Path(SUBSTATION_PROFILE_FILE).resolve()}"
            )

        final_ranking = build_final_location_ranking(
            location_candidates,
            relevant_feeder_map,
            constrained_substations
        )

        global_top, best_by_feeder, best_by_substation = build_shortlists(
            final_ranking
        )

        constraint_audit = build_constraint_audit(
            final_ranking
        )

        assumptions = build_assumptions()

        output_frames = {
            "Final_Location_Ranking": final_ranking,
            "Global_Top_Locations": global_top,
            "Best_By_Feeder": best_by_feeder,
            "Best_By_Substation": best_by_substation,
            "Constraint_Audit": constraint_audit,
            "Substation_Summary": constrained_substations,
            "Substation_Peak_Contrib": peak_contributions,
            "Feeder_Load_Summary": feeder_summary,
            "Feeder_Substation_Map": relevant_feeder_map,
            "SCADA_Source_Audit": source_audit,
            "Substation_Capacity": capacity_resolution,
            "Substation_Meta_Audit": metadata_inventory,
            "Assumptions": assumptions
        }

        output_frames = {
            name: make_excel_safe(dataframe)
            for name, dataframe in output_frames.items()
        }

        print("\nWriting final workbook...")

        with pd.ExcelWriter(
            OUTPUT_FILE,
            engine="xlsxwriter",
            datetime_format="yyyy-mm-dd hh:mm:ss"
        ) as writer:
            for sheet_name, dataframe in output_frames.items():
                dataframe.to_excel(
                    writer,
                    sheet_name=sheet_name[:31],
                    index=False
                )

                format_excel(
                    writer,
                    sheet_name[:31],
                    dataframe
                )

        ready_count = (
            final_ranking["FinalRankingStatus"] == "Ready"
        ).sum()

        resolved_count = (
            capacity_resolution["CapacityResolutionStatus"] == "Resolved"
        ).sum()

        print("\nSUBSTATION CONSTRAINED RANKING COMPLETE")
        print(f"Workbook: {Path(OUTPUT_FILE).resolve()}")
        print(f"Ready ranked locations: {ready_count:,}")
        print(f"Substation capacities resolved: {resolved_count:,} / {len(capacity_resolution):,}")

        if not global_top.empty:
            display_columns = [
                column
                for column in [
                    "FinalRank",
                    "GridID",
                    "CandidateLineID",
                    "SubstationID",
                    "FeederPathHostingMW",
                    "SubstationHardHeadroomMW",
                    "FinalBaseHostingMW",
                    "Preferred90HostingMW",
                    "FinalConstraintClass",
                    "CandidateConfidence"
                ]
                if column in global_top.columns
            ]

            print("\nTOP FINAL LOCATIONS")

            print(
                global_top[
                    display_columns
                ]
                .head(25)
                .to_string(index=False)
            )

        else:
            print("\nNo locations received a final rank")

            print(
                "Most likely cause is unresolved explicit "
                "substation capacity values"
            )

            print(
                "Populate SUBSTATION_CAPACITY_MW or "
                "SUBSTATION_CAPACITY_FILE and rerun"
            )

        print("\nReview these sheets first")
        print("1 Final_Location_Ranking")
        print("2 Best_By_Substation")
        print("3 Substation_Summary")
        print("4 Constraint_Audit")
        print("5 Assumptions")

        print(
            "\nOnce these rankings are resolved the next stage is "
            "voltage and power flow validation of the strongest "
            "electrically distinct locations"
        )

    finally:
        conn.close()


if __name__ == "__main__":
    main()