from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyomo.environ as pyo
except ImportError as exc:
    raise ImportError("Pyomo is required. Install with: pip install pyomo highspy") from exc


# ======================================================================================
# PURPOSE
# ======================================================================================
# FINAL EXISTING-SYSTEM Greensboro data-center siting / hosting MILP.
#
# This model intentionally answers only:
#   "How much centralized data-center load can the EXISTING modeled distribution system
#    support, and which admitted candidate location provides the largest supportable MW?"
#
# Design principles:
#   1. Use the full calculated AvailableThermalHostingMW with NO arbitrary 10% deduction.
#   2. Keep exactly one centralized site.
#   3. Constrain substation/source capacity only when an explicit defensible capacity or
#      additional-headroom value is actually supplied.
#   4. Do not give battery, PV, EV shifting, DR, flexible compute, or feeder rerouting any
#      capacity credit in this existing-system answer.
#   5. Keep reliability findings as an audit/reporting layer rather than inventing a
#      reliability score or hidden MW penalty.
#   6. Preserve limiting timestamps/elements so every capacity result can be audited.
#   7. Report 90% / 80% incremental-headroom scaling only as separate sensitivities.
#
# IMPORTANT:
# AvailableThermalHostingMW is already an upstream modeled incremental hosting result.
# A sensitivity factor such as 0.90 * AvailableThermalHostingMW is NOT equivalent to an
# exact equipment utilization rule of 0.90*C_e - P_e,t. Exact utilization sensitivities
# belong in thermal_model.py where individual equipment capacities and interval loads are
# available.
# ======================================================================================


# ======================================================================================
# FILES / SETTINGS
# ======================================================================================

AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
AUDIT_SHEET = "MILP_Readiness"

THERMAL_INTERVAL_FILE = "greensboro_5min_candidate_hosting_2024.csv.gz"

SUBSTATION_PROFILE_FILE = "greensboro_substation_5min_profiles_2024.csv.gz"
SUBSTATION_CAPACITY_FILE = "greensboro_substation_capacity_inputs.xlsx"
SUBSTATION_CAPACITY_SHEET = "Substation_Capacity"

# Optional reliability audit. It is REPORTING ONLY and never changes the selected MW.
INTERNAL_CONTINGENCY_FILE = "greensboro_internal_path_contingency_audit.xlsx"
INTERNAL_CONTINGENCY_SHEETS = ["Candidate_Summary", "Candidate Summary", "Summary"]

# Keep the established workbook name so the separate battery-resilience MILP can continue
# reading Candidate_Optimization without changing its BASE_MILP_FILE setting.
OUTPUT_FILE = "greensboro_final_integrated_milp_results.xlsx"

MODEL_YEAR = 2024
INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0

# Headline existing-system case: NO arbitrary incremental-headroom reserve.
BASE_THERMAL_HEADROOM_FACTOR = 1.00

# Sensitivities only. These do not replace the 1.00 headline result.
THERMAL_HEADROOM_SENSITIVITY_FACTORS = [1.00, 0.90, 0.80]

# Explicit station capacity, if available, is used at the stated capacity/headroom with no
# extra arbitrary loading haircut in the headline case.
BASE_SUBSTATION_CAPACITY_FACTOR = 1.00

# Unresolved station capacity is NOT treated as zero. ALLOW_UPPER_BOUND keeps the feeder-
# thermal result but clearly labels it as an upper bound with respect to station/source
# capacity. EXCLUDE can be used later if a strict station-resolved-only answer is desired.
UNRESOLVED_SUBSTATION_POLICY = "ALLOW_UPPER_BOUND"  # ALLOW_UPPER_BOUND or EXCLUDE
STRICT_FINAL_MODE = False

# Candidate/audit validation.
FIRM_HOSTING_VALIDATION_TOLERANCE_MW = 0.01
SITE_CAPACITY_TIE_TOLERANCE_MW = 0.001
USE_COMMON_TIMESTAMP_INTERSECTION = True

# These are reporting flags, not optimization credits.
ENABLE_RESOURCE_CREDIT = False
ENABLE_REROUTING_CREDIT = False

SOLVER_CANDIDATES = ["appsi_highs", "highs", "cbc", "glpk"]


# ======================================================================================
# HELPERS
# ======================================================================================

def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def truthy(value):
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def safe_float(value, default=np.nan):
    if value is None or pd.isna(value):
        return default
    try:
        return float(value)
    except Exception:
        return default


def choose_solver():
    for solver_name in SOLVER_CANDIDATES:
        try:
            solver = pyo.SolverFactory(solver_name)
            if solver is not None and solver.available(exception_flag=False):
                return solver_name, solver
        except Exception:
            continue
    raise RuntimeError("No supported solver was found. Recommended installation: pip install pyomo highspy")


def solve_checked(model, solver, label):
    result = solver.solve(model, tee=False)
    termination = str(result.solver.termination_condition)
    if termination.lower() not in {"optimal", "locallyoptimal", "globallyoptimal"}:
        raise RuntimeError(f"{label} did not solve optimally. Termination condition: {termination}")
    return termination


def expected_intervals_for_year(year):
    start = pd.Timestamp(f"{year}-01-01T00:00:00Z")
    end = pd.Timestamp(f"{year + 1}-01-01T00:00:00Z")
    return int((end - start) / pd.Timedelta(minutes=INTERVAL_MINUTES))


def make_excel_safe(df):
    if df is None:
        return pd.DataFrame()
    df = df.copy()
    for column in df.columns:
        if isinstance(df[column].dtype, pd.DatetimeTZDtype):
            df[column] = df[column].dt.tz_localize(None)
        elif pd.api.types.is_datetime64_any_dtype(df[column]):
            try:
                df[column] = pd.to_datetime(df[column], utc=True, errors="coerce").dt.tz_localize(None)
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
        width = min(max(max_length, len(str(column_name))) + 2, 60)
        if pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, decimal_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)
    worksheet.freeze_panes(1, 0)
    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


def first_present_column(df, candidates):
    for column in candidates:
        if column in df.columns:
            return column
    return None


# ======================================================================================
# CANDIDATE POOL
# ======================================================================================

def load_candidates():
    path = Path(AUDIT_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {AUDIT_FILE}. Run thermal_model_audit.py first.")

    raw = pd.read_excel(path, sheet_name=AUDIT_SHEET)

    required = {"CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ReadyForFeederBoundMILP"}
    missing = required - set(raw.columns)
    if missing:
        raise RuntimeError(f"{AUDIT_FILE} is missing required columns: {sorted(missing)}")

    total_rows = len(raw)
    raw["CandidateKey"] = raw["CandidateKey"].astype(str)
    raw["GridID"] = raw["GridID"].astype(str)
    raw["SubstationID"] = raw["SubstationID"].astype(str)
    raw["CandidateLineID"] = raw["CandidateLineID"].astype(str)
    raw["FirmHostingMW"] = pd.to_numeric(raw["FirmHostingMW"], errors="coerce")
    raw["ReadyForFeederBoundMILP"] = raw["ReadyForFeederBoundMILP"].map(truthy)

    ready_rows = int(raw["ReadyForFeederBoundMILP"].sum())
    positive_ready_rows = int((raw["ReadyForFeederBoundMILP"] & raw["FirmHostingMW"].notna() & (raw["FirmHostingMW"] > 0)).sum())

    candidates = raw[raw["ReadyForFeederBoundMILP"] & raw["FirmHostingMW"].notna() & (raw["FirmHostingMW"] > 0)].copy()

    if candidates.empty:
        raise RuntimeError("No MILP-ready candidates with positive FirmHostingMW were found.")

    if candidates["CandidateKey"].duplicated().any():
        duplicates = candidates.loc[candidates["CandidateKey"].duplicated(keep=False), ["CandidateKey", "GridID", "CandidateLineID"]]
        raise RuntimeError("Duplicate CandidateKey values were found:\n" + duplicates.to_string(index=False))

    pool_audit = pd.DataFrame([{
        "AuditFile": AUDIT_FILE,
        "AuditSheet": AUDIT_SHEET,
        "TotalRowsInAuditSheet": total_rows,
        "ReadyForFeederBoundMILPRows": ready_rows,
        "PositiveReadyCandidatesAdmitted": positive_ready_rows,
        "UniqueCandidateFeeders": candidates["GridID"].nunique(),
        "UniqueCandidateSubstations": candidates["SubstationID"].nunique(),
        "CandidatePoolScope": "ALL_POSITIVE_READY_ROWS_PRESENT_IN_AUDIT_SHEET",
        "ImportantScopeNote": "The final site is the maximum among all positive ReadyForFeederBoundMILP rows present in this audit sheet. If the audit sheet itself is a pre-screened subset, the result is only global within that supplied subset."
    }])

    return candidates.reset_index(drop=True), pool_audit


# ======================================================================================
# THERMAL INPUT
# ======================================================================================

def load_thermal_profiles(candidates):
    path = Path(THERMAL_INTERVAL_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {THERMAL_INTERVAL_FILE}. Run thermal_model.py first.")

    df = pd.read_csv(path, compression="gzip")

    required = {
        "TimestampUTC", "CandidateKey", "GridID", "SubstationID", "CandidateLineID",
        "ExistingBreakerMW", "AvailableThermalHostingMW", "RawIncrementalHeadroomMW",
        "LimitingElementID", "BaselinePathRatingExceedance"
    }
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{THERMAL_INTERVAL_FILE} is missing required columns: {sorted(missing)}")

    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["CandidateKey"] = df["CandidateKey"].astype(str)
    df["GridID"] = df["GridID"].astype(str)
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["CandidateLineID"] = df["CandidateLineID"].astype(str)
    df["ExistingBreakerMW"] = pd.to_numeric(df["ExistingBreakerMW"], errors="coerce")
    df["AvailableThermalHostingMW"] = pd.to_numeric(df["AvailableThermalHostingMW"], errors="coerce")
    df["RawIncrementalHeadroomMW"] = pd.to_numeric(df["RawIncrementalHeadroomMW"], errors="coerce")
    df["BaselinePathRatingExceedance"] = df["BaselinePathRatingExceedance"].map(truthy)

    allowed_keys = set(candidates["CandidateKey"])
    df = df[df["CandidateKey"].isin(allowed_keys)].dropna(subset=["TimestampUTC", "AvailableThermalHostingMW"]).copy()
    df["AvailableThermalHostingMW"] = df["AvailableThermalHostingMW"].clip(lower=0.0)

    # A pre-existing path-rating exceedance means there is no defensible incremental hosting
    # credit at that interval.
    df.loc[df["BaselinePathRatingExceedance"], "AvailableThermalHostingMW"] = 0.0

    # If duplicate candidate/timestamp rows exist, retain the lowest hosting value.
    if df.duplicated(subset=["CandidateKey", "TimestampUTC"], keep=False).any():
        df = df.sort_values(["CandidateKey", "TimestampUTC", "AvailableThermalHostingMW"]).drop_duplicates(
            subset=["CandidateKey", "TimestampUTC"], keep="first"
        )

    present_keys = set(df["CandidateKey"])
    missing_keys = sorted(allowed_keys - present_keys)
    if missing_keys:
        raise RuntimeError("The thermal interval file is missing admitted candidates: " + ", ".join(missing_keys))

    expected_intervals = expected_intervals_for_year(MODEL_YEAR)

    counts_before = df.groupby("CandidateKey").size().rename("RawThermalIntervals").reset_index()
    counts_before["RawThermalCoveragePctOfYear"] = counts_before["RawThermalIntervals"] / expected_intervals * 100.0

    if USE_COMMON_TIMESTAMP_INTERSECTION:
        timestamp_candidate_counts = df.groupby("TimestampUTC")["CandidateKey"].nunique()
        common_timestamps = timestamp_candidate_counts[timestamp_candidate_counts == len(candidates)].index
        if len(common_timestamps) == 0:
            raise RuntimeError("No common five-minute timestamp intersection exists across all admitted candidates.")
        df = df[df["TimestampUTC"].isin(common_timestamps)].copy()
    else:
        common_timestamps = pd.DatetimeIndex(sorted(df["TimestampUTC"].unique()))

    counts_after = df.groupby("CandidateKey").size().rename("ModeledCommonIntervals").reset_index()
    profile_audit = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].merge(counts_before, on="CandidateKey", how="left").merge(counts_after, on="CandidateKey", how="left")
    profile_audit["ExpectedFullYearIntervals"] = expected_intervals
    profile_audit["ModeledCommonCoveragePctOfYear"] = profile_audit["ModeledCommonIntervals"] / expected_intervals * 100.0
    profile_audit["CommonTimestampIntersectionUsed"] = USE_COMMON_TIMESTAMP_INTERSECTION

    return df.sort_values(["CandidateKey", "TimestampUTC"]).reset_index(drop=True), profile_audit


# ======================================================================================
# SUBSTATION INPUT
# ======================================================================================

def load_substation_profiles(candidates):
    path = Path(SUBSTATION_PROFILE_FILE)
    if not path.exists():
        return pd.DataFrame(columns=["TimestampUTC", "SubstationID", "ExistingLoadMW"])

    df = pd.read_csv(path, compression="gzip")

    required = {"TimestampUTC", "SubstationID", "ExistingLoadMW"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{SUBSTATION_PROFILE_FILE} is missing required columns: {sorted(missing)}")

    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["ExistingLoadMW"] = pd.to_numeric(df["ExistingLoadMW"], errors="coerce")

    if "CompleteInterval" in df.columns:
        df["CompleteInterval"] = df["CompleteInterval"].map(truthy)
        df = df[df["CompleteInterval"]].copy()

    df = df[df["SubstationID"].isin(set(candidates["SubstationID"]))].dropna(subset=["TimestampUTC", "ExistingLoadMW"]).copy()

    if df.duplicated(subset=["SubstationID", "TimestampUTC"], keep=False).any():
        duplicates = df.loc[df.duplicated(subset=["SubstationID", "TimestampUTC"], keep=False), ["SubstationID", "TimestampUTC"]]
        raise RuntimeError("Duplicate substation/timestamp rows were found. Example:\n" + duplicates.head(20).to_string(index=False))

    return df.sort_values(["SubstationID", "TimestampUTC"]).reset_index(drop=True)


def load_substation_capacities(candidates):
    path = Path(SUBSTATION_CAPACITY_FILE)

    if not path.exists():
        return pd.DataFrame([{
            "SubstationID": substation_id,
            "TotalCapacityMW": np.nan,
            "ExistingCoincidentPeakMW": np.nan,
            "AvailableAdditionalCapacityMW": np.nan,
            "StaticAdditionalHeadroomMW": np.nan,
            "CapacityResolved": False,
            "CapacityMethod": "Unresolved",
            "CapacitySource": "Missing",
            "Notes": "No substation capacity workbook found."
        } for substation_id in sorted(candidates["SubstationID"].unique())])

    df = pd.read_excel(path, sheet_name=SUBSTATION_CAPACITY_SHEET)

    if "SubstationID" not in df.columns:
        raise RuntimeError(f"{SUBSTATION_CAPACITY_FILE} must contain SubstationID.")

    for column in ["TotalCapacityMW", "ExistingCoincidentPeakMW", "AvailableAdditionalCapacityMW"]:
        if column not in df.columns:
            df[column] = np.nan
        df[column] = pd.to_numeric(df[column], errors="coerce")

    if "CapacitySource" not in df.columns:
        df["CapacitySource"] = "Review"

    if "Notes" not in df.columns:
        df["Notes"] = ""

    df["SubstationID"] = df["SubstationID"].astype(str)
    df = df.drop_duplicates(subset=["SubstationID"], keep="last")

    rows = []

    for substation_id in sorted(candidates["SubstationID"].unique()):
        match = df[df["SubstationID"] == substation_id]
        row = match.iloc[0] if not match.empty else pd.Series(dtype=object)

        total_capacity = safe_float(row.get("TotalCapacityMW"))
        existing_peak = safe_float(row.get("ExistingCoincidentPeakMW"))
        direct_headroom = safe_float(row.get("AvailableAdditionalCapacityMW"))
        source = row.get("CapacitySource", "Missing") if not match.empty else "Missing"
        notes = row.get("Notes", "") if not match.empty else ""

        if pd.notna(total_capacity) and total_capacity >= 0:
            method = "TotalCapacityMW_TimeSeries"
            resolved = True
            static_headroom = max(BASE_SUBSTATION_CAPACITY_FACTOR * total_capacity - existing_peak, 0.0) if pd.notna(existing_peak) else np.nan
        elif pd.notna(direct_headroom) and direct_headroom >= 0:
            method = "DirectAvailableAdditionalCapacityMW"
            resolved = True
            static_headroom = direct_headroom
        else:
            method = "Unresolved"
            resolved = False
            static_headroom = np.nan

        rows.append({
            "SubstationID": substation_id,
            "TotalCapacityMW": total_capacity,
            "ExistingCoincidentPeakMW": existing_peak,
            "AvailableAdditionalCapacityMW": direct_headroom,
            "StaticAdditionalHeadroomMW": static_headroom,
            "CapacityResolved": resolved,
            "CapacityMethod": method,
            "CapacitySource": source,
            "Notes": notes
        })

    return pd.DataFrame(rows)


# ======================================================================================
# OPTIONAL RELIABILITY AUDIT - REPORTING ONLY
# ======================================================================================

def load_optional_reliability_audit(candidates):
    path = Path(INTERNAL_CONTINGENCY_FILE)

    if not path.exists():
        return pd.DataFrame([{
            "ReliabilityAuditStatus": "FILE_NOT_FOUND_REPORTING_ONLY",
            "ReliabilityOptimizationCredit": 0.0,
            "Notes": "Internal contingency audit workbook was not found. Reliability receives no optimization credit."
        }])

    workbook = pd.ExcelFile(path)
    selected_sheet = next((sheet for sheet in INTERNAL_CONTINGENCY_SHEETS if sheet in workbook.sheet_names), None)

    if selected_sheet is None:
        return pd.DataFrame([{
            "ReliabilityAuditStatus": "SUMMARY_SHEET_NOT_FOUND_REPORTING_ONLY",
            "ReliabilityOptimizationCredit": 0.0,
            "AvailableSheets": ", ".join(workbook.sheet_names),
            "Notes": "Reliability workbook exists, but no recognized candidate summary sheet was found."
        }])

    df = pd.read_excel(path, sheet_name=selected_sheet)

    if "CandidateKey" in df.columns:
        df["CandidateKey"] = df["CandidateKey"].astype(str)
        df = df[df["CandidateKey"].isin(set(candidates["CandidateKey"]))].copy()

    df["ReliabilityOptimizationCredit"] = 0.0
    df["ReliabilityUse"] = "REPORTING_ONLY_NO_SITE_SELECTION_CREDIT"

    return df


# ======================================================================================
# CANDIDATE CAPACITY EVALUATION
# ======================================================================================

def evaluate_candidates(candidates, thermal_profiles, thermal_profile_audit, substation_profiles, substation_capacities):
    station_lookup = substation_capacities.set_index("SubstationID").to_dict("index")
    profile_lookup = thermal_profile_audit.set_index("CandidateKey").to_dict("index")

    candidate_rows = []
    limiting_rows = []
    errors = []

    for _, candidate in candidates.iterrows():
        candidate_key = str(candidate["CandidateKey"])
        substation_id = str(candidate["SubstationID"])

        thermal = thermal_profiles[thermal_profiles["CandidateKey"] == candidate_key].copy().sort_values("TimestampUTC")

        if thermal.empty:
            errors.append({"CandidateKey": candidate_key, "Stage": "ThermalProfile", "Error": "No modeled thermal intervals."})
            continue

        feeder_firm = float(thermal["AvailableThermalHostingMW"].min())
        audit_firm = float(candidate["FirmHostingMW"])
        firm_difference = feeder_firm - audit_firm
        firm_match = abs(firm_difference) <= FIRM_HOSTING_VALIDATION_TOLERANCE_MW

        minimum_mask = np.isclose(thermal["AvailableThermalHostingMW"].to_numpy(dtype=float), feeder_firm, atol=1e-9, rtol=0.0)
        feeder_limiting = thermal.loc[minimum_mask].copy()

        for _, row in feeder_limiting.iterrows():
            limiting_row = {
                "CandidateKey": candidate_key,
                "GridID": candidate["GridID"],
                "SubstationID": substation_id,
                "CandidateLineID": candidate["CandidateLineID"],
                "ConstraintFamily": "FEEDER_PATH_THERMAL",
                "TimestampUTC": row["TimestampUTC"],
                "AvailableIncrementalHostingMW": safe_float(row.get("AvailableThermalHostingMW")),
                "ExistingBreakerMW": safe_float(row.get("ExistingBreakerMW")),
                "RawIncrementalHeadroomMW": safe_float(row.get("RawIncrementalHeadroomMW")),
                "LimitingElementID": row.get("LimitingElementID", ""),
                "BaselinePathRatingExceedance": truthy(row.get("BaselinePathRatingExceedance")),
                "LimitingElementClass": row.get("LimitingElementClass", row.get("LimitingElementType", "Review - not exported in thermal profile")),
                "LimitingElementCapacityMW": safe_float(row.get("LimitingElementCapacityMW", row.get("LimitingElementRatingMW", np.nan))),
                "LimitingElementRatingA": safe_float(row.get("LimitingElementRatingA", row.get("RatingA", np.nan)))
            }
            limiting_rows.append(limiting_row)

        station = station_lookup.get(substation_id)

        if station is None:
            errors.append({"CandidateKey": candidate_key, "Stage": "Substation", "Error": f"No substation capacity row exists for {substation_id}."})
            continue

        station_resolved = bool(station["CapacityResolved"])
        station_method = str(station["CapacityMethod"])
        station_firm = np.nan
        station_limiting_timestamp = pd.NaT
        station_existing_load_at_limit = np.nan

        if station_resolved and station_method == "TotalCapacityMW_TimeSeries":
            station_profile = substation_profiles[substation_profiles["SubstationID"] == substation_id][["TimestampUTC", "ExistingLoadMW"]].copy()

            if station_profile.empty:
                errors.append({"CandidateKey": candidate_key, "Stage": "SubstationProfile", "Error": f"{substation_id} has TotalCapacityMW but no usable substation load profile."})
                continue

            common_timestamps = thermal[["TimestampUTC"]].drop_duplicates()
            aligned = common_timestamps.merge(station_profile, on="TimestampUTC", how="left")

            if aligned["ExistingLoadMW"].isna().any():
                missing_count = int(aligned["ExistingLoadMW"].isna().sum())
                errors.append({"CandidateKey": candidate_key, "Stage": "SubstationProfile", "Error": f"{substation_id} is missing {missing_count} station-load intervals from the thermal comparison window."})
                continue

            total_capacity = float(station["TotalCapacityMW"])
            aligned["SubstationHeadroomMW"] = (BASE_SUBSTATION_CAPACITY_FACTOR * total_capacity - aligned["ExistingLoadMW"]).clip(lower=0.0)
            station_firm = float(aligned["SubstationHeadroomMW"].min())
            station_min_row = aligned.sort_values(["SubstationHeadroomMW", "TimestampUTC"]).iloc[0]
            station_limiting_timestamp = station_min_row["TimestampUTC"]
            station_existing_load_at_limit = float(station_min_row["ExistingLoadMW"])

            limiting_rows.append({
                "CandidateKey": candidate_key,
                "GridID": candidate["GridID"],
                "SubstationID": substation_id,
                "CandidateLineID": candidate["CandidateLineID"],
                "ConstraintFamily": "EXPLICIT_SUBSTATION_CAPACITY",
                "TimestampUTC": station_limiting_timestamp,
                "AvailableIncrementalHostingMW": station_firm,
                "ExistingBreakerMW": np.nan,
                "RawIncrementalHeadroomMW": np.nan,
                "LimitingElementID": substation_id,
                "BaselinePathRatingExceedance": False,
                "LimitingElementClass": "Substation",
                "LimitingElementCapacityMW": total_capacity,
                "LimitingElementRatingA": np.nan
            })

        elif station_resolved and station_method == "DirectAvailableAdditionalCapacityMW":
            station_firm = float(station["AvailableAdditionalCapacityMW"])

        elif not station_resolved:
            if UNRESOLVED_SUBSTATION_POLICY.upper() == "EXCLUDE":
                station_firm = np.nan
            else:
                station_firm = np.inf

        if np.isfinite(station_firm):
            existing_system_firm = min(feeder_firm, station_firm)
            binding_constraint = "FEEDER_PATH_THERMAL" if feeder_firm <= station_firm + 1e-9 else "EXPLICIT_SUBSTATION_CAPACITY"
        else:
            existing_system_firm = feeder_firm
            binding_constraint = "FEEDER_PATH_THERMAL_WITH_SUBSTATION_UNRESOLVED"

        selection_eligible = firm_match

        if STRICT_FINAL_MODE and not station_resolved:
            selection_eligible = False

        if UNRESOLVED_SUBSTATION_POLICY.upper() == "EXCLUDE" and not station_resolved:
            selection_eligible = False

        profile_info = profile_lookup.get(candidate_key, {})
        status = "FEEDER_AND_STATION_CONSTRAINED_BASE_CASE" if station_resolved else "FEEDER_THERMAL_UPPER_BOUND_STATION_UNRESOLVED"

        candidate_rows.append({
            "CandidateKey": candidate_key,
            "GridID": candidate["GridID"],
            "SubstationID": substation_id,
            "CandidateLineID": candidate["CandidateLineID"],
            "OriginalFirmHostingMW": audit_firm,
            "ThermalProfileFirmHostingMW": feeder_firm,
            "FirmHostingValidationDifferenceMW": firm_difference,
            "FirmHostingValidationMatch": firm_match,
            "FirmHostingValidationToleranceMW": FIRM_HOSTING_VALIDATION_TOLERANCE_MW,
            "ModeledIntervals": int(profile_info.get("ModeledCommonIntervals", len(thermal))),
            "ModeledHoursEquivalent": int(profile_info.get("ModeledCommonIntervals", len(thermal))) * DT_HOURS,
            "RawThermalCoveragePctOfYear": safe_float(profile_info.get("RawThermalCoveragePctOfYear")),
            "ModeledCommonCoveragePctOfYear": safe_float(profile_info.get("ModeledCommonCoveragePctOfYear")),
            "CommonTimestampIntersectionUsed": USE_COMMON_TIMESTAMP_INTERSECTION,
            "ThermalIncrementalReserveFactor": BASE_THERMAL_HEADROOM_FACTOR,
            "SubstationLoadingLimitFraction": BASE_SUBSTATION_CAPACITY_FACTOR,
            "ThermalFirmHostingMW": feeder_firm,
            "ThermalOnlyReservedFirmMW": feeder_firm,
            "SubstationOnlyFirmIncrementalMW": station_firm if np.isfinite(station_firm) else np.nan,
            "CombinedNoFlexFirmMW": existing_system_firm,
            "ExistingSystemFirmMW": existing_system_firm,
            "MaxOptimizedDCMW": existing_system_firm,
            "BindingConstraint": binding_constraint,
            "AnswerStatus": status,
            "SubstationCapacityResolved": station_resolved,
            "SubstationCapacityMethod": station_method,
            "SubstationCapacitySource": station["CapacitySource"],
            "StationLimitingTimestampUTC": station_limiting_timestamp,
            "StationExistingLoadAtLimitingIntervalMW": station_existing_load_at_limit,
            "CapacityGainFromFlexMW": 0.0,
            "BatteryPowerMW": 0.0,
            "BatteryEnergyMWh": 0.0,
            "BatteryThroughputMWh": 0.0,
            "PVCapacityMW": 0.0,
            "PVUsedMWh": 0.0,
            "FlexibleComputeMWh": 0.0,
            "DRMWh": 0.0,
            "EVShiftedMWh": 0.0,
            "BatteryActive": False,
            "PVActive": False,
            "EVActive": False,
            "DRActive": False,
            "FlexibleComputeActive": False,
            "ReliabilityModelEnabled": False,
            "ReliabilityReady": False,
            "ReliabilityCapacityMW": np.nan,
            "ReliabilityOptimizationCreditMW": 0.0,
            "SelectionEligible": selection_eligible
        })

    return pd.DataFrame(candidate_rows), pd.DataFrame(limiting_rows), pd.DataFrame(errors)


# ======================================================================================
# FINAL ONE-SITE MILP
# ======================================================================================

def solve_site_selection(candidate_results, solver, capacity_column="ExistingSystemFirmMW"):
    pool = candidate_results[candidate_results["SelectionEligible"]].copy()

    if pool.empty:
        raise RuntimeError("No candidates are eligible for final selection after input validation.")

    keys = pool["CandidateKey"].tolist()
    capacity_lookup = pool.set_index("CandidateKey")[capacity_column].astype(float).to_dict()

    model = pyo.ConcreteModel()
    model.CANDIDATES = pyo.Set(initialize=keys, ordered=True)
    model.x = pyo.Var(model.CANDIDATES, within=pyo.Binary)
    model.pdc = pyo.Var(model.CANDIDATES, within=pyo.NonNegativeReals)

    model.OneCentralSite = pyo.Constraint(expr=sum(model.x[j] for j in model.CANDIDATES) == 1)
    model.SiteCapacity = pyo.Constraint(model.CANDIDATES, rule=lambda m, j: m.pdc[j] <= float(capacity_lookup[j]) * m.x[j])

    model.TotalSelectedDCMW = pyo.Expression(expr=sum(model.pdc[j] for j in model.CANDIDATES))
    model.MaximizeExistingSystemCapacity = pyo.Objective(expr=model.TotalSelectedDCMW, sense=pyo.maximize)

    termination = solve_checked(model, solver, f"One-site selection using {capacity_column}")
    optimum = float(pyo.value(model.TotalSelectedDCMW))
    selected_key = next(j for j in keys if pyo.value(model.x[j]) >= 0.5)

    return selected_key, optimum, termination


def apply_final_selection(candidate_results, solver):
    selected_key, optimum, termination = solve_site_selection(candidate_results, solver, "ExistingSystemFirmMW")

    out = candidate_results.copy()
    out["Selected"] = out["CandidateKey"].eq(selected_key)
    out["CapacitySelectionOptimumMW"] = optimum
    out["CoOptimalWithinTolerance"] = out["SelectionEligible"] & ((optimum - out["ExistingSystemFirmMW"]).abs() <= SITE_CAPACITY_TIE_TOLERANCE_MW)
    out["SiteCapacityTieToleranceMW"] = SITE_CAPACITY_TIE_TOLERANCE_MW
    out["PrimarySelectionTermination"] = termination
    out["ReliabilitySecondarySelectionUsed"] = False
    out["SecondarySelectionTermination"] = "NotRun_NoReliabilityRanking"

    return out, selected_key


# ======================================================================================
# SENSITIVITY ANALYSIS - SEPARATE FROM HEADLINE BASE CASE
# ======================================================================================

def build_headroom_sensitivity(candidate_results, solver):
    sensitivity_rows = []

    for factor in THERMAL_HEADROOM_SENSITIVITY_FACTORS:
        scenario = candidate_results.copy()
        scenario["SensitivityThermalFirmMW"] = scenario["ThermalFirmHostingMW"] * float(factor)

        def sensitivity_capacity(row):
            station_value = safe_float(row.get("SubstationOnlyFirmIncrementalMW"), np.nan)
            if pd.notna(station_value):
                return min(float(row["SensitivityThermalFirmMW"]), station_value)
            return float(row["SensitivityThermalFirmMW"])

        scenario["SensitivityExistingSystemFirmMW"] = scenario.apply(sensitivity_capacity, axis=1)
        scenario["SensitivityCapacityMW"] = scenario["SensitivityExistingSystemFirmMW"]

        selected_key, optimum, termination = solve_site_selection(
            scenario.rename(columns={"SensitivityCapacityMW": "_SensitivityCapacityMW"}),
            solver,
            capacity_column="_SensitivityCapacityMW"
        )

        for _, row in scenario.iterrows():
            sensitivity_rows.append({
                "ThermalIncrementalHeadroomFactor": float(factor),
                "IsHeadlineBaseCase": abs(float(factor) - BASE_THERMAL_HEADROOM_FACTOR) < 1e-12,
                "SensitivityInterpretation": "SCALING_OF_ALREADY_CALCULATED_INCREMENTAL_HOSTING_NOT_EXACT_EQUIPMENT_UTILIZATION",
                "CandidateKey": row["CandidateKey"],
                "GridID": row["GridID"],
                "SubstationID": row["SubstationID"],
                "CandidateLineID": row["CandidateLineID"],
                "BaseThermalFirmHostingMW": row["ThermalFirmHostingMW"],
                "SensitivityThermalFirmMW": row["SensitivityThermalFirmMW"],
                "SubstationOnlyFirmIncrementalMW": row["SubstationOnlyFirmIncrementalMW"],
                "SensitivityExistingSystemFirmMW": row["SensitivityExistingSystemFirmMW"],
                "SelectedUnderSensitivity": row["CandidateKey"] == selected_key,
                "SensitivityOptimumMW": optimum,
                "SelectionTermination": termination
            })

    return pd.DataFrame(sensitivity_rows)


# ======================================================================================
# SELECTED-SITE INTERVAL AUDIT
# ======================================================================================

def build_selected_site_interval_audit(selected_candidate, thermal_profiles, substation_profiles, substation_row):
    candidate_key = str(selected_candidate["CandidateKey"])
    substation_id = str(selected_candidate["SubstationID"])
    pdc = float(selected_candidate["MaxOptimizedDCMW"])

    data = thermal_profiles[thermal_profiles["CandidateKey"] == candidate_key].copy().sort_values("TimestampUTC")
    data.insert(1, "CandidateKey", candidate_key)
    data.insert(2, "SelectedGridID", selected_candidate["GridID"])
    data.insert(3, "SelectedSubstationID", substation_id)
    data.insert(4, "SelectedCandidateLineID", selected_candidate["CandidateLineID"])

    data["DCNameplateMW"] = pdc
    data["GridImportMW"] = pdc
    data["ThermalConstraintMW"] = data["AvailableThermalHostingMW"]
    data["ThermalReserveSlackMW"] = data["AvailableThermalHostingMW"] - pdc

    if bool(substation_row["CapacityResolved"]) and str(substation_row["CapacityMethod"]) == "TotalCapacityMW_TimeSeries":
        station = substation_profiles[substation_profiles["SubstationID"] == substation_id][["TimestampUTC", "ExistingLoadMW"]].copy()
        data = data.merge(station, on="TimestampUTC", how="left")
        station_limit = BASE_SUBSTATION_CAPACITY_FACTOR * float(substation_row["TotalCapacityMW"])
        data["SubstationCapacityLimitMW"] = station_limit
        data["SubstationIncrementalHeadroomMW"] = station_limit - data["ExistingLoadMW"]
        data["SubstationSlackMW"] = data["SubstationIncrementalHeadroomMW"] - pdc
    elif bool(substation_row["CapacityResolved"]) and str(substation_row["CapacityMethod"]) == "DirectAvailableAdditionalCapacityMW":
        data["ExistingLoadMW"] = np.nan
        data["SubstationCapacityLimitMW"] = np.nan
        data["SubstationIncrementalHeadroomMW"] = float(substation_row["AvailableAdditionalCapacityMW"])
        data["SubstationSlackMW"] = data["SubstationIncrementalHeadroomMW"] - pdc
    else:
        data["ExistingLoadMW"] = np.nan
        data["SubstationCapacityLimitMW"] = np.nan
        data["SubstationIncrementalHeadroomMW"] = np.nan
        data["SubstationSlackMW"] = np.nan

    return data.reset_index(drop=True)


# ======================================================================================
# OUTPUT / ASSUMPTIONS
# ======================================================================================

def build_assumptions(solver_name, candidates, substation_capacities, thermal_profile_audit, reliability_audit):
    return pd.DataFrame([
        {"Item": "Model purpose", "Value": "Existing-system centralized data-center hosting and site selection only."},
        {"Item": "Centralized siting decision", "Value": "Exactly one candidate location is selected."},
        {"Item": "Candidate pool", "Value": f"All {len(candidates)} positive ReadyForFeederBoundMILP rows present in {AUDIT_FILE}:{AUDIT_SHEET} are admitted before validation. The model does not claim to search candidates absent from that audit sheet."},
        {"Item": "Thermal input", "Value": f"Five-minute AvailableThermalHostingMW from {THERMAL_INTERVAL_FILE}."},
        {"Item": "Headline thermal treatment", "Value": "100% of calculated AvailableThermalHostingMW is used. No arbitrary 10% incremental-headroom deduction is applied."},
        {"Item": "90% / 80% values", "Value": "Reported only as separate planning sensitivities. They scale already-calculated incremental hosting and are NOT exact 90%/80% equipment-nameplate utilization constraints."},
        {"Item": "Firm-hosting validation", "Value": f"Audit FirmHostingMW is compared with the minimum interval AvailableThermalHostingMW. Candidates differing by more than {FIRM_HOSTING_VALIDATION_TOLERANCE_MW:.3f} MW are not eligible for final selection until reviewed."},
        {"Item": "Timestamp comparison", "Value": f"Common candidate timestamp intersection used={USE_COMMON_TIMESTAMP_INTERSECTION}. This prevents one candidate from benefiting from a different set of missing five-minute intervals."},
        {"Item": "Thermal profile coverage", "Value": f"Coverage is reported against the complete {MODEL_YEAR} five-minute calendar ({expected_intervals_for_year(MODEL_YEAR):,} intervals). No missing interval is silently treated as zero load or infinite headroom."},
        {"Item": "Baseline path exceedance", "Value": "Any interval flagged BaselinePathRatingExceedance receives 0 MW incremental hosting credit."},
        {"Item": "Substation capacity", "Value": "Only explicit TotalCapacityMW or AvailableAdditionalCapacityMW values constrain the model. No feeder-breaker sum or inferred transformer proxy is substituted for unresolved station/source capacity."},
        {"Item": "Headline station factor", "Value": f"{BASE_SUBSTATION_CAPACITY_FACTOR:.2f}. If an explicit station capacity is supplied, it is used as stated in the headline case rather than applying an unsupported planning haircut."},
        {"Item": "Unresolved substation policy", "Value": UNRESOLVED_SUBSTATION_POLICY + ". Under ALLOW_UPPER_BOUND the feeder/path thermal result is retained but explicitly labeled an upper bound with respect to station/source capacity."},
        {"Item": "Battery / PV / EV / DR / flexible compute", "Value": "Zero optimization credit in MILP 1. These resources belong in separate counterfactual upgrade/resilience analyses."},
        {"Item": "Reliability / rerouting", "Value": "Zero optimization credit in MILP 1. Internal contingency results may be reported from the optional audit workbook but do not create a hidden penalty, score, or secondary ranking."},
        {"Item": "AC power flow", "Value": "Not claimed. R/X/transformer-impedance data are not available for a defensible AC power-flow model."},
        {"Item": "Power factor sensitivity", "Value": "Not recomputed in this script because line/transformer capacity was already calculated upstream. Any PF sensitivity should rerun thermal_model.py rather than rescale final MW after the fact."},
        {"Item": "Limiting-equipment audit", "Value": "Minimum-hosting timestamps and LimitingElementID are exported. Element class/rating are included only if the thermal interval file actually exports them; otherwise they remain Review/NaN rather than being invented."},
        {"Item": "Reliability audit rows loaded", "Value": len(reliability_audit)},
        {"Item": "Resolved substations", "Value": f"{int(substation_capacities['CapacityResolved'].sum())}/{len(substation_capacities)}"},
        {"Item": "Solver", "Value": solver_name}
    ])


def main():
    heading("GREENSBORO FINAL EXISTING-SYSTEM SINGLE-SITE DATA-CENTER MILP")
    print("Headline case uses the full calculated feeder/path thermal hosting with no arbitrary 10% headroom deduction.")
    print("Battery, PV, EV shifting, DR, flexible compute, and rerouting receive zero capacity credit in this existing-system model.")

    candidates, candidate_pool_audit = load_candidates()
    thermal_profiles, thermal_profile_audit = load_thermal_profiles(candidates)
    substation_profiles = load_substation_profiles(candidates)
    substation_capacities = load_substation_capacities(candidates)
    reliability_audit = load_optional_reliability_audit(candidates)
    solver_name, solver = choose_solver()

    heading("1. INPUT / CANDIDATE AUDIT")
    print(f"Candidates admitted from audit sheet: {len(candidates)}")
    print(f"Candidate feeders represented: {candidates['GridID'].nunique()}")
    print(f"Candidate substations represented: {candidates['SubstationID'].nunique()}")
    print(f"Common thermal intervals used per candidate: {int(thermal_profile_audit['ModeledCommonIntervals'].min()):,}")
    print(f"Common thermal coverage of {MODEL_YEAR}: {thermal_profile_audit['ModeledCommonCoveragePctOfYear'].min():.3f}%")
    print(f"Resolved substation capacities: {int(substation_capacities['CapacityResolved'].sum())}/{len(substation_capacities)}")
    print(f"Headline thermal incremental-headroom factor: {BASE_THERMAL_HEADROOM_FACTOR:.0%}")
    print(f"Resource capacity credit: {ENABLE_RESOURCE_CREDIT}")
    print(f"Rerouting/reliability capacity credit: {ENABLE_REROUTING_CREDIT}")
    print(f"Solver: {solver_name}")

    if len(candidates) <= 10:
        print("IMPORTANT CANDIDATE-POOL NOTE: The audit sheet contains 10 or fewer admitted candidates. The selected site is therefore the maximum within that supplied audit population; verify upstream screening if you intend to claim a global maximum across every raw GIS candidate.")

    if not substation_capacities["CapacityResolved"].all():
        print("IMPORTANT STATION NOTE: At least one candidate substation lacks explicit defensible capacity. Those results remain upper bounds with respect to station/source capacity.")

    heading("2. EXISTING-SYSTEM CANDIDATE CAPACITY")
    candidate_results, limiting_intervals, errors = evaluate_candidates(
        candidates=candidates,
        thermal_profiles=thermal_profiles,
        thermal_profile_audit=thermal_profile_audit,
        substation_profiles=substation_profiles,
        substation_capacities=substation_capacities
    )

    if candidate_results.empty:
        raise RuntimeError("No candidate completed the existing-system capacity evaluation.")

    for _, row in candidate_results.sort_values("ExistingSystemFirmMW", ascending=False).iterrows():
        print(f"{row['GridID']} | {row['CandidateLineID']} | feeder firm={row['ThermalFirmHostingMW']:.3f} MW | existing-system firm={row['ExistingSystemFirmMW']:.3f} MW | station={row['SubstationCapacityMethod']} | eligible={row['SelectionEligible']}")

    heading("3. FINAL ONE-SITE MILP")
    selection_df, selected_key = apply_final_selection(candidate_results, solver)
    selected = selection_df[selection_df["CandidateKey"] == selected_key].iloc[0]

    print(f"Selected candidate: {selected['GridID']} | {selected['CandidateLineID']} | {selected['SubstationID']}")
    print(f"Selected existing-system centralized DC capacity: {selected['MaxOptimizedDCMW']:.3f} MW")
    print(f"Binding interpretation: {selected['BindingConstraint']}")
    print(f"Answer status: {selected['AnswerStatus']}")

    cooptimal = selection_df[selection_df["CoOptimalWithinTolerance"]]
    if len(cooptimal) > 1:
        print(f"NOTE: {len(cooptimal)} candidates are within {SITE_CAPACITY_TIE_TOLERANCE_MW:.3f} MW of the optimum. No arbitrary reliability/cost score is used to break that near-tie.")

    heading("4. PLANNING SENSITIVITIES")
    sensitivity_df = build_headroom_sensitivity(selection_df, solver)
    sensitivity_summary = sensitivity_df[sensitivity_df["SelectedUnderSensitivity"]].copy().sort_values("ThermalIncrementalHeadroomFactor", ascending=False)

    for _, row in sensitivity_summary.iterrows():
        label = "HEADLINE" if row["IsHeadlineBaseCase"] else "SENSITIVITY"
        print(f"{label} | incremental-headroom factor={row['ThermalIncrementalHeadroomFactor']:.0%} | selected={row['GridID']} | capacity={row['SensitivityOptimumMW']:.3f} MW")

    selected_candidate = selection_df[selection_df["CandidateKey"] == selected_key].iloc[0]
    selected_substation = substation_capacities[substation_capacities["SubstationID"] == selected_candidate["SubstationID"]].iloc[0]
    selected_dispatch = build_selected_site_interval_audit(selected_candidate, thermal_profiles, substation_profiles, selected_substation)

    selected_limiting = limiting_intervals[limiting_intervals["CandidateKey"] == selected_key].copy() if not limiting_intervals.empty else pd.DataFrame()

    if not selected_dispatch.empty:
        min_thermal_slack = float(selected_dispatch["ThermalReserveSlackMW"].min())
        min_station_slack = float(selected_dispatch["SubstationSlackMW"].min()) if selected_dispatch["SubstationSlackMW"].notna().any() else np.nan
    else:
        min_thermal_slack = np.nan
        min_station_slack = np.nan

    answer_status = selected["AnswerStatus"]

    scenario_summary = pd.DataFrame([{
        "AnswerStatus": answer_status,
        "SelectedCandidateKey": selected_key,
        "SelectedGridID": selected["GridID"],
        "SelectedSubstationID": selected["SubstationID"],
        "SelectedCandidateLineID": selected["CandidateLineID"],
        "SelectedOptimizedDCMW": selected["MaxOptimizedDCMW"],
        "SelectedNoFlexFirmMW": selected["CombinedNoFlexFirmMW"],
        "SelectedThermalFirmHostingMW": selected["ThermalFirmHostingMW"],
        "SelectedSubstationFirmIncrementalMW": selected["SubstationOnlyFirmIncrementalMW"],
        "BindingConstraint": selected["BindingConstraint"],
        "SelectedSubstationCapacityResolved": selected["SubstationCapacityResolved"],
        "ThermalIncrementalReserveFactor": BASE_THERMAL_HEADROOM_FACTOR,
        "SubstationLoadingLimitFraction": BASE_SUBSTATION_CAPACITY_FACTOR,
        "CapacityGainFromFlexMW": 0.0,
        "BatteryPowerMW": 0.0,
        "BatteryEnergyMWh": 0.0,
        "PVCapacityMW": 0.0,
        "PVUsedMWh": 0.0,
        "EVShiftedMWh": 0.0,
        "DRMWh": 0.0,
        "FlexibleComputeMWh": 0.0,
        "ReroutingCreditTreatment": "NO_OPTIMIZATION_CREDIT_REPORTING_ONLY",
        "ReliabilitySecondarySelectionUsed": False,
        "MinimumThermalSlackMW": min_thermal_slack,
        "MinimumSubstationSlackMW": min_station_slack,
        "CandidatePoolSize": len(candidates),
        "ModeledCommonIntervals": int(thermal_profile_audit["ModeledCommonIntervals"].min()),
        "ModeledCommonCoveragePctOfYear": float(thermal_profile_audit["ModeledCommonCoveragePctOfYear"].min()),
        "Solver": solver_name
    }])

    selected_site_output = selection_df[selection_df["Selected"]].copy()
    selected_site_output["MinimumThermalReserveSlackMW"] = min_thermal_slack
    selected_site_output["MinimumSubstationSlackMW"] = min_station_slack

    assumptions = build_assumptions(
        solver_name=solver_name,
        candidates=candidates,
        substation_capacities=substation_capacities,
        thermal_profile_audit=thermal_profile_audit,
        reliability_audit=reliability_audit
    )

    selection_df = selection_df.sort_values(["Selected", "SelectionEligible", "MaxOptimizedDCMW"], ascending=[False, False, False]).reset_index(drop=True)
    limiting_intervals = limiting_intervals.sort_values(["CandidateKey", "ConstraintFamily", "TimestampUTC"]).reset_index(drop=True) if not limiting_intervals.empty else limiting_intervals
    selected_dispatch = selected_dispatch.sort_values("TimestampUTC").reset_index(drop=True)
    errors_df = errors.copy()

    workbook_frames = {
        "Scenario_Summary": scenario_summary,
        "Selected_Site": selected_site_output,
        "Candidate_Optimization": selection_df,
        "Selected_5Min_Dispatch": selected_dispatch,
        "Selected_Limiting": selected_limiting,
        "Candidate_Limiting": limiting_intervals,
        "Planning_Sensitivities": sensitivity_df,
        "Sensitivity_Summary": sensitivity_summary,
        "Candidate_Pool_Audit": candidate_pool_audit,
        "Thermal_Profile_Audit": thermal_profile_audit,
        "Substation_Capacities": substation_capacities,
        "Reliability_Audit": reliability_audit,
        "Errors": errors_df,
        "Assumptions": assumptions
    }

    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
        for sheet_name, frame in workbook_frames.items():
            safe_frame = make_excel_safe(frame)
            safe_frame.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            format_excel(writer, sheet_name[:31], safe_frame)

    heading("FINAL EXISTING-SYSTEM MILP RUN COMPLETE")
    print(f"Results workbook: {Path(OUTPUT_FILE).resolve()}")
    print(f"Selected site: {selected['GridID']} | {selected['CandidateLineID']}")
    print(f"Headline existing-system capacity: {selected['MaxOptimizedDCMW']:.3f} MW")
    print(f"Headline thermal factor: {BASE_THERMAL_HEADROOM_FACTOR:.0%} of calculated incremental hosting")
    print(f"Answer status: {answer_status}")
    print("Battery/PV/EV/DR/flexible-compute credit: NONE")
    print("Rerouting/reliability optimization credit: NONE")

    if not bool(selected["SubstationCapacityResolved"]):
        print("IMPORTANT: The selected-site MW is a feeder/path thermal upper bound with respect to unresolved substation/source capacity. Do not describe it as fully station-constrained until explicit station capacity/headroom is supplied.")

    print("The 90% and 80% cases are sensitivity outputs only; they are not the headline answer and are not exact equipment-utilization constraints.")


if __name__ == "__main__":
    main()
