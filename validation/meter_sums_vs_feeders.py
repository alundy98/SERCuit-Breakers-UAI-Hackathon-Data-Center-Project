import os
from pathlib import Path

import numpy as np
import pandas as pd
import psycopg2
from dotenv import load_dotenv

RANKING_FILE = "greensboro_substation_constrained_location_rankings.xlsx"
RANKING_SHEET = "Final_Location_Ranking"
YEAR = 2024
TOP_FEEDERS = 10
RUN_PARENT_TRANSFORMER_AUDIT = True
OUTPUT_FILE = "greensboro_meter_scada_reconciliation_audit.xlsx"

load_dotenv()

DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")


def heading(text):
    print("\n" + "=" * 110)
    print(text)
    print("=" * 110)


def query_df(conn, sql, params=None):
    with conn.cursor() as cursor:
        cursor.execute(sql, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def safe_query(conn, sql, params=None):
    try:
        return query_df(conn, sql, params), None
    except Exception as exc:
        conn.rollback()
        return pd.DataFrame(), str(exc)


def connect():
    missing = [name for name, value in {"EDM_HOST": DB_HOST, "EDM_USER": DB_USER, "EDM_PASSWORD": DB_PASSWORD}.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME)


def choose_feeders():
    path = Path(RANKING_FILE)

    if not path.exists():
        raise FileNotFoundError(f"Could not find {RANKING_FILE}: {path.resolve()}")

    df = pd.read_excel(path, sheet_name=RANKING_SHEET)

    for column in ["FinalBaseHostingMW", "FeederPathHostingMW", "EstimatedBaseHostingMW", "GlobalRank"]:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["HostingMW"] = df["FinalBaseHostingMW"].combine_first(df["FeederPathHostingMW"]).combine_first(df["EstimatedBaseHostingMW"])
    df = df[df["HostingMW"].notna()].copy()

    if df.empty:
        raise RuntimeError("No usable hosting values were found in the ranking workbook.")

    df = df.sort_values(["GridID", "HostingMW", "GlobalRank"], ascending=[True, False, True], na_position="last")
    best = df.groupby("GridID", as_index=False).head(1).copy()
    best = best.sort_values(["HostingMW", "GlobalRank"], ascending=[False, True], na_position="last").head(TOP_FEEDERS)

    return best[["GridID", "CandidateLineID", "SubstationID", "HostingMW"]].reset_index(drop=True)


def get_feeder_inventory(conn, grid_ids):
    return query_df(conn, """
        SELECT ge.grid_id AS "GridID",
               COUNT(*) FILTER (WHERE LOWER(ge.type) LIKE '%%meter%%') AS "MeterCount",
               COUNT(*) FILTER (WHERE LOWER(ge.type) LIKE '%%transformer%%') AS "TransformerCount",
               COUNT(*) FILTER (WHERE LOWER(ge.type) LIKE '%%line%%') AS "LineCount",
               COUNT(*) FILTER (WHERE LOWER(ge.type) LIKE '%%photovoltaic%%') AS "PVCount",
               COUNT(*) FILTER (WHERE LOWER(ge.type) LIKE '%%evcharger%%') AS "EVCount"
        FROM grid_element ge
        WHERE ge.grid_id = ANY(%s)
        GROUP BY ge.grid_id
        ORDER BY ge.grid_id;
    """, (grid_ids,))


def get_meter_source_audit(conn, grid_ids):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    return query_df(conn, """
        WITH meter_sources AS (
            SELECT ge.grid_id,
                   ge.grid_element_id AS meter_id,
                   ds.grid_element_data_source_id,
                   ds.type AS data_source_type,
                   ds.provider,
                   ds.phases,
                   ds.direction,
                   ds.valid
            FROM grid_element ge
            LEFT JOIN grid_element_data_source ds
              ON ds.grid_id = ge.grid_id
             AND ds.grid_element_id = ge.grid_element_id
             AND 'kWh' = ANY(ds.metrics)
            WHERE ge.grid_id = ANY(%s)
              AND LOWER(ge.type) LIKE '%%meter%%'
        )
        SELECT grid_id AS "GridID",
               COUNT(DISTINCT meter_id) AS "Meters",
               COUNT(DISTINCT meter_id) FILTER (WHERE grid_element_data_source_id IS NOT NULL) AS "MetersWithKWh",
               COUNT(grid_element_data_source_id) AS "KWhDataSources",
               ROUND(COUNT(grid_element_data_source_id)::numeric / NULLIF(COUNT(DISTINCT meter_id), 0), 3) AS "SourcesPerMeter",
               COUNT(*) FILTER (WHERE phases IS NOT NULL) AS "SourcesWithPhase",
               COUNT(*) FILTER (WHERE valid IS NULL) AS "SourcesWithoutValidityRange",
               COUNT(*) FILTER (
                   WHERE valid IS NOT NULL
                     AND valid && tstzrange(%s::timestamptz, %s::timestamptz, '[)')
               ) AS "SourcesOverlappingAuditYear"
        FROM meter_sources
        GROUP BY grid_id
        ORDER BY grid_id;
    """, (grid_ids, start, end))


def get_meter_source_detail(conn, grid_ids):
    return query_df(conn, """
        SELECT ge.grid_id AS "GridID",
               ge.grid_element_id AS "MeterID",
               ds.grid_element_data_source_id AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.phases AS "Phase",
               ds.direction AS "Direction",
               ds.valid AS "Valid"
        FROM grid_element ge
        JOIN grid_element_data_source ds
          ON ds.grid_id = ge.grid_id
         AND ds.grid_element_id = ge.grid_element_id
        WHERE ge.grid_id = ANY(%s)
          AND LOWER(ge.type) LIKE '%%meter%%'
          AND 'kWh' = ANY(ds.metrics)
        ORDER BY ge.grid_id, ge.grid_element_id, ds.grid_element_data_source_id;
    """, (grid_ids,))


def get_breaker_sources(conn, grid_ids):
    return query_df(conn, """
        SELECT ge.grid_id AS "GridID",
               ge.grid_element_id AS "BreakerID",
               ds.grid_element_data_source_id AS "DataSourceID",
               ds.type AS "DataSourceType",
               ds.provider AS "Provider",
               ds.phases AS "Phase",
               ds.direction AS "Direction",
               ds.valid AS "Valid"
        FROM grid_element ge
        JOIN grid_element_data_source ds
          ON ds.grid_id = ge.grid_id
         AND ds.grid_element_id = ge.grid_element_id
        WHERE ge.grid_id = ANY(%s)
          AND ge.type = 'CircuitBreaker'
          AND 'kWh' = ANY(ds.metrics)
        ORDER BY ge.grid_id, ge.grid_element_id, ds.grid_element_data_source_id;
    """, (grid_ids,))


def load_hourly_source(conn, source_id):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    df, error = safe_query(conn, """
        SELECT date_trunc('hour', timestamp) AS "TimestampUTC",
               SUM(value) AS "HourlyKWh",
               COUNT(*) AS "Intervals"
        FROM ts_data_source_select(%s::uuid, 'kWh', tstzrange(%s::timestamptz, %s::timestamptz, '[)'))
        GROUP BY date_trunc('hour', timestamp)
        ORDER BY 1;
    """, (str(source_id), start, end))

    if error:
        raise RuntimeError(f"Time-series query failed for source {source_id}: {error}")

    if df.empty:
        return df

    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["HourlyKWh"] = pd.to_numeric(df["HourlyKWh"], errors="coerce")
    return df.dropna(subset=["TimestampUTC", "HourlyKWh"]).sort_values("TimestampUTC")


def load_feeder_meter_hourly(conn, grid_id, source_detail):
    feeder_sources = source_detail[source_detail["GridID"] == grid_id].copy()
    if feeder_sources.empty:
        return pd.DataFrame(), []

    frames = []
    errors = []

    for _, source in feeder_sources.iterrows():
        try:
            ts = load_hourly_source(conn, source["DataSourceID"])
            if ts.empty:
                continue
            ts["MeterID"] = source["MeterID"]
            ts["DataSourceID"] = str(source["DataSourceID"])
            frames.append(ts)
        except Exception as exc:
            errors.append(f"{source['MeterID']} / {source['DataSourceID']}: {exc}")

    if not frames:
        return pd.DataFrame(), errors

    all_sources = pd.concat(frames, ignore_index=True)

    meter_hourly = all_sources.groupby(["TimestampUTC", "MeterID"], as_index=False).agg(
        MeterKWh=("HourlyKWh", "sum"),
        SourceCount=("DataSourceID", "nunique")
    )

    feeder_hourly = meter_hourly.groupby("TimestampUTC", as_index=False).agg(
        MeterAggregateKWh=("MeterKWh", "sum"),
        MetersPresent=("MeterID", "nunique")
    )

    return feeder_hourly, errors


def load_feeder_breaker_hourly(conn, grid_id, breaker_sources):
    feeder_sources = breaker_sources[breaker_sources["GridID"] == grid_id].copy()
    if feeder_sources.empty:
        return pd.DataFrame(), []

    frames = []
    errors = []

    for _, source in feeder_sources.iterrows():
        try:
            ts = load_hourly_source(conn, source["DataSourceID"])
            if ts.empty:
                continue
            ts["DataSourceID"] = str(source["DataSourceID"])
            ts["Phase"] = source.get("Phase")
            frames.append(ts)
        except Exception as exc:
            errors.append(f"{source['DataSourceID']}: {exc}")

    if not frames:
        return pd.DataFrame(), errors

    all_sources = pd.concat(frames, ignore_index=True)

    phase_hourly = all_sources.groupby(["TimestampUTC", "DataSourceID"], as_index=False).agg(BreakerSourceKWh=("HourlyKWh", "sum"))
    feeder_hourly = phase_hourly.groupby("TimestampUTC", as_index=False).agg(
        BreakerAggregateKWh=("BreakerSourceKWh", "sum"),
        BreakerSourceCount=("DataSourceID", "nunique")
    )

    return feeder_hourly, errors


def compare_profiles(grid_id, meter_hourly, breaker_hourly, expected_meter_count):
    if meter_hourly.empty or breaker_hourly.empty:
        return {
            "GridID": grid_id,
            "Status": "MISSING PROFILE",
            "MatchedHours": 0
        }, pd.DataFrame()

    merged = meter_hourly.merge(breaker_hourly, on="TimestampUTC", how="inner")
    merged = merged.dropna(subset=["MeterAggregateKWh", "BreakerAggregateKWh"]).copy()

    if merged.empty:
        return {
            "GridID": grid_id,
            "Status": "NO OVERLAP",
            "MatchedHours": 0
        }, merged

    raw_corr = merged["MeterAggregateKWh"].corr(merged["BreakerAggregateKWh"])
    abs_corr = merged["MeterAggregateKWh"].corr(merged["BreakerAggregateKWh"].abs())

    use_abs_breaker = pd.notna(abs_corr) and (pd.isna(raw_corr) or abs_corr > raw_corr + 0.05)
    merged["BreakerComparisonKWh"] = merged["BreakerAggregateKWh"].abs() if use_abs_breaker else merged["BreakerAggregateKWh"]

    merged["ErrorKWh"] = merged["MeterAggregateKWh"] - merged["BreakerComparisonKWh"]
    merged["AbsoluteErrorKWh"] = merged["ErrorKWh"].abs()

    meter_total = merged["MeterAggregateKWh"].sum()
    breaker_total = merged["BreakerComparisonKWh"].sum()

    correlation = merged["MeterAggregateKWh"].corr(merged["BreakerComparisonKWh"])
    mae = merged["AbsoluteErrorKWh"].mean()
    rmse = float(np.sqrt(np.mean(np.square(merged["ErrorKWh"]))))
    mean_breaker = merged["BreakerComparisonKWh"].mean()
    nmae = mae / mean_breaker * 100.0 if mean_breaker != 0 else np.nan
    nrmse = rmse / mean_breaker * 100.0 if mean_breaker != 0 else np.nan

    ratios = merged.loc[merged["BreakerComparisonKWh"] > 0, "MeterAggregateKWh"] / merged.loc[merged["BreakerComparisonKWh"] > 0, "BreakerComparisonKWh"]

    best_lag = None
    best_lag_corr = -np.inf
    for lag in range(-3, 4):
        lagged = merged["MeterAggregateKWh"].shift(lag)
        corr = lagged.corr(merged["BreakerComparisonKWh"])
        if pd.notna(corr) and corr > best_lag_corr:
            best_lag_corr = corr
            best_lag = lag

    meter_presence = merged["MetersPresent"].median() if "MetersPresent" in merged.columns else np.nan
    meter_completeness = meter_presence / expected_meter_count * 100.0 if expected_meter_count and expected_meter_count > 0 else np.nan

    if pd.notna(correlation) and correlation >= 0.90 and pd.notna(nmae) and nmae <= 20 and pd.notna(meter_completeness) and meter_completeness >= 90:
        quality = "STRONG"
    elif pd.notna(correlation) and correlation >= 0.75 and pd.notna(nmae) and nmae <= 40 and pd.notna(meter_completeness) and meter_completeness >= 75:
        quality = "USABLE"
    else:
        quality = "REVIEW"

    summary = {
        "GridID": grid_id,
        "Status": "OK",
        "ReconciliationQuality": quality,
        "MatchedHours": len(merged),
        "MedianMetersPresent": meter_presence,
        "ExpectedMeters": expected_meter_count,
        "MedianMeterCompletenessPct": meter_completeness,
        "MeterEnergyMWh": meter_total / 1000.0,
        "BreakerEnergyMWh": breaker_total / 1000.0,
        "MeterToBreakerEnergyRatio": meter_total / breaker_total if breaker_total != 0 else np.nan,
        "Correlation": correlation,
        "RawCorrelation": raw_corr,
        "AbsBreakerCorrelation": abs_corr,
        "UsedAbsoluteBreakerSign": use_abs_breaker,
        "MAE_kWh_per_hour": mae,
        "RMSE_kWh_per_hour": rmse,
        "NormalizedMAE_PctOfMeanBreaker": nmae,
        "NormalizedRMSE_PctOfMeanBreaker": nrmse,
        "MedianHourlyMeterToBreakerRatio": ratios.median() if not ratios.empty else np.nan,
        "P05HourlyMeterToBreakerRatio": ratios.quantile(0.05) if not ratios.empty else np.nan,
        "P95HourlyMeterToBreakerRatio": ratios.quantile(0.95) if not ratios.empty else np.nan,
        "BestLagHours": best_lag,
        "BestLagCorrelation": best_lag_corr if best_lag_corr > -np.inf else np.nan
    }

    merged["GridID"] = grid_id
    return summary, merged


def parent_transformer_audit(conn, grid_ids):
    if not RUN_PARENT_TRANSFORMER_AUDIT:
        return pd.DataFrame()

    df, error = safe_query(conn, """
        WITH meters AS (
            SELECT grid_id, grid_element_id AS meter_id
            FROM grid_element
            WHERE grid_id = ANY(%s)
              AND LOWER(type) LIKE '%%meter%%'
        )
        SELECT m.grid_id AS "GridID",
               m.meter_id AS "MeterID",
               ggs.grid_element_id AS "TransformerID"
        FROM meters m
        JOIN LATERAL grid_get_sources(m.grid_id, m.meter_id, TRUE) ggs ON TRUE
        JOIN LATERAL grid_get_same_voltage(m.grid_id, ggs.grid_element_id) ggsv ON TRUE
        WHERE ggs.type = 'Transformer'
          AND ggsv.grid_element_id = m.meter_id
        ORDER BY m.grid_id, m.meter_id, ggs.grid_element_id;
    """, (grid_ids,))

    if error:
        print(f"Parent-transformer trace audit failed: {error}")
        return pd.DataFrame()

    return df


def summarize_parent_mapping(parent_df, inventory):
    if parent_df.empty:
        return pd.DataFrame()

    mapped = parent_df.groupby("GridID")["MeterID"].nunique().rename("MetersMappedToParentTransformer")
    duplicate = parent_df.groupby(["GridID", "MeterID"])["TransformerID"].nunique().reset_index(name="TransformerMatches")
    ambiguous = duplicate.groupby("GridID")["TransformerMatches"].apply(lambda x: int((x > 1).sum())).rename("MetersWithMultipleTransformerMatches")

    summary = inventory[["GridID", "MeterCount"]].copy()
    summary = summary.merge(mapped, on="GridID", how="left").merge(ambiguous, on="GridID", how="left")
    summary["MetersMappedToParentTransformer"] = summary["MetersMappedToParentTransformer"].fillna(0).astype(int)
    summary["MetersWithMultipleTransformerMatches"] = summary["MetersWithMultipleTransformerMatches"].fillna(0).astype(int)
    summary["ParentTransformerMappingPct"] = np.where(summary["MeterCount"] > 0, summary["MetersMappedToParentTransformer"] / summary["MeterCount"] * 100.0, np.nan)

    return summary


def main():
    targets = choose_feeders()
    grid_ids = targets["GridID"].astype(str).tolist()

    heading("GREENSBORO METER VS BREAKER TIME-SERIES RECONCILIATION")
    print(targets.to_string(index=False))

    conn = connect()

    try:
        heading("1. FEEDER INVENTORY")
        inventory = get_feeder_inventory(conn, grid_ids)
        print(inventory.to_string(index=False))

        heading("2. METER kWh DATA-SOURCE STRUCTURE")
        meter_source_audit = get_meter_source_audit(conn, grid_ids)
        print(meter_source_audit.to_string(index=False))

        meter_source_detail = get_meter_source_detail(conn, grid_ids)
        breaker_sources = get_breaker_sources(conn, grid_ids)

        heading("3. BREAKER kWh SOURCE COUNTS")
        if breaker_sources.empty:
            print("No breaker kWh sources found.")
        else:
            breaker_counts = breaker_sources.groupby("GridID").agg(
                BreakerIDs=("BreakerID", "nunique"),
                BreakerKWhSources=("DataSourceID", "nunique"),
                Providers=("Provider", lambda x: ", ".join(sorted(set(str(v) for v in x.dropna()))))
            ).reset_index()
            print(breaker_counts.to_string(index=False))

        heading("4. HOURLY METER-SUM VS BREAKER-SCADA RECONCILIATION")
        summaries = []
        hourly_frames = []
        meter_errors = []
        breaker_errors = []

        meter_count_lookup = inventory.set_index("GridID")["MeterCount"].to_dict()

        for index, grid_id in enumerate(grid_ids, start=1):
            print(f"\n[{index}/{len(grid_ids)}] {grid_id}")

            meter_hourly, meter_error_list = load_feeder_meter_hourly(conn, grid_id, meter_source_detail)
            breaker_hourly, breaker_error_list = load_feeder_breaker_hourly(conn, grid_id, breaker_sources)

            meter_errors.extend([f"{grid_id}: {error}" for error in meter_error_list])
            breaker_errors.extend([f"{grid_id}: {error}" for error in breaker_error_list])

            summary, merged = compare_profiles(grid_id, meter_hourly, breaker_hourly, meter_count_lookup.get(grid_id, 0))
            summaries.append(summary)

            if not merged.empty:
                hourly_frames.append(merged)

            print(pd.DataFrame([summary]).to_string(index=False))

        reconciliation = pd.DataFrame(summaries)
        hourly_detail = pd.concat(hourly_frames, ignore_index=True) if hourly_frames else pd.DataFrame()

        heading("5. METER TO PARENT-TRANSFORMER MAPPING")
        parent_df = parent_transformer_audit(conn, grid_ids)
        parent_summary = summarize_parent_mapping(parent_df, inventory)

        if parent_summary.empty:
            print("No parent-transformer mapping summary available.")
        else:
            print(parent_summary.to_string(index=False))

        heading("6. MODELING DECISION")
        if reconciliation.empty:
            print("No reconciliation results were available.")
        else:
            quality_counts = reconciliation["ReconciliationQuality"].value_counts(dropna=False)
            print("Reconciliation quality counts:")
            print(quality_counts.to_string())

            strong_or_usable = reconciliation["ReconciliationQuality"].isin(["STRONG", "USABLE"]).mean() * 100.0
            median_corr = reconciliation["Correlation"].median()
            median_nmae = reconciliation["NormalizedMAE_PctOfMeanBreaker"].median()
            median_ratio = reconciliation["MeterToBreakerEnergyRatio"].median()

            print(f"\nFeeders rated STRONG or USABLE: {strong_or_usable:.1f}%")
            print(f"Median meter-vs-breaker correlation: {median_corr:.3f}")
            print(f"Median normalized MAE: {median_nmae:.2f}%")
            print(f"Median annual meter/breaker energy ratio: {median_ratio:.3f}")

            if strong_or_usable >= 80 and pd.notna(median_corr) and median_corr >= 0.80:
                print("\nRECOMMENDATION: Use observed meter time series as the bottom-up load-shape basis for the thermal network simulator.")
                print("This supports time-coherent empirical Monte Carlo by sampling actual historical hours/days and preserving feeder/meter correlation.")
            elif strong_or_usable >= 50:
                print("\nRECOMMENDATION: Meter time series are promising but should be calibrated to feeder breaker SCADA before use.")
                print("Use meter profiles for relative spatial/temporal allocation and scale each feeder to its measured breaker profile.")
            else:
                print("\nRECOMMENDATION: Do not use raw bottom-up meter sums as feeder truth yet.")
                print("Use breaker SCADA as feeder truth and meter profiles only as proportional allocation weights until the mismatch is understood.")

        if not parent_summary.empty:
            median_parent_mapping = parent_summary["ParentTransformerMappingPct"].median()
            print(f"\nMedian parent-transformer mapping completeness: {median_parent_mapping:.2f}%")
            if median_parent_mapping >= 90:
                print("Transformer mapping is strong enough to place observed meter demand onto the network by transformer.")
            elif median_parent_mapping >= 70:
                print("Transformer mapping is usable with fallback allocation for unmapped meters.")
            else:
                print("Transformer mapping is too incomplete for fully bottom-up branch loading without additional topology logic.")

        heading("7. DATA ISSUES TO REVIEW")
        if meter_errors:
            print("\nMeter time-series errors:")
            for error in meter_errors[:30]:
                print("  " + error)
        else:
            print("No meter time-series query errors.")

        if breaker_errors:
            print("\nBreaker time-series errors:")
            for error in breaker_errors[:30]:
                print("  " + error)
        else:
            print("No breaker time-series query errors.")

        heading("8. NEXT MODEL AFTER THIS AUDIT")
        print("If meter reconciliation and transformer mapping are strong:")
        print("  Build an hourly bottom-up radial thermal simulator using observed meter loads.")
        print("  Use breaker SCADA as the feeder-level calibration/control total.")
        print("  Add candidate data-center load at the selected network node and propagate it upstream.")
        print("  Enforce line ampacity, transformer kVA, breaker kVA, and later explicit substation capacity.")
        print("  Wrap the simulator in empirical Monte Carlo using sampled historical hours/days.")
        print("  Compare greedy/local search, MILP, and NSGA-II on the same simulator rather than committing to one optimizer first.")

        print("\nIf meter reconciliation is weak:")
        print("  Keep breaker SCADA as feeder truth.")
        print("  Use meter profiles only as load-allocation weights, similar to the current source_rating_kw allocation but time varying.")

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            targets.to_excel(writer, sheet_name="Targets", index=False)
            inventory.to_excel(writer, sheet_name="Feeder_Inventory", index=False)
            meter_source_audit.to_excel(writer, sheet_name="Meter_Source_Audit", index=False)
            meter_source_detail.to_excel(writer, sheet_name="Meter_Source_Detail", index=False)
            breaker_sources.to_excel(writer, sheet_name="Breaker_Sources", index=False)
            reconciliation.to_excel(writer, sheet_name="Reconciliation", index=False)
            parent_summary.to_excel(writer, sheet_name="Parent_Map_Summary", index=False)
            parent_df.to_excel(writer, sheet_name="Parent_Map_Detail", index=False)

            if not hourly_detail.empty:
                max_excel_rows = 1_000_000
                hourly_detail.head(max_excel_rows).to_excel(writer, sheet_name="Hourly_Comparison", index=False)

        print(f"\nAudit workbook saved to: {Path(OUTPUT_FILE).resolve()}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
