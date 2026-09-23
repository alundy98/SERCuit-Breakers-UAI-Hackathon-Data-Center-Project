import os
import math
import gzip
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

YEAR = 2024
INTERVAL_MINUTES = 5
MAX_INTERPOLATION_GAP_INTERVALS = 12
MIN_FEEDER_PROFILE_COVERAGE_PCT = 99.0
MIN_SUBSTATION_PROFILE_COVERAGE_PCT = 99.0

AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
AUDIT_SHEET = "MILP_Readiness"

CAPACITY_TEMPLATE_FILE = "greensboro_substation_capacity_inputs.xlsx"
CAPACITY_TEMPLATE_SHEET = "Substation_Capacity"

OUTPUT_FILE = "greensboro_substation_load_analysis.xlsx"
PROFILE_OUTPUT_FILE = "greensboro_substation_5min_profiles_2024.csv.gz"

load_dotenv()
DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")


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
    savepoint = "substation_query"
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


def safe_float(value):
    if value is None or pd.isna(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def get_full_year_index():
    start = pd.Timestamp(f"{YEAR}-01-01 00:00:00", tz="UTC")
    end = pd.Timestamp(f"{YEAR + 1}-01-01 00:00:00", tz="UTC")
    return pd.date_range(start=start, end=end, freq=f"{INTERVAL_MINUTES}min", inclusive="left")


def get_breaker_substation_map(conn):
    return query_dataframe(conn, """
        WITH substations AS (
            SELECT DISTINCT grid_element_id
            FROM grid_element
            WHERE LOWER(type) = 'substation'
        )
        SELECT DISTINCT
               b.grid_id AS "GridID",
               b.grid_element_id AS "BreakerID",
               b.meta->>'enclosure_id' AS "SubstationID",
               CASE WHEN s.grid_element_id IS NOT NULL THEN TRUE ELSE FALSE END AS "SubstationEntityFound"
        FROM grid_element b
        LEFT JOIN substations s
          ON s.grid_element_id = b.meta->>'enclosure_id'
        WHERE b.grid_id ~ '^GSO_[0-9]+$'
          AND LOWER(b.type) LIKE '%breaker%'
          AND b.meta ? 'enclosure_id'
          AND NULLIF(TRIM(b.meta->>'enclosure_id'), '') IS NOT NULL
        ORDER BY b.meta->>'enclosure_id', b.grid_id, b.grid_element_id;
    """)


def get_breaker_source_inventory(conn, breaker_map):
    if breaker_map.empty:
        return pd.DataFrame()

    grid_ids = breaker_map["GridID"].astype(str).unique().tolist()

    return query_dataframe(conn, """
        SELECT ds.grid_id AS "GridID",
               ds.grid_element_id AS "BreakerID",
               ds.grid_element_data_source_id::text AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.phases AS "Phase",
               ds.direction AS "Direction",
               ds.valid AS "ValidRange"
        FROM grid_element_data_source ds
        WHERE ds.grid_id = ANY(%s)
          AND 'kWh' = ANY(ds.metrics)
          AND EXISTS (
              SELECT 1
              FROM grid_element ge
              WHERE ge.grid_id = ds.grid_id
                AND ge.grid_element_id = ds.grid_element_id
                AND LOWER(ge.type) ~ 'breaker'
          )
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """, (grid_ids,))


def load_feeder_5min_profile(conn, grid_id, breaker_id, full_index):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    query = """
        SELECT ds.grid_element_data_source_id::text AS "DataSourceID",
               date_trunc('hour', td.timestamp)
                   + floor(EXTRACT(MINUTE FROM td.timestamp) / 5) * INTERVAL '5 minutes' AS "TimestampUTC",
               SUM(td.value) AS "IntervalKWh"
        FROM grid_element_data_source ds
        JOIN LATERAL ts_data_source_select(
            ds.grid_element_data_source_id,
            'kWh',
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        ) td ON TRUE
        WHERE ds.grid_id = %s
          AND ds.grid_element_id = %s
          AND 'kWh' = ANY(ds.metrics)
        GROUP BY ds.grid_element_data_source_id,
                 date_trunc('hour', td.timestamp)
                   + floor(EXTRACT(MINUTE FROM td.timestamp) / 5) * INTERVAL '5 minutes'
        ORDER BY "TimestampUTC", "DataSourceID";
    """

    raw, error = safe_query_dataframe(conn, query, (start, end, grid_id, breaker_id))

    if error:
        return pd.Series(index=full_index, dtype=float), {
            "GridID": grid_id,
            "BreakerID": breaker_id,
            "SourceCount": 0,
            "ExpectedIntervals": len(full_index),
            "RawCompleteIntervals": 0,
            "AcceptedIntervals": 0,
            "CoveragePct": 0.0,
            "PeakMW": np.nan,
            "P95MW": np.nan,
            "AverageMW": np.nan,
            "AnnualEnergyMWh": np.nan,
            "FirstAcceptedTimestampUTC": pd.NaT,
            "LastAcceptedTimestampUTC": pd.NaT,
            "ProfileStatus": "QUERY_ERROR",
            "Error": error
        }

    if raw.empty:
        return pd.Series(index=full_index, dtype=float), {
            "GridID": grid_id,
            "BreakerID": breaker_id,
            "SourceCount": 0,
            "ExpectedIntervals": len(full_index),
            "RawCompleteIntervals": 0,
            "AcceptedIntervals": 0,
            "CoveragePct": 0.0,
            "PeakMW": np.nan,
            "P95MW": np.nan,
            "AverageMW": np.nan,
            "AnnualEnergyMWh": np.nan,
            "FirstAcceptedTimestampUTC": pd.NaT,
            "LastAcceptedTimestampUTC": pd.NaT,
            "ProfileStatus": "NO_DATA",
            "Error": "No breaker kWh time-series rows returned"
        }

    raw["TimestampUTC"] = pd.to_datetime(raw["TimestampUTC"], utc=True, errors="coerce")
    raw["IntervalKWh"] = pd.to_numeric(raw["IntervalKWh"], errors="coerce")
    raw = raw.dropna(subset=["TimestampUTC", "IntervalKWh"])

    source_ids = sorted(raw["DataSourceID"].astype(str).unique())
    source_frame = raw.pivot_table(index="TimestampUTC", columns="DataSourceID", values="IntervalKWh", aggfunc="sum").reindex(full_index)

    raw_complete = int(source_frame.notna().all(axis=1).sum())
    interpolated = source_frame.interpolate(
        method="time",
        limit=MAX_INTERPOLATION_GAP_INTERVALS,
        limit_direction="both",
        limit_area="inside"
    )

    accepted_mask = interpolated.notna().all(axis=1)
    feeder_kwh = interpolated.sum(axis=1, min_count=len(source_ids)).where(accepted_mask)
    feeder_mw = feeder_kwh / 1000.0 / (INTERVAL_MINUTES / 60.0)

    accepted = feeder_mw.dropna()
    coverage_pct = len(accepted) / len(full_index) * 100.0 if len(full_index) else 0.0

    if len(source_ids) == 0:
        status = "NO_SOURCES"
    elif coverage_pct >= MIN_FEEDER_PROFILE_COVERAGE_PCT:
        status = "READY"
    else:
        status = "REVIEW_LOW_COVERAGE"

    diagnostics = {
        "GridID": grid_id,
        "BreakerID": breaker_id,
        "SourceCount": len(source_ids),
        "ExpectedIntervals": len(full_index),
        "RawCompleteIntervals": raw_complete,
        "AcceptedIntervals": len(accepted),
        "CoveragePct": coverage_pct,
        "PeakMW": float(accepted.max()) if not accepted.empty else np.nan,
        "P95MW": float(accepted.quantile(0.95)) if not accepted.empty else np.nan,
        "AverageMW": float(accepted.mean()) if not accepted.empty else np.nan,
        "AnnualEnergyMWh": float(feeder_kwh.dropna().sum() / 1000.0) if not feeder_kwh.dropna().empty else np.nan,
        "FirstAcceptedTimestampUTC": accepted.index.min() if not accepted.empty else pd.NaT,
        "LastAcceptedTimestampUTC": accepted.index.max() if not accepted.empty else pd.NaT,
        "ProfileStatus": status,
        "Error": ""
    }

    return feeder_mw, diagnostics


def load_candidate_context():
    path = Path(AUDIT_FILE)

    if not path.exists():
        return pd.DataFrame(columns=["GridID", "SubstationID", "FirmHostingMW", "ReadyForFeederBoundMILP"])

    df = pd.read_excel(path, sheet_name=AUDIT_SHEET)

    required = {"GridID", "SubstationID", "FirmHostingMW"}
    if not required.issubset(df.columns):
        return pd.DataFrame(columns=["GridID", "SubstationID", "FirmHostingMW", "ReadyForFeederBoundMILP"])

    df["GridID"] = df["GridID"].astype(str)
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["FirmHostingMW"] = pd.to_numeric(df["FirmHostingMW"], errors="coerce")

    if "ReadyForFeederBoundMILP" not in df.columns:
        df["ReadyForFeederBoundMILP"] = True

    return df


def existing_capacity_template():
    path = Path(CAPACITY_TEMPLATE_FILE)

    if not path.exists():
        return pd.DataFrame(columns=[
            "SubstationID",
            "TotalCapacityMW",
            "ExistingCoincidentPeakMW",
            "AvailableAdditionalCapacityMW",
            "CapacitySource",
            "Notes"
        ])

    try:
        df = pd.read_excel(path, sheet_name=CAPACITY_TEMPLATE_SHEET)
    except Exception:
        return pd.DataFrame(columns=[
            "SubstationID",
            "TotalCapacityMW",
            "ExistingCoincidentPeakMW",
            "AvailableAdditionalCapacityMW",
            "CapacitySource",
            "Notes"
        ])

    if "SubstationID" not in df.columns:
        return pd.DataFrame(columns=[
            "SubstationID",
            "TotalCapacityMW",
            "ExistingCoincidentPeakMW",
            "AvailableAdditionalCapacityMW",
            "CapacitySource",
            "Notes"
        ])

    df["SubstationID"] = df["SubstationID"].astype(str)
    return df.drop_duplicates(subset=["SubstationID"], keep="last")


def build_capacity_input(substation_summary):
    existing = existing_capacity_template()
    existing_lookup = existing.set_index("SubstationID").to_dict("index") if not existing.empty else {}
    rows = []

    for _, summary in substation_summary.iterrows():
        substation_id = str(summary["SubstationID"])
        old = existing_lookup.get(substation_id, {})

        total_capacity = safe_float(old.get("TotalCapacityMW"))
        direct_available = safe_float(old.get("AvailableAdditionalCapacityMW"))
        capacity_source = old.get("CapacitySource", "Review")
        notes = old.get("Notes", "")

        profile_ready = str(summary["SubstationProfileStatus"]).startswith("READY")
        existing_peak = safe_float(summary["CoincidentPeakMW"]) if profile_ready else np.nan

        headroom_80 = max(0.80 * total_capacity - existing_peak, 0.0) if pd.notna(total_capacity) and pd.notna(existing_peak) else np.nan
        headroom_90 = max(0.90 * total_capacity - existing_peak, 0.0) if pd.notna(total_capacity) and pd.notna(existing_peak) else np.nan
        headroom_100 = max(total_capacity - existing_peak, 0.0) if pd.notna(total_capacity) and pd.notna(existing_peak) else np.nan

        rows.append({
            "SubstationID": substation_id,
            "TotalCapacityMW": total_capacity,
            "ExistingCoincidentPeakMW": existing_peak,
            "AvailableAdditionalCapacityMW": direct_available,
            "HeadroomAt80PctMW": headroom_80,
            "HeadroomAt90PctMW": headroom_90,
            "HeadroomAt100PctMW": headroom_100,
            "CapacitySource": capacity_source,
            "SubstationProfileStatus": summary["SubstationProfileStatus"],
            "ProfileCoveragePct": summary["ProfileCoveragePct"],
            "ExpectedFeederCount": summary["ExpectedFeederCount"],
            "LoadedFeederCount": summary["LoadedFeederCount"],
            "Notes": notes
        })

    return pd.DataFrame(rows).sort_values("SubstationID").reset_index(drop=True)


def write_capacity_template(capacity_input):
    with pd.ExcelWriter(CAPACITY_TEMPLATE_FILE, engine="xlsxwriter") as writer:
        capacity_input.to_excel(writer, sheet_name=CAPACITY_TEMPLATE_SHEET, index=False)

        workbook = writer.book
        worksheet = writer.sheets[CAPACITY_TEMPLATE_SHEET]
        header_format = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})

        for column_index, column_name in enumerate(capacity_input.columns):
            worksheet.write(0, column_index, column_name, header_format)
            width = min(max(len(column_name) + 2, 16), 45)

            if not capacity_input.empty:
                width = min(
                    max(width, max((len(str(value)) for value in capacity_input[column_name].tolist() if not pd.isna(value)), default=0) + 2),
                    45
                )

            worksheet.set_column(column_index, column_index, width)

        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, len(capacity_input), len(capacity_input.columns) - 1)


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


def assumptions_table():
    return pd.DataFrame([
        {"Item": "Substation relationship", "Assumption": "CircuitBreaker.meta['enclosure_id'] identifies the modeled substation shared by Greensboro feeder breakers."},
        {"Item": "Population", "Assumption": "All mapped GSO feeder breakers are included in each substation load profile, not only current data-center candidate feeders."},
        {"Item": "Time resolution", "Assumption": "Breaker kWh is aggregated to five-minute intervals and converted to average interval MW."},
        {"Item": "Phase/source handling", "Assumption": "All breaker kWh sources are aligned. A feeder interval is accepted only when every breaker kWh source is present after limited internal-gap interpolation."},
        {"Item": "Interpolation", "Assumption": f"Only internal gaps up to {MAX_INTERPOLATION_GAP_INTERVALS} five-minute intervals are interpolated. Boundary gaps and longer gaps remain missing."},
        {"Item": "Missing feeder data", "Assumption": "Missing feeder load is never treated as zero. A substation interval is complete only when every expected mapped feeder has a usable value."},
        {"Item": "Coincident peak", "Assumption": "ExistingCoincidentPeakMW is the maximum timestamp-aligned sum of all mapped feeder breaker MW for the substation, not the sum of independent feeder annual peaks."},
        {"Item": "Coincidence factor", "Assumption": "CoincidenceFactor = coincident substation peak / sum of individual feeder peaks."},
        {"Item": "Diversity factor", "Assumption": "DiversityFactor = sum of individual feeder peaks / coincident substation peak."},
        {"Item": "Load factor", "Assumption": "LoadFactor = average complete-interval substation MW / coincident peak MW."},
        {"Item": "MILP admission", "Assumption": f"ExistingCoincidentPeakMW is populated into the MILP capacity input only when every mapped feeder is loaded and complete substation profile coverage is at least {MIN_SUBSTATION_PROFILE_COVERAGE_PCT:.1f}%."},
        {"Item": "Capacity", "Assumption": "The script does not infer or sum breaker ratings to create substation capacity. TotalCapacityMW remains an explicit external input."},
        {"Item": "Planning views", "Assumption": "When TotalCapacityMW is supplied, the script reports headroom at 80%, 90%, and 100% of stated capacity. The base MILP uses its configured capacity rule separately."},
        {"Item": "Direction", "Assumption": "Substation load is the signed sum of feeder real-power estimates. CoincidentPeakMW uses the maximum positive net load because the modeled data-center load is an added positive load."},
        {"Item": "Interpretation", "Assumption": "These are modeled existing substation load profiles. They do not establish a substation thermal capacity without an explicit capacity value."}
    ])


def main():
    heading("GREENSBORO SUBSTATION FIVE-MINUTE LOAD PROFILE MODEL")

    full_index = get_full_year_index()
    candidate_context = load_candidate_context()

    conn = connect_to_edm()

    feeder_quality_rows = []
    substation_summary_rows = []
    coincidence_rows = []
    error_rows = []
    feeder_map_output = pd.DataFrame()

    try:
        breaker_map = get_breaker_substation_map(conn)

        if breaker_map.empty:
            raise RuntimeError("No Greensboro breaker-to-substation mappings were found.")

        duplicate_breakers = breaker_map.duplicated(subset=["GridID"], keep=False)
        if duplicate_breakers.any():
            duplicated = breaker_map.loc[duplicate_breakers, ["GridID", "BreakerID", "SubstationID"]]
            raise RuntimeError("More than one mapped breaker was returned for at least one GSO feeder:\n" + duplicated.to_string(index=False))

        source_inventory = get_breaker_source_inventory(conn, breaker_map)
        source_counts = source_inventory.groupby(["GridID", "BreakerID"]).size().rename("BreakerKWhSourceCount").reset_index() if not source_inventory.empty else pd.DataFrame(columns=["GridID", "BreakerID", "BreakerKWhSourceCount"])

        feeder_map_output = breaker_map.merge(source_counts, on=["GridID", "BreakerID"], how="left")
        feeder_map_output["BreakerKWhSourceCount"] = feeder_map_output["BreakerKWhSourceCount"].fillna(0).astype(int)

        if not candidate_context.empty:
            candidate_feeders = candidate_context[["GridID", "SubstationID", "FirmHostingMW"]].copy()
            candidate_feeders["IsCurrentMILPCandidate"] = True
            feeder_map_output = feeder_map_output.merge(candidate_feeders[["GridID", "FirmHostingMW", "IsCurrentMILPCandidate"]], on="GridID", how="left")
            feeder_map_output["IsCurrentMILPCandidate"] = feeder_map_output["IsCurrentMILPCandidate"].fillna(False)
        else:
            feeder_map_output["FirmHostingMW"] = np.nan
            feeder_map_output["IsCurrentMILPCandidate"] = False

        print(f"Mapped Greensboro feeders: {len(feeder_map_output):,}")
        print(f"Unique mapped substations: {feeder_map_output['SubstationID'].nunique():,}")
        print(f"Current MILP candidate feeders represented: {int(feeder_map_output['IsCurrentMILPCandidate'].sum()):,}")

        with gzip.open(PROFILE_OUTPUT_FILE, "wt", newline="", encoding="utf-8") as profile_file:
            profile_header_written = False

            for substation_number, (substation_id, group) in enumerate(feeder_map_output.groupby("SubstationID", sort=True), start=1):
                heading(f"[{substation_number}/{feeder_map_output['SubstationID'].nunique()}] {substation_id}")

                group = group.sort_values("GridID").reset_index(drop=True)
                expected_feeders = len(group)
                feeder_series = {}
                individual_peaks = {}
                loaded_feeders = 0

                print(f"Expected mapped feeders: {expected_feeders}")
                print("Feeders: " + ", ".join(group["GridID"].astype(str).tolist()))

                for feeder_number, feeder_row in group.iterrows():
                    grid_id = str(feeder_row["GridID"])
                    breaker_id = str(feeder_row["BreakerID"])

                    print(f"  Loading {grid_id} ({feeder_number + 1}/{expected_feeders})...", end=" ")

                    profile, diagnostics = load_feeder_5min_profile(conn, grid_id, breaker_id, full_index)
                    diagnostics["SubstationID"] = substation_id
                    diagnostics["SubstationEntityFound"] = feeder_row["SubstationEntityFound"]
                    diagnostics["IsCurrentMILPCandidate"] = bool(feeder_row["IsCurrentMILPCandidate"])
                    feeder_quality_rows.append(diagnostics)

                    if diagnostics["ProfileStatus"] == "READY":
                        loaded_feeders += 1
                        feeder_series[grid_id] = profile
                        individual_peaks[grid_id] = diagnostics["PeakMW"]
                        print(f"READY | coverage {diagnostics['CoveragePct']:.3f}% | peak {diagnostics['PeakMW']:.3f} MW")
                    else:
                        feeder_series[grid_id] = profile
                        individual_peaks[grid_id] = diagnostics["PeakMW"]
                        print(f"{diagnostics['ProfileStatus']} | coverage {diagnostics['CoveragePct']:.3f}%")
                        if diagnostics["Error"]:
                            error_rows.append({
                                "SubstationID": substation_id,
                                "GridID": grid_id,
                                "BreakerID": breaker_id,
                                "Stage": "FeederProfile",
                                "Error": diagnostics["Error"]
                            })

                feeder_frame = pd.DataFrame(feeder_series, index=full_index)
                complete_mask = feeder_frame.notna().all(axis=1) if not feeder_frame.empty else pd.Series(False, index=full_index)
                complete_feeder_count = feeder_frame.notna().sum(axis=1) if not feeder_frame.empty else pd.Series(0, index=full_index)
                substation_mw = feeder_frame.sum(axis=1, min_count=expected_feeders).where(complete_mask)
                accepted = substation_mw.dropna()

                profile_coverage_pct = len(accepted) / len(full_index) * 100.0 if len(full_index) else 0.0
                all_feeders_loaded = loaded_feeders == expected_feeders
                profile_ready = all_feeders_loaded and profile_coverage_pct >= MIN_SUBSTATION_PROFILE_COVERAGE_PCT

                if profile_ready:
                    profile_status = "READY"
                elif not all_feeders_loaded:
                    profile_status = "REVIEW_MISSING_OR_LOW_QUALITY_FEEDER"
                else:
                    profile_status = "REVIEW_LOW_SUBSTATION_COVERAGE"

                if not accepted.empty:
                    peak_timestamp = accepted.idxmax()
                    coincident_peak = float(accepted.max())
                    average_mw = float(accepted.mean())
                    p95_mw = float(accepted.quantile(0.95))
                    p99_mw = float(accepted.quantile(0.99))
                    minimum_net_mw = float(accepted.min())
                    peak_absolute_mw = float(accepted.abs().max())
                    annual_energy_mwh = float((accepted * (INTERVAL_MINUTES / 60.0)).sum())
                    load_factor = average_mw / coincident_peak if coincident_peak > 0 else np.nan
                else:
                    peak_timestamp = pd.NaT
                    coincident_peak = np.nan
                    average_mw = np.nan
                    p95_mw = np.nan
                    p99_mw = np.nan
                    minimum_net_mw = np.nan
                    peak_absolute_mw = np.nan
                    annual_energy_mwh = np.nan
                    load_factor = np.nan

                valid_individual_peaks = [value for value in individual_peaks.values() if pd.notna(value)]
                sum_individual_peaks = float(np.sum(valid_individual_peaks)) if valid_individual_peaks else np.nan
                coincidence_factor = coincident_peak / sum_individual_peaks if pd.notna(coincident_peak) and pd.notna(sum_individual_peaks) and sum_individual_peaks > 0 else np.nan
                diversity_factor = sum_individual_peaks / coincident_peak if pd.notna(coincident_peak) and coincident_peak > 0 and pd.notna(sum_individual_peaks) else np.nan

                candidate_group = candidate_context[candidate_context["SubstationID"].astype(str) == str(substation_id)] if not candidate_context.empty else pd.DataFrame()
                candidate_count = len(candidate_group)
                candidate_firm_sum = float(candidate_group["FirmHostingMW"].sum()) if candidate_count else 0.0

                substation_summary_rows.append({
                    "SubstationID": substation_id,
                    "ExpectedFeederCount": expected_feeders,
                    "LoadedFeederCount": loaded_feeders,
                    "AllExpectedFeedersLoaded": all_feeders_loaded,
                    "CompleteIntervals": len(accepted),
                    "ExpectedIntervals": len(full_index),
                    "ProfileCoveragePct": profile_coverage_pct,
                    "SubstationProfileStatus": profile_status,
                    "AverageMW": average_mw,
                    "P95MW": p95_mw,
                    "P99MW": p99_mw,
                    "CoincidentPeakMW": coincident_peak,
                    "PeakTimestampUTC": peak_timestamp,
                    "MinimumNetMW": minimum_net_mw,
                    "PeakAbsoluteMW": peak_absolute_mw,
                    "AnnualEnergyMWh": annual_energy_mwh,
                    "LoadFactor": load_factor,
                    "SumIndividualFeederPeaksMW": sum_individual_peaks,
                    "CoincidenceFactor": coincidence_factor,
                    "DiversityFactor": diversity_factor,
                    "CurrentMILPCandidateFeederCount": candidate_count,
                    "CurrentCandidateFirmHostingSumMW_UpperBound": candidate_firm_sum
                })

                coincidence_rows.append({
                    "SubstationID": substation_id,
                    "CoincidentPeakMW": coincident_peak,
                    "SumIndividualFeederPeaksMW": sum_individual_peaks,
                    "PeakReductionFromCoincidenceMW": sum_individual_peaks - coincident_peak if pd.notna(sum_individual_peaks) and pd.notna(coincident_peak) else np.nan,
                    "CoincidenceFactor": coincidence_factor,
                    "DiversityFactor": diversity_factor,
                    "PeakTimestampUTC": peak_timestamp,
                    "ProfileCoveragePct": profile_coverage_pct,
                    "ProfileStatus": profile_status
                })

                if not accepted.empty:
                    profile_df = pd.DataFrame({
                        "TimestampUTC": accepted.index,
                        "SubstationID": substation_id,
                        "ExistingLoadMW": accepted.to_numpy(dtype=float),
                        "ExpectedFeederCount": expected_feeders,
                        "LoadedFeederCount": loaded_feeders,
                        "CompleteFeederCount": complete_feeder_count.loc[accepted.index].to_numpy(dtype=int),
                        "CompleteInterval": True
                    })

                    profile_df.to_csv(profile_file, index=False, header=not profile_header_written)
                    profile_header_written = True

                print(f"Substation profile status: {profile_status}")
                print(f"Complete-profile coverage: {profile_coverage_pct:.3f}%")
                if pd.notna(coincident_peak):
                    print(f"Coincident peak: {coincident_peak:.3f} MW at {peak_timestamp}")
                    print(f"Sum of individual feeder peaks: {sum_individual_peaks:.3f} MW")
                    print(f"Coincidence factor: {coincidence_factor:.4f}")
                    print(f"Diversity factor: {diversity_factor:.4f}")

        substation_summary = pd.DataFrame(substation_summary_rows).sort_values(["SubstationProfileStatus", "CoincidentPeakMW"], ascending=[True, False]).reset_index(drop=True)
        feeder_quality = pd.DataFrame(feeder_quality_rows)
        coincidence_analysis = pd.DataFrame(coincidence_rows).sort_values("CoincidentPeakMW", ascending=False).reset_index(drop=True)
        errors = pd.DataFrame(error_rows)

        capacity_input = build_capacity_input(substation_summary)
        write_capacity_template(capacity_input)

        current_candidate_substations = substation_summary[substation_summary["CurrentMILPCandidateFeederCount"] > 0].copy()
        current_candidate_substations = current_candidate_substations.sort_values("CurrentCandidateFirmHostingSumMW_UpperBound", ascending=False)

        workbook_frames = {
            "Substation_Summary": substation_summary,
            "Current_Candidate_Subs": current_candidate_substations,
            "Substation_Feeder_Map": feeder_map_output,
            "Feeder_Profile_Quality": feeder_quality,
            "Coincidence_Analysis": coincidence_analysis,
            "MILP_Capacity_Input": capacity_input,
            "Errors": errors,
            "Assumptions": assumptions_table()
        }

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe_frame = make_excel_safe(frame)
                safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
                format_excel(writer, sheet_name, safe_frame)

        heading("SUBSTATION LOAD MODEL COMPLETE")
        ready_substations = int((substation_summary["SubstationProfileStatus"] == "READY").sum())
        candidate_ready = int((current_candidate_substations["SubstationProfileStatus"] == "READY").sum())

        print(f"Mapped substations modeled: {len(substation_summary)}")
        print(f"Substation profiles ready: {ready_substations}/{len(substation_summary)}")
        print(f"Current candidate substations ready: {candidate_ready}/{len(current_candidate_substations)}")
        print(f"Analysis workbook saved to: {Path(OUTPUT_FILE).resolve()}")
        print(f"Five-minute profiles saved to: {Path(PROFILE_OUTPUT_FILE).resolve()}")
        print(f"MILP capacity template refreshed at: {Path(CAPACITY_TEMPLATE_FILE).resolve()}")

        if not current_candidate_substations.empty:
            print("\nCurrent candidate substations:")
            display_columns = [
                "SubstationID",
                "ExpectedFeederCount",
                "LoadedFeederCount",
                "ProfileCoveragePct",
                "CoincidentPeakMW",
                "SumIndividualFeederPeaksMW",
                "CoincidenceFactor",
                "CurrentCandidateFirmHostingSumMW_UpperBound",
                "SubstationProfileStatus"
            ]
            print(current_candidate_substations[display_columns].to_string(index=False))

        heading("NEXT MODELING INPUT")
        print("ExistingCoincidentPeakMW is now calculated from timestamp-aligned feeder breaker load for every READY substation.")
        print("Do not enter a guessed substation capacity.")
        print("Once TotalCapacityMW or a defensible AvailableAdditionalCapacityMW is supplied, rerun greensboro_base_milp.py.")
        print("The existing MILP will then activate the shared-substation constraints automatically.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
