import math
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyomo.environ as pyo
except ImportError as exc:
    raise ImportError("Pyomo is required. Install with: pip install pyomo highspy") from exc


# ======================================================================================
# PURPOSE:
# Comprehensive single-site Greensboro data-center MILP.
#
# This script keeps the final existing-system thermal methodology but can additionally
# evaluate battery, new local PV, flexible compute, EV shifting, substation DR, validated
# feeder-tie/N-1 contingency support, and validated AC/protection engineering envelopes.
#
# IMPORTANT:
# - The headline existing-system answer remains the no-resource thermal result.
# - Optional resources are scenario additions, not evidence that the existing grid already
#   has those capabilities.
# - AC voltage/reactive/loss and protection/short-circuit physics are NOT invented here.
#   They enter only through externally validated engineering limits/envelopes.
# - The script itself has no top-10 restriction. It evaluates every candidate present in
#   the upstream MILP_Readiness sheet that also has a five-minute thermal profile.
# ======================================================================================

AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
AUDIT_SHEET = "MILP_Readiness"
THERMAL_INTERVAL_FILE = "greensboro_5min_candidate_hosting_2024.csv.gz"
SUBSTATION_PROFILE_FILE = "greensboro_substation_5min_profiles_2024.csv.gz"
SUBSTATION_CAPACITY_FILE = "greensboro_substation_capacity_inputs.xlsx"
SUBSTATION_CAPACITY_SHEET = "Substation_Capacity"
RESOURCE_INPUT_FILE = "greensboro_candidate_resource_inputs.xlsx"
RESOURCE_INPUT_SHEET = "Candidate_Resources"
RELIABILITY_INPUT_FILE = "greensboro_reliability_rerouting_inputs.xlsx"
RELIABILITY_INPUT_SHEET = "Reliability_Inputs"
PV_SHAPE_FILE = "greensboro_pv_availability_shape_2024.csv.gz"
EV_PROFILE_FILE = "greensboro_substation_ev_flex_profiles_2024.csv.gz"
DR_PROFILE_FILE = "greensboro_substation_dr_profiles_2024.csv.gz"

# Optional engineering-study handoff files. These are intentionally external interfaces:
# the MILP will use them if populated, but it will not fabricate AC power-flow, voltage,
# reactive-power, loss, protection, or short-circuit results from missing R/X/Z/settings.
ENGINEERING_INPUT_FILE = "greensboro_candidate_engineering_limits.xlsx"
ENGINEERING_INPUT_SHEET = "Engineering_Limits"
AC_LIMIT_PROFILE_FILE = "greensboro_candidate_ac_limits_2024.csv.gz"

OUTPUT_FILE = "greensboro_comprehensive_all_resources_milp_results.xlsx"

INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0
MIN_PROFILE_INTERSECTION_PCT = 95.0
OPTIMALITY_TOLERANCE_MW = 1e-6
SOLVER_CANDIDATES = ["appsi_highs", "highs", "cbc", "glpk"]

# Final base case uses 100% of the already-calculated incremental thermal hosting.
# 90% / 80% planning sensitivities belong in the existing-system reporting model, not as
# hidden deductions inside this all-resource scenario.
THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR = 1.00
SUBSTATION_LOADING_LIMIT_FRACTION = 1.00
UNRESOLVED_SUBSTATION_POLICY = "ALLOW_UPPER_BOUND"  # ALLOW_UPPER_BOUND or EXCLUDE
STRICT_FINAL_MODE = False

# All resource families are enabled structurally. They receive zero credit unless their
# required input values/profiles are actually present. This prevents the MILP from inventing
# battery, PV, EV, DR, or reliability capability.
ENABLE_BATTERY = True
ENABLE_FLEXIBLE_COMPUTE = True
ENABLE_INCREMENTAL_LOCAL_PV = True
ENABLE_EV_LOAD_SHIFT = True
ENABLE_SUBSTATION_DR = True  # Receives zero credit unless a validated profile exists.
ENABLE_RELIABILITY = True  # Applied only when ApplyReliabilityConstraint=True and validated capacity is supplied.
ALLOW_BATTERY_FOR_CONTINGENCY_SUPPORT = True

ENABLE_AC_ENGINEERING_LIMITS = True
ENABLE_PROTECTION_LIMITS = True

# If True, candidates explicitly failing a validated protection or short-circuit study are
# excluded from final selection. Missing studies remain unresolved rather than being treated
# as a pass or fail.
EXCLUDE_VALIDATED_ENGINEERING_FAILURES = True

# An optional validated incremental delivery efficiency may represent feeder-to-facility
# real-power losses from an external AC study. 1.0 means no additional loss adjustment.
DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY = 1.0

DEFAULT_BATTERY_CHARGE_EFFICIENCY = 0.95
DEFAULT_BATTERY_DISCHARGE_EFFICIENCY = 0.95
DEFAULT_BATTERY_INITIAL_SOC_FRACTION = 0.90
DEFAULT_BATTERY_MIN_SOC_FRACTION = 0.10
DEFAULT_CRITICAL_LOAD_FRACTION = 1.00
DEFAULT_SWITCHING_DELAY_MINUTES = 0.0
EV_SHIFT_ENERGY_EFFICIENCY = 1.00
EV_REPAYMENT_TIMEZONE = "America/New_York"
PV_AVAILABILITY_CLIP_PU = 1.10
RELIABILITY_CAPACITY_RETENTION_FRACTION = 0.95

# Optional global DR energy cap. Leave NaN to rely only on the time-varying AvailableDRMW
# profile. Set a value only when a defensible program/event-energy limit is available.
DR_MAX_TOTAL_ENERGY_MWH = np.nan

CREATE_INPUT_TEMPLATES_IF_MISSING = True


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


def clip_fraction(value, default):
    value = safe_float(value, default)
    return float(np.clip(value, 0.0, 1.0))


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


def find_contiguous_blocks(timestamps):
    timestamps = pd.DatetimeIndex(timestamps)
    if len(timestamps) == 0:
        return []
    expected_seconds = INTERVAL_MINUTES * 60
    blocks = []
    start = 0
    for i in range(1, len(timestamps)):
        delta_seconds = (timestamps[i] - timestamps[i - 1]).total_seconds()
        if delta_seconds > expected_seconds * 1.5 or delta_seconds <= 0:
            blocks.append((start, i - 1))
            start = i
    blocks.append((start, len(timestamps) - 1))
    return blocks


def create_empty_profile_template(path_string, columns):
    path = Path(path_string)
    if path.exists() or not CREATE_INPUT_TEMPLATES_IF_MISSING:
        return
    pd.DataFrame(columns=columns).to_csv(path, index=False, compression="gzip" if str(path).lower().endswith(".gz") else None)
    print(f"Created optional profile template: {path.resolve()}")


# ======================================================================================
# CORE INPUTS
# ======================================================================================

def load_candidates():
    path = Path(AUDIT_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {AUDIT_FILE}. Run thermal_model_audit.py first.")
    df = pd.read_excel(path, sheet_name=AUDIT_SHEET)
    required = {"CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW", "ReadyForFeederBoundMILP"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{AUDIT_FILE} is missing required columns: {sorted(missing)}")
    df["CandidateKey"] = df["CandidateKey"].astype(str)
    df["GridID"] = df["GridID"].astype(str)
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["CandidateLineID"] = df["CandidateLineID"].astype(str)
    df["FirmHostingMW"] = pd.to_numeric(df["FirmHostingMW"], errors="coerce")
    df["ReadyForFeederBoundMILP"] = df["ReadyForFeederBoundMILP"].map(truthy)
    df = df[df["ReadyForFeederBoundMILP"] & df["FirmHostingMW"].notna() & (df["FirmHostingMW"] > 0)].copy()
    if df.empty:
        raise RuntimeError("No MILP-ready candidates with positive FirmHostingMW were found.")
    if df["CandidateKey"].duplicated().any():
        duplicates = df.loc[df["CandidateKey"].duplicated(keep=False), ["CandidateKey", "GridID", "CandidateLineID"]]
        raise RuntimeError("Duplicate CandidateKey values were found:\n" + duplicates.to_string(index=False))
    return df.reset_index(drop=True)


def load_thermal_profiles(candidates):
    path = Path(THERMAL_INTERVAL_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {THERMAL_INTERVAL_FILE}. Run thermal_model.py first.")
    df = pd.read_csv(path, compression="gzip")
    required = {"TimestampUTC", "CandidateKey", "GridID", "SubstationID", "CandidateLineID", "ExistingBreakerMW", "AvailableThermalHostingMW", "RawIncrementalHeadroomMW", "LimitingElementID", "BaselinePathRatingExceedance"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{THERMAL_INTERVAL_FILE} is missing required columns: {sorted(missing)}")
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["CandidateKey"] = df["CandidateKey"].astype(str)
    df["GridID"] = df["GridID"].astype(str)
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["AvailableThermalHostingMW"] = pd.to_numeric(df["AvailableThermalHostingMW"], errors="coerce")
    df["RawIncrementalHeadroomMW"] = pd.to_numeric(df["RawIncrementalHeadroomMW"], errors="coerce")
    df["ExistingBreakerMW"] = pd.to_numeric(df["ExistingBreakerMW"], errors="coerce")
    df["BaselinePathRatingExceedance"] = df["BaselinePathRatingExceedance"].map(truthy)
    df = df[df["CandidateKey"].isin(set(candidates["CandidateKey"]))].dropna(subset=["TimestampUTC", "AvailableThermalHostingMW"]).copy()
    df["AvailableThermalHostingMW"] = df["AvailableThermalHostingMW"].clip(lower=0.0)
    df.loc[df["BaselinePathRatingExceedance"], "AvailableThermalHostingMW"] = 0.0
    if df.duplicated(subset=["CandidateKey", "TimestampUTC"], keep=False).any():
        df = df.sort_values("AvailableThermalHostingMW").drop_duplicates(subset=["CandidateKey", "TimestampUTC"], keep="first")
    return df.sort_values(["CandidateKey", "TimestampUTC"]).reset_index(drop=True)


def load_substation_profiles(candidates):
    path = Path(SUBSTATION_PROFILE_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {SUBSTATION_PROFILE_FILE}. Run substation_milp_constraint.py first.")
    df = pd.read_csv(path, compression="gzip")
    required = {"TimestampUTC", "SubstationID", "ExistingLoadMW", "ExpectedFeederCount", "LoadedFeederCount", "CompleteFeederCount", "CompleteInterval"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{SUBSTATION_PROFILE_FILE} is missing required columns: {sorted(missing)}")
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["ExistingLoadMW"] = pd.to_numeric(df["ExistingLoadMW"], errors="coerce")
    df["CompleteInterval"] = df["CompleteInterval"].map(truthy)
    df = df[df["SubstationID"].isin(set(candidates["SubstationID"])) & df["CompleteInterval"]].dropna(subset=["TimestampUTC", "ExistingLoadMW"]).copy()
    if df.duplicated(subset=["SubstationID", "TimestampUTC"], keep=False).any():
        duplicates = df.loc[df.duplicated(subset=["SubstationID", "TimestampUTC"], keep=False), ["SubstationID", "TimestampUTC"]]
        raise RuntimeError("Duplicate substation/timestamp rows were found. Example:\n" + duplicates.head(20).to_string(index=False))
    return df.sort_values(["SubstationID", "TimestampUTC"]).reset_index(drop=True)


def load_substation_capacities(candidates):
    path = Path(SUBSTATION_CAPACITY_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {SUBSTATION_CAPACITY_FILE}. Run substation_milp_constraint.py first.")
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
            static_headroom = max(SUBSTATION_LOADING_LIMIT_FRACTION * total_capacity - existing_peak, 0.0) if pd.notna(existing_peak) else np.nan
        elif pd.notna(direct_headroom) and direct_headroom >= 0:
            method = "DirectAvailableAdditionalCapacityMW"
            resolved = True
            static_headroom = direct_headroom
        else:
            method = "Unresolved"
            resolved = False
            static_headroom = np.nan
        rows.append({"SubstationID": substation_id, "TotalCapacityMW": total_capacity, "ExistingCoincidentPeakMW": existing_peak, "AvailableAdditionalCapacityMW": direct_headroom, "StaticAdditionalHeadroomMW": static_headroom, "CapacityResolved": resolved, "CapacityMethod": method, "CapacitySource": source, "Notes": notes})
    return pd.DataFrame(rows)


# ======================================================================================
# RESOURCE INPUTS
# ======================================================================================

def create_resource_template(candidates):
    path = Path(RESOURCE_INPUT_FILE)
    if path.exists() or not CREATE_INPUT_TEMPLATES_IF_MISSING:
        return
    df = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()
    df["BatteryMaxPowerMW"] = np.nan
    df["BatteryMaxEnergyMWh"] = np.nan
    df["BatteryChargeEfficiency"] = DEFAULT_BATTERY_CHARGE_EFFICIENCY
    df["BatteryDischargeEfficiency"] = DEFAULT_BATTERY_DISCHARGE_EFFICIENCY
    df["BatteryInitialSOCFraction"] = DEFAULT_BATTERY_INITIAL_SOC_FRACTION
    df["BatteryMinSOCFraction"] = DEFAULT_BATTERY_MIN_SOC_FRACTION
    df["ProposedPVCapacityMW"] = 0.0
    df["FlexibleComputeMaxReductionFraction"] = np.nan
    df["FlexibleComputeMaxEnergyFraction"] = np.nan
    df["ResourceSource"] = "Review"
    df["Notes"] = "Enter explicit scenario design/program caps. BatteryMaxPowerMW/BatteryMaxEnergyMWh and ProposedPVCapacityMW are maximum design bounds; the MILP minimizes installed amount after maximizing DC MW. Blank/zero values receive no credit."
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name=RESOURCE_INPUT_SHEET, index=False)
        format_excel(writer, RESOURCE_INPUT_SHEET, df)
    print(f"Created candidate resource-input template: {path.resolve()}")


def load_resource_inputs(candidates):
    create_resource_template(candidates)
    base = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()
    path = Path(RESOURCE_INPUT_FILE)
    if not path.exists():
        df = base.copy()
    else:
        raw = pd.read_excel(path, sheet_name=RESOURCE_INPUT_SHEET)
        if "CandidateKey" not in raw.columns:
            raise RuntimeError(f"{RESOURCE_INPUT_FILE} must contain CandidateKey.")
        raw["CandidateKey"] = raw["CandidateKey"].astype(str)
        raw = raw.drop_duplicates(subset=["CandidateKey"], keep="last")
        keep = ["CandidateKey", "BatteryMaxPowerMW", "BatteryMaxEnergyMWh", "BatteryChargeEfficiency", "BatteryDischargeEfficiency", "BatteryInitialSOCFraction", "BatteryMinSOCFraction", "ProposedPVCapacityMW", "FlexibleComputeMaxReductionFraction", "FlexibleComputeMaxEnergyFraction", "ResourceSource", "Notes"]
        for column in keep:
            if column not in raw.columns:
                raw[column] = np.nan if column not in {"ResourceSource", "Notes"} else ""
        df = base.merge(raw[keep], on="CandidateKey", how="left")
    numeric_defaults = {
        "BatteryMaxPowerMW": 0.0,
        "BatteryMaxEnergyMWh": 0.0,
        "BatteryChargeEfficiency": DEFAULT_BATTERY_CHARGE_EFFICIENCY,
        "BatteryDischargeEfficiency": DEFAULT_BATTERY_DISCHARGE_EFFICIENCY,
        "BatteryInitialSOCFraction": DEFAULT_BATTERY_INITIAL_SOC_FRACTION,
        "BatteryMinSOCFraction": DEFAULT_BATTERY_MIN_SOC_FRACTION,
        "ProposedPVCapacityMW": 0.0,
        "FlexibleComputeMaxReductionFraction": 0.0,
        "FlexibleComputeMaxEnergyFraction": 0.0
    }
    for column, default in numeric_defaults.items():
        if column not in df.columns:
            df[column] = default
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(default)
    for column in ["BatteryMaxPowerMW", "BatteryMaxEnergyMWh", "ProposedPVCapacityMW"]:
        df[column] = df[column].clip(lower=0.0)
    for column in ["BatteryChargeEfficiency", "BatteryDischargeEfficiency"]:
        df[column] = df[column].clip(lower=1e-6, upper=1.0)
    for column in ["BatteryInitialSOCFraction", "BatteryMinSOCFraction", "FlexibleComputeMaxReductionFraction", "FlexibleComputeMaxEnergyFraction"]:
        df[column] = df[column].clip(lower=0.0, upper=1.0)
    df["BatteryInitialSOCFraction"] = np.maximum(df["BatteryInitialSOCFraction"], df["BatteryMinSOCFraction"])
    if "ResourceSource" not in df.columns:
        df["ResourceSource"] = "Missing"
    if "Notes" not in df.columns:
        df["Notes"] = ""
    return df

def create_reliability_template(candidates):
    path = Path(RELIABILITY_INPUT_FILE)
    if path.exists() or not CREATE_INPUT_TEMPLATES_IF_MISSING:
        return
    df = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()
    df["ApplyReliabilityConstraint"] = False
    df["NMinus1CapacityMW"] = np.nan
    df["RerouteCapacityMW"] = np.nan
    df["CriticalLoadFraction"] = DEFAULT_CRITICAL_LOAD_FRACTION
    df["ContingencyDurationHours"] = np.nan
    df["SwitchingDelayMinutes"] = DEFAULT_SWITCHING_DELAY_MINUTES
    df["ReliabilitySource"] = "Review"
    df["Notes"] = "Set ApplyReliabilityConstraint=True only for a validated contingency study. NMinus1CapacityMW is preferred; otherwise RerouteCapacityMW may be used. Do not guess values."
    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name=RELIABILITY_INPUT_SHEET, index=False)
        format_excel(writer, RELIABILITY_INPUT_SHEET, df)
    print(f"Created reliability/rerouting input template: {path.resolve()}")


def load_reliability_inputs(candidates):
    base = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()

    if not ENABLE_RELIABILITY:
        base["ApplyReliabilityConstraint"] = False
        base["NMinus1CapacityMW"] = np.nan
        base["RerouteCapacityMW"] = np.nan
        base["CriticalLoadFraction"] = DEFAULT_CRITICAL_LOAD_FRACTION
        base["ContingencyDurationHours"] = np.nan
        base["SwitchingDelayMinutes"] = DEFAULT_SWITCHING_DELAY_MINUTES
        base["ReliabilitySource"] = "Disabled"
        base["ReliabilityNotes"] = "Reliability/rerouting constraint disabled."
        base["ReliabilityCapacityMW"] = np.nan
        base["ReliabilityDataReady"] = False
        return base

    create_reliability_template(candidates)
    path = Path(RELIABILITY_INPUT_FILE)

    if not path.exists():
        raw = pd.DataFrame(columns=["CandidateKey"])
    else:
        raw = pd.read_excel(path, sheet_name=RELIABILITY_INPUT_SHEET)

    if not raw.empty and "CandidateKey" not in raw.columns:
        raise RuntimeError(f"{RELIABILITY_INPUT_FILE} must contain CandidateKey.")

    if not raw.empty:
        raw["CandidateKey"] = raw["CandidateKey"].astype(str)
        raw = raw.drop_duplicates(subset=["CandidateKey"], keep="last")

    required_columns = [
        "ApplyReliabilityConstraint", "NMinus1CapacityMW", "RerouteCapacityMW",
        "CriticalLoadFraction", "ContingencyDurationHours", "SwitchingDelayMinutes",
        "ReliabilitySource", "Notes"
    ]
    for column in required_columns:
        if column not in raw.columns:
            if column == "ApplyReliabilityConstraint":
                raw[column] = False
            elif column in {"ReliabilitySource", "Notes"}:
                raw[column] = ""
            else:
                raw[column] = np.nan

    merged = base.merge(raw[required_columns + ["CandidateKey"]], on="CandidateKey", how="left")
    merged["ApplyReliabilityConstraint"] = merged["ApplyReliabilityConstraint"].map(truthy).fillna(False)

    for column in ["NMinus1CapacityMW", "RerouteCapacityMW", "ContingencyDurationHours", "SwitchingDelayMinutes"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")

    merged["CriticalLoadFraction"] = pd.to_numeric(merged["CriticalLoadFraction"], errors="coerce").fillna(DEFAULT_CRITICAL_LOAD_FRACTION).clip(lower=0.0, upper=1.0)
    merged["SwitchingDelayMinutes"] = merged["SwitchingDelayMinutes"].fillna(DEFAULT_SWITCHING_DELAY_MINUTES).clip(lower=0.0)
    merged["ReliabilityCapacityMW"] = np.where(merged["NMinus1CapacityMW"].notna(), merged["NMinus1CapacityMW"], merged["RerouteCapacityMW"])

    merged["ReliabilityDataReady"] = (
        merged["ApplyReliabilityConstraint"]
        & merged["ReliabilityCapacityMW"].notna()
        & (merged["ReliabilityCapacityMW"] >= 0.0)
    )

    merged = merged.rename(columns={"Notes": "ReliabilityNotes"})
    return merged



# ======================================================================================
# VALIDATED AC / PROTECTION ENGINEERING INPUTS
# ======================================================================================

def create_engineering_template(candidates):
    path = Path(ENGINEERING_INPUT_FILE)
    if path.exists() or not CREATE_INPUT_TEMPLATES_IF_MISSING:
        return

    df = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()
    df["ApplyACConstraint"] = False
    df["ACStaticAdditionalMW"] = np.nan
    df["IncrementalDeliveryEfficiency"] = DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY
    df["DataCenterPowerFactor"] = 0.95
    df["ReactivePowerLimitMVAr"] = np.nan
    df["ACStudySource"] = "Review"
    df["ApplyProtectionConstraint"] = False
    df["ProtectionStudyPass"] = np.nan
    df["ShortCircuitStudyPass"] = np.nan
    df["ProtectionMaxAdditionalMW"] = np.nan
    df["ProtectionStudySource"] = "Review"
    df["Notes"] = (
        "Populate only from a validated AC power-flow / voltage / reactive / loss / protection / "
        "short-circuit study. Do not estimate these values from the MILP itself."
    )

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        df.to_excel(writer, sheet_name=ENGINEERING_INPUT_SHEET, index=False)
        format_excel(writer, ENGINEERING_INPUT_SHEET, df)

    print(f"Created engineering-limit template: {path.resolve()}")


def load_engineering_inputs(candidates):
    create_engineering_template(candidates)
    base = candidates[["CandidateKey", "GridID", "SubstationID", "CandidateLineID"]].copy()
    path = Path(ENGINEERING_INPUT_FILE)

    if not path.exists():
        raw = pd.DataFrame(columns=["CandidateKey"])
    else:
        raw = pd.read_excel(path, sheet_name=ENGINEERING_INPUT_SHEET)

    if not raw.empty and "CandidateKey" not in raw.columns:
        raise RuntimeError(f"{ENGINEERING_INPUT_FILE} must contain CandidateKey.")

    if not raw.empty:
        raw["CandidateKey"] = raw["CandidateKey"].astype(str)
        raw = raw.drop_duplicates(subset=["CandidateKey"], keep="last")

    columns = [
        "ApplyACConstraint", "ACStaticAdditionalMW", "IncrementalDeliveryEfficiency",
        "DataCenterPowerFactor", "ReactivePowerLimitMVAr", "ACStudySource",
        "ApplyProtectionConstraint", "ProtectionStudyPass", "ShortCircuitStudyPass",
        "ProtectionMaxAdditionalMW", "ProtectionStudySource", "Notes"
    ]

    for column in columns:
        if column not in raw.columns:
            if column in {"ApplyACConstraint", "ApplyProtectionConstraint"}:
                raw[column] = False
            elif column in {"ACStudySource", "ProtectionStudySource", "Notes"}:
                raw[column] = ""
            elif column == "IncrementalDeliveryEfficiency":
                raw[column] = DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY
            elif column == "DataCenterPowerFactor":
                raw[column] = 0.95
            else:
                raw[column] = np.nan

    merged = base.merge(raw[["CandidateKey"] + columns], on="CandidateKey", how="left")

    for column in ["ApplyACConstraint", "ApplyProtectionConstraint"]:
        merged[column] = merged[column].map(truthy).fillna(False)

    for column in ["ACStaticAdditionalMW", "IncrementalDeliveryEfficiency", "DataCenterPowerFactor", "ReactivePowerLimitMVAr", "ProtectionMaxAdditionalMW"]:
        merged[column] = pd.to_numeric(merged[column], errors="coerce")

    merged["IncrementalDeliveryEfficiency"] = merged["IncrementalDeliveryEfficiency"].fillna(DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY).clip(lower=1e-6, upper=1.0)
    merged["DataCenterPowerFactor"] = merged["DataCenterPowerFactor"].fillna(0.95).clip(lower=1e-6, upper=1.0)

    for column in ["ProtectionStudyPass", "ShortCircuitStudyPass"]:
        merged[column] = merged[column].apply(lambda value: truthy(value) if pd.notna(value) else np.nan)

    merged["ACStaticAdditionalMW"] = merged["ACStaticAdditionalMW"].clip(lower=0.0)
    merged["ReactivePowerLimitMVAr"] = merged["ReactivePowerLimitMVAr"].clip(lower=0.0)
    merged["ProtectionMaxAdditionalMW"] = merged["ProtectionMaxAdditionalMW"].clip(lower=0.0)
    merged = merged.rename(columns={"Notes": "EngineeringNotes"})

    return merged


def load_ac_limit_profile(candidates):
    if not ENABLE_AC_ENGINEERING_LIMITS:
        return pd.DataFrame(columns=["TimestampUTC", "CandidateKey", "ACAdditionalRealPowerLimitMW"])

    path = Path(AC_LIMIT_PROFILE_FILE)
    if not path.exists():
        create_empty_profile_template(
            AC_LIMIT_PROFILE_FILE,
            ["TimestampUTC", "CandidateKey", "ACAdditionalRealPowerLimitMW"]
        )
        return pd.DataFrame(columns=["TimestampUTC", "CandidateKey", "ACAdditionalRealPowerLimitMW"])

    df = pd.read_csv(path, compression="gzip" if str(path).lower().endswith(".gz") else "infer")
    required = {"TimestampUTC", "CandidateKey", "ACAdditionalRealPowerLimitMW"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{AC_LIMIT_PROFILE_FILE} is missing required columns: {sorted(missing)}")

    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["CandidateKey"] = df["CandidateKey"].astype(str)
    df["ACAdditionalRealPowerLimitMW"] = pd.to_numeric(df["ACAdditionalRealPowerLimitMW"], errors="coerce")
    df = df[
        df["CandidateKey"].isin(set(candidates["CandidateKey"]))
        & df["ACAdditionalRealPowerLimitMW"].notna()
        & (df["ACAdditionalRealPowerLimitMW"] >= 0.0)
    ].dropna(subset=["TimestampUTC"]).copy()

    return df.drop_duplicates(subset=["CandidateKey", "TimestampUTC"], keep="last").sort_values(["CandidateKey", "TimestampUTC"]).reset_index(drop=True)

def load_single_value_profile(path_string, key_column, value_column, allowed_keys):
    path = Path(path_string)
    if not path.exists():
        return pd.DataFrame(columns=["TimestampUTC", key_column, value_column])
    df = pd.read_csv(path, compression="gzip" if str(path).lower().endswith(".gz") else "infer")
    required = {"TimestampUTC", key_column, value_column}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{path_string} is missing required columns: {sorted(missing)}")
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df[key_column] = df[key_column].astype(str)
    df[value_column] = pd.to_numeric(df[value_column], errors="coerce").fillna(0.0).clip(lower=0.0)
    df = df[df[key_column].isin(set(allowed_keys))].dropna(subset=["TimestampUTC"]).copy()
    return df.drop_duplicates(subset=[key_column, "TimestampUTC"], keep="last").sort_values([key_column, "TimestampUTC"]).reset_index(drop=True)


def load_pv_shape():
    path = Path(PV_SHAPE_FILE)
    if not path.exists():
        return pd.DataFrame(columns=["TimestampUTC", "SystemPVAvailabilityPU"])
    df = pd.read_csv(path, compression="gzip" if str(path).lower().endswith(".gz") else "infer")
    required = {"TimestampUTC", "SystemPVAvailabilityPU"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{PV_SHAPE_FILE} is missing required columns: {sorted(missing)}")
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    availability_columns = [column for column in df.columns if column == "SystemPVAvailabilityPU" or str(column).startswith("PVAvailabilityPU__")]
    for column in availability_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").clip(lower=0.0, upper=PV_AVAILABILITY_CLIP_PU)
    df = df.dropna(subset=["TimestampUTC"]).drop_duplicates(subset=["TimestampUTC"], keep="last").sort_values("TimestampUTC").reset_index(drop=True)
    return df[["TimestampUTC"] + availability_columns]


def load_ev_profile(allowed_substations):
    path = Path(EV_PROFILE_FILE)
    if not path.exists():
        return pd.DataFrame(columns=["TimestampUTC", "SubstationID", "AvailableEVReductionMW", "AvailableEVReboundMW"])
    df = pd.read_csv(path, compression="gzip" if str(path).lower().endswith(".gz") else "infer")
    required = {"TimestampUTC", "SubstationID", "AvailableEVReductionMW", "AvailableEVReboundMW"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{EV_PROFILE_FILE} is missing required columns: {sorted(missing)}")
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["SubstationID"] = df["SubstationID"].astype(str)
    for column in ["AvailableEVReductionMW", "AvailableEVReboundMW"]:
        df[column] = pd.to_numeric(df[column], errors="coerce").fillna(0.0).clip(lower=0.0)
    df = df[df["SubstationID"].isin(set(allowed_substations))].dropna(subset=["TimestampUTC"]).copy()
    return df.drop_duplicates(subset=["SubstationID", "TimestampUTC"], keep="last").sort_values(["SubstationID", "TimestampUTC"]).reset_index(drop=True)


def load_optional_profiles(candidates):
    create_empty_profile_template(EV_PROFILE_FILE, ["TimestampUTC", "SubstationID", "AvailableEVReductionMW", "AvailableEVReboundMW"])
    if ENABLE_SUBSTATION_DR:
        create_empty_profile_template(DR_PROFILE_FILE, ["TimestampUTC", "SubstationID", "AvailableDRMW"])
    pv_shape = load_pv_shape() if ENABLE_INCREMENTAL_LOCAL_PV else pd.DataFrame(columns=["TimestampUTC", "SystemPVAvailabilityPU"])
    ev = load_ev_profile(candidates["SubstationID"]) if ENABLE_EV_LOAD_SHIFT else pd.DataFrame(columns=["TimestampUTC", "SubstationID", "AvailableEVReductionMW", "AvailableEVReboundMW"])
    dr = load_single_value_profile(DR_PROFILE_FILE, "SubstationID", "AvailableDRMW", candidates["SubstationID"]) if ENABLE_SUBSTATION_DR else pd.DataFrame(columns=["TimestampUTC", "SubstationID", "AvailableDRMW"])
    return pv_shape, ev, dr


# ======================================================================================
# FIVE-MINUTE DATA PREPARATION
# ======================================================================================

def prepare_candidate_timeseries(candidate_row, thermal_profiles, substation_profiles, substation_capacity_row, resource_row, reliability_row, engineering_row, pv_shape, ev_profiles, dr_profiles, ac_limit_profiles):
    candidate_key = str(candidate_row["CandidateKey"])
    substation_id = str(candidate_row["SubstationID"])
    thermal = thermal_profiles[thermal_profiles["CandidateKey"] == candidate_key].copy()
    if thermal.empty:
        raise RuntimeError(f"No five-minute thermal profile was found for {candidate_key}.")
    thermal = thermal[["TimestampUTC", "ExistingBreakerMW", "AvailableThermalHostingMW", "RawIncrementalHeadroomMW", "LimitingElementID", "BaselinePathRatingExceedance"]].copy()
    thermal["ThermalHeadroomMW"] = thermal["AvailableThermalHostingMW"] * THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR
    thermal["ReservedThermalHeadroomMW"] = thermal["ThermalHeadroomMW"]
    thermal_count = len(thermal)

    station = substation_profiles[substation_profiles["SubstationID"] == substation_id][["TimestampUTC", "ExistingLoadMW"]].copy()
    capacity_resolved = bool(substation_capacity_row["CapacityResolved"])
    capacity_method = str(substation_capacity_row["CapacityMethod"])
    if capacity_resolved and capacity_method == "TotalCapacityMW_TimeSeries":
        if station.empty:
            raise RuntimeError(f"Substation {substation_id} has a TotalCapacityMW constraint but no five-minute load profile.")
        data = thermal.merge(station, on="TimestampUTC", how="inner")
        coverage_pct = len(data) / thermal_count * 100.0 if thermal_count else 0.0
        if coverage_pct < MIN_PROFILE_INTERSECTION_PCT:
            raise RuntimeError(f"Candidate {candidate_key} thermal/substation intersection is {coverage_pct:.2f}% (< {MIN_PROFILE_INTERSECTION_PCT:.2f}%).")
        data["SubstationHeadroomMW"] = (SUBSTATION_LOADING_LIMIT_FRACTION * float(substation_capacity_row["TotalCapacityMW"]) - data["ExistingLoadMW"]).clip(lower=0.0)
    elif capacity_resolved and capacity_method == "DirectAvailableAdditionalCapacityMW":
        data = thermal.merge(station, on="TimestampUTC", how="left") if not station.empty else thermal.copy()
        data["SubstationHeadroomMW"] = float(substation_capacity_row["AvailableAdditionalCapacityMW"])
        coverage_pct = 100.0
    else:
        if UNRESOLVED_SUBSTATION_POLICY.upper() == "EXCLUDE":
            raise RuntimeError(f"Candidate {candidate_key} is behind unresolved substation {substation_id} and unresolved substations are excluded.")
        data = thermal.merge(station, on="TimestampUTC", how="left") if not station.empty else thermal.copy()
        data["SubstationHeadroomMW"] = np.inf
        coverage_pct = 100.0

    if not pv_shape.empty:
        local_column = f"PVAvailabilityPU__{substation_id}"
        pv_columns = ["TimestampUTC", "SystemPVAvailabilityPU"] + ([local_column] if local_column in pv_shape.columns else [])
        pv = pv_shape[pv_columns].copy()
        data = data.merge(pv, on="TimestampUTC", how="left")
        system_shape = pd.to_numeric(data["SystemPVAvailabilityPU"], errors="coerce").fillna(0.0)
        if local_column in data.columns:
            local_shape = pd.to_numeric(data[local_column], errors="coerce")
            data["PVAvailabilityPU"] = local_shape.where(local_shape.notna(), system_shape).clip(lower=0.0, upper=PV_AVAILABILITY_CLIP_PU)
            data = data.drop(columns=[local_column])
        else:
            data["PVAvailabilityPU"] = system_shape.clip(lower=0.0, upper=PV_AVAILABILITY_CLIP_PU)
        data = data.drop(columns=["SystemPVAvailabilityPU"])
    else:
        data["PVAvailabilityPU"] = 0.0

    ev = ev_profiles[ev_profiles["SubstationID"] == substation_id][["TimestampUTC", "AvailableEVReductionMW", "AvailableEVReboundMW"]].copy() if not ev_profiles.empty else pd.DataFrame(columns=["TimestampUTC", "AvailableEVReductionMW", "AvailableEVReboundMW"])
    dr = dr_profiles[dr_profiles["SubstationID"] == substation_id][["TimestampUTC", "AvailableDRMW"]].copy() if not dr_profiles.empty else pd.DataFrame(columns=["TimestampUTC", "AvailableDRMW"])
    data = data.merge(ev, on="TimestampUTC", how="left").merge(dr, on="TimestampUTC", how="left")
    for column in ["PVAvailabilityPU", "AvailableEVReductionMW", "AvailableEVReboundMW", "AvailableDRMW"]:
        data[column] = pd.to_numeric(data[column], errors="coerce").fillna(0.0).clip(lower=0.0)

    # Validated AC study can provide a time-varying maximum additional real-power envelope.
    # If absent, an optional static validated AC limit may be used. Otherwise AC remains
    # unresolved and receives no hidden constraint/credit.
    apply_ac = ENABLE_AC_ENGINEERING_LIMITS and truthy(engineering_row.get("ApplyACConstraint"))
    static_ac_limit = safe_float(engineering_row.get("ACStaticAdditionalMW"), np.nan)

    ac = ac_limit_profiles[ac_limit_profiles["CandidateKey"] == candidate_key][["TimestampUTC", "ACAdditionalRealPowerLimitMW"]].copy() if not ac_limit_profiles.empty else pd.DataFrame(columns=["TimestampUTC", "ACAdditionalRealPowerLimitMW"])
    data = data.merge(ac, on="TimestampUTC", how="left")

    if apply_ac:
        if pd.notna(static_ac_limit):
            data["ACAdditionalRealPowerLimitMW"] = pd.to_numeric(data["ACAdditionalRealPowerLimitMW"], errors="coerce").fillna(static_ac_limit)
        elif data["ACAdditionalRealPowerLimitMW"].notna().all():
            data["ACAdditionalRealPowerLimitMW"] = pd.to_numeric(data["ACAdditionalRealPowerLimitMW"], errors="coerce")
        else:
            raise RuntimeError(f"{candidate_key}: ApplyACConstraint=True but no complete validated AC limit profile or static AC limit is available.")
    else:
        data["ACAdditionalRealPowerLimitMW"] = np.inf

    data["IncrementalDeliveryEfficiency"] = safe_float(engineering_row.get("IncrementalDeliveryEfficiency"), DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY)
    data["DataCenterPowerFactor"] = safe_float(engineering_row.get("DataCenterPowerFactor"), 0.95)
    data["ReactivePowerLimitMVAr"] = safe_float(engineering_row.get("ReactivePowerLimitMVAr"), np.nan)
    data["ProtectionMaxAdditionalMW"] = safe_float(engineering_row.get("ProtectionMaxAdditionalMW"), np.nan)

    data = data.sort_values("TimestampUTC").reset_index(drop=True)
    data["CombinedNoFlexHeadroomMW"] = np.minimum.reduce([
        data["ThermalHeadroomMW"].to_numpy(dtype=float),
        data["SubstationHeadroomMW"].to_numpy(dtype=float),
        data["ACAdditionalRealPowerLimitMW"].to_numpy(dtype=float)
    ])
    return data, coverage_pct


# ======================================================================================
# CANDIDATE OPERATIONAL MODEL
# ======================================================================================

def candidate_resource_flags(resource_row, data):
    battery_active = ENABLE_BATTERY and safe_float(resource_row.get("BatteryMaxPowerMW"), 0.0) > 0 and safe_float(resource_row.get("BatteryMaxEnergyMWh"), 0.0) > 0
    flex_active = ENABLE_FLEXIBLE_COMPUTE and safe_float(resource_row.get("FlexibleComputeMaxReductionFraction"), 0.0) > 0 and safe_float(resource_row.get("FlexibleComputeMaxEnergyFraction"), 0.0) > 0
    pv_active = ENABLE_INCREMENTAL_LOCAL_PV and safe_float(resource_row.get("ProposedPVCapacityMW"), 0.0) > 0 and float(data["PVAvailabilityPU"].max()) > 0
    ev_active = ENABLE_EV_LOAD_SHIFT and float(data["AvailableEVReductionMW"].max()) > 0 and float(data["AvailableEVReboundMW"].max()) > 0
    dr_active = ENABLE_SUBSTATION_DR and float(data["AvailableDRMW"].max()) > 0
    return battery_active, flex_active, pv_active, ev_active, dr_active


def build_candidate_model(candidate_row, data, substation_capacity_row, resource_row, reliability_row, engineering_row):
    n = len(data)
    if n == 0:
        raise RuntimeError("Cannot build a candidate model with zero five-minute intervals.")

    thermal = data["ThermalHeadroomMW"].to_numpy(dtype=float)
    existing_station = data["ExistingLoadMW"].to_numpy(dtype=float) if "ExistingLoadMW" in data.columns else np.full(n, np.nan)
    pv_availability = data["PVAvailabilityPU"].to_numpy(dtype=float)
    ev_reduce_available = data["AvailableEVReductionMW"].to_numpy(dtype=float)
    ev_rebound_available = data["AvailableEVReboundMW"].to_numpy(dtype=float)
    dr_available = data["AvailableDRMW"].to_numpy(dtype=float)
    ac_limit = data["ACAdditionalRealPowerLimitMW"].to_numpy(dtype=float)
    delivery_efficiency = float(np.clip(safe_float(engineering_row.get("IncrementalDeliveryEfficiency"), DEFAULT_INCREMENTAL_DELIVERY_EFFICIENCY), 1e-6, 1.0))
    protection_limit = safe_float(engineering_row.get("ProtectionMaxAdditionalMW"), np.nan)
    apply_protection = ENABLE_PROTECTION_LIMITS and truthy(engineering_row.get("ApplyProtectionConstraint")) and pd.notna(protection_limit)
    apply_ac = ENABLE_AC_ENGINEERING_LIMITS and truthy(engineering_row.get("ApplyACConstraint"))
    blocks = find_contiguous_blocks(data["TimestampUTC"])
    battery_active, flex_active, pv_active, ev_active, dr_active = candidate_resource_flags(resource_row, data)

    battery_power_cap = safe_float(resource_row.get("BatteryMaxPowerMW"), 0.0) if battery_active else 0.0
    battery_energy_cap = safe_float(resource_row.get("BatteryMaxEnergyMWh"), 0.0) if battery_active else 0.0
    pv_capacity_cap = safe_float(resource_row.get("ProposedPVCapacityMW"), 0.0) if pv_active else 0.0
    eta_c = safe_float(resource_row.get("BatteryChargeEfficiency"), DEFAULT_BATTERY_CHARGE_EFFICIENCY)
    eta_d = safe_float(resource_row.get("BatteryDischargeEfficiency"), DEFAULT_BATTERY_DISCHARGE_EFFICIENCY)
    initial_soc_fraction = clip_fraction(resource_row.get("BatteryInitialSOCFraction"), DEFAULT_BATTERY_INITIAL_SOC_FRACTION)
    min_soc_fraction = clip_fraction(resource_row.get("BatteryMinSOCFraction"), DEFAULT_BATTERY_MIN_SOC_FRACTION)
    initial_soc_fraction = max(initial_soc_fraction, min_soc_fraction)
    flex_interval_fraction = clip_fraction(resource_row.get("FlexibleComputeMaxReductionFraction"), 0.0) if flex_active else 0.0
    flex_energy_fraction = clip_fraction(resource_row.get("FlexibleComputeMaxEnergyFraction"), 0.0) if flex_active else 0.0

    max_thermal = float(np.nanmax(thermal)) if len(thermal) else 0.0
    max_pv = pv_capacity_cap * float(np.nanmax(pv_availability)) if pv_active else 0.0
    denominator = max(1.0 - flex_interval_fraction, 1e-6)
    dc_upper = max((max_thermal + battery_power_cap + max_pv) / denominator, max_thermal)
    if bool(substation_capacity_row["CapacityResolved"]) and str(substation_capacity_row["CapacityMethod"]) == "TotalCapacityMW_TimeSeries":
        dc_upper = max(dc_upper, SUBSTATION_LOADING_LIMIT_FRACTION * float(substation_capacity_row["TotalCapacityMW"]) + battery_power_cap + max_pv)
    finite_no_flex = data["CombinedNoFlexHeadroomMW"].replace([np.inf, -np.inf], np.nan)
    if finite_no_flex.notna().any():
        dc_upper = max(dc_upper, float(finite_no_flex.max()))

    model = pyo.ConcreteModel()
    model.T = pyo.RangeSet(0, n - 1)
    model.pdc = pyo.Var(within=pyo.NonNegativeReals, bounds=(0.0, max(dc_upper, 0.001)))

    if battery_active:
        model.battery_power = pyo.Var(within=pyo.NonNegativeReals, bounds=(0.0, battery_power_cap))
        model.battery_energy = pyo.Var(within=pyo.NonNegativeReals, bounds=(0.0, battery_energy_cap))
        model.charge = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.discharge = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.soc = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.BatteryChargeLimit = pyo.Constraint(model.T, rule=lambda m, t: m.charge[t] <= m.battery_power)
        model.BatteryDischargeLimit = pyo.Constraint(model.T, rule=lambda m, t: m.discharge[t] <= m.battery_power)
        model.BatteryCombinedPowerLimit = pyo.Constraint(model.T, rule=lambda m, t: m.charge[t] + m.discharge[t] <= m.battery_power)
        model.BatterySOCHigh = pyo.Constraint(model.T, rule=lambda m, t: m.soc[t] <= m.battery_energy)
        model.BatterySOCLow = pyo.Constraint(model.T, rule=lambda m, t: m.soc[t] >= min_soc_fraction * m.battery_energy)
        for block_number, (start, end) in enumerate(blocks):
            setattr(model, f"BatteryBlockStart_{block_number}", pyo.Constraint(expr=model.soc[start] == initial_soc_fraction * model.battery_energy))
            for t in range(start + 1, end + 1):
                setattr(model, f"BatterySOC_{block_number}_{t}", pyo.Constraint(expr=model.soc[t] == model.soc[t - 1] + eta_c * model.charge[t - 1] * DT_HOURS - model.discharge[t - 1] * DT_HOURS / eta_d))
            setattr(model, f"BatteryBlockEnd_{block_number}", pyo.Constraint(expr=model.soc[end] + eta_c * model.charge[end] * DT_HOURS - model.discharge[end] * DT_HOURS / eta_d == initial_soc_fraction * model.battery_energy))
    else:
        model.battery_power = pyo.Param(initialize=0.0, mutable=False)
        model.battery_energy = pyo.Param(initialize=0.0, mutable=False)
        model.charge = pyo.Param(model.T, initialize=0.0, mutable=False)
        model.discharge = pyo.Param(model.T, initialize=0.0, mutable=False)
        model.soc = pyo.Param(model.T, initialize=0.0, mutable=False)

    if flex_active:
        model.flex = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.FlexIntervalLimit = pyo.Constraint(model.T, rule=lambda m, t: m.flex[t] <= flex_interval_fraction * m.pdc)
        model.FlexEnergyLimit = pyo.Constraint(expr=sum(model.flex[t] * DT_HOURS for t in model.T) <= flex_energy_fraction * model.pdc * n * DT_HOURS)
    else:
        model.flex = pyo.Param(model.T, initialize=0.0, mutable=False)

    if pv_active:
        model.pv_capacity = pyo.Var(within=pyo.NonNegativeReals, bounds=(0.0, pv_capacity_cap))
        model.pv = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.PVLimit = pyo.Constraint(model.T, rule=lambda m, t: m.pv[t] <= float(pv_availability[t]) * m.pv_capacity)
    else:
        model.pv_capacity = pyo.Param(initialize=0.0, mutable=False)
        model.pv = pyo.Param(model.T, initialize=0.0, mutable=False)

    if dr_active:
        model.dr = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.DRLimit = pyo.Constraint(model.T, rule=lambda m, t: m.dr[t] <= float(dr_available[t]))
        if pd.notna(DR_MAX_TOTAL_ENERGY_MWH):
            model.DREnergyLimit = pyo.Constraint(expr=sum(model.dr[t] * DT_HOURS for t in model.T) <= float(DR_MAX_TOTAL_ENERGY_MWH))
    else:
        model.dr = pyo.Param(model.T, initialize=0.0, mutable=False)

    if ev_active:
        model.ev_reduce = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.ev_rebound = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.ev_backlog = pyo.Var(model.T, within=pyo.NonNegativeReals)
        model.EVReductionLimit = pyo.Constraint(model.T, rule=lambda m, t: m.ev_reduce[t] <= float(ev_reduce_available[t]))
        model.EVReboundLimit = pyo.Constraint(model.T, rule=lambda m, t: m.ev_rebound[t] <= float(ev_rebound_available[t]))

        local_dates = pd.DatetimeIndex(data["TimestampUTC"]).tz_convert(EV_REPAYMENT_TIMEZONE).date
        day_groups = defaultdict(list)
        for t, local_date in enumerate(local_dates):
            day_groups[local_date].append(t)

        for day_number, (_, indices) in enumerate(sorted(day_groups.items(), key=lambda item: item[0])):
            first = indices[0]
            setattr(model, f"EVBacklogStart_{day_number}", pyo.Constraint(expr=model.ev_backlog[first] == model.ev_reduce[first] * DT_HOURS - model.ev_rebound[first] * DT_HOURS * EV_SHIFT_ENERGY_EFFICIENCY))
            for position in range(1, len(indices)):
                t = indices[position]
                previous = indices[position - 1]
                setattr(model, f"EVBacklog_{day_number}_{t}", pyo.Constraint(expr=model.ev_backlog[t] == model.ev_backlog[previous] + model.ev_reduce[t] * DT_HOURS - model.ev_rebound[t] * DT_HOURS * EV_SHIFT_ENERGY_EFFICIENCY))
            setattr(model, f"EVSameDayRepayment_{day_number}", pyo.Constraint(expr=model.ev_backlog[indices[-1]] == 0.0))
    else:
        model.ev_reduce = pyo.Param(model.T, initialize=0.0, mutable=False)
        model.ev_rebound = pyo.Param(model.T, initialize=0.0, mutable=False)
        model.ev_backlog = pyo.Param(model.T, initialize=0.0, mutable=False)

    model.FacilityNetGridDemand = pyo.Expression(model.T, rule=lambda m, t: m.pdc - m.flex[t] - m.pv[t] + m.charge[t] - m.discharge[t])
    model.GridImport = pyo.Expression(model.T, rule=lambda m, t: m.FacilityNetGridDemand[t] / delivery_efficiency)
    model.GridNonnegative = pyo.Constraint(model.T, rule=lambda m, t: m.GridImport[t] >= 0.0)
    model.ThermalReserveConstraint = pyo.Constraint(model.T, rule=lambda m, t: m.GridImport[t] <= float(thermal[t]))

    if apply_ac:
        model.ACEngineeringEnvelope = pyo.Constraint(model.T, rule=lambda m, t: m.GridImport[t] <= float(ac_limit[t]))

    if apply_protection:
        model.ProtectionAdditionalMWLimit = pyo.Constraint(model.T, rule=lambda m, t: m.GridImport[t] <= float(protection_limit))

    # Optional coarse candidate-level MVAr ceiling from an external study. This does NOT
    # replace a network AC power-flow model; it only enforces a validated envelope.
    data_center_pf = float(np.clip(safe_float(engineering_row.get("DataCenterPowerFactor"), 0.95), 1e-6, 1.0))
    reactive_limit_mvar = safe_float(engineering_row.get("ReactivePowerLimitMVAr"), np.nan)
    if ENABLE_AC_ENGINEERING_LIMITS and truthy(engineering_row.get("ApplyACConstraint")) and pd.notna(reactive_limit_mvar):
        reactive_ratio = math.tan(math.acos(data_center_pf))
        model.ReactivePowerEnvelope = pyo.Constraint(model.T, rule=lambda m, t: (m.pdc - m.flex[t]) * reactive_ratio <= float(reactive_limit_mvar))

    if bool(substation_capacity_row["CapacityResolved"]):
        method = str(substation_capacity_row["CapacityMethod"])
        if method == "TotalCapacityMW_TimeSeries":
            station_limit = SUBSTATION_LOADING_LIMIT_FRACTION * float(substation_capacity_row["TotalCapacityMW"])
            model.SubstationConstraint = pyo.Constraint(model.T, rule=lambda m, t: float(existing_station[t]) + m.GridImport[t] - m.dr[t] - m.ev_reduce[t] + m.ev_rebound[t] <= station_limit)
        elif method == "DirectAvailableAdditionalCapacityMW":
            direct_headroom = float(substation_capacity_row["AvailableAdditionalCapacityMW"])
            model.SubstationConstraint = pyo.Constraint(model.T, rule=lambda m, t: m.GridImport[t] - m.dr[t] - m.ev_reduce[t] + m.ev_rebound[t] <= direct_headroom)

    reliability_ready = ENABLE_RELIABILITY and truthy(reliability_row.get("ReliabilityDataReady"))
    reliability_capacity = safe_float(reliability_row.get("ReliabilityCapacityMW"), np.nan)
    critical_fraction = clip_fraction(reliability_row.get("CriticalLoadFraction"), DEFAULT_CRITICAL_LOAD_FRACTION)
    duration_hours = safe_float(reliability_row.get("ContingencyDurationHours"), np.nan)
    switching_delay_hours = safe_float(reliability_row.get("SwitchingDelayMinutes"), DEFAULT_SWITCHING_DELAY_MINUTES) / 60.0
    reliability_battery_credit = False

    if reliability_ready:
        model.CriticalContingencyLoadMW = pyo.Expression(expr=critical_fraction * model.pdc)
        if battery_active and ALLOW_BATTERY_FOR_CONTINGENCY_SUPPORT and pd.notna(duration_hours) and duration_hours > 0:
            reliability_battery_credit = True
            model.ContingencyGapMW = pyo.Var(within=pyo.NonNegativeReals)
            model.ContingencyGapDefinition = pyo.Constraint(expr=model.ContingencyGapMW >= model.CriticalContingencyLoadMW - reliability_capacity)
            model.ContingencyGapPower = pyo.Constraint(expr=model.ContingencyGapMW <= model.battery_power)
            delay_hours = min(max(switching_delay_hours, 0.0), duration_hours)
            post_switch_hours = max(duration_hours - delay_hours, 0.0)
            if delay_hours > 0:
                model.SwitchingBridgePower = pyo.Constraint(expr=model.CriticalContingencyLoadMW <= model.battery_power)
            model.RequiredContingencyEnergyMWh = pyo.Expression(expr=(model.CriticalContingencyLoadMW * delay_hours + model.ContingencyGapMW * post_switch_hours) / eta_d)
            model.EmergencyReserve = pyo.Constraint(model.T, rule=lambda m, t: m.soc[t] >= min_soc_fraction * m.battery_energy + m.RequiredContingencyEnergyMWh)
        else:
            model.ContingencyCapacity = pyo.Constraint(expr=model.CriticalContingencyLoadMW <= reliability_capacity)

    model.MaximizeDC = pyo.Objective(expr=model.pdc, sense=pyo.maximize)
    metadata = {
        "BatteryActive": battery_active,
        "FlexActive": flex_active,
        "PVActive": pv_active,
        "EVActive": ev_active,
        "DRActive": dr_active,
        "ReliabilityReady": reliability_ready,
        "ReliabilityCapacityMW": reliability_capacity,
        "CriticalLoadFraction": critical_fraction,
        "ContingencyDurationHours": duration_hours,
        "SwitchingDelayHours": switching_delay_hours,
        "ReliabilityBatteryCredit": reliability_battery_credit,
        "BatteryPowerCapMW": battery_power_cap,
        "BatteryEnergyCapMWh": battery_energy_cap,
        "PVCapacityCapMW": pv_capacity_cap,
        "BatteryChargeEfficiency": eta_c,
        "BatteryDischargeEfficiency": eta_d,
        "BatteryMinSOCFraction": min_soc_fraction,
        "BatteryInitialSOCFraction": initial_soc_fraction,
        "ACConstraintApplied": apply_ac,
        "ProtectionConstraintApplied": apply_protection,
        "IncrementalDeliveryEfficiency": delivery_efficiency,
        "ProtectionMaxAdditionalMW": protection_limit,
        "ReactivePowerLimitMVAr": reactive_limit_mvar
    }
    return model, metadata

def solve_candidate(candidate_row, data, substation_capacity_row, resource_row, reliability_row, engineering_row, solver, capture_dispatch=False):
    model, metadata = build_candidate_model(candidate_row, data, substation_capacity_row, resource_row, reliability_row, engineering_row)
    primary_termination = solve_checked(model, solver, f"Capacity optimization for {candidate_row['CandidateKey']}")
    optimum_dc = float(pyo.value(model.pdc))
    model.CapacityFloor = pyo.Constraint(expr=model.pdc >= max(optimum_dc - OPTIMALITY_TOLERANCE_MW, 0.0))
    model.MaximizeDC.deactivate()

    secondary_termination = "NotNeeded"
    if metadata["BatteryActive"]:
        model.MinBatteryPower = pyo.Objective(expr=model.battery_power, sense=pyo.minimize)
        secondary_termination = solve_checked(model, solver, f"Battery-power tie-break for {candidate_row['CandidateKey']}")
        best_power = float(pyo.value(model.battery_power))
        model.BatteryPowerTie = pyo.Constraint(expr=model.battery_power <= best_power + OPTIMALITY_TOLERANCE_MW)
        model.MinBatteryPower.deactivate()
        model.MinBatteryEnergy = pyo.Objective(expr=model.battery_energy, sense=pyo.minimize)
        secondary_termination = solve_checked(model, solver, f"Battery-energy tie-break for {candidate_row['CandidateKey']}")
        best_energy = float(pyo.value(model.battery_energy))
        model.BatteryEnergyTie = pyo.Constraint(expr=model.battery_energy <= best_energy + OPTIMALITY_TOLERANCE_MW)
        model.MinBatteryEnergy.deactivate()

    if metadata["PVActive"]:
        model.MinPVCapacity = pyo.Objective(expr=model.pv_capacity, sense=pyo.minimize)
        secondary_termination = solve_checked(model, solver, f"PV-capacity tie-break for {candidate_row['CandidateKey']}")
        best_pv_capacity = float(pyo.value(model.pv_capacity))
        model.PVCapacityTie = pyo.Constraint(expr=model.pv_capacity <= best_pv_capacity + OPTIMALITY_TOLERANCE_MW)
        model.MinPVCapacity.deactivate()

    intervention_terms = []
    if metadata["BatteryActive"]:
        intervention_terms.append(sum((model.charge[t] + model.discharge[t]) * DT_HOURS for t in model.T))
    if metadata["FlexActive"]:
        intervention_terms.append(sum(model.flex[t] * DT_HOURS for t in model.T))
    if metadata["DRActive"]:
        intervention_terms.append(sum(model.dr[t] * DT_HOURS for t in model.T))
    if metadata["EVActive"]:
        intervention_terms.append(sum((model.ev_reduce[t] + model.ev_rebound[t]) * DT_HOURS for t in model.T))
    if intervention_terms:
        model.MinIntervention = pyo.Objective(expr=sum(intervention_terms), sense=pyo.minimize)
        secondary_termination = solve_checked(model, solver, f"Intervention tie-break for {candidate_row['CandidateKey']}")

    n = len(data)
    pdc = float(pyo.value(model.pdc))
    grid = np.array([float(pyo.value(model.GridImport[t])) for t in range(n)], dtype=float)
    charge = np.array([float(pyo.value(model.charge[t])) for t in range(n)], dtype=float)
    discharge = np.array([float(pyo.value(model.discharge[t])) for t in range(n)], dtype=float)
    soc = np.array([float(pyo.value(model.soc[t])) for t in range(n)], dtype=float)
    flex = np.array([float(pyo.value(model.flex[t])) for t in range(n)], dtype=float)
    pv = np.array([float(pyo.value(model.pv[t])) for t in range(n)], dtype=float)
    dr = np.array([float(pyo.value(model.dr[t])) for t in range(n)], dtype=float)
    ev_reduce = np.array([float(pyo.value(model.ev_reduce[t])) for t in range(n)], dtype=float)
    ev_rebound = np.array([float(pyo.value(model.ev_rebound[t])) for t in range(n)], dtype=float)
    ev_backlog = np.array([float(pyo.value(model.ev_backlog[t])) for t in range(n)], dtype=float)
    battery_power = float(pyo.value(model.battery_power))
    battery_energy = float(pyo.value(model.battery_energy))
    pv_capacity = float(pyo.value(model.pv_capacity))
    thermal_only = float(data["ThermalHeadroomMW"].min())
    station_only = float(data["SubstationHeadroomMW"].min()) if np.isfinite(data["SubstationHeadroomMW"]).any() else np.nan
    combined_no_flex = float(data["CombinedNoFlexHeadroomMW"].min()) * metadata["IncrementalDeliveryEfficiency"]
    reliability_margin = metadata["ReliabilityCapacityMW"] - metadata["CriticalLoadFraction"] * pdc if metadata["ReliabilityReady"] else np.nan
    contingency_energy = float(pyo.value(model.RequiredContingencyEnergyMWh)) if metadata["ReliabilityBatteryCredit"] else 0.0
    contingency_gap = float(pyo.value(model.ContingencyGapMW)) if metadata["ReliabilityBatteryCredit"] else max(metadata["CriticalLoadFraction"] * pdc - metadata["ReliabilityCapacityMW"], 0.0) if metadata["ReliabilityReady"] else np.nan

    summary = {
        "MaxOptimizedDCMW": pdc,
        "ThermalOnlyReservedFirmMW": thermal_only,
        "SubstationOnlyFirmIncrementalMW": station_only,
        "CombinedNoFlexFirmMW": combined_no_flex,
        "CapacityGainFromFlexMW": pdc - combined_no_flex,
        "BatteryPowerMW": battery_power,
        "BatteryEnergyMWh": battery_energy,
        "BatteryThroughputMWh": float(np.sum(charge + discharge) * DT_HOURS),
        "PVCapacityMW": pv_capacity,
        "PVCapacityCapMW": metadata["PVCapacityCapMW"],
        "FlexibleComputeMWh": float(np.sum(flex) * DT_HOURS),
        "PVUsedMWh": float(np.sum(pv) * DT_HOURS),
        "DRMWh": float(np.sum(dr) * DT_HOURS),
        "EVShiftedMWh": float(np.sum(ev_reduce) * DT_HOURS),
        "EVMaximumBacklogMWh": float(np.max(ev_backlog)) if len(ev_backlog) else 0.0,
        "ReliabilityReady": metadata["ReliabilityReady"],
        "ReliabilityCapacityMW": metadata["ReliabilityCapacityMW"],
        "CriticalLoadFraction": metadata["CriticalLoadFraction"],
        "ReliabilityCapacityMarginMW": reliability_margin,
        "ReliabilityBatteryCredit": metadata["ReliabilityBatteryCredit"],
        "ContingencyBatteryGapMW": contingency_gap,
        "RequiredContingencyEnergyMWh": contingency_energy,
        "BatteryActive": metadata["BatteryActive"],
        "PVActive": metadata["PVActive"],
        "EVActive": metadata["EVActive"],
        "DRActive": metadata["DRActive"],
        "FlexibleComputeActive": metadata["FlexActive"],
        "ACConstraintApplied": metadata["ACConstraintApplied"],
        "ProtectionConstraintApplied": metadata["ProtectionConstraintApplied"],
        "IncrementalDeliveryEfficiency": metadata["IncrementalDeliveryEfficiency"],
        "ProtectionMaxAdditionalMW": metadata["ProtectionMaxAdditionalMW"],
        "ReactivePowerLimitMVAr": metadata["ReactivePowerLimitMVAr"],
        "PrimaryTermination": primary_termination,
        "SecondaryTermination": secondary_termination
    }

    dispatch = pd.DataFrame()
    if capture_dispatch:
        dispatch = data.copy()
        dispatch["DCNameplateMW"] = pdc
        dispatch["FlexibleComputeReductionMW"] = flex
        dispatch["ServedComputeMW"] = pdc - flex
        dispatch["PVCapacityMW"] = pv_capacity
        dispatch["PVUsedMW"] = pv
        dispatch["BatteryChargeMW"] = charge
        dispatch["BatteryDischargeMW"] = discharge
        dispatch["BatterySOCMWh"] = soc
        dispatch["GridImportMW"] = grid
        dispatch["DRUsedMW"] = dr
        dispatch["EVReductionMW"] = ev_reduce
        dispatch["EVReboundMW"] = ev_rebound
        dispatch["EVDeferredEnergyBacklogMWh"] = ev_backlog
        dispatch["FacilityNetGridDemandMW"] = np.array([float(pyo.value(model.FacilityNetGridDemand[t])) for t in range(n)], dtype=float)
        dispatch["ThermalReserveSlackMW"] = dispatch["ThermalHeadroomMW"] - dispatch["GridImportMW"]
        dispatch["ACEngineeringSlackMW"] = dispatch["ACAdditionalRealPowerLimitMW"] - dispatch["GridImportMW"]
        dispatch["ProtectionSlackMW"] = metadata["ProtectionMaxAdditionalMW"] - dispatch["GridImportMW"] if metadata["ProtectionConstraintApplied"] else np.nan
        if bool(substation_capacity_row["CapacityResolved"]):
            if str(substation_capacity_row["CapacityMethod"]) == "TotalCapacityMW_TimeSeries":
                station_limit = SUBSTATION_LOADING_LIMIT_FRACTION * float(substation_capacity_row["TotalCapacityMW"])
                dispatch["SubstationSlackMW"] = station_limit - dispatch["ExistingLoadMW"] - dispatch["GridImportMW"] + dispatch["DRUsedMW"] + dispatch["EVReductionMW"] - dispatch["EVReboundMW"]
            else:
                dispatch["SubstationSlackMW"] = float(substation_capacity_row["AvailableAdditionalCapacityMW"]) - dispatch["GridImportMW"] + dispatch["DRUsedMW"] + dispatch["EVReductionMW"] - dispatch["EVReboundMW"]
        else:
            dispatch["SubstationSlackMW"] = np.nan
    return summary, dispatch


# ======================================================================================
# FINAL SITE-SELECTION MILP
# ======================================================================================

def select_final_site(candidate_results, solver):
    pool = candidate_results[candidate_results["SelectionEligible"]].copy()
    if pool.empty:
        raise RuntimeError("No candidates are eligible for final selection under the current data-completeness policy.")
    keys = pool["CandidateKey"].tolist()
    lookup = pool.set_index("CandidateKey").to_dict("index")
    model = pyo.ConcreteModel()
    model.CANDIDATES = pyo.Set(initialize=keys, ordered=True)
    model.x = pyo.Var(model.CANDIDATES, within=pyo.Binary)
    model.OneCentralSite = pyo.Constraint(expr=sum(model.x[j] for j in model.CANDIDATES) == 1)
    model.SelectedMW = pyo.Expression(expr=sum(float(lookup[j]["MaxOptimizedDCMW"]) * model.x[j] for j in model.CANDIDATES))
    model.MaximizeCapacity = pyo.Objective(expr=model.SelectedMW, sense=pyo.maximize)
    primary_termination = solve_checked(model, solver, "Final one-site capacity selection")
    best_capacity = float(pyo.value(model.SelectedMW))

    reliability_complete = pool["ReliabilityReady"].all() and pool["ReliabilityModelEnabled"].all()
    reliability_secondary_used = ENABLE_RELIABILITY and reliability_complete
    secondary_termination = "NotRun"
    if reliability_secondary_used:
        model.CapacityRetention = pyo.Constraint(expr=model.SelectedMW >= RELIABILITY_CAPACITY_RETENTION_FRACTION * best_capacity)
        model.MaximizeCapacity.deactivate()
        model.SelectedRerouteCapacity = pyo.Expression(expr=sum(float(lookup[j]["ReliabilityCapacityMW"]) * model.x[j] for j in model.CANDIDATES))
        model.MaximizeReroute = pyo.Objective(expr=model.SelectedRerouteCapacity, sense=pyo.maximize)
        secondary_termination = solve_checked(model, solver, "Reliability/reroute secondary selection")

    selected_key = next(j for j in keys if pyo.value(model.x[j]) >= 0.5)
    out = candidate_results.copy()
    out["Selected"] = out["CandidateKey"].eq(selected_key)
    out["CapacitySelectionOptimumMW"] = best_capacity
    out["ReliabilitySecondarySelectionUsed"] = reliability_secondary_used
    out["PrimarySelectionTermination"] = primary_termination
    out["SecondarySelectionTermination"] = secondary_termination
    return out, selected_key, reliability_secondary_used


# ======================================================================================
# OUTPUT HELPERS
# ======================================================================================

def build_candidate_output(candidate_row, data, coverage_pct, substation_row, resource_row, reliability_row, engineering_row, summary):
    resolved_station = bool(substation_row["CapacityResolved"])
    reliability_ready = bool(summary["ReliabilityReady"])
    selection_eligible = True
    if STRICT_FINAL_MODE and not resolved_station:
        selection_eligible = False
    if STRICT_FINAL_MODE and ENABLE_RELIABILITY and truthy(reliability_row.get("ApplyReliabilityConstraint")) and not reliability_ready:
        selection_eligible = False

    protection_study_pass = engineering_row.get("ProtectionStudyPass", np.nan)
    short_circuit_study_pass = engineering_row.get("ShortCircuitStudyPass", np.nan)
    validated_engineering_failure = (
        truthy(engineering_row.get("ApplyProtectionConstraint"))
        and (
            (pd.notna(protection_study_pass) and not truthy(protection_study_pass))
            or (pd.notna(short_circuit_study_pass) and not truthy(short_circuit_study_pass))
        )
    )
    if EXCLUDE_VALIDATED_ENGINEERING_FAILURES and validated_engineering_failure:
        selection_eligible = False
    return {
        "CandidateKey": candidate_row["CandidateKey"],
        "GridID": candidate_row["GridID"],
        "SubstationID": candidate_row["SubstationID"],
        "CandidateLineID": candidate_row["CandidateLineID"],
        "OriginalFirmHostingMW": float(candidate_row["FirmHostingMW"]),
        "ModeledIntervals": len(data),
        "ModeledHoursEquivalent": len(data) * DT_HOURS,
        "ThermalSubstationIntersectionPct": coverage_pct,
        "ThermalIncrementalHeadroomFactor": THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR,
        "SubstationLoadingLimitFraction": SUBSTATION_LOADING_LIMIT_FRACTION,
        "ThermalOnlyFirmMW": summary["ThermalOnlyReservedFirmMW"],
        "SubstationOnlyFirmIncrementalMW": summary["SubstationOnlyFirmIncrementalMW"],
        "CombinedNoFlexFirmMW": summary["CombinedNoFlexFirmMW"],
        "MaxOptimizedDCMW": summary["MaxOptimizedDCMW"],
        "CapacityGainFromFlexMW": summary["CapacityGainFromFlexMW"],
        "BatteryPowerMW": summary["BatteryPowerMW"],
        "BatteryPowerCapMW": safe_float(resource_row.get("BatteryMaxPowerMW"), 0.0),
        "BatteryEnergyMWh": summary["BatteryEnergyMWh"],
        "BatteryEnergyCapMWh": safe_float(resource_row.get("BatteryMaxEnergyMWh"), 0.0),
        "BatteryThroughputMWh": summary["BatteryThroughputMWh"],
        "PVCapacityMW": summary["PVCapacityMW"],
        "PVDesignCapMW": summary["PVCapacityCapMW"],
        "FlexibleComputeMWh": summary["FlexibleComputeMWh"],
        "PVUsedMWh": summary["PVUsedMWh"],
        "DRMWh": summary["DRMWh"],
        "EVShiftedMWh": summary["EVShiftedMWh"],
        "EVMaximumBacklogMWh": summary["EVMaximumBacklogMWh"],
        "BatteryActive": summary["BatteryActive"],
        "PVActive": summary["PVActive"],
        "EVActive": summary["EVActive"],
        "DRActive": summary["DRActive"],
        "FlexibleComputeActive": summary["FlexibleComputeActive"],
        "ACConstraintApplied": summary["ACConstraintApplied"],
        "ProtectionConstraintApplied": summary["ProtectionConstraintApplied"],
        "IncrementalDeliveryEfficiency": summary["IncrementalDeliveryEfficiency"],
        "ProtectionMaxAdditionalMW": summary["ProtectionMaxAdditionalMW"],
        "ReactivePowerLimitMVAr": summary["ReactivePowerLimitMVAr"],
        "ProtectionStudyPass": engineering_row.get("ProtectionStudyPass", np.nan),
        "ShortCircuitStudyPass": engineering_row.get("ShortCircuitStudyPass", np.nan),
        "ACStudySource": engineering_row.get("ACStudySource", ""),
        "ProtectionStudySource": engineering_row.get("ProtectionStudySource", ""),
        "SubstationCapacityResolved": resolved_station,
        "SubstationCapacityMethod": substation_row["CapacityMethod"],
        "SubstationCapacitySource": substation_row["CapacitySource"],
        "ReliabilityModelEnabled": ENABLE_RELIABILITY and truthy(reliability_row.get("ApplyReliabilityConstraint")),
        "ReliabilityReady": reliability_ready,
        "ReliabilityCapacityMW": summary["ReliabilityCapacityMW"],
        "CriticalLoadFraction": summary["CriticalLoadFraction"],
        "ReliabilityCapacityMarginMW": summary["ReliabilityCapacityMarginMW"],
        "ReliabilityBatteryCredit": summary["ReliabilityBatteryCredit"],
        "ContingencyBatteryGapMW": summary["ContingencyBatteryGapMW"],
        "RequiredContingencyEnergyMWh": summary["RequiredContingencyEnergyMWh"],
        "ReliabilitySource": reliability_row.get("ReliabilitySource", ""),
        "ResourceSource": resource_row.get("ResourceSource", ""),
        "SelectionEligible": selection_eligible,
        "PrimaryOperationalTermination": summary["PrimaryTermination"],
        "SecondaryOperationalTermination": summary["SecondaryTermination"]
    }

def build_assumptions(solver_name, substation_capacities, resource_inputs, reliability_inputs, engineering_inputs, pv_shape, ev_profiles, dr_profiles, ac_limit_profiles):
    return pd.DataFrame([
        {"Item": "Centralized siting decision", "Value": "Exactly one candidate location is selected."},
        {"Item": "Candidate pool", "Value": "This script evaluates every candidate supplied by the upstream MILP_Readiness/thermal workflow. It has no internal top-10 cutoff. To evaluate more locations, rerun the upstream five-minute thermal model/audit on those locations first."},
        {"Item": "Thermal input", "Value": f"Five-minute AvailableThermalHostingMW from {THERMAL_INTERVAL_FILE}."},
        {"Item": "Thermal treatment", "Value": f"Headline all-resource scenario uses {THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR:.3f} of the already-calculated incremental hosting. Current setting is the full 100% base result."},
        {"Item": "Substation limit", "Value": f"When TotalCapacityMW is known, five-minute existing station load plus new net load must remain <= {SUBSTATION_LOADING_LIMIT_FRACTION:.0%} of stated station capacity. Current setting uses the explicit stated capacity with no extra hidden haircut."},
        {"Item": "Unresolved substation policy", "Value": UNRESOLVED_SUBSTATION_POLICY},
        {"Item": "Strict final mode", "Value": STRICT_FINAL_MODE},
        {"Item": "Resolved substations", "Value": f"{int(substation_capacities['CapacityResolved'].sum())}/{len(substation_capacities)}"},
        {"Item": "Battery", "Value": f"BatteryMaxPowerMW and BatteryMaxEnergyMWh in {RESOURCE_INPUT_FILE} are explicit design caps. The MILP optimizes the smallest required battery size after maximizing DC MW."},
        {"Item": "PV", "Value": f"ProposedPVCapacityMW in {RESOURCE_INPUT_FILE} is interpreted as the maximum allowed new co-located PV size. The MILP optimizes actual PV MW against empirical availability from {PV_SHAPE_FILE}; historical PV is not double-counted."},
        {"Item": "EV", "Value": f"{EV_PROFILE_FILE} provides measured flexibility envelopes. EV deferred energy is causal and must be fully repaid by the end of each {EV_REPAYMENT_TIMEZONE} local calendar day."},
        {"Item": "DR", "Value": f"External DR from {DR_PROFILE_FILE} affects only the shared-substation constraint unless separately localized."},
        {"Item": "Flexible compute", "Value": "Candidate-specific interval and annual-equivalent curtailment limits are read from the resource workbook."},
        {"Item": "Reliability/rerouting", "Value": "Feeder-tie/N-1 support is applied only where ApplyReliabilityConstraint=True and a validated contingency capacity is supplied. It does not create normal-state hosting capacity."},
        {"Item": "AC / voltage / reactive / losses", "Value": f"The MILP does not fabricate AC physics. Validated AC study envelopes may be supplied through {ENGINEERING_INPUT_FILE} and {AC_LIMIT_PROFILE_FILE}. IncrementalDeliveryEfficiency may represent validated losses; an optional MVAr envelope may also be enforced."},
        {"Item": "Protection / short circuit", "Value": f"Validated protection/short-circuit pass/fail and optional additional-MW limits may be supplied through {ENGINEERING_INPUT_FILE}. Missing studies remain unresolved."},
        {"Item": "PV shape rows", "Value": len(pv_shape)},
        {"Item": "EV flexibility profile rows", "Value": len(ev_profiles)},
        {"Item": "DR profile rows", "Value": len(dr_profiles)},
        {"Item": "Resource input rows", "Value": len(resource_inputs)},
        {"Item": "Reliability input rows", "Value": len(reliability_inputs)},
        {"Item": "Engineering input rows", "Value": len(engineering_inputs)},
        {"Item": "AC limit profile rows", "Value": len(ac_limit_profiles)},
        {"Item": "Solver", "Value": solver_name}
    ])


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading("GREENSBORO COMPREHENSIVE SINGLE-SITE DATA-CENTER MILP")
    print("One centralized site is selected using five-minute thermal hosting plus every optional resource or engineering constraint that has a defensible input. Missing capabilities receive zero credit rather than being invented.")

    candidates = load_candidates()
    thermal_profiles = load_thermal_profiles(candidates)
    substation_profiles = load_substation_profiles(candidates)
    substation_capacities = load_substation_capacities(candidates)
    resource_inputs = load_resource_inputs(candidates)
    reliability_inputs = load_reliability_inputs(candidates)
    engineering_inputs = load_engineering_inputs(candidates)
    pv_shape, ev_profiles, dr_profiles = load_optional_profiles(candidates)
    ac_limit_profiles = load_ac_limit_profile(candidates)
    solver_name, solver = choose_solver()

    print(f"Candidates admitted: {len(candidates)}")
    print(f"Substations represented: {candidates['SubstationID'].nunique()}")
    print(f"Resolved substation capacities: {int(substation_capacities['CapacityResolved'].sum())}/{len(substation_capacities)}")
    print(f"Reliability/rerouting constraints ready: {int(reliability_inputs['ReliabilityDataReady'].sum())}/{len(reliability_inputs)}")
    print(f"PV availability-shape rows: {len(pv_shape):,}")
    print(f"EV flexibility profile rows: {len(ev_profiles):,}")
    print(f"DR profile rows: {len(dr_profiles):,}")
    print(f"Engineering input rows: {len(engineering_inputs):,}")
    print(f"AC limit profile rows: {len(ac_limit_profiles):,}")
    print(f"Thermal incremental-headroom factor: {THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR:.0%}")
    print(f"Explicit substation capacity factor: {SUBSTATION_LOADING_LIMIT_FRACTION:.0%}")
    print(f"Solver: {solver_name}")

    if not substation_capacities["CapacityResolved"].all():
        print("IMPORTANT: At least one candidate substation still lacks a defensible explicit capacity. Such candidates are upper-bound cases unless excluded by policy.")
    if int(reliability_inputs["ReliabilityDataReady"].sum()) == 0:
        print("Feeder-tie/N-1 credit: no validated reliability constraints are active. This is expected unless an external/validated transfer result is supplied.")
    if int(engineering_inputs["ApplyACConstraint"].sum()) == 0:
        print("AC voltage/reactive/loss constraints: no validated AC study envelopes are active.")
    if int(engineering_inputs["ApplyProtectionConstraint"].sum()) == 0:
        print("Protection/short-circuit constraints: no validated engineering limits are active.")

    substation_lookup = substation_capacities.set_index("SubstationID").to_dict("index")
    resource_lookup = resource_inputs.set_index("CandidateKey").to_dict("index")
    reliability_lookup = reliability_inputs.set_index("CandidateKey").to_dict("index")
    engineering_lookup = engineering_inputs.set_index("CandidateKey").to_dict("index")
    candidate_results = []
    errors = []
    prepared_cache = {}

    heading("1. CANDIDATE INTEGRATED OPTIMIZATION")
    for index, candidate_row in candidates.iterrows():
        candidate_key = str(candidate_row["CandidateKey"])
        substation_id = str(candidate_row["SubstationID"])
        print(f"[{index + 1}/{len(candidates)}] {candidate_row['GridID']} | {candidate_row['CandidateLineID']} | {substation_id}")
        substation_row = substation_lookup.get(substation_id)
        resource_row = resource_lookup.get(candidate_key, {})
        reliability_row = reliability_lookup.get(candidate_key, {})
        engineering_row = engineering_lookup.get(candidate_key, {})
        if substation_row is None:
            errors.append({"CandidateKey": candidate_key, "Stage": "Substation", "Error": f"No substation capacity record for {substation_id}"})
            continue
        try:
            data, coverage_pct = prepare_candidate_timeseries(candidate_row, thermal_profiles, substation_profiles, substation_row, resource_row, reliability_row, engineering_row, pv_shape, ev_profiles, dr_profiles, ac_limit_profiles)
            summary, _ = solve_candidate(candidate_row, data, substation_row, resource_row, reliability_row, engineering_row, solver, capture_dispatch=False)
            row = build_candidate_output(candidate_row, data, coverage_pct, substation_row, resource_row, reliability_row, engineering_row, summary)
            candidate_results.append(row)
            prepared_cache[candidate_key] = (data, coverage_pct)
            print(f"  No-flex firm ceiling: {row['CombinedNoFlexFirmMW']:.3f} MW")
            print(f"  Optimized supportable DC: {row['MaxOptimizedDCMW']:.3f} MW")
            print(f"  Flex gain: {row['CapacityGainFromFlexMW']:.3f} MW")
            print(f"  Resources/engineering: battery={row['BatteryActive']} | PV={row['PVActive']} | EV={row['EVActive']} | DR={row['DRActive']} | flex={row['FlexibleComputeActive']} | AC={row['ACConstraintApplied']} | protection={row['ProtectionConstraintApplied']}")
            print(f"  Substation: {row['SubstationCapacityMethod']} | reliability ready={row['ReliabilityReady']} | final-selection eligible={row['SelectionEligible']}")
        except Exception as exc:
            errors.append({"CandidateKey": candidate_key, "Stage": "IntegratedOptimization", "Error": str(exc)})
            print(f"  ERROR: {exc}")

    candidate_results_df = pd.DataFrame(candidate_results)
    errors_df = pd.DataFrame(errors)
    if candidate_results_df.empty:
        raise RuntimeError("No candidate completed the integrated optimization. Review inputs and errors.")

    heading("2. FINAL ONE-SITE SELECTION")
    selection_df, selected_key, reliability_secondary_used = select_final_site(candidate_results_df, solver)
    selected = selection_df[selection_df["CandidateKey"] == selected_key].iloc[0]
    print(f"Selected candidate: {selected['GridID']} | {selected['CandidateLineID']} | {selected['SubstationID']}")
    print(f"Selected optimized centralized DC capacity: {selected['MaxOptimizedDCMW']:.3f} MW")
    print(f"Safe no-flex firm baseline at selected site: {selected['CombinedNoFlexFirmMW']:.3f} MW")
    print(f"Capacity unlocked by modeled flexibility: {selected['CapacityGainFromFlexMW']:.3f} MW")
    print(f"Reliability secondary selection used: {reliability_secondary_used}")

    heading("3. SELECTED-SITE FULL FIVE-MINUTE DISPATCH")
    selected_candidate = candidates[candidates["CandidateKey"] == selected_key].iloc[0]
    selected_substation = substation_lookup[str(selected_candidate["SubstationID"])]
    selected_resource = resource_lookup[selected_key]
    selected_reliability = reliability_lookup[selected_key]
    selected_engineering = engineering_lookup[selected_key]
    selected_data, selected_coverage = prepared_cache[selected_key]
    selected_summary, selected_dispatch = solve_candidate(selected_candidate, selected_data, selected_substation, selected_resource, selected_reliability, selected_engineering, solver, capture_dispatch=True)
    selected_dispatch.insert(1, "CandidateKey", selected_key)
    selected_dispatch.insert(2, "GridID", selected_candidate["GridID"])
    selected_dispatch.insert(3, "SubstationID", selected_candidate["SubstationID"])
    selected_dispatch.insert(4, "CandidateLineID", selected_candidate["CandidateLineID"])

    selected_station_resolved = bool(selected["SubstationCapacityResolved"])
    selected_reliability_ready = bool(selected["ReliabilityReady"])
    selected_ac_applied = bool(selected["ACConstraintApplied"])
    selected_protection_applied = bool(selected["ProtectionConstraintApplied"])

    unresolved_parts = []
    if not selected_station_resolved:
        unresolved_parts.append("SUBSTATION_SOURCE")
    if not selected_ac_applied:
        unresolved_parts.append("AC_VOLTAGE_REACTIVE_LOSS")
    if not selected_protection_applied:
        unresolved_parts.append("PROTECTION_SHORT_CIRCUIT")
    if truthy(selected_reliability.get("ApplyReliabilityConstraint")) and not selected_reliability_ready:
        unresolved_parts.append("RELIABILITY_REROUTING")

    answer_status = "FULLY_CONSTRAINED_BY_SUPPLIED_INPUTS" if not unresolved_parts else "UPPER_BOUND_UNRESOLVED_" + "_".join(unresolved_parts)

    scenario_summary = pd.DataFrame([{
        "AnswerStatus": answer_status,
        "SelectedCandidateKey": selected_key,
        "SelectedGridID": selected_candidate["GridID"],
        "SelectedSubstationID": selected_candidate["SubstationID"],
        "SelectedCandidateLineID": selected_candidate["CandidateLineID"],
        "SelectedOptimizedDCMW": selected_summary["MaxOptimizedDCMW"],
        "SelectedNoFlexFirmMW": selected_summary["CombinedNoFlexFirmMW"],
        "CapacityGainFromFlexMW": selected_summary["CapacityGainFromFlexMW"],
        "BatteryPowerMW": selected_summary["BatteryPowerMW"],
        "BatteryEnergyMWh": selected_summary["BatteryEnergyMWh"],
        "PVCapacityMW": selected_summary["PVCapacityMW"],
        "PVDesignCapMW": selected_summary["PVCapacityCapMW"],
        "PVUsedMWh": selected_summary["PVUsedMWh"],
        "EVShiftedMWh": selected_summary["EVShiftedMWh"],
        "EVMaximumBacklogMWh": selected_summary["EVMaximumBacklogMWh"],
        "DRMWh": selected_summary["DRMWh"],
        "FlexibleComputeMWh": selected_summary["FlexibleComputeMWh"],
        "ReliabilityCapacityMW": selected_summary["ReliabilityCapacityMW"],
        "CriticalLoadFraction": selected_summary["CriticalLoadFraction"],
        "ReliabilityCapacityMarginMW": selected_summary["ReliabilityCapacityMarginMW"],
        "RequiredContingencyEnergyMWh": selected_summary["RequiredContingencyEnergyMWh"],
        "SelectedSubstationCapacityResolved": selected_station_resolved,
        "SelectedReliabilityReady": selected_reliability_ready,
        "SelectedACConstraintApplied": selected_ac_applied,
        "SelectedProtectionConstraintApplied": selected_protection_applied,
        "SelectedIncrementalDeliveryEfficiency": selected_summary["IncrementalDeliveryEfficiency"],
        "SelectedProtectionMaxAdditionalMW": selected_summary["ProtectionMaxAdditionalMW"],
        "SelectedReactivePowerLimitMVAr": selected_summary["ReactivePowerLimitMVAr"],
        "ReroutingCreditTreatment": "VALIDATED_INPUT_ONLY_WHEN_APPLY_FLAG_IS_TRUE",
        "ThermalIncrementalHeadroomFactor": THERMAL_INCREMENTAL_HEADROOM_RESERVE_FACTOR,
        "SubstationLoadingLimitFraction": SUBSTATION_LOADING_LIMIT_FRACTION,
        "ThermalSubstationIntersectionPct": selected_coverage,
        "Solver": solver_name
    }])

    assumptions = build_assumptions(solver_name, substation_capacities, resource_inputs, reliability_inputs, engineering_inputs, pv_shape, ev_profiles, dr_profiles, ac_limit_profiles)
    selection_df = selection_df.sort_values(["Selected", "SelectionEligible", "MaxOptimizedDCMW"], ascending=[False, False, False]).reset_index(drop=True)
    selected_dispatch = selected_dispatch.sort_values("TimestampUTC").reset_index(drop=True)
    selected_site_output = selection_df[selection_df["Selected"]].copy()
    if not selected_dispatch.empty:
        selected_site_output["MinimumThermalReserveSlackMW"] = float(selected_dispatch["ThermalReserveSlackMW"].min())
        selected_site_output["MinimumSubstationSlackMW"] = float(selected_dispatch["SubstationSlackMW"].min()) if selected_dispatch["SubstationSlackMW"].notna().any() else np.nan

    workbook_frames = {
        "Scenario_Summary": scenario_summary,
        "Selected_Site": selected_site_output,
        "Candidate_Optimization": selection_df,
        "Selected_5Min_Dispatch": selected_dispatch,
        "Substation_Capacities": substation_capacities,
        "Candidate_Resources": resource_inputs,
        "Reliability_Inputs": reliability_inputs,
        "Engineering_Limits": engineering_inputs,
        "AC_Limit_Profile": ac_limit_profiles,
        "Errors": errors_df,
        "Assumptions": assumptions
    }
    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
        for sheet_name, frame in workbook_frames.items():
            safe_frame = make_excel_safe(frame)
            safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
            format_excel(writer, sheet_name, safe_frame)

    heading("COMPREHENSIVE ALL-RESOURCES MILP RUN COMPLETE")
    print(f"Results workbook: {Path(OUTPUT_FILE).resolve()}")
    print(f"Selected site: {selected_candidate['GridID']} | {selected_candidate['CandidateLineID']}")
    print(f"All-resource scenario centralized data-center capacity: {selected_summary['MaxOptimizedDCMW']:.3f} MW")
    print(f"Answer status: {answer_status}")
    if not selected_station_resolved:
        print("IMPORTANT: The selected-site MW remains an upper bound with respect to unresolved substation capacity. Do not present it as a fully station-constrained final capacity until an explicit station capacity/headroom value is supplied.")
    if not ENABLE_RELIABILITY:
        print("Rerouting/N-1 benefit is intentionally assigned zero optimization credit because the EDM audits could not validate an operable alternate feed.")


if __name__ == "__main__":
    main()
