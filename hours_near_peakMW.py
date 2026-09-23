import os
import calendar
import pandas as pd
import numpy as np
import psycopg2
from dotenv import load_dotenv
from pathlib import Path

# PURPOSE:
# Analyzes 2024 breaker SCADA for the shortlisted Greensboro feeders to measure annual peak load and how often each feeder operates close to that peak.
# WHY: This shows whether a feeder's peak is brief or sustained and provides the peak-load values used by the later location hosting screen.
# DEPENDENCIES: Queries breaker kWh SCADA from the EDM using .env credentials for the configured target feeders.
# OUTPUT: Writes greensboro_top15_peak_duration_analysis.xlsx, including feeder summaries, near-peak duration thresholds, load-duration data, and SCADA source audits.
OUTPUT_FILE = "greensboro_top15_peak_duration_analysis.xlsx"
YEAR = 2024

# "Within 10% of peak" means the feeder load is at least 90% of its annual peak.
# Additional thresholds are included so we can compare how persistent high-load
# conditions are across the shortlisted feeders.
WITHIN_PEAK_PERCENTAGES = [1, 5, 10, 15, 20, 25, 30, 40, 50]

# Current validated top 15 feeders from the feeder-capacity/topology screening.
TARGET_FEEDERS = [
    "GSO_122",
    "GSO_124",
    "GSO_123",
    "GSO_121",
    "GSO_144",
    "GSO_141",
    "GSO_145",
    "GSO_142",
    "GSO_18",
    "GSO_127",
    "GSO_73",
    "GSO_128",
    "GSO_126",
    "GSO_55",
    "GSO_125"
]

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
        if params is None:
            cursor.execute(query)
        else:
            cursor.execute(query, params)

        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]

    return pd.DataFrame(rows, columns=columns)


# FIND BREAKERS FOR THE SHORTLISTED FEEDERS
def get_breakers(conn):
    query = """
        SELECT
            grid_id,
            grid_element_id,
            type,
            is_producer,
            is_consumer,
            terminal1_cn,
            terminal2_cn
        FROM grid_element
        WHERE grid_id ~ '^GSO_[0-9]+$'
          AND LOWER(type) LIKE '%breaker%'
        ORDER BY grid_id, grid_element_id;
    """

    breakers = query_dataframe(conn, query)

    if breakers.empty:
        raise RuntimeError("No Greensboro breaker elements were found.")

    breakers = breakers[breakers["grid_id"].isin(TARGET_FEEDERS)].copy()

    if breakers.empty:
        raise RuntimeError("None of the target feeders had a breaker element.")

    breakers = breakers.drop_duplicates(subset=["grid_id"], keep="first").copy()

    rank_map = {grid_id: rank for rank, grid_id in enumerate(TARGET_FEEDERS, start=1)}
    breakers["ShortlistRank"] = breakers["grid_id"].map(rank_map)

    breakers = breakers.sort_values("ShortlistRank").reset_index(drop=True)

    return breakers



def get_breaker_data_sources(conn, breaker_ids):
    if len(breaker_ids) == 0:
        return pd.DataFrame()

    placeholders = ",".join(["%s"] * len(breaker_ids))

    query = f"""
        SELECT
            ds.grid_id,
            ds.grid_element_id,
            ds.grid_element_data_source_id AS data_source_id,
            ds.type AS data_source_type,
            ds.provider,
            ds.metrics,
            ds.friendly_id,
            ds.phases AS phase,
            ds.direction,
            ds.valid
        FROM grid_element_data_source ds
        WHERE ds.grid_element_id IN ({placeholders})
          AND ds.metrics::text ~* 'kWh'
        ORDER BY ds.grid_id, ds.grid_element_id, ds.grid_element_data_source_id;
    """

    return query_dataframe(conn, query, tuple(breaker_ids))



def get_scada_timeseries(conn, data_source_id):
    start_timestamp = f"{YEAR}-01-01 00:00:00+00"
    end_timestamp = f"{YEAR + 1}-01-01 00:00:00+00"

    query = """
        SELECT *
        FROM ts_data_source_select(
            %s::uuid,
            'kWh',
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        );
    """

    df = query_dataframe(conn, query, (data_source_id, start_timestamp, end_timestamp))

    if df.empty:
        return pd.DataFrame(columns=["Timestamp", "IntervalKWh"])

    column_map = {str(column).lower(): column for column in df.columns}

    timestamp_candidates = ["timestamp", "time", "ts", "datetime", "date_time"]
    value_candidates = ["value", "metric_value", "double_value", "kwh"]

    timestamp_col = next((column_map[candidate] for candidate in timestamp_candidates if candidate in column_map), None)
    value_col = next((column_map[candidate] for candidate in value_candidates if candidate in column_map), None)

    # Fallback timestamp detection. Avoid trying numeric measurement columns as dates.
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

    # Fallback numeric value detection.
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
    result = result.sort_values("Timestamp").drop_duplicates(subset=["Timestamp"], keep="last").reset_index(drop=True)

    return result


def load_feeder_scada(conn, grid_id, source_rows):
    source_frames = []

    for _, row in source_rows.iterrows():
        data_source_id = row["data_source_id"]

        try:
            ts = get_scada_timeseries(conn, data_source_id)

            if ts.empty:
                print(f"    Source {data_source_id}: no {YEAR} observations")
                continue

            ts["DataSourceID"] = str(data_source_id)
            ts["Phase"] = str(row["phase"]) if pd.notna(row["phase"]) else None

            source_frames.append(ts)

            print(f"    Source {data_source_id}: {len(ts):,} observations")

        except Exception as exc:
            print(f"    WARNING - source {data_source_id} failed: {exc}")

    if not source_frames:
        return pd.DataFrame(), 0

    all_source_data = pd.concat(source_frames, ignore_index=True)

    sources_used = all_source_data["DataSourceID"].nunique()

    # Sum phase/source kWh measurements for each timestamp to produce total feeder energy.
    feeder = all_source_data.groupby("Timestamp", as_index=False).agg(
        IntervalKWh=("IntervalKWh", "sum"),
        SourceCount=("DataSourceID", "nunique")
    )

    feeder = feeder.sort_values("Timestamp").reset_index(drop=True)

    # Only use intervals where every successfully loaded SCADA source is represented.
    complete_intervals = feeder[feeder["SourceCount"] == sources_used].copy()

    if not complete_intervals.empty:
        feeder = complete_intervals.reset_index(drop=True)

    time_differences = feeder["Timestamp"].diff().dt.total_seconds().div(60)

    valid_differences = time_differences[
        (time_differences > 0) &
        (time_differences <= 60)
    ]

    interval_minutes = float(valid_differences.median()) if not valid_differences.empty else 5.0
    interval_hours = interval_minutes / 60.0

    # kWh / hours = kW
    # kW / 1000 = MW
    feeder["LoadMW"] = feeder["IntervalKWh"] / interval_hours / 1000.0
    feeder["IntervalMinutes"] = interval_minutes

    return feeder, sources_used


# ANALYZE HOURS NEAR THE ANNUAL PEAK
def analyze_peak_duration(grid_id, shortlist_rank, feeder, sources_used):
    if feeder.empty:
        return None, pd.DataFrame(), pd.DataFrame()

    peak_mw = float(feeder["LoadMW"].max())
    average_mw = float(feeder["LoadMW"].mean())
    median_mw = float(feeder["LoadMW"].median())
    p95_mw = float(feeder["LoadMW"].quantile(0.95))

    peak_timestamp = feeder.loc[
        feeder["LoadMW"].idxmax(),
        "Timestamp"
    ]

    interval_minutes = float(feeder["IntervalMinutes"].iloc[0])
    interval_hours = interval_minutes / 60.0

    observed_hours = len(feeder) * interval_hours
    calendar_year_hours = 8784.0 if calendar.isleap(YEAR) else 8760.0
    data_coverage_pct = observed_hours / calendar_year_hours * 100.0

    summary = {
        "ShortlistRank": shortlist_rank,
        "GridID": grid_id,
        "PeakLoadMW": peak_mw,
        "PeakTimestampUTC": peak_timestamp,
        "AverageLoadMW": average_mw,
        "MedianLoadMW": median_mw,
        "P95LoadMW": p95_mw,
        "LoadFactor_AvgDivPeak": average_mw / peak_mw if peak_mw > 0 else np.nan,
        "SCADAIntervalMinutes": interval_minutes,
        "SourcesUsed": sources_used,
        "Observations": len(feeder),
        "ObservedHours": observed_hours,
        "CalendarYearHours": calendar_year_hours,
        "DataCoveragePct": data_coverage_pct
    }

    threshold_rows = []

    for within_pct in WITHIN_PEAK_PERCENTAGES:
        minimum_fraction = 1.0 - within_pct / 100.0
        threshold_mw = peak_mw * minimum_fraction

        qualifying = feeder["LoadMW"] >= threshold_mw

        qualifying_intervals = int(qualifying.sum())
        qualifying_hours = qualifying_intervals * interval_hours

        pct_observed_time = qualifying_hours / observed_hours * 100.0 if observed_hours > 0 else np.nan
        pct_calendar_year = qualifying_hours / calendar_year_hours * 100.0 if calendar_year_hours > 0 else np.nan

        summary[f"HoursWithin{within_pct}PctOfPeak"] = qualifying_hours
        summary[f"PctObservedTimeWithin{within_pct}PctOfPeak"] = pct_observed_time

        threshold_rows.append({
            "ShortlistRank": shortlist_rank,
            "GridID": grid_id,
            "WithinPctOfPeak": within_pct,
            "MinimumLoadPctOfPeak": minimum_fraction * 100.0,
            "PeakLoadMW": peak_mw,
            "ThresholdMW": threshold_mw,
            "QualifyingIntervals": qualifying_intervals,
            "HoursWithinThreshold": qualifying_hours,
            "PctObservedTime": pct_observed_time,
            "PctCalendarYear": pct_calendar_year,
            "ObservedHours": observed_hours,
            "DataCoveragePct": data_coverage_pct
        })

    feeder_detail = feeder.copy()

    feeder_detail["ShortlistRank"] = shortlist_rank
    feeder_detail["GridID"] = grid_id
    feeder_detail["PctOfPeak"] = np.where(peak_mw > 0, feeder_detail["LoadMW"] / peak_mw * 100.0, np.nan)
    feeder_detail["Within10PctOfPeak"] = feeder_detail["LoadMW"] >= peak_mw * 0.90

    return summary, pd.DataFrame(threshold_rows), feeder_detail

def build_hourly_load_duration(detail_df):
    if detail_df.empty:
        return pd.DataFrame()

    duration_frames = []

    for grid_id, group in detail_df.groupby("GridID"):
        group = group.sort_values("LoadMW", ascending=False).reset_index(drop=True)

        shortlist_rank = int(group["ShortlistRank"].iloc[0])
        interval_minutes = float(group["IntervalMinutes"].iloc[0])
        intervals_per_hour = max(int(round(60.0 / interval_minutes)), 1)
        peak_mw = float(group["LoadMW"].max())

        sampled = group.iloc[::intervals_per_hour].copy().reset_index(drop=True)

        sampled["CumulativeHours"] = np.arange(len(sampled), dtype=float)
        sampled["PctOfPeak"] = np.where(peak_mw > 0, sampled["LoadMW"] / peak_mw * 100.0, np.nan)

        duration_frames.append(
            sampled[
                [
                    "ShortlistRank",
                    "GridID",
                    "CumulativeHours",
                    "LoadMW",
                    "PctOfPeak"
                ]
            ]
        )

    duration_df = pd.concat(duration_frames, ignore_index=True)

    return duration_df.sort_values(["ShortlistRank", "CumulativeHours"]).reset_index(drop=True)



def make_excel_safe(df):
    if df is None or df.empty:
        return df

    df = df.copy()

    for column in df.columns:
        series = df[column]

        # Handle a normal pandas timezone-aware datetime column.
        if isinstance(series.dtype, pd.DatetimeTZDtype):
            df[column] = series.dt.tz_localize(None)
            continue

        # Handle object columns that may contain timezone-aware Timestamp objects.
        if series.dtype == "object":
            contains_timestamp = series.dropna().map(lambda value: isinstance(value, (pd.Timestamp,))).any()

            if contains_timestamp:
                converted = pd.to_datetime(series, utc=True, errors="coerce")

                if converted.notna().any():
                    df[column] = converted.dt.tz_localize(None)

    return df


def format_excel(writer, sheet_name, dataframe):
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]

    header_format = workbook.add_format({
        "bold": True,
        "border": 1,
        "align": "center",
        "valign": "vcenter"
    })

    number_format = workbook.add_format({"num_format": "0.000"})
    percent_format = workbook.add_format({"num_format": "0.00"})
    integer_format = workbook.add_format({"num_format": "0"})
    datetime_format = workbook.add_format({"num_format": "yyyy-mm-dd hh:mm:ss"})

    if dataframe is None or dataframe.shape[1] == 0:
        return

    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)

        # Robust width calculation that handles floats, NaN, lists, arrays,
        # Arrow-backed columns, UUIDs, booleans, and other mixed data types.
        if dataframe.empty:
            max_value_length = 0
        else:
            column_values = dataframe[column_name].tolist()
            max_value_length = max(
                (len(str(value)) for value in column_values if value is not None and not (isinstance(value, float) and np.isnan(value))),
                default=0
            )

        width = min(max(max_value_length, len(str(column_name))) + 2, 40)
        lower_name = str(column_name).lower()

        if "timestamp" in lower_name:
            worksheet.set_column(column_index, column_index, max(width, 20), datetime_format)

        elif "pct" in lower_name or "percent" in lower_name:
            worksheet.set_column(column_index, column_index, width, percent_format)

        elif (
            "rank" in lower_name
            or "observations" in lower_name
            or "intervals" in lower_name
            or "sourcesused" in lower_name
        ):
            worksheet.set_column(column_index, column_index, width, integer_format)

        elif pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, number_format)

        else:
            worksheet.set_column(column_index, column_index, width)

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)
# ======================================================================================

def main():
    print("=" * 100)
    print("GREENSBORO TOP-15 2024 SCADA PEAK-DURATION ANALYSIS")
    print("=" * 100)
    print()
    print("Definition:")
    print("  Within 10% of peak = feeder load is at least 90% of its annual maximum.")
    print()

    conn = connect_to_edm()

    try:
        breakers = get_breakers(conn)

        print(f"Target feeders requested: {len(TARGET_FEEDERS):,}")
        print(f"Target feeder breakers found: {len(breakers):,}")
        print()

        found_feeders = set(breakers["grid_id"])

        missing_feeders = [
            grid_id
            for grid_id in TARGET_FEEDERS
            if grid_id not in found_feeders
        ]

        if missing_feeders:
            print(f"WARNING - target feeders without identified breaker: {', '.join(missing_feeders)}")
            print()

        breaker_ids = breakers["grid_element_id"].tolist()

        data_sources = get_breaker_data_sources(
            conn,
            breaker_ids
        )

        print(f"Breaker kWh SCADA data-source rows found: {len(data_sources):,}")
        print()

        if not data_sources.empty:
            source_counts = (
                data_sources
                .groupby("grid_id")["data_source_id"]
                .nunique()
                .reindex(TARGET_FEEDERS)
                .dropna()
            )

            print("SCADA source counts by target feeder:")
            print(source_counts.to_string())
            print()

        summaries = []
        threshold_frames = []
        detail_frames = []

        total = len(breakers)

        for index, breaker in breakers.iterrows():
            shortlist_rank = int(breaker["ShortlistRank"])
            grid_id = breaker["grid_id"]
            breaker_id = breaker["grid_element_id"]

            print("-" * 100)
            print(f"[{index + 1}/{total}] Rank {shortlist_rank} - Processing {grid_id}")
            print(f"  Breaker: {breaker_id}")

            source_rows = data_sources[
                data_sources["grid_element_id"] == breaker_id
            ].copy()

            if source_rows.empty:
                print("  WARNING - no breaker kWh SCADA sources found.")
                continue

            print(
                f"  kWh sources identified: "
                f"{source_rows['data_source_id'].nunique()}"
            )

            feeder, sources_used = load_feeder_scada(
                conn,
                grid_id,
                source_rows
            )

            if feeder.empty:
                print(f"  WARNING - no usable {YEAR} feeder SCADA observations returned.")
                continue

            summary, threshold_df, detail_df = analyze_peak_duration(
                grid_id=grid_id,
                shortlist_rank=shortlist_rank,
                feeder=feeder,
                sources_used=sources_used
            )

            if summary is None:
                continue

            summaries.append(summary)
            threshold_frames.append(threshold_df)
            detail_frames.append(detail_df)

            print(f"  Complete observations: {summary['Observations']:,}")
            print(f"  Observed hours: {summary['ObservedHours']:,.2f}")
            print(f"  Data coverage: {summary['DataCoveragePct']:.2f}%")
            print(f"  Average load: {summary['AverageLoadMW']:.4f} MW")
            print(f"  P95 load: {summary['P95LoadMW']:.4f} MW")
            print(f"  Annual peak: {summary['PeakLoadMW']:.4f} MW")
            print(f"  Hours within 10% of peak: {summary['HoursWithin10PctOfPeak']:.2f}")
            print(f"  Percent of observed time within 10% of peak: {summary['PctObservedTimeWithin10PctOfPeak']:.2f}%")

        print()
        print("=" * 100)
        print("BUILDING OUTPUT TABLES")
        print("=" * 100)

        summary_df = pd.DataFrame(summaries)

        if not summary_df.empty:
            summary_df = (
                summary_df
                .sort_values("ShortlistRank")
                .reset_index(drop=True)
            )

            # Explicit timezone removal for the peak timestamp.
            if "PeakTimestampUTC" in summary_df.columns:
                summary_df["PeakTimestampUTC"] = (
                    pd.to_datetime(
                        summary_df["PeakTimestampUTC"],
                        utc=True,
                        errors="coerce"
                    )
                    .dt
                    .tz_localize(None)
                )

        threshold_df = (
            pd.concat(
                threshold_frames,
                ignore_index=True
            )
            if threshold_frames
            else pd.DataFrame()
        )

        detail_df = (
            pd.concat(
                detail_frames,
                ignore_index=True
            )
            if detail_frames
            else pd.DataFrame()
        )

        if not threshold_df.empty:
            threshold_df = (
                threshold_df
                .sort_values(
                    [
                        "ShortlistRank",
                        "WithinPctOfPeak"
                    ]
                )
                .reset_index(drop=True)
            )

            within_10_df = (
                threshold_df[
                    threshold_df["WithinPctOfPeak"] == 10
                ]
                .copy()
                .sort_values("ShortlistRank")
                .reset_index(drop=True)
            )

        else:
            within_10_df = pd.DataFrame()

        load_duration_df = build_hourly_load_duration(detail_df)

        source_audit_df = data_sources.copy()

        if not source_audit_df.empty:
            rank_map = {
                grid_id: rank
                for rank, grid_id in enumerate(
                    TARGET_FEEDERS,
                    start=1
                )
            }

            source_audit_df["ShortlistRank"] = (
                source_audit_df["grid_id"]
                .map(rank_map)
            )

            ordered_columns = [
                "ShortlistRank",
                "grid_id",
                "grid_element_id",
                "data_source_id",
                "data_source_type",
                "provider",
                "metrics",
                "friendly_id",
                "phase",
                "direction",
                "valid"
            ]

            source_audit_df = source_audit_df[
                [
                    column
                    for column in ordered_columns
                    if column in source_audit_df.columns
                ]
            ]

            source_audit_df = (
                source_audit_df
                .sort_values(
                    [
                        "ShortlistRank",
                        "data_source_id"
                    ]
                )
                .reset_index(drop=True)
            )

        summary_df = make_excel_safe(summary_df)
        within_10_df = make_excel_safe(within_10_df)
        threshold_df = make_excel_safe(threshold_df)
        load_duration_df = make_excel_safe(load_duration_df)
        source_audit_df = make_excel_safe(source_audit_df)

        print("Writing Excel workbook...")

        with pd.ExcelWriter(
            OUTPUT_FILE,
            engine="xlsxwriter",
            datetime_format="yyyy-mm-dd hh:mm:ss"
        ) as writer:

            summary_df.to_excel(
                writer,
                sheet_name="Feeder_Summary",
                index=False
            )

            within_10_df.to_excel(
                writer,
                sheet_name="Within_10Pct_Peak",
                index=False
            )

            threshold_df.to_excel(
                writer,
                sheet_name="All_Thresholds",
                index=False
            )

            load_duration_df.to_excel(
                writer,
                sheet_name="Load_Duration_Curve",
                index=False
            )

            source_audit_df.to_excel(
                writer,
                sheet_name="SCADA_Source_Audit",
                index=False
            )

            format_excel(
                writer,
                "Feeder_Summary",
                summary_df
            )

            format_excel(
                writer,
                "Within_10Pct_Peak",
                within_10_df
            )

            format_excel(
                writer,
                "All_Thresholds",
                threshold_df
            )

            format_excel(
                writer,
                "Load_Duration_Curve",
                load_duration_df
            )

            format_excel(
                writer,
                "SCADA_Source_Audit",
                source_audit_df
            )

        print()
        print("=" * 100)
        print("ANALYSIS COMPLETE")
        print("=" * 100)
        print(f"Feeders successfully analyzed: {len(summary_df):,} / {len(TARGET_FEEDERS):,}")
        print(f"Output file: {Path(OUTPUT_FILE).resolve()}")

        if not within_10_df.empty:
            print()
            print("HOURS WITHIN 10% OF ANNUAL PEAK")
            print("-" * 100)

            display_columns = [
                "ShortlistRank",
                "GridID",
                "PeakLoadMW",
                "ThresholdMW",
                "HoursWithinThreshold",
                "PctObservedTime",
                "DataCoveragePct"
            ]

            print(
                within_10_df[
                    display_columns
                ]
                .to_string(index=False)
            )

        print()
        print("Interpretation:")
        print("  Within 10% of peak means load >= 90% of that feeder's 2024 maximum.")
        print("  HoursWithinThreshold is the equivalent number of hours spent at or above that threshold.")
        print("  Low hours near peak indicate a short-duration or relatively rare peak.")
        print("  High hours near peak indicate sustained operation near the feeder's maximum.")
        print("  DataCoveragePct should be checked before comparing feeders with incomplete SCADA histories.")
        print()

    finally:
        conn.close()


if __name__ == "__main__":
    main()