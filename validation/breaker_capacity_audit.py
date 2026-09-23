import os
import re
import math
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


# ======================================================================================
# SETTINGS
# ======================================================================================

OUTPUT_FILE = Path("greensboro_breaker_capacity_audit.xlsx")
GSO_FEEDERS = []
ANALYSIS_START = "2024-01-01 00:00:00+00"
ANALYSIS_END = "2025-01-01 00:00:00+00"
ASSUMED_POWER_FACTOR = 0.95
HIGH_CONFIDENCE_DIFF_PCT = 10.0
MEDIUM_CONFIDENCE_DIFF_PCT = 20.0
SHORTLIST_N = 15
MAX_UPSTREAM_DEPTH = 100
VERBOSE = True


# ======================================================================================
# CONNECTION
# ======================================================================================

def connect_edm():
    load_dotenv()
    edm_host = os.getenv("EDM_HOST")
    edm_user = os.getenv("EDM_USER")
    edm_password = os.getenv("EDM_PASSWORD")
    edm_database = os.getenv("EDM_DATABASE", "edm")
    missing = []
    if not edm_host:
        missing.append("EDM_HOST")
    if not edm_user:
        missing.append("EDM_USER")
    if not edm_password:
        missing.append("EDM_PASSWORD")
    if missing:
        raise RuntimeError(f"Missing required value(s) in .env file: {', '.join(missing)}")
    print("\nConnecting to Awesense EDM...")
    print(f"Server: {edm_host}")
    print(f"User: {edm_user}")
    print(f"Database: {edm_database}")
    conn = psycopg2.connect(host=edm_host, user=edm_user, password=edm_password, dbname=edm_database, connect_timeout=30)
    conn.autocommit = True
    print("Connection successful.\n")
    return conn


# ======================================================================================
# GENERAL HELPERS
# ======================================================================================

def query_df(conn, sql, params=None):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        columns = [desc.name for desc in cur.description]
        rows = cur.fetchall()
    return pd.DataFrame(rows, columns=columns)


def feeder_sort_number(grid_id):
    match = re.search(r"(\d+)$", str(grid_id))
    return int(match.group(1)) if match else 999999


def parse_numeric(value):
    if value is None:
        return np.nan
    if isinstance(value, (int, float, np.integer, np.floating)):
        return float(value)
    text = str(value).strip().replace(",", "")
    if not text:
        return np.nan
    match = re.search(r"[-+]?\d*\.?\d+(?:[eE][-+]?\d+)?", text)
    if not match:
        return np.nan
    try:
        return float(match.group())
    except ValueError:
        return np.nan


def parse_metric_array(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, np.ndarray)):
        return [str(x) for x in value]
    text = str(value).strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1]
    if not text:
        return []
    return [x.strip().strip('"') for x in text.split(",") if x.strip()]


def contains_metric(metrics, wanted):
    wanted_norm = wanted.lower().replace(" ", "")
    return any(str(metric).lower().replace(" ", "") == wanted_norm for metric in parse_metric_array(metrics))


def choose_metric_name(metrics, wanted):
    wanted_norm = wanted.lower().replace(" ", "")
    for metric in parse_metric_array(metrics):
        if metric.lower().replace(" ", "") == wanted_norm:
            return metric
    return None


def normalize_phase(value):
    if value is None:
        return ""
    return str(value).upper().replace("{", "").replace("}", "").replace('"', "").replace(" ", "")


def print_stage(text):
    print(f"\n{'=' * 110}\n{text}\n{'=' * 110}")


def excel_safe_df(df):
    safe = df.copy()
    for column in safe.columns:
        if isinstance(safe[column].dtype, pd.DatetimeTZDtype):
            safe[column] = safe[column].dt.tz_convert("UTC").dt.tz_localize(None)
        elif safe[column].dtype == "object":
            safe[column] = safe[column].apply(lambda value: value if value is None or isinstance(value, (str, int, float, bool, np.integer, np.floating)) else str(value))
    return safe


# ======================================================================================
# 1. DISCOVER GREENSBORO FEEDERS
# ======================================================================================

def get_gso_grids(conn):
    print_stage("1. DISCOVERING GREENSBORO FEEDERS")
    grids = query_df(conn, """
        SELECT grid_id, description, last_updated
        FROM grid
        WHERE grid_id ~ '^GSO_[0-9]+$'
        ORDER BY grid_id;
    """)
    if grids.empty:
        raise RuntimeError("No Greensboro feeder grids matching GSO_<number> were found.")
    grids["FeederNumber"] = grids["grid_id"].apply(feeder_sort_number)
    grids = grids.sort_values(["FeederNumber", "grid_id"]).reset_index(drop=True)
    if GSO_FEEDERS:
        requested = set(GSO_FEEDERS)
        grids = grids[grids["grid_id"].isin(requested)].copy()
        missing = requested - set(grids["grid_id"])
        if missing:
            print(f"Requested feeders not found: {sorted(missing)}")
    if grids.empty:
        raise RuntimeError("No valid Greensboro feeders remain after applying GSO_FEEDERS.")
    print(f"Feeders selected: {len(grids):,}")
    return grids


# ======================================================================================
# 2. BREAKER INVENTORY
# ======================================================================================

def get_breaker_inventory(conn, feeders):
    print_stage("2. PULLING FEEDER BREAKERS AND RATING METADATA")
    df = query_df(conn, """
        SELECT ge.grid_id,
               ge.grid_element_id AS "BreakerElementID",
               ge.type AS "ElementType",
               ge.customer_type AS "ElementCustomerType",
               ge.phases AS "BreakerPhases",
               ge.is_producer AS "IsProducer",
               ge.is_consumer AS "IsConsumer",
               ge.is_switchable AS "IsSwitchable",
               ge.switch_is_open AS "SwitchIsOpen",
               ge.power_flow_direction AS "PowerFlowDirection",
               ge.upstream_grid_element_id AS "UpstreamElementID",
               ge.terminal1_cn AS "Terminal1CN",
               ge.terminal2_cn AS "Terminal2CN",
               ge.meta ->> 'rating_kva' AS "RawRatingKVA",
               ge.meta ->> 'rating_a' AS "RawRatingA",
               ge.meta ->> 'voltage_level' AS "RawVoltageLevel",
               CASE
                   WHEN ge.geometry IS NULL THEN NULL
                   WHEN ST_SRID(ge.geometry) = 4326 THEN ST_Y(ST_Centroid(ge.geometry))
                   WHEN ST_SRID(ge.geometry) > 0 THEN ST_Y(ST_Centroid(ST_Transform(ge.geometry, 4326)))
                   ELSE ST_Y(ST_Centroid(ge.geometry))
               END AS "Latitude",
               CASE
                   WHEN ge.geometry IS NULL THEN NULL
                   WHEN ST_SRID(ge.geometry) = 4326 THEN ST_X(ST_Centroid(ge.geometry))
                   WHEN ST_SRID(ge.geometry) > 0 THEN ST_X(ST_Centroid(ST_Transform(ge.geometry, 4326)))
                   ELSE ST_X(ST_Centroid(ge.geometry))
               END AS "Longitude"
        FROM grid_element ge
        WHERE ge.grid_id = ANY(%s)
          AND (COALESCE(ge.type, '') ~* 'breaker' OR COALESCE(ge.customer_type, '') ~* 'breaker')
        ORDER BY ge.grid_id, ge.grid_element_id;
    """, (feeders,))
    if df.empty:
        raise RuntimeError("No breaker elements were found for the selected Greensboro feeders.")
    df["StoredBreakerRatingKVA"] = df["RawRatingKVA"].apply(parse_numeric)
    df["BreakerRatingA"] = df["RawRatingA"].apply(parse_numeric)
    df["BreakerVoltageRaw"] = df["RawVoltageLevel"].apply(parse_numeric)
    print(f"Breaker rows found: {len(df):,}")
    print(f"Feeders represented by breakers: {df['grid_id'].nunique():,}")
    print(f"Breakers with rating_kva: {df['StoredBreakerRatingKVA'].notna().sum():,}")
    print(f"Breakers with rating_a: {df['BreakerRatingA'].notna().sum():,}")
    print(f"Breakers with voltage_level: {df['BreakerVoltageRaw'].notna().sum():,}")
    return df


# ======================================================================================
# 3. BREAKER DATA SOURCES
# ======================================================================================

def get_breaker_data_sources(conn, feeders):
    print_stage("3. FINDING BREAKER-LEVEL SCADA SOURCES")
    df = query_df(conn, """
        SELECT ds.grid_id,
               ds.grid_element_data_source_id AS "DataSourceID",
               ds.grid_element_id AS "BreakerElementID",
               ge.type AS "ElementType",
               ge.customer_type AS "ElementCustomerType",
               ge.is_producer AS "IsProducer",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.metrics AS "Metrics",
               ds.friendly_id AS "FriendlyID",
               ds.phases AS "DataSourcePhases",
               ds.direction AS "Direction",
               ds.valid AS "ValidRange"
        FROM grid_element_data_source ds
        JOIN grid_element ge ON ds.grid_id = ge.grid_id AND ds.grid_element_id = ge.grid_element_id
        WHERE ds.grid_id = ANY(%s)
          AND (COALESCE(ge.type, '') ~* 'breaker' OR COALESCE(ge.customer_type, '') ~* 'breaker')
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """, (feeders,))
    if df.empty:
        raise RuntimeError("No breaker-level data sources were found.")
    df["HasKWh"] = df["Metrics"].apply(lambda x: contains_metric(x, "kWh"))
    df["HasVoltage"] = df["Metrics"].apply(lambda x: contains_metric(x, "V"))
    df["NormalizedPhase"] = df["DataSourcePhases"].apply(normalize_phase)
    print(f"Breaker data-source rows found: {len(df):,}")
    print(f"Breaker data sources containing kWh: {df['HasKWh'].sum():,}")
    print(f"Breaker data sources containing voltage: {df['HasVoltage'].sum():,}")
    return df


# ======================================================================================
# 4. SELECT ONE FEEDER BREAKER PER GRID
# ======================================================================================

def select_feeder_breakers(breakers, data_sources, feeders):
    print_stage("4. SELECTING ONE FEEDER BREAKER PER GRID")
    kwh_sources = data_sources[data_sources["HasKWh"] == True].copy()
    if kwh_sources.empty:
        raise RuntimeError("No breaker kWh data sources were found.")
    source_summary = kwh_sources.groupby(["grid_id", "BreakerElementID"], as_index=False).agg(
        KWhSourceCount=("DataSourceID", "count"),
        UniquePhaseCount=("NormalizedPhase", "nunique"),
        DataSourceTypes=("DataSourceType", lambda x: ", ".join(sorted(set(str(v) for v in x if pd.notna(v))))),
        Providers=("Provider", lambda x: ", ".join(sorted(set(str(v) for v in x if pd.notna(v))))),
        FriendlyIDs=("FriendlyID", lambda x: ", ".join(sorted(set(str(v) for v in x if pd.notna(v))))),
    )
    candidates = breakers.merge(source_summary, on=["grid_id", "BreakerElementID"], how="left")
    candidates["KWhSourceCount"] = candidates["KWhSourceCount"].fillna(0).astype(int)
    candidates["UniquePhaseCount"] = candidates["UniquePhaseCount"].fillna(0).astype(int)
    candidates["BreakerSelectionScore"] = 0
    candidates.loc[candidates["KWhSourceCount"] > 0, "BreakerSelectionScore"] += 100
    candidates.loc[candidates["KWhSourceCount"] >= 3, "BreakerSelectionScore"] += 40
    candidates.loc[candidates["UniquePhaseCount"] >= 3, "BreakerSelectionScore"] += 20
    candidates.loc[candidates["IsProducer"] == True, "BreakerSelectionScore"] += 20
    candidates.loc[candidates["StoredBreakerRatingKVA"].notna(), "BreakerSelectionScore"] += 10
    candidates.loc[candidates["BreakerRatingA"].notna(), "BreakerSelectionScore"] += 5
    candidates.loc[candidates["BreakerVoltageRaw"].notna(), "BreakerSelectionScore"] += 5
    candidates.loc[candidates["SwitchIsOpen"] == True, "BreakerSelectionScore"] -= 50
    candidates = candidates.sort_values(["grid_id", "BreakerSelectionScore", "KWhSourceCount", "StoredBreakerRatingKVA"], ascending=[True, False, False, False])
    candidates["BreakerCandidateRank"] = candidates.groupby("grid_id").cumcount() + 1
    selected = candidates[candidates["BreakerCandidateRank"] == 1].copy()
    missing_feeders = set(feeders) - set(selected["grid_id"])
    if missing_feeders:
        print(f"WARNING: no feeder breaker could be selected for {len(missing_feeders)} feeders: {sorted(missing_feeders)}")
    print(f"Selected feeder breakers: {len(selected):,}")
    return selected, candidates


# ======================================================================================
# 5. VALIDATE BREAKER RATINGS
# ======================================================================================

def calculate_rating_candidates(row):
    stored_kva = parse_numeric(row.get("StoredBreakerRatingKVA"))
    amps = parse_numeric(row.get("BreakerRatingA"))
    voltage_raw = parse_numeric(row.get("BreakerVoltageRaw"))
    result = {
        "VoltageKV": np.nan,
        "VoltageInterpretation": None,
        "CalculatedBreakerKVA": np.nan,
        "RatingDifferencePct": np.nan,
        "RatingValidationStatus": "Insufficient rating data",
        "CapacityConfidence": "Unknown",
        "CapacityBasis": None,
        "AuditedBreakerCapacityKVA": np.nan,
        "ConservativeBreakerCapacityKVA": np.nan,
        "AuditFlag": None,
    }
    if pd.isna(stored_kva) and (pd.isna(amps) or pd.isna(voltage_raw)):
        result["AuditFlag"] = "No usable breaker capacity rating"
        return pd.Series(result)
    calculation_options = []
    if not pd.isna(amps) and not pd.isna(voltage_raw) and amps > 0 and voltage_raw > 0:
        voltage_options = [
            ("kV line-to-line", voltage_raw, math.sqrt(3) * voltage_raw * amps),
            ("V line-to-line", voltage_raw / 1000.0, math.sqrt(3) * (voltage_raw / 1000.0) * amps),
            ("kV phase-to-neutral", voltage_raw, 3.0 * voltage_raw * amps),
            ("V phase-to-neutral", voltage_raw / 1000.0, 3.0 * (voltage_raw / 1000.0) * amps),
        ]
        for interpretation, voltage_kv, calculated_kva in voltage_options:
            diff_pct = abs(calculated_kva - stored_kva) / stored_kva * 100.0 if not pd.isna(stored_kva) and stored_kva > 0 else np.nan
            calculation_options.append({"VoltageInterpretation": interpretation, "VoltageKV": voltage_kv, "CalculatedBreakerKVA": calculated_kva, "RatingDifferencePct": diff_pct})
    best = None
    if calculation_options:
        if not pd.isna(stored_kva) and stored_kva > 0:
            best = min(calculation_options, key=lambda x: x["RatingDifferencePct"] if not pd.isna(x["RatingDifferencePct"]) else float("inf"))
        else:
            best = calculation_options[0]
    if best is not None:
        result.update(best)
    calculated_kva = result["CalculatedBreakerKVA"]
    diff_pct = result["RatingDifferencePct"]
    valid_capacities = [value for value in [stored_kva, calculated_kva] if not pd.isna(value) and value > 0]
    if valid_capacities:
        result["ConservativeBreakerCapacityKVA"] = min(valid_capacities)
    if not pd.isna(stored_kva) and not pd.isna(calculated_kva):
        if diff_pct <= HIGH_CONFIDENCE_DIFF_PCT:
            result["RatingValidationStatus"] = "Stored kVA agrees with amp/voltage calculation"
            result["CapacityConfidence"] = "High"
            result["CapacityBasis"] = "Stored breaker rating_kva validated by rating_a + voltage"
            result["AuditedBreakerCapacityKVA"] = stored_kva
            result["AuditFlag"] = ""
        elif diff_pct <= MEDIUM_CONFIDENCE_DIFF_PCT:
            result["RatingValidationStatus"] = "Stored kVA reasonably agrees with amp/voltage calculation"
            result["CapacityConfidence"] = "Medium"
            result["CapacityBasis"] = "Stored breaker rating_kva with moderate amp/voltage agreement"
            result["AuditedBreakerCapacityKVA"] = stored_kva
            result["AuditFlag"] = "Review rating difference"
        else:
            result["RatingValidationStatus"] = "Stored kVA does not agree with amp/voltage calculation"
            result["CapacityConfidence"] = "Low"
            result["CapacityBasis"] = "Conservative minimum of stored and calculated breaker capacity"
            result["AuditFlag"] = "Breaker rating mismatch - topology/unit review required"
    elif not pd.isna(stored_kva):
        result["RatingValidationStatus"] = "Stored kVA available; independent calculation unavailable"
        result["CapacityConfidence"] = "Medium-Low"
        result["CapacityBasis"] = "Stored breaker rating_kva only"
        result["AuditFlag"] = "Cannot independently validate stored breaker rating"
    elif not pd.isna(calculated_kva):
        result["RatingValidationStatus"] = "Capacity calculated from rating_a and voltage; stored kVA unavailable"
        result["CapacityConfidence"] = "Low"
        result["CapacityBasis"] = "Calculated from rating_a + voltage only"
        result["AuditFlag"] = "Stored breaker rating_kva unavailable"
    return pd.Series(result)


def validate_breaker_ratings(selected_breakers):
    print_stage("5. VALIDATING BREAKER CAPACITY RATINGS")
    validation = selected_breakers.apply(calculate_rating_candidates, axis=1)
    audited = pd.concat([selected_breakers.reset_index(drop=True), validation.reset_index(drop=True)], axis=1)
    print("\nCapacity confidence counts:")
    print(audited["CapacityConfidence"].value_counts(dropna=False).to_string())
    return audited


# ======================================================================================
# 6. SCADA SOURCE SELECTION AND SUMMARY
# ======================================================================================

def select_scada_sources_for_breaker(data_sources, feeder, breaker_element_id):
    rows = data_sources[(data_sources["grid_id"] == feeder) & (data_sources["BreakerElementID"] == breaker_element_id) & (data_sources["HasKWh"] == True)].copy()
    if rows.empty:
        return rows
    abc_rows = rows[rows["NormalizedPhase"].apply(lambda x: all(phase in x for phase in ["A", "B", "C"]))]
    if not abc_rows.empty:
        return abc_rows.head(1).copy()
    selected_parts = []
    for phase in ["A", "B", "C"]:
        phase_rows = rows[rows["NormalizedPhase"] == phase]
        if not phase_rows.empty:
            selected_parts.append(phase_rows.head(1))
    if selected_parts:
        return pd.concat(selected_parts, ignore_index=True)
    return rows.head(1).copy()


def summarize_breaker_scada(conn, feeder, breaker_element_id, selected_sources):
    if selected_sources.empty:
        return {"grid_id": feeder, "BreakerElementID": breaker_element_id, "SCADAStatus": "No kWh source selected"}
    union_parts = []
    params = []
    for _, row in selected_sources.iterrows():
        metric = choose_metric_name(row["Metrics"], "kWh")
        if metric is None:
            continue
        union_parts.append("""
            SELECT timestamp, value
            FROM ts_data_source_select(%s, %s, tstzrange(%s::timestamptz, %s::timestamptz, '[)'))
        """)
        params.extend([str(row["DataSourceID"]), metric, ANALYSIS_START, ANALYSIS_END])
    if not union_parts:
        return {"grid_id": feeder, "BreakerElementID": breaker_element_id, "SCADAStatus": "Selected source did not expose kWh metric"}
    union_sql = "\nUNION ALL\n".join(union_parts)
    sql = f"""
        WITH raw AS (
            {union_sql}
        ),
        interval_energy AS (
            SELECT timestamp, SUM(value) AS interval_kwh
            FROM raw
            WHERE value IS NOT NULL
            GROUP BY timestamp
        ),
        timed AS (
            SELECT timestamp,
                   interval_kwh,
                   EXTRACT(EPOCH FROM (timestamp - LAG(timestamp) OVER (ORDER BY timestamp))) / 3600.0 AS interval_hours
            FROM interval_energy
        ),
        calculated AS (
            SELECT timestamp,
                   interval_kwh,
                   interval_hours,
                   CASE WHEN interval_hours > 0 AND interval_hours <= 24 THEN interval_kwh / interval_hours / 1000.0 END AS estimated_mw
            FROM timed
        ),
        peak_row AS (
            SELECT timestamp AS peak_timestamp, estimated_mw AS peak_mw
            FROM calculated
            WHERE estimated_mw IS NOT NULL
            ORDER BY estimated_mw DESC
            LIMIT 1
        )
        SELECT COUNT(*) AS "Observations",
               MIN(timestamp) AS "FirstTimestamp",
               MAX(timestamp) AS "LastTimestamp",
               PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY interval_hours) FILTER (WHERE interval_hours > 0) * 60.0 AS "MedianIntervalMinutes",
               AVG(interval_kwh) AS "AverageIntervalKWh",
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY interval_kwh) AS "P95IntervalKWh",
               MAX(interval_kwh) AS "PeakIntervalKWh",
               AVG(estimated_mw) AS "AverageLoadMW",
               PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY estimated_mw) AS "P95LoadMW",
               MAX(estimated_mw) AS "PeakLoadMW",
               (SELECT peak_timestamp FROM peak_row) AS "PeakLoadTimestamp"
        FROM calculated;
    """
    result = query_df(conn, sql, tuple(params))
    if result.empty:
        return {"grid_id": feeder, "BreakerElementID": breaker_element_id, "SCADAStatus": "No time-series observations returned"}
    row = result.iloc[0].to_dict()
    row["grid_id"] = feeder
    row["BreakerElementID"] = breaker_element_id
    row["SelectedSCADASourceCount"] = len(selected_sources)
    row["SelectedSCADASourceIDs"] = ", ".join(selected_sources["DataSourceID"].astype(str).tolist())
    row["SelectedSCADAPhases"] = ", ".join(selected_sources["DataSourcePhases"].fillna("").astype(str).tolist())
    row["SCADAStatus"] = "OK"
    return row


def get_scada_summary(conn, audited_breakers, data_sources):
    print_stage("6. SUMMARIZING FULL-YEAR BREAKER SCADA LOAD")
    rows = []
    total = len(audited_breakers)
    for position, (_, breaker) in enumerate(audited_breakers.iterrows(), start=1):
        feeder = breaker["grid_id"]
        breaker_id = breaker["BreakerElementID"]
        if VERBOSE:
            print(f"[{position}/{total}] {feeder} -> {breaker_id}")
        selected_sources = select_scada_sources_for_breaker(data_sources, feeder, breaker_id)
        try:
            rows.append(summarize_breaker_scada(conn, feeder, breaker_id, selected_sources))
        except Exception as exc:
            print(f"  SCADA summary failed for {feeder}: {exc}")
            rows.append({"grid_id": feeder, "BreakerElementID": breaker_id, "SCADAStatus": f"ERROR: {exc}"})
    summary = pd.DataFrame(rows)
    print(f"\nSCADA summaries created: {len(summary):,}")
    print(f"Successful SCADA summaries: {(summary['SCADAStatus'] == 'OK').sum():,}")
    return summary


# ======================================================================================
# 7. UPSTREAM PATH TRACE
# ======================================================================================

def get_upstream_path(conn, feeder, breaker_element_id):
    df = query_df(conn, """
        WITH RECURSIVE path AS (
            SELECT ge.grid_id,
                   ge.grid_element_id,
                   ge.type,
                   ge.customer_type,
                   ge.phases,
                   ge.is_producer,
                   ge.is_consumer,
                   ge.is_switchable,
                   ge.switch_is_open,
                   ge.upstream_grid_element_id,
                   ge.terminal1_cn,
                   ge.terminal2_cn,
                   ge.meta ->> 'rating_kva' AS rating_kva,
                   ge.meta ->> 'rating_a' AS rating_a,
                   ge.meta ->> 'voltage_level' AS voltage_level,
                   ge.meta ->> 'primary_voltage' AS primary_voltage,
                   ge.meta ->> 'secondary_voltage' AS secondary_voltage,
                   0 AS depth,
                   ARRAY[ge.grid_element_id::text] AS visited_ids
            FROM grid_element ge
            WHERE ge.grid_id = %s AND ge.grid_element_id = %s
            UNION ALL
            SELECT up.grid_id,
                   up.grid_element_id,
                   up.type,
                   up.customer_type,
                   up.phases,
                   up.is_producer,
                   up.is_consumer,
                   up.is_switchable,
                   up.switch_is_open,
                   up.upstream_grid_element_id,
                   up.terminal1_cn,
                   up.terminal2_cn,
                   up.meta ->> 'rating_kva' AS rating_kva,
                   up.meta ->> 'rating_a' AS rating_a,
                   up.meta ->> 'voltage_level' AS voltage_level,
                   up.meta ->> 'primary_voltage' AS primary_voltage,
                   up.meta ->> 'secondary_voltage' AS secondary_voltage,
                   p.depth + 1,
                   p.visited_ids || up.grid_element_id::text
            FROM path p
            JOIN grid_element up ON up.grid_id = p.grid_id AND up.grid_element_id = p.upstream_grid_element_id
            WHERE p.depth < %s AND NOT up.grid_element_id::text = ANY(p.visited_ids)
        )
        SELECT grid_id,
               grid_element_id AS "PathElementID",
               type AS "ElementType",
               customer_type AS "ElementCustomerType",
               phases AS "Phases",
               is_producer AS "IsProducer",
               is_consumer AS "IsConsumer",
               is_switchable AS "IsSwitchable",
               switch_is_open AS "SwitchIsOpen",
               upstream_grid_element_id AS "UpstreamElementID",
               terminal1_cn AS "Terminal1CN",
               terminal2_cn AS "Terminal2CN",
               rating_kva AS "RawRatingKVA",
               rating_a AS "RawRatingA",
               voltage_level AS "RawVoltageLevel",
               primary_voltage AS "RawPrimaryVoltage",
               secondary_voltage AS "RawSecondaryVoltage",
               depth AS "UpstreamDepth"
        FROM path
        ORDER BY depth;
    """, (feeder, breaker_element_id, MAX_UPSTREAM_DEPTH))
    if not df.empty:
        df.insert(1, "SelectedBreakerElementID", breaker_element_id)
    return df


def build_upstream_paths(conn, audited_breakers):
    print_stage("7. TRACING UPSTREAM PATHS FROM SELECTED BREAKERS")
    path_frames = []
    summaries = []
    total = len(audited_breakers)
    for position, (_, breaker) in enumerate(audited_breakers.iterrows(), start=1):
        feeder = breaker["grid_id"]
        breaker_id = breaker["BreakerElementID"]
        if VERBOSE:
            print(f"[{position}/{total}] {feeder} -> {breaker_id}")
        try:
            path = get_upstream_path(conn, feeder, breaker_id)
        except Exception as exc:
            print(f"  Upstream trace failed for {feeder}: {exc}")
            summaries.append({"grid_id": feeder, "BreakerElementID": breaker_id, "UpstreamPathStatus": f"ERROR: {exc}"})
            continue
        if path.empty:
            summaries.append({"grid_id": feeder, "BreakerElementID": breaker_id, "UpstreamPathStatus": "No path returned"})
            continue
        path_frames.append(path)
        root = path.sort_values("UpstreamDepth").iloc[-1]
        terminated_at_null = pd.isna(root["UpstreamElementID"])
        max_depth_hit = int(root["UpstreamDepth"]) >= MAX_UPSTREAM_DEPTH
        summaries.append({
            "grid_id": feeder,
            "BreakerElementID": breaker_id,
            "UpstreamPathElementCount": len(path),
            "UpstreamPathDepth": int(root["UpstreamDepth"]),
            "SourceElementID": root["PathElementID"],
            "SourceElementType": root["ElementType"],
            "SourceElementCustomerType": root["ElementCustomerType"],
            "SourceIsProducer": root["IsProducer"],
            "PathTerminatedAtNullUpstream": terminated_at_null,
            "PathHitMaxDepth": max_depth_hit,
            "UpstreamPathStatus": "OK" if terminated_at_null and not max_depth_hit else "Review",
        })
    detail = pd.concat(path_frames, ignore_index=True) if path_frames else pd.DataFrame()
    summary = pd.DataFrame(summaries)
    print(f"\nFeeders with upstream path results: {len(summary):,}")
    return detail, summary


# ======================================================================================
# 8. BUILD AUDITED CAPACITY SCREEN
# ======================================================================================

def build_capacity_screen(grids, audited_breakers, scada_summary, path_summary):
    print_stage("8. BUILDING AUDITED FEEDER CAPACITY SCREEN")
    screen = grids.rename(columns={"grid_id": "FeederID", "description": "Description", "last_updated": "GridLastUpdated"}).copy()
    breaker_df = audited_breakers.rename(columns={"grid_id": "FeederID"}).copy()
    scada_df = scada_summary.rename(columns={"grid_id": "FeederID"}).copy()
    path_df = path_summary.rename(columns={"grid_id": "FeederID"}).copy()
    screen = screen.merge(breaker_df, on="FeederID", how="left")
    screen = screen.merge(scada_df.drop(columns=["BreakerElementID"], errors="ignore"), on="FeederID", how="left")
    screen = screen.merge(path_df.drop(columns=["BreakerElementID"], errors="ignore"), on="FeederID", how="left")
    required = ["StoredBreakerRatingKVA", "CalculatedBreakerKVA", "AuditedBreakerCapacityKVA", "ConservativeBreakerCapacityKVA", "PeakLoadMW", "P95LoadMW", "AverageLoadMW"]
    for column in required:
        if column not in screen.columns:
            screen[column] = np.nan
    screen["StoredBreakerCapacityMVA"] = pd.to_numeric(screen["StoredBreakerRatingKVA"], errors="coerce") / 1000.0
    screen["CalculatedBreakerCapacityMVA"] = pd.to_numeric(screen["CalculatedBreakerKVA"], errors="coerce") / 1000.0
    screen["AuditedBreakerCapacityMVA"] = pd.to_numeric(screen["AuditedBreakerCapacityKVA"], errors="coerce") / 1000.0
    screen["ConservativeBreakerCapacityMVA"] = pd.to_numeric(screen["ConservativeBreakerCapacityKVA"], errors="coerce") / 1000.0
    screen["AuditedBreakerCapacityMW"] = screen["AuditedBreakerCapacityMVA"] * ASSUMED_POWER_FACTOR
    screen["ConservativeBreakerCapacityMW"] = screen["ConservativeBreakerCapacityMVA"] * ASSUMED_POWER_FACTOR
    screen["ValidatedHeadroomMW"] = screen["AuditedBreakerCapacityMW"] - pd.to_numeric(screen["PeakLoadMW"], errors="coerce")
    screen["ValidatedHeadroomMW"] = screen["ValidatedHeadroomMW"].where(screen["ValidatedHeadroomMW"].isna() | (screen["ValidatedHeadroomMW"] >= 0), 0)
    screen["ConservativeHeadroomMW"] = screen["ConservativeBreakerCapacityMW"] - pd.to_numeric(screen["PeakLoadMW"], errors="coerce")
    screen["ConservativeHeadroomMW"] = screen["ConservativeHeadroomMW"].where(screen["ConservativeHeadroomMW"].isna() | (screen["ConservativeHeadroomMW"] >= 0), 0)
    screen["PeakUtilizationPct_Audited"] = pd.to_numeric(screen["PeakLoadMW"], errors="coerce") / screen["AuditedBreakerCapacityMW"] * 100.0
    screen["PeakUtilizationPct_Conservative"] = pd.to_numeric(screen["PeakLoadMW"], errors="coerce") / screen["ConservativeBreakerCapacityMW"] * 100.0
    screen["P95UtilizationPct_Audited"] = pd.to_numeric(screen["P95LoadMW"], errors="coerce") / screen["AuditedBreakerCapacityMW"] * 100.0
    screen["AverageUtilizationPct_Audited"] = pd.to_numeric(screen["AverageLoadMW"], errors="coerce") / screen["AuditedBreakerCapacityMW"] * 100.0
    eligible = screen["CapacityConfidence"].isin(["High", "Medium"]) & screen["ValidatedHeadroomMW"].notna() & (screen["SCADAStatus"] == "OK")
    screen["RankEligible"] = eligible
    screen["CapacityRank"] = pd.Series(pd.NA, index=screen.index, dtype="Int64")
    if eligible.any():
        screen.loc[eligible, "CapacityRank"] = screen.loc[eligible, "ValidatedHeadroomMW"].rank(method="min", ascending=False).astype("Int64")
    screen["TopologyReadyFlag"] = screen["RankEligible"] & (screen["UpstreamPathStatus"] == "OK") & screen["SourceElementID"].notna()
    screen["RecommendedNextStep"] = np.where(
        screen["TopologyReadyFlag"],
        "Eligible for detailed line/transformer bottleneck and hosting-capacity analysis",
        np.where(screen["CapacityConfidence"].isin(["Low", "Unknown", "Medium-Low"]), "Resolve breaker rating/unit mismatch before using capacity result", "Review SCADA or upstream topology completeness")
    )
    preferred_order = [
        "CapacityRank", "RankEligible", "TopologyReadyFlag", "FeederID", "Description", "BreakerElementID", "Latitude", "Longitude",
        "CapacityConfidence", "RatingValidationStatus", "AuditFlag", "StoredBreakerRatingKVA", "BreakerRatingA", "BreakerVoltageRaw", "VoltageKV", "VoltageInterpretation",
        "CalculatedBreakerKVA", "RatingDifferencePct", "StoredBreakerCapacityMVA", "CalculatedBreakerCapacityMVA", "AuditedBreakerCapacityMVA", "ConservativeBreakerCapacityMVA",
        "AuditedBreakerCapacityMW", "ConservativeBreakerCapacityMW", "AverageLoadMW", "P95LoadMW", "PeakLoadMW", "PeakLoadTimestamp", "ValidatedHeadroomMW", "ConservativeHeadroomMW",
        "AverageUtilizationPct_Audited", "P95UtilizationPct_Audited", "PeakUtilizationPct_Audited", "PeakUtilizationPct_Conservative", "Observations", "MedianIntervalMinutes",
        "FirstTimestamp", "LastTimestamp", "SelectedSCADASourceCount", "SelectedSCADASourceIDs", "SelectedSCADAPhases", "SCADAStatus", "UpstreamElementID", "SourceElementID",
        "SourceElementType", "SourceElementCustomerType", "SourceIsProducer", "UpstreamPathElementCount", "UpstreamPathDepth", "PathTerminatedAtNullUpstream", "PathHitMaxDepth",
        "UpstreamPathStatus", "CapacityBasis", "RecommendedNextStep", "GridLastUpdated"
    ]
    existing = [column for column in preferred_order if column in screen.columns]
    remaining = [column for column in screen.columns if column not in existing and column != "FeederNumber"]
    screen = screen[existing + remaining]
    screen = screen.sort_values(["RankEligible", "CapacityRank", "FeederID"], ascending=[False, True, True], na_position="last").reset_index(drop=True)
    print("\nValidated capacity summary:")
    print(f"  High confidence:   {(screen['CapacityConfidence'] == 'High').sum():,}")
    print(f"  Medium confidence: {(screen['CapacityConfidence'] == 'Medium').sum():,}")
    print(f"  Rank eligible:     {screen['RankEligible'].sum():,}")
    print(f"  Topology ready:    {screen['TopologyReadyFlag'].sum():,}")
    top_cols = ["CapacityRank", "FeederID", "CapacityConfidence", "AuditedBreakerCapacityMW", "PeakLoadMW", "ValidatedHeadroomMW", "PeakUtilizationPct_Audited", "SourceElementID"]
    print("\nTop audited feeders:")
    print(screen[top_cols].head(SHORTLIST_N).to_string(index=False))
    return screen


# ======================================================================================
# 9. SHORTLIST
# ======================================================================================

def build_shortlist(screen):
    eligible = screen[(screen["RankEligible"] == True) & (screen["TopologyReadyFlag"] == True)].copy()
    if eligible.empty:
        eligible = screen[screen["RankEligible"] == True].copy()
    shortlist = eligible.sort_values("CapacityRank").head(SHORTLIST_N).copy()
    if shortlist.empty:
        return shortlist
    keep_columns = ["CapacityRank", "FeederID", "BreakerElementID", "SourceElementID", "SourceElementType", "Latitude", "Longitude", "CapacityConfidence", "AuditedBreakerCapacityMW", "AverageLoadMW", "P95LoadMW", "PeakLoadMW", "ValidatedHeadroomMW", "PeakUtilizationPct_Audited", "UpstreamPathDepth", "RecommendedNextStep"]
    keep_columns = [column for column in keep_columns if column in shortlist.columns]
    return shortlist[keep_columns].reset_index(drop=True)


# ======================================================================================
# 10. EXCEL OUTPUT
# ======================================================================================

def write_excel(screen, shortlist, breaker_candidates, audited_breakers, scada_summary, breaker_data_sources, upstream_detail, upstream_summary):
    print_stage("9. WRITING ONE EXCEL WORKBOOK")
    methodology = pd.DataFrame([
        ["Purpose", "Validate Greensboro feeder-breaker capacity before detailed topology, bottleneck, hosting-capacity, and optimization modeling."],
        ["Primary measurement point", "One feeder breaker per GSO grid is selected using breaker-level kWh sources, phase coverage, producer flag, and rating metadata."],
        ["Stored capacity metadata", "Breaker rating_kva is treated as the stored apparent-power rating candidate."],
        ["Independent rating check", "Breaker kVA is independently calculated from rating_a and voltage_level using multiple plausible voltage interpretations; the interpretation that best agrees with stored rating_kva is retained."],
        ["High confidence", f"Stored breaker kVA and amp/voltage calculation differ by no more than {HIGH_CONFIDENCE_DIFF_PCT:.1f}%."],
        ["Medium confidence", f"Difference is greater than {HIGH_CONFIDENCE_DIFF_PCT:.1f}% but no more than {MEDIUM_CONFIDENCE_DIFF_PCT:.1f}%."],
        ["Low confidence", f"Difference exceeds {MEDIUM_CONFIDENCE_DIFF_PCT:.1f}%; formal audited headroom is withheld."],
        ["Real-power conversion", f"Audited breaker MVA is converted to preliminary MW using assumed power factor {ASSUMED_POWER_FACTOR:.2f}."],
        ["SCADA period", f"Breaker load is summarized from {ANALYSIS_START} through {ANALYSIS_END}."],
        ["SCADA conversion", "Interval kWh is divided by observed interval duration and 1000 to estimate MW. This assumes kWh values represent interval energy rather than cumulative register values."],
        ["Validated headroom", "Audited breaker MW minus observed full-year peak MW. Only High/Medium confidence breaker ratings are formally ranked."],
        ["Conservative headroom", "Uses the smaller of stored breaker kVA and independently calculated breaker kVA; retained for audit but not used as the formal rank when validation fails."],
        ["Upstream topology", "Recursively follows upstream_grid_element_id from the selected feeder breaker to the highest reachable upstream element."],
        ["Important limitation", "Breaker headroom is still a feeder-level screen, not final hosting capacity. Path-specific line, transformer, voltage, and power-flow constraints must be evaluated next."],
        ["Next phase", "Use Topology_Shortlist feeders for detailed electrical-path tracing, downstream load allocation, bottleneck identification, and incremental data-center load testing."],
    ], columns=["Item", "Explanation"])
    dataframes = {
        "Capacity_Audit": screen,
        "Topology_Shortlist": shortlist,
        "Selected_Breakers": audited_breakers,
        "Breaker_Candidates": breaker_candidates,
        "SCADA_Summary": scada_summary,
        "Breaker_Data_Sources": breaker_data_sources,
        "Upstream_Path_Detail": upstream_detail,
        "Upstream_Path_Summary": upstream_summary,
        "Methodology": methodology,
    }
    dataframes = {name: excel_safe_df(df) for name, df in dataframes.items()}
    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter", datetime_format="yyyy-mm-dd hh:mm:ss", date_format="yyyy-mm-dd") as writer:
        for sheet_name, df in dataframes.items():
            df.to_excel(writer, sheet_name=sheet_name, index=False)
        workbook = writer.book
        header_format = workbook.add_format({"bold": True, "font_color": "white", "bg_color": "#1F4E78", "border": 1, "align": "center", "valign": "vcenter"})
        note_format = workbook.add_format({"text_wrap": True, "valign": "top"})
        mw_format = workbook.add_format({"num_format": "0.000"})
        pct_format = workbook.add_format({"num_format": "0.0"})
        rating_format = workbook.add_format({"num_format": "0.00"})
        integer_format = workbook.add_format({"num_format": "0"})
        datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm"})
        for sheet_name, df in dataframes.items():
            worksheet = writer.sheets[sheet_name]
            if len(df.columns) == 0:
                continue
            worksheet.freeze_panes(1, 0)
            worksheet.autofilter(0, 0, max(len(df), 1), len(df.columns) - 1)
            worksheet.set_row(0, 28)
            for col_idx, col in enumerate(df.columns):
                worksheet.write(0, col_idx, col, header_format)
                sample_values = df[col].head(200) if len(df) else pd.Series(dtype=object)
                sample_lengths = [len(str(value)) for value in sample_values.tolist()]
                max_len = max([len(str(col))] + sample_lengths) + 2
                worksheet.set_column(col_idx, col_idx, min(max(max_len, 10), 38))
        if not screen.empty:
            ws = writer.sheets["Capacity_Audit"]
            col_map = {name: idx for idx, name in enumerate(screen.columns)}
            last_row = max(len(screen), 1)
            for name in ["AuditedBreakerCapacityMW", "ConservativeBreakerCapacityMW", "AverageLoadMW", "P95LoadMW", "PeakLoadMW", "ValidatedHeadroomMW", "ConservativeHeadroomMW"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 18, mw_format)
            for name in ["RatingDifferencePct", "AverageUtilizationPct_Audited", "P95UtilizationPct_Audited", "PeakUtilizationPct_Audited", "PeakUtilizationPct_Conservative"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 18, pct_format)
            for name in ["StoredBreakerRatingKVA", "BreakerRatingA", "BreakerVoltageRaw", "VoltageKV", "CalculatedBreakerKVA"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 18, rating_format)
            for name in ["CapacityRank", "Observations", "UpstreamPathDepth", "UpstreamPathElementCount"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 14, integer_format)
            for name in ["PeakLoadTimestamp", "FirstTimestamp", "LastTimestamp", "GridLastUpdated"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 20, datetime_format)
            for name in ["RecommendedNextStep", "CapacityBasis", "AuditFlag", "RatingValidationStatus"]:
                if name in col_map:
                    ws.set_column(col_map[name], col_map[name], 48, note_format)
            if "CapacityConfidence" in col_map:
                c = col_map["CapacityConfidence"]
                ws.conditional_format(1, c, last_row, c, {"type": "text", "criteria": "containing", "value": "High", "format": workbook.add_format({"bg_color": "#C6EFCE", "font_color": "#006100"})})
                ws.conditional_format(1, c, last_row, c, {"type": "text", "criteria": "containing", "value": "Medium", "format": workbook.add_format({"bg_color": "#FFEB9C", "font_color": "#9C6500"})})
                ws.conditional_format(1, c, last_row, c, {"type": "text", "criteria": "containing", "value": "Low", "format": workbook.add_format({"bg_color": "#FFC7CE", "font_color": "#9C0006"})})
            if "ValidatedHeadroomMW" in col_map:
                c = col_map["ValidatedHeadroomMW"]
                ws.conditional_format(1, c, last_row, c, {"type": "data_bar", "bar_color": "#5B9BD5"})
            if "PeakUtilizationPct_Audited" in col_map:
                c = col_map["PeakUtilizationPct_Audited"]
                ws.conditional_format(1, c, last_row, c, {"type": "3_color_scale", "min_color": "#63BE7B", "mid_color": "#FFEB84", "max_color": "#F8696B"})
            if "RatingDifferencePct" in col_map:
                c = col_map["RatingDifferencePct"]
                ws.conditional_format(1, c, last_row, c, {"type": "3_color_scale", "min_color": "#63BE7B", "mid_color": "#FFEB84", "max_color": "#F8696B"})
            if "FeederID" in col_map and "ValidatedHeadroomMW" in col_map and screen["ValidatedHeadroomMW"].notna().any():
                chart_rows = screen[screen["RankEligible"] == True].head(SHORTLIST_N)
                if not chart_rows.empty:
                    last_excel_row = len(chart_rows)
                    chart = workbook.add_chart({"type": "bar"})
                    chart.add_series({"name": "Validated Headroom MW", "categories": ["Capacity_Audit", 1, col_map["FeederID"], last_excel_row, col_map["FeederID"]], "values": ["Capacity_Audit", 1, col_map["ValidatedHeadroomMW"], last_excel_row, col_map["ValidatedHeadroomMW"]]})
                    chart.set_title({"name": "Top Audited Greensboro Feeder Headroom"})
                    chart.set_x_axis({"name": "Validated Headroom MW"})
                    chart.set_y_axis({"name": "Feeder"})
                    chart.set_legend({"none": True})
                    chart.set_size({"width": 720, "height": 420})
                    ws.insert_chart(1, len(screen.columns) + 2, chart)
        methodology_ws = writer.sheets["Methodology"]
        methodology_ws.set_column(0, 0, 30)
        methodology_ws.set_column(1, 1, 110, note_format)
    print(f"\nExcel workbook created: {OUTPUT_FILE.resolve()}")


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    print("\nGREENSBORO BREAKER / SOURCE CAPACITY AUDIT")
    print("Purpose: validate feeder-breaker capacity against breaker amp/voltage metadata and measured SCADA load.")
    print("This is the next step after broad feeder screening and before detailed line/transformer bottleneck modeling.\n")
    conn = None
    try:
        conn = connect_edm()
        grids = get_gso_grids(conn)
        feeders = grids["grid_id"].tolist()
        breakers = get_breaker_inventory(conn, feeders)
        breaker_data_sources = get_breaker_data_sources(conn, feeders)
        selected_breakers, breaker_candidates = select_feeder_breakers(breakers, breaker_data_sources, feeders)
        audited_breakers = validate_breaker_ratings(selected_breakers)
        scada_summary = get_scada_summary(conn, audited_breakers, breaker_data_sources)
        upstream_detail, upstream_summary = build_upstream_paths(conn, audited_breakers)
        screen = build_capacity_screen(grids, audited_breakers, scada_summary, upstream_summary)
        shortlist = build_shortlist(screen)
        write_excel(screen, shortlist, breaker_candidates, audited_breakers, scada_summary, breaker_data_sources, upstream_detail, upstream_summary)
        print("\nAnalysis complete.")
        print("\nReview these sheets first:")
        print("  1. Capacity_Audit")
        print("  2. Topology_Shortlist")
        print("  3. Selected_Breakers")
        print("  4. Upstream_Path_Summary")
        print("\nThe key question is whether stored breaker rating_kva agrees with the independent rating_a + voltage calculation.")
        print("High/Medium confidence feeders with complete upstream paths are the candidates to carry into detailed topology and hosting-capacity analysis.")
    finally:
        if conn is not None:
            conn.close()
            print("\nDatabase connection closed.")


if __name__ == "__main__":
    main()