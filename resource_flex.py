import os
import re
import json
from pathlib import Path
from collections import Counter

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv


# PURPOSE:
# Audits Greensboro EDM data for PV, EV charging, battery/storage, and demand-response/flexible-load evidence that could feed the final data-center MILP.
# WHY:
# The integrated MILP must not assume a resource has zero value simply because its input template is empty. This script determines what resource assets, metadata, and 2024 time-series data actually exist.
# DEPENDENCIES:
# Reads the current candidate context from greensboro_flag_resolution_audit.xlsx when available, otherwise greensboro_final_thermal_hosting_results.xlsx, and queries the EDM using .env credentials.
# OUTPUT:
# Writes greensboro_resource_flexibility_audit.xlsx and prints a detailed terminal handoff containing the exact resource evidence needed for the next modeling step.


# ======================================================================================
# SETTINGS
# ======================================================================================

YEAR = 2024

CANDIDATE_AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
CANDIDATE_AUDIT_SHEET = "MILP_Readiness"
THERMAL_RESULTS_FILE = "greensboro_final_thermal_hosting_results.xlsx"
THERMAL_RESULTS_SHEET = "Candidate_Summary"

OUTPUT_FILE = "greensboro_resource_flexibility_audit.xlsx"

MIN_PROFILE_SPAN_DAYS_FOR_YEAR_EVIDENCE = 300.0
MAX_TIMESERIES_SOURCES_PER_RESOURCE = 1000
PRINT_TOP_META_KEYS = 15
PRINT_TOP_TYPES = 25
PRINT_SAMPLE_ASSETS_PER_RESOURCE = 8

RESOURCE_ORDER = ["PV", "EV", "BATTERY", "DR_FLEX"]

POWER_METRIC_PRIORITY = ["kWh", "kW", "MW", "W", "Wh"]
RESOURCE_LABELS = {
    "PV": "PV / Solar",
    "EV": "EV Charging",
    "BATTERY": "Battery / Storage",
    "DR_FLEX": "Demand Response / Flexible Load"
}

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


def safe_query_dataframe(conn, query, params=None, savepoint_name="resource_audit"):
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


def safe_float(value):
    if value is None:
        return np.nan
    try:
        if pd.isna(value):
            return np.nan
    except Exception:
        pass
    try:
        return float(value)
    except Exception:
        return np.nan


def normalize_text(value):
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple, set)):
        try:
            return json.dumps(value, default=str)
        except Exception:
            return str(value)
    return str(value)


def parse_metrics(value):
    if value is None:
        return []
    if isinstance(value, (list, tuple, set, np.ndarray)):
        return [str(x) for x in value if x is not None]
    text = str(value).strip().strip("{}[]")
    if not text:
        return []
    return [item.strip().strip('"').strip("'") for item in text.split(",") if item.strip()]


def choose_power_metric(metrics):
    metrics = [str(metric) for metric in metrics]
    for preferred in POWER_METRIC_PRIORITY:
        for metric in metrics:
            if metric.lower() == preferred.lower():
                return metric
    return None


def truthy(value):
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    try:
        if pd.isna(value):
            return False
    except Exception:
        pass
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


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
    decimal_format = workbook.add_format({"num_format": "0.000"})
    integer_format = workbook.add_format({"num_format": "0"})
    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)
        values = dataframe[column_name].tolist() if not dataframe.empty else []
        max_length = max((len(str(value)) for value in values if value is not None), default=0)
        width = min(max(max_length, len(str(column_name))) + 2, 50)
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
# LOAD CURRENT DATA-CENTER CANDIDATE CONTEXT
# ======================================================================================

def load_candidate_context():
    audit_path = Path(CANDIDATE_AUDIT_FILE)
    thermal_path = Path(THERMAL_RESULTS_FILE)

    if audit_path.exists():
        df = pd.read_excel(audit_path, sheet_name=CANDIDATE_AUDIT_SHEET)
        required = {"GridID", "SubstationID"}
        if required.issubset(df.columns):
            if "ReadyForFeederBoundMILP" in df.columns:
                ready = df["ReadyForFeederBoundMILP"].map(truthy)
                if ready.any():
                    df = df[ready].copy()
            keep = [column for column in ["CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ReadyForFeederBoundMILP"] if column in df.columns]
            df = df[keep].copy()
            df["CandidateContextSource"] = f"{CANDIDATE_AUDIT_FILE}:{CANDIDATE_AUDIT_SHEET}"
            return df.drop_duplicates().reset_index(drop=True)

    if thermal_path.exists():
        df = pd.read_excel(thermal_path, sheet_name=THERMAL_RESULTS_SHEET)
        required = {"GridID", "SubstationID"}
        if required.issubset(df.columns):
            keep = [column for column in ["CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ModelStatus"] if column in df.columns]
            df = df[keep].copy()
            df["CandidateContextSource"] = f"{THERMAL_RESULTS_FILE}:{THERMAL_RESULTS_SHEET}"
            return df.drop_duplicates().reset_index(drop=True)

    raise FileNotFoundError(
        f"Could not load candidate context. Expected {CANDIDATE_AUDIT_FILE}/{CANDIDATE_AUDIT_SHEET} "
        f"or {THERMAL_RESULTS_FILE}/{THERMAL_RESULTS_SHEET}."
    )


# ======================================================================================
# BREAKER -> SUBSTATION MAPPING AND RELEVANT FEEDER FOOTPRINT
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
        ORDER BY b.meta ->> 'enclosure_id', b.grid_id;
    """)


def build_relevant_feeder_map(candidate_context, breaker_map):
    candidate_substations = sorted(candidate_context["SubstationID"].dropna().astype(str).unique().tolist())
    relevant = breaker_map[breaker_map["SubstationID"].astype(str).isin(candidate_substations)].copy()
    candidate_feeders = set(candidate_context["GridID"].dropna().astype(str))
    relevant["IsCandidateFeeder"] = relevant["GridID"].astype(str).isin(candidate_feeders)
    return relevant.sort_values(["SubstationID", "GridID"]).reset_index(drop=True)


# ======================================================================================
# TYPE INVENTORY AND RESOURCE CLASSIFICATION
# ======================================================================================

def get_type_inventory(conn, feeders):
    return query_dataframe(conn, """
        SELECT grid_id AS "GridID",
               COALESCE(type, '') AS "ElementType",
               COALESCE(customer_type, '') AS "CustomerType",
               COUNT(*) AS "ElementCount"
        FROM grid_element
        WHERE grid_id = ANY(%s)
        GROUP BY grid_id, COALESCE(type, ''), COALESCE(customer_type, '')
        ORDER BY grid_id, "ElementCount" DESC, "ElementType", "CustomerType";
    """, (feeders,))


def get_all_elements(conn, feeders):
    return query_dataframe(conn, """
        SELECT grid_id AS "GridID",
               grid_element_id AS "ElementID",
               type AS "ElementType",
               customer_type AS "CustomerType",
               is_producer AS "IsProducer",
               is_consumer AS "IsConsumer",
               is_switchable AS "IsSwitchable",
               switch_is_open AS "SwitchIsOpen",
               upstream_grid_element_id AS "UpstreamElementID",
               terminal1_cn AS "Terminal1CN",
               terminal2_cn AS "Terminal2CN",
               meta AS "Meta"
        FROM grid_element
        WHERE grid_id = ANY(%s)
        ORDER BY grid_id, grid_element_id;
    """, (feeders,))


def classify_resource(row):
    type_text = normalize_text(row.get("ElementType")).lower()
    customer_text = normalize_text(row.get("CustomerType")).lower()
    meta = row.get("Meta")
    meta_text = normalize_text(meta).lower()
    primary_text = f"{type_text} {customer_text}"

    if re.search(r"photovoltaic|solar|\bpv\b", primary_text):
        return "PV"
    if re.search(r"electric.?vehicle|\bev\b|ev.?charger|vehicle.?charger|charging.?station", primary_text):
        return "EV"
    if re.search(r"battery|\bbess\b|energy.?storage|powerwall|\bstorage\b", primary_text):
        return "BATTERY"
    if re.search(r"demand.?response|curtail|dispatchable.?load|controllable.?load|flexible.?load|load.?control", primary_text):
        return "DR_FLEX"

    # Metadata-only matches are retained as lower-confidence evidence instead of silently ignored.
    if re.search(r"photovoltaic|solar|generation_capacity|dc_size|ac_size", meta_text):
        return "PV"
    if re.search(r"electric.?vehicle|ev.?charger|charger.?kw|charging.?kw", meta_text):
        return "EV"
    if re.search(r"battery|\bbess\b|energy.?storage|powerwall|state.?of.?charge|\bsoc\b", meta_text):
        return "BATTERY"
    if re.search(r"demand.?response|curtail|dispatchable.?load|controllable.?load|flexible.?load|load.?control", meta_text):
        return "DR_FLEX"

    return None


def classify_match_basis(row):
    resource = row.get("ResourceType")
    type_text = normalize_text(row.get("ElementType")).lower()
    customer_text = normalize_text(row.get("CustomerType")).lower()
    meta_text = normalize_text(row.get("Meta")).lower()
    primary_text = f"{type_text} {customer_text}"

    patterns = {
        "PV": r"photovoltaic|solar|\bpv\b",
        "EV": r"electric.?vehicle|\bev\b|ev.?charger|vehicle.?charger|charging.?station",
        "BATTERY": r"battery|\bbess\b|energy.?storage|powerwall|\bstorage\b",
        "DR_FLEX": r"demand.?response|curtail|dispatchable.?load|controllable.?load|flexible.?load|load.?control"
    }
    if resource in patterns and re.search(patterns[resource], primary_text):
        return "TYPE_OR_CUSTOMER_TYPE"
    if resource and resource != "DR_FLEX":
        return "METADATA"
    if resource == "DR_FLEX" and re.search(patterns["DR_FLEX"], meta_text):
        return "METADATA"
    return "UNKNOWN"


# ======================================================================================
# FLATTEN RESOURCE METADATA
# ======================================================================================

def flatten_metadata(resource_assets):
    rows = []
    for _, row in resource_assets.iterrows():
        meta = row.get("Meta")
        if not isinstance(meta, dict):
            continue
        for key, value in meta.items():
            rows.append({
                "ResourceType": row["ResourceType"],
                "GridID": row["GridID"],
                "SubstationID": row.get("SubstationID"),
                "ElementID": row["ElementID"],
                "MetaKey": str(key),
                "MetaValue": normalize_text(value)
            })
    return pd.DataFrame(rows)


def summarize_metadata(meta_long):
    if meta_long.empty:
        return pd.DataFrame(columns=["ResourceType", "MetaKey", "Occurrences", "DistinctValues", "ExampleValues", "CapacityOrFlexRelevant"])
    grouped = []
    for (resource_type, key), group in meta_long.groupby(["ResourceType", "MetaKey"]):
        values = [str(value) for value in group["MetaValue"].dropna().astype(str).unique().tolist() if str(value).strip()][:8]
        grouped.append({
            "ResourceType": resource_type,
            "MetaKey": key,
            "Occurrences": len(group),
            "DistinctValues": group["MetaValue"].nunique(dropna=True),
            "ExampleValues": " | ".join(values),
            "CapacityOrFlexRelevant": bool(re.search(r"rating|rated|capacity|kw|mw|kwh|mwh|size|power|energy|charge|soc|flex|curtail|control|dispatch", str(key), flags=re.I))
        })
    return pd.DataFrame(grouped).sort_values(["ResourceType", "CapacityOrFlexRelevant", "Occurrences"], ascending=[True, False, False]).reset_index(drop=True)


# ======================================================================================
# DATA-SOURCE INVENTORY
# ======================================================================================

def get_data_source_inventory(conn, feeders):
    return query_dataframe(conn, """
        SELECT ds.grid_id AS "GridID",
               ds.grid_element_id AS "ElementID",
               ds.grid_element_data_source_id::text AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.metrics AS "Metrics",
               ds.friendly_id AS "FriendlyID",
               ds.phases AS "Phase",
               ds.direction AS "Direction",
               ds.valid AS "ValidRange"
        FROM grid_element_data_source ds
        WHERE ds.grid_id = ANY(%s)
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """, (feeders,))


def attach_resource_data_sources(data_sources, resource_assets):
    if data_sources.empty or resource_assets.empty:
        return pd.DataFrame()
    keys = resource_assets[["GridID", "ElementID", "ResourceType", "SubstationID", "IsCandidateFeeder", "ResourceMatchBasis"]].drop_duplicates()
    merged = data_sources.merge(keys, on=["GridID", "ElementID"], how="inner")
    merged["MetricList"] = merged["Metrics"].map(parse_metrics)
    merged["ChosenPowerMetric"] = merged["MetricList"].map(choose_power_metric)
    merged["HasPowerOrEnergyMetric"] = merged["ChosenPowerMetric"].notna()
    return merged


# ======================================================================================
# 2024 TIME-SERIES AVAILABILITY AUDIT
# ======================================================================================

def summarize_one_data_source(conn, data_source_id, metric):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"
    query = """
        SELECT COUNT(*)::bigint AS "ObservationCount",
               MIN(td.timestamp) AS "FirstTimestampUTC",
               MAX(td.timestamp) AS "LastTimestampUTC",
               MIN(td.value) AS "MinimumValue",
               MAX(td.value) AS "MaximumValue",
               AVG(td.value) AS "AverageValue",
               SUM(td.value) AS "SumValue"
        FROM ts_data_source_select(
            %s::uuid,
            %s,
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        ) td;
    """
    result, error = safe_query_dataframe(conn, query, (data_source_id, metric, start, end), savepoint_name="resource_ts")
    if error or result.empty:
        return {
            "ObservationCount": 0,
            "FirstTimestampUTC": pd.NaT,
            "LastTimestampUTC": pd.NaT,
            "SpanDays": 0.0,
            "ApproxIntervalMinutes": np.nan,
            "MinimumValue": np.nan,
            "MaximumValue": np.nan,
            "AverageValue": np.nan,
            "SumValue": np.nan,
            "Has2024Data": False,
            "YearSpanEvidence": False,
            "TimeSeriesError": error or "No rows returned"
        }

    row = result.iloc[0]
    count = int(row["ObservationCount"] or 0)
    first = pd.to_datetime(row["FirstTimestampUTC"], utc=True, errors="coerce")
    last = pd.to_datetime(row["LastTimestampUTC"], utc=True, errors="coerce")
    span_days = (last - first).total_seconds() / 86400.0 if pd.notna(first) and pd.notna(last) and last >= first else 0.0
    approximate_interval = ((last - first).total_seconds() / 60.0 / max(count - 1, 1)) if count > 1 and pd.notna(first) and pd.notna(last) else np.nan

    return {
        "ObservationCount": count,
        "FirstTimestampUTC": first,
        "LastTimestampUTC": last,
        "SpanDays": span_days,
        "ApproxIntervalMinutes": approximate_interval,
        "MinimumValue": safe_float(row["MinimumValue"]),
        "MaximumValue": safe_float(row["MaximumValue"]),
        "AverageValue": safe_float(row["AverageValue"]),
        "SumValue": safe_float(row["SumValue"]),
        "Has2024Data": count > 0,
        "YearSpanEvidence": count > 0 and span_days >= MIN_PROFILE_SPAN_DAYS_FOR_YEAR_EVIDENCE,
        "TimeSeriesError": ""
    }


def audit_resource_timeseries(conn, resource_sources):
    if resource_sources.empty:
        return pd.DataFrame()

    rows = []
    for resource_type in RESOURCE_ORDER:
        subset = resource_sources[(resource_sources["ResourceType"] == resource_type) & resource_sources["HasPowerOrEnergyMetric"]].copy()
        if subset.empty:
            print(f"{RESOURCE_LABELS[resource_type]}: no data sources with kWh/kW/MW/W/Wh metrics to test.")
            continue

        if len(subset) > MAX_TIMESERIES_SOURCES_PER_RESOURCE:
            print(f"{RESOURCE_LABELS[resource_type]}: {len(subset):,} candidate data sources; auditing first {MAX_TIMESERIES_SOURCES_PER_RESOURCE:,} to avoid an unbounded query run.")
            subset = subset.head(MAX_TIMESERIES_SOURCES_PER_RESOURCE).copy()
        else:
            print(f"{RESOURCE_LABELS[resource_type]}: auditing {len(subset):,} candidate power/energy data sources.")

        for position, (_, source) in enumerate(subset.iterrows(), start=1):
            if position == 1 or position % 25 == 0 or position == len(subset):
                print(f"  {resource_type}: source {position:,}/{len(subset):,}")
            summary = summarize_one_data_source(conn, source["DataSourceID"], source["ChosenPowerMetric"])
            rows.append({
                "ResourceType": resource_type,
                "SubstationID": source.get("SubstationID"),
                "GridID": source["GridID"],
                "ElementID": source["ElementID"],
                "DataSourceID": source["DataSourceID"],
                "DataSourceType": source.get("DataSourceType"),
                "Provider": source.get("Provider"),
                "ChosenMetric": source["ChosenPowerMetric"],
                "AllMetrics": normalize_text(source["Metrics"]),
                "IsCandidateFeeder": truthy(source.get("IsCandidateFeeder")),
                **summary
            })

    return pd.DataFrame(rows)


# ======================================================================================
# SUMMARY TABLES
# ======================================================================================

def build_resource_summary(resource_assets, resource_sources, ts_audit):
    rows = []
    for resource_type in RESOURCE_ORDER:
        assets = resource_assets[resource_assets["ResourceType"] == resource_type] if not resource_assets.empty else pd.DataFrame()
        sources = resource_sources[resource_sources["ResourceType"] == resource_type] if not resource_sources.empty else pd.DataFrame()
        ts = ts_audit[ts_audit["ResourceType"] == resource_type] if not ts_audit.empty else pd.DataFrame()
        rows.append({
            "ResourceType": resource_type,
            "ResourceLabel": RESOURCE_LABELS[resource_type],
            "AssetCount": len(assets),
            "CandidateFeederAssetCount": int(assets["IsCandidateFeeder"].sum()) if not assets.empty else 0,
            "SubstationsWithAssets": assets["SubstationID"].nunique() if not assets.empty else 0,
            "DataSourceRows": len(sources),
            "AssetsWithDataSources": sources[["GridID", "ElementID"]].drop_duplicates().shape[0] if not sources.empty else 0,
            "PowerOrEnergyDataSourceRows": int(sources["HasPowerOrEnergyMetric"].sum()) if not sources.empty else 0,
            "TimeSeriesSourcesAudited": len(ts),
            "SourcesWith2024Data": int(ts["Has2024Data"].sum()) if not ts.empty else 0,
            "SourcesWithYearSpanEvidence": int(ts["YearSpanEvidence"].sum()) if not ts.empty else 0,
            "ResourceEvidenceStatus": "FOUND_WITH_2024_TS" if not ts.empty and ts["Has2024Data"].any() else ("FOUND_NO_USABLE_2024_TS" if len(assets) > 0 else "NOT_FOUND")
        })
    return pd.DataFrame(rows)


def build_substation_summary(resource_assets, resource_sources, ts_audit, candidate_context):
    candidate_substations = sorted(candidate_context["SubstationID"].dropna().astype(str).unique().tolist())
    rows = []
    for substation_id in candidate_substations:
        row = {"SubstationID": substation_id}
        for resource_type in RESOURCE_ORDER:
            assets = resource_assets[(resource_assets["SubstationID"].astype(str) == str(substation_id)) & (resource_assets["ResourceType"] == resource_type)] if not resource_assets.empty else pd.DataFrame()
            sources = resource_sources[(resource_sources["SubstationID"].astype(str) == str(substation_id)) & (resource_sources["ResourceType"] == resource_type)] if not resource_sources.empty else pd.DataFrame()
            ts = ts_audit[(ts_audit["SubstationID"].astype(str) == str(substation_id)) & (ts_audit["ResourceType"] == resource_type)] if not ts_audit.empty else pd.DataFrame()
            row[f"{resource_type}_Assets"] = len(assets)
            row[f"{resource_type}_PowerDataSources"] = int(sources["HasPowerOrEnergyMetric"].sum()) if not sources.empty else 0
            row[f"{resource_type}_SourcesWith2024Data"] = int(ts["Has2024Data"].sum()) if not ts.empty else 0
        rows.append(row)
    return pd.DataFrame(rows)


def build_metric_summary(resource_sources):
    if resource_sources.empty:
        return pd.DataFrame()
    rows = []
    for _, row in resource_sources.iterrows():
        metrics = parse_metrics(row["Metrics"])
        if not metrics:
            rows.append({
                "ResourceType": row["ResourceType"],
                "Metric": "(none)",
                "DataSourceID": row["DataSourceID"],
                "Provider": row.get("Provider"),
                "DataSourceType": row.get("DataSourceType")
            })
        else:
            for metric in metrics:
                rows.append({
                    "ResourceType": row["ResourceType"],
                    "Metric": metric,
                    "DataSourceID": row["DataSourceID"],
                    "Provider": row.get("Provider"),
                    "DataSourceType": row.get("DataSourceType")
                })
    long_df = pd.DataFrame(rows)
    return long_df.groupby(["ResourceType", "Metric"], as_index=False).agg(DataSourceRows=("DataSourceID", "count"), UniqueDataSources=("DataSourceID", "nunique")).sort_values(["ResourceType", "UniqueDataSources"], ascending=[True, False]).reset_index(drop=True)


def build_assumptions():
    return pd.DataFrame([
        {"Item": "Purpose", "Assumption": "Discovery/audit only. This script does not automatically credit PV, EV flexibility, batteries, or DR in the MILP."},
        {"Item": "Scope", "Assumption": "Scans every Greensboro feeder mapped to the candidate substations, not only the candidate feeder itself, because substation-level flexibility can exist on neighboring feeders."},
        {"Item": "PV interpretation", "Assumption": "Existing PV is evidence about available DER and historical production. Its historical effect is already embedded in breaker SCADA and must not be double-counted as new generation in the MILP."},
        {"Item": "EV interpretation", "Assumption": "Existing EV charging is historical load already embedded in SCADA. Only demonstrably shiftable charging can later be credited as flexibility."},
        {"Item": "Battery interpretation", "Assumption": "Existing storage is audited if present, but a proposed data-center battery is a design variable and should be parameterized separately rather than assumed from historical EDM assets."},
        {"Item": "DR interpretation", "Assumption": "The script searches for explicit demand-response/flexible-load evidence. Absence of evidence is reported as NOT_FOUND rather than silently interpreted as zero potential."},
        {"Item": "Time-series audit", "Assumption": "For each resource data source with kWh/kW/MW/W/Wh, the script checks 2024 observation count, time span, approximate interval, and basic values without automatically transforming the data into a MILP profile."},
        {"Item": "Metadata capacity", "Assumption": "Metadata is preserved raw. Fields without explicit units are not silently converted into MW/MWh."},
        {"Item": "Continuation", "Assumption": "The terminal handoff is intentionally verbose enough to paste back into ChatGPT so the next profile-generation/modeling step can be based on observed EDM evidence."}
    ])


# ======================================================================================
# TERMINAL REPORTING
# ======================================================================================

def print_type_matches(type_inventory):
    if type_inventory.empty:
        return
    pattern = r"photo|solar|\bpv\b|electric.?vehicle|\bev\b|charger|battery|bess|storage|demand.?response|curtail|flex|control"
    matching = type_inventory[
        type_inventory["ElementType"].astype(str).str.contains(pattern, case=False, regex=True, na=False) |
        type_inventory["CustomerType"].astype(str).str.contains(pattern, case=False, regex=True, na=False)
    ].copy()
    if matching.empty:
        print("No resource-like type/customer_type labels were found in the relevant feeder footprint.")
        return
    collapsed = matching.groupby(["ElementType", "CustomerType"], as_index=False)["ElementCount"].sum().sort_values("ElementCount", ascending=False).head(PRINT_TOP_TYPES)
    print(collapsed.to_string(index=False))


def print_resource_asset_samples(resource_assets):
    for resource_type in RESOURCE_ORDER:
        heading(f"{RESOURCE_LABELS[resource_type].upper()} ASSET EVIDENCE")
        subset = resource_assets[resource_assets["ResourceType"] == resource_type].copy()
        if subset.empty:
            print("No matching assets found.")
            continue
        print(f"Assets found: {len(subset):,}")
        print(f"Candidate-feeder assets: {int(subset['IsCandidateFeeder'].sum()):,}")
        print(f"Substations represented: {subset['SubstationID'].nunique():,}")
        display = [column for column in ["SubstationID", "GridID", "ElementID", "ElementType", "CustomerType", "IsProducer", "IsConsumer", "ResourceMatchBasis"] if column in subset.columns]
        print("\nSample assets:")
        print(subset[display].head(PRINT_SAMPLE_ASSETS_PER_RESOURCE).to_string(index=False))


def print_meta_summary(meta_summary):
    for resource_type in RESOURCE_ORDER:
        heading(f"{RESOURCE_LABELS[resource_type].upper()} METADATA KEYS")
        subset = meta_summary[meta_summary["ResourceType"] == resource_type].copy()
        if subset.empty:
            print("No metadata keys available.")
            continue
        relevant = subset[subset["CapacityOrFlexRelevant"]].head(PRINT_TOP_META_KEYS)
        if relevant.empty:
            relevant = subset.head(PRINT_TOP_META_KEYS)
        print(relevant[["MetaKey", "Occurrences", "DistinctValues", "ExampleValues", "CapacityOrFlexRelevant"]].to_string(index=False))


def print_timeseries_summary(resource_summary, metric_summary, ts_audit):
    heading("RESOURCE DATA-SOURCE / TIME-SERIES RESULTS")
    print(resource_summary.to_string(index=False))

    if not metric_summary.empty:
        print("\nMetrics discovered:")
        print(metric_summary.to_string(index=False))

    if not ts_audit.empty:
        print("\n2024 time-series evidence by resource:")
        grouped = ts_audit.groupby("ResourceType", as_index=False).agg(
            SourcesAudited=("DataSourceID", "count"),
            SourcesWithData=("Has2024Data", "sum"),
            SourcesWithYearSpan=("YearSpanEvidence", "sum"),
            MinApproxIntervalMinutes=("ApproxIntervalMinutes", "min"),
            MedianApproxIntervalMinutes=("ApproxIntervalMinutes", "median"),
            MaxApproxIntervalMinutes=("ApproxIntervalMinutes", "max")
        )
        print(grouped.to_string(index=False))


def print_handoff(candidate_context, relevant_feeder_map, resource_summary, substation_summary, meta_summary, metric_summary, ts_audit):
    heading("RESOURCE FLEXIBILITY AUDIT HANDOFF - COPY THIS SECTION BACK INTO CHATGPT")
    print(f"Candidate feeders: {candidate_context['GridID'].nunique()}")
    print(f"Candidate substations: {candidate_context['SubstationID'].nunique()}")
    print(f"All feeders behind candidate substations scanned: {relevant_feeder_map['GridID'].nunique()}")
    print()

    for resource_type in RESOURCE_ORDER:
        row = resource_summary[resource_summary["ResourceType"] == resource_type].iloc[0]
        print(
            f"{resource_type}: assets={int(row['AssetCount'])}, candidate_feeder_assets={int(row['CandidateFeederAssetCount'])}, "
            f"data_source_rows={int(row['DataSourceRows'])}, power_energy_sources={int(row['PowerOrEnergyDataSourceRows'])}, "
            f"sources_with_2024_data={int(row['SourcesWith2024Data'])}, year_span_sources={int(row['SourcesWithYearSpanEvidence'])}, "
            f"status={row['ResourceEvidenceStatus']}"
        )

    print("\nRESOURCE COUNTS BY CANDIDATE SUBSTATION:")
    print(substation_summary.to_string(index=False))

    print("\nTOP CAPACITY/FLEXIBILITY METADATA KEYS BY RESOURCE:")
    for resource_type in RESOURCE_ORDER:
        subset = meta_summary[(meta_summary["ResourceType"] == resource_type) & meta_summary["CapacityOrFlexRelevant"]].head(10)
        print(f"\n{resource_type}:")
        if subset.empty:
            print("  none")
        else:
            for _, row in subset.iterrows():
                print(f"  {row['MetaKey']}: occurrences={int(row['Occurrences'])}; examples={row['ExampleValues']}")

    print("\nDISCOVERED METRICS BY RESOURCE:")
    for resource_type in RESOURCE_ORDER:
        subset = metric_summary[metric_summary["ResourceType"] == resource_type] if not metric_summary.empty else pd.DataFrame()
        if subset.empty:
            print(f"{resource_type}: none")
        else:
            metric_text = ", ".join(f"{row['Metric']} ({int(row['UniqueDataSources'])} source{'s' if int(row['UniqueDataSources']) != 1 else ''})" for _, row in subset.iterrows())
            print(f"{resource_type}: {metric_text}")

    print("\nTIME-SERIES SOURCES WITH 2024 DATA:")
    if ts_audit.empty:
        print("none")
    else:
        ready = ts_audit[ts_audit["Has2024Data"]].copy()
        if ready.empty:
            print("none")
        else:
            display = [column for column in ["ResourceType", "SubstationID", "GridID", "ElementID", "DataSourceID", "ChosenMetric", "ObservationCount", "SpanDays", "ApproxIntervalMinutes"] if column in ready.columns]
            print(ready[display].sort_values(["ResourceType", "SubstationID", "GridID"]).to_string(index=False))

    print("\nINTERPRETATION RULES FOR THE NEXT STEP:")
    print("1. PV FOUND does not mean it can be added again to the MILP; existing PV is already embedded in historical SCADA. We next determine whether its profile is useful for estimating DER behavior or whether a separate new-PV design profile is needed.")
    print("2. EV FOUND does not create capacity by itself. We next need measured charging profiles and a defensible shift/rebound rule before creating AvailableEVReductionMW.")
    print("3. BATTERY NOT FOUND does not prevent battery modeling. A new data-center battery is a design variable; this audit only tells us whether existing storage evidence is present.")
    print("4. DR_FLEX NOT FOUND means the EDM did not provide explicit evidence under the searched asset/metadata labels. External program assumptions may still be needed.")
    print("5. Paste this entire handoff section back into ChatGPT. The next script can then convert the evidence into MILP-ready PV/EV/resource profiles without guessing.")
    print(f"\nAudit workbook: {Path(OUTPUT_FILE).resolve()}")


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading("GREENSBORO RESOURCE FLEXIBILITY AUDIT")
    print("This script searches the actual EDM before assigning zero value to PV, EV flexibility, battery/storage, or demand response.")
    print("It does not automatically credit any resource in the MILP; it first establishes what evidence exists.")

    candidate_context = load_candidate_context()
    print(f"\nCandidate context source: {candidate_context['CandidateContextSource'].iloc[0]}")
    print(f"Candidates loaded: {len(candidate_context):,}")
    print(f"Candidate feeders: {candidate_context['GridID'].nunique():,}")
    print(f"Candidate substations: {candidate_context['SubstationID'].nunique():,}")

    conn = connect_to_edm()

    try:
        heading("1. BUILDING CANDIDATE-SUBSTATION FEEDER FOOTPRINT")
        breaker_map = get_breaker_substation_map(conn)
        relevant_feeder_map = build_relevant_feeder_map(candidate_context, breaker_map)
        relevant_feeders = sorted(relevant_feeder_map["GridID"].astype(str).unique().tolist())

        print(f"All mapped Greensboro feeder breakers: {len(breaker_map):,}")
        print(f"Feeders behind the {candidate_context['SubstationID'].nunique():,} candidate substations: {len(relevant_feeders):,}")
        print("\nCandidate-substation feeder footprint:")
        print(relevant_feeder_map[["SubstationID", "GridID", "BreakerID", "IsCandidateFeeder"]].to_string(index=False))

        heading("2. INVENTORYING ELEMENT TYPES")
        type_inventory = get_type_inventory(conn, relevant_feeders)
        print("Resource-like type/customer_type labels found:")
        print_type_matches(type_inventory)

        heading("3. DISCOVERING RESOURCE ASSETS")
        all_elements = get_all_elements(conn, relevant_feeders)
        all_elements["ResourceType"] = all_elements.apply(classify_resource, axis=1)
        resource_assets = all_elements[all_elements["ResourceType"].notna()].copy()
        resource_assets = resource_assets.merge(relevant_feeder_map[["GridID", "SubstationID", "IsCandidateFeeder"]], on="GridID", how="left")
        resource_assets["ResourceMatchBasis"] = resource_assets.apply(classify_match_basis, axis=1)
        resource_assets = resource_assets.sort_values(["ResourceType", "SubstationID", "GridID", "ElementID"]).reset_index(drop=True)

        print_resource_asset_samples(resource_assets)

        heading("4. AUDITING RESOURCE METADATA")
        meta_long = flatten_metadata(resource_assets)
        meta_summary = summarize_metadata(meta_long)
        print_meta_summary(meta_summary)

        heading("5. INVENTORYING RESOURCE DATA SOURCES")
        all_data_sources = get_data_source_inventory(conn, relevant_feeders)
        resource_sources = attach_resource_data_sources(all_data_sources, resource_assets)
        metric_summary = build_metric_summary(resource_sources)

        for resource_type in RESOURCE_ORDER:
            subset = resource_sources[resource_sources["ResourceType"] == resource_type] if not resource_sources.empty else pd.DataFrame()
            power_subset = subset[subset["HasPowerOrEnergyMetric"]] if not subset.empty else pd.DataFrame()
            print(f"{RESOURCE_LABELS[resource_type]}: {len(subset):,} data-source rows, {len(power_subset):,} with kWh/kW/MW/W/Wh.")

        heading("6. CHECKING 2024 RESOURCE TIME-SERIES AVAILABILITY")
        ts_audit = audit_resource_timeseries(conn, resource_sources)

        resource_summary = build_resource_summary(resource_assets, resource_sources, ts_audit)
        substation_summary = build_substation_summary(resource_assets, resource_sources, ts_audit, candidate_context)
        print_timeseries_summary(resource_summary, metric_summary, ts_audit)

        heading("7. WRITING AUDIT WORKBOOK")
        workbook_frames = {
            "Resource_Summary": resource_summary,
            "Substation_Summary": substation_summary,
            "Candidate_Context": candidate_context,
            "Relevant_Feeder_Map": relevant_feeder_map,
            "Resource_Assets": resource_assets,
            "Resource_Meta_Long": meta_long,
            "Resource_Meta_Summary": meta_summary,
            "Resource_Data_Sources": resource_sources,
            "Metric_Summary": metric_summary,
            "TimeSeries_Audit": ts_audit,
            "Element_Type_Inventory": type_inventory,
            "Assumptions": build_assumptions()
        }

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe_frame = make_excel_safe(frame)
                safe_frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
                format_excel(writer, sheet_name[:31], safe_frame)

        print(f"Audit workbook saved to: {Path(OUTPUT_FILE).resolve()}")

        print_handoff(candidate_context, relevant_feeder_map, resource_summary, substation_summary, meta_summary, metric_summary, ts_audit)

    finally:
        conn.close()


if __name__ == "__main__":
    main()
