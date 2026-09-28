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
# MILP 2 / BATTERY + PV OUTAGE RESILIENCE - OPTIMIZED PV TREATMENT
#
# MILP 1 remains the normal-state existing-grid siting / hosting-capacity answer.
# This script DOES NOT increase or re-optimize the MILP-1 DC load.
#
# For each fixed MILP-1 site, outage-service target, outage duration, and proposed-PV
# scenario, this script sizes a battery that is robust to EVERY complete historical outage
# start represented by the 2024 Greensboro PV availability profile.
#
# Key improvement over the prior conservative resilience model:
#
#     PV -> critical data-center load first
#     excess PV -> battery charging
#     remaining excess PV -> curtailed
#     battery -> any remaining critical load
#
# Battery charging is constrained by:
#     - the battery PCS MW rating,
#     - battery maximum SOC,
#     - charge efficiency,
#     - the actual interval PV excess available.
#
# Battery discharge is constrained by:
#     - the same battery PCS MW rating,
#     - battery minimum SOC,
#     - discharge efficiency.
#
# A constraint-generation loop keeps the robust optimization computationally tractable:
#     1. seed the Pyomo model with the most stressful historical outage windows;
#     2. optimize battery nameplate MWh;
#     3. hold minimum MWh and minimize battery MW;
#     4. test the design against EVERY valid historical outage start;
#     5. add violating windows and re-solve until all historical windows pass.
#
# This is called "MILP 2" to preserve the project stage naming. Once the MILP-1 site is
# fixed, this battery/PV dispatch problem is linear and does not require artificial binary
# variables. Pyomo + HiGHS is still used for the optimization.
#
# IMPORTANT:
# Proposed PV is evaluated as explicit capacity scenarios rather than optimized without a
# cost/land/interconnection constraint. Battery MW/MWh are optimized automatically.
#
# PV receives outage credit only if the proposed PV and BESS are assumed to be on the
# data-center side of the outage boundary with grid-forming/islanding capability.
# ======================================================================================


# ======================================================================================
# FILES
# ======================================================================================

BASE_MILP_FILE = "greensboro_final_integrated_milp_results.xlsx"
BASE_MILP_CANDIDATE_SHEET = "Candidate_Optimization"
BASE_MILP_SELECTED_SHEET = "Selected_Site"

PV_SHAPE_FILE = "greensboro_pv_availability_shape_2024.csv.gz"

OUTPUT_FILE = "greensboro_milp2_battery_pv_resilience_optimized_results.xlsx"
OUTPUT_CSV = "greensboro_milp2_battery_pv_resilience_optimized_results.csv"


# ======================================================================================
# STUDY SETTINGS
# ======================================================================================

INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0
MODEL_TIMEZONE = "America/New_York"

# False = only the final MILP-1 selected site.
# True  = run the same resilience study for every MILP-1 candidate.
ANALYZE_ALL_MILP1_CANDIDATES = False

# Fraction of the fixed MILP-1 DC load that must remain served throughout the outage.
SERVICE_TARGETS = [0.50, 0.75, 0.90, 1.00]

# Outage lengths.
OUTAGE_DURATIONS_HOURS = [1.0, 2.0, 4.0, 8.0]

# Proposed new PV scenarios as multiples of the fixed MILP-1 DC load.
# Example with BaseDCMW = 13.51:
#   0.25 -> 3.38 MW new PV
#   0.50 -> 6.75 MW
#   0.75 -> 10.13 MW
#   1.00 -> 13.51 MW
PV_CAPACITY_RATIOS = [0.00, 0.25, 0.50, 0.75, 1.00]

# Optional absolute PV scenarios. Duplicates are removed.
PV_CAPACITY_MW_SCENARIOS = []

PV_AVAILABILITY_CLIP_PU = 1.10

# Required physical assumption for PV to operate while the utility source is unavailable.
ASSUME_GRID_FORMING_ISLANDING = True
PV_AVAILABLE_DURING_OUTAGE = True

# Battery assumptions.
BATTERY_CHARGE_EFFICIENCY = 0.95
BATTERY_DISCHARGE_EFFICIENCY = 0.95
BATTERY_START_SOC_FRACTION = 0.90
BATTERY_MIN_SOC_FRACTION = 0.10
BATTERY_MAX_SOC_FRACTION = 1.00

# Optional design margins. Leave at zero for the deterministic base case.
BATTERY_POWER_MARGIN_FRACTION = 0.00
BATTERY_ENERGY_MARGIN_FRACTION = 0.00

# Set, for example, to (8, 20) to evaluate only outages beginning between 08:00 and 19:59
# local time. None evaluates every historically represented start time.
OUTAGE_START_LOCAL_HOURS = None

# Robust constraint-generation controls.
INITIAL_CRITICAL_WINDOWS = 12
VIOLATING_WINDOWS_ADDED_PER_ITERATION = 6
MAX_CONSTRAINT_GENERATION_ITERATIONS = 25
DESIGN_VALIDATION_TOLERANCE_MWH = 1e-7
DESIGN_VALIDATION_TOLERANCE_MW = 1e-7

# Window-processing chunk size prevents large 8-hour sliding-window arrays from consuming
# excessive memory.
WINDOW_CHUNK_SIZE = 10000

# Number of bisection iterations used only for historical start-time energy diagnostics
# (P99 / P95 / P50 minimum-nameplate-energy estimates).
ENERGY_DIAGNOSTIC_BISECTION_ITERATIONS = 36

# Solver.
SOLVER_CANDIDATES = ["appsi_highs", "highs", "cbc", "glpk"]
OPTIMALITY_TOLERANCE = 1e-7


# ======================================================================================
# GENERAL HELPERS
# ======================================================================================

def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def truthy(value):
    if value is None or pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


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


def excel_safe(df):
    if df is None:
        return pd.DataFrame()

    out = df.copy()

    for column in out.columns:
        if isinstance(out[column].dtype, pd.DatetimeTZDtype):
            out[column] = out[column].dt.tz_localize(None)

    return out


def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return

    workbook = writer.book
    worksheet = writer.sheets[sheet_name]

    header_format = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})
    float_format = workbook.add_format({"num_format": "0.000"})
    percent_format = workbook.add_format({"num_format": "0.0%"})
    integer_format = workbook.add_format({"num_format": "0"})

    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)

        values = dataframe[column_name].tolist() if not dataframe.empty else []
        max_length = max((len(str(value)) for value in values if value is not None), default=0)
        width = min(max(max_length, len(str(column_name))) + 2, 55)
        lower = str(column_name).lower()

        if "fraction" in lower:
            worksheet.set_column(column_index, column_index, width, percent_format)
        elif pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, float_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


def percentile(values, q):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(np.quantile(values, q)) if len(values) else np.nan


def contiguous_blocks(timestamps):
    timestamps = pd.Series(pd.to_datetime(timestamps, utc=True, errors="coerce")).reset_index(drop=True)

    if timestamps.isna().any():
        raise RuntimeError("PV profile contains invalid timestamps.")

    expected = pd.Timedelta(minutes=INTERVAL_MINUTES)
    block_id = timestamps.diff().ne(expected).cumsum().to_numpy(dtype=int)

    blocks = []

    for block in np.unique(block_id):
        idx = np.where(block_id == block)[0]

        if len(idx):
            blocks.append((int(idx[0]), int(idx[-1])))

    return blocks


def valid_start_indices(timestamps, duration_steps):
    blocks = contiguous_blocks(timestamps)
    starts = []

    for first, last in blocks:
        block_length = last - first + 1

        if block_length < duration_steps:
            continue

        starts.extend(range(first, last - duration_steps + 2))

    starts = np.asarray(starts, dtype=int)

    if OUTAGE_START_LOCAL_HOURS is not None and len(starts):
        start_hour, end_hour = OUTAGE_START_LOCAL_HOURS
        local_hours = pd.to_datetime(pd.Series(timestamps).iloc[starts], utc=True).dt.tz_convert(MODEL_TIMEZONE).dt.hour.to_numpy()

        if start_hour <= end_hour:
            keep = (local_hours >= start_hour) & (local_hours < end_hour)
        else:
            keep = (local_hours >= start_hour) | (local_hours < end_hour)

        starts = starts[keep]

    return starts


def duration_to_steps(duration_hours):
    steps_float = float(duration_hours) / DT_HOURS
    steps = int(round(steps_float))

    if abs(steps - steps_float) > 1e-9:
        raise RuntimeError(f"Outage duration {duration_hours} h is not divisible by {INTERVAL_MINUTES} minutes.")

    if steps < 1:
        raise RuntimeError("Outage duration must contain at least one model interval.")

    return steps


# ======================================================================================
# MILP-1 HANDOFF
# ======================================================================================

def load_milp1_sites():
    path = Path(BASE_MILP_FILE)

    if not path.exists():
        raise FileNotFoundError(f"Could not find {BASE_MILP_FILE}. Run the final existing-system MILP first.")

    workbook = pd.ExcelFile(path)

    candidate_sheet = BASE_MILP_CANDIDATE_SHEET if BASE_MILP_CANDIDATE_SHEET in workbook.sheet_names else None
    selected_sheet = BASE_MILP_SELECTED_SHEET if BASE_MILP_SELECTED_SHEET in workbook.sheet_names else None

    if candidate_sheet is None and selected_sheet is None:
        raise RuntimeError(f"{BASE_MILP_FILE} must contain {BASE_MILP_CANDIDATE_SHEET} or {BASE_MILP_SELECTED_SHEET}.")

    candidates = pd.read_excel(path, sheet_name=candidate_sheet if candidate_sheet is not None else selected_sheet)

    required = {"CandidateKey", "GridID", "SubstationID", "CandidateLineID", "MaxOptimizedDCMW"}
    missing = required - set(candidates.columns)

    if missing:
        raise RuntimeError(f"MILP-1 handoff is missing required columns: {sorted(missing)}")

    candidates["CandidateKey"] = candidates["CandidateKey"].astype(str)
    candidates["GridID"] = candidates["GridID"].astype(str)
    candidates["SubstationID"] = candidates["SubstationID"].astype(str)
    candidates["CandidateLineID"] = candidates["CandidateLineID"].astype(str)
    candidates["MaxOptimizedDCMW"] = pd.to_numeric(candidates["MaxOptimizedDCMW"], errors="coerce")
    candidates = candidates[candidates["MaxOptimizedDCMW"].notna() & (candidates["MaxOptimizedDCMW"] > 0)].copy()

    if candidates.empty:
        raise RuntimeError("No positive MILP-1 site capacities were found.")

    if ANALYZE_ALL_MILP1_CANDIDATES:
        sites = candidates.copy()

    elif "Selected" in candidates.columns and candidates["Selected"].map(truthy).any():
        sites = candidates[candidates["Selected"].map(truthy)].copy()

    elif selected_sheet is not None:
        selected = pd.read_excel(path, sheet_name=selected_sheet)
        selected["CandidateKey"] = selected["CandidateKey"].astype(str)
        selected_key = str(selected.iloc[0]["CandidateKey"])
        sites = candidates[candidates["CandidateKey"] == selected_key].copy()

    else:
        sites = candidates.sort_values("MaxOptimizedDCMW", ascending=False).head(1).copy()

    if sites.empty:
        raise RuntimeError("Could not identify the MILP-1 selected site.")

    sites = sites.drop_duplicates(subset=["CandidateKey"], keep="first").reset_index(drop=True)
    sites = sites.rename(columns={"MaxOptimizedDCMW": "BaseDCMW"})

    return sites


# ======================================================================================
# PV PROFILE
# ======================================================================================

def load_pv_shape():
    path = Path(PV_SHAPE_FILE)

    if not path.exists():
        if any(float(ratio) > 0 for ratio in PV_CAPACITY_RATIOS) or len(PV_CAPACITY_MW_SCENARIOS):
            raise FileNotFoundError(f"Could not find {PV_SHAPE_FILE}. Run the PV/resource profile builder first.")

        return pd.DataFrame(columns=["TimestampUTC", "SystemPVAvailabilityPU"])

    df = pd.read_csv(path, compression="gzip" if str(path).lower().endswith(".gz") else "infer")

    required = {"TimestampUTC", "SystemPVAvailabilityPU"}
    missing = required - set(df.columns)

    if missing:
        raise RuntimeError(f"{PV_SHAPE_FILE} is missing required columns: {sorted(missing)}")

    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")

    availability_columns = [
        column
        for column in df.columns
        if column == "SystemPVAvailabilityPU" or str(column).startswith("PVAvailabilityPU__")
    ]

    for column in availability_columns:
        df[column] = pd.to_numeric(df[column], errors="coerce").clip(lower=0.0, upper=PV_AVAILABILITY_CLIP_PU)

    df = (
        df
        .dropna(subset=["TimestampUTC"])
        .drop_duplicates(subset=["TimestampUTC"], keep="last")
        .sort_values("TimestampUTC")
        .reset_index(drop=True)
    )

    return df[["TimestampUTC"] + availability_columns]


def site_pv_profile(site, pv_shape):
    if pv_shape.empty:
        return pd.DataFrame(columns=["TimestampUTC", "PVAvailabilityPU"])

    substation_id = str(site["SubstationID"])
    local_column = f"PVAvailabilityPU__{substation_id}"

    columns = ["TimestampUTC", "SystemPVAvailabilityPU"] + ([local_column] if local_column in pv_shape.columns else [])
    out = pv_shape[columns].copy()

    system_shape = pd.to_numeric(out["SystemPVAvailabilityPU"], errors="coerce")

    if local_column in out.columns:
        local_shape = pd.to_numeric(out[local_column], errors="coerce")
        out["PVAvailabilityPU"] = local_shape.where(local_shape.notna(), system_shape)
    else:
        out["PVAvailabilityPU"] = system_shape

    # Missing PV output is conservatively treated as zero production.
    out["PVAvailabilityPU"] = out["PVAvailabilityPU"].fillna(0.0).clip(lower=0.0, upper=PV_AVAILABILITY_CLIP_PU)

    return out[["TimestampUTC", "PVAvailabilityPU"]].sort_values("TimestampUTC").reset_index(drop=True)


# ======================================================================================
# PV SCENARIOS
# ======================================================================================

def build_pv_scenarios(base_dc_mw):
    values = [float(base_dc_mw) * float(ratio) for ratio in PV_CAPACITY_RATIOS]
    values.extend(float(value) for value in PV_CAPACITY_MW_SCENARIOS)

    values = sorted({round(max(value, 0.0), 9) for value in values})

    rows = []

    for pv_mw in values:
        ratio = pv_mw / float(base_dc_mw) if base_dc_mw > 0 else np.nan

        rows.append({
            "PVCapacityMW": pv_mw,
            "PVCapacityRatioToDC": ratio
        })

    return pd.DataFrame(rows)


# ======================================================================================
# OUTAGE WINDOW ARRAYS
# ======================================================================================

def outage_arrays(pv_availability_pu, start_index, duration_steps, served_load_mw, pv_capacity_mw):
    availability = np.asarray(pv_availability_pu[start_index:start_index + duration_steps], dtype=float)

    if ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0:
        pv_available_mw = availability * float(pv_capacity_mw)
    else:
        pv_available_mw = np.zeros(duration_steps, dtype=float)

    pv_direct_mw = np.minimum(float(served_load_mw), pv_available_mw)
    battery_discharge_mw = np.maximum(float(served_load_mw) - pv_direct_mw, 0.0)
    excess_pv_mw = np.maximum(pv_available_mw - pv_direct_mw, 0.0)

    return pv_available_mw, pv_direct_mw, battery_discharge_mw, excess_pv_mw


# ======================================================================================
# CRITICAL-WINDOW SEEDING
# ======================================================================================

def initial_critical_starts(pv_availability_pu, starts, duration_steps, served_load_mw, pv_capacity_mw):
    if len(starts) <= INITIAL_CRITICAL_WINDOWS:
        return [int(value) for value in starts]

    selected = set()

    for first in range(0, len(starts), WINDOW_CHUNK_SIZE):
        chunk_starts = starts[first:first + WINDOW_CHUNK_SIZE]

        windows = np.stack([
            pv_availability_pu[start:start + duration_steps]
            for start in chunk_starts
        ])

        if ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0:
            pv_mw = windows * float(pv_capacity_mw)
        else:
            pv_mw = np.zeros_like(windows)

        residual = np.maximum(float(served_load_mw) - pv_mw, 0.0)
        excess = np.maximum(pv_mw - float(served_load_mw), 0.0)

        no_recharge_delivered_energy = residual.sum(axis=1) * DT_HOURS
        peak_discharge = residual.max(axis=1)

        # Approximate storage stress with unrestricted PV charging. This is only for
        # selecting seed windows; the actual design is solved by Pyomo and then validated
        # against every start.
        stored_delta = BATTERY_CHARGE_EFFICIENCY * excess * DT_HOURS - residual * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
        cumulative = np.cumsum(stored_delta, axis=1)
        approximate_deficit = np.maximum(-np.min(cumulative, axis=1), 0.0)

        metrics = [
            no_recharge_delivered_energy,
            peak_discharge,
            approximate_deficit
        ]

        per_metric = max(1, INITIAL_CRITICAL_WINDOWS // len(metrics))

        for metric in metrics:
            local_count = min(per_metric, len(metric))
            local_indices = np.argpartition(metric, -local_count)[-local_count:]

            for local_index in local_indices:
                selected.add(int(chunk_starts[int(local_index)]))

    # Guarantee exact cap while preserving the union of different stress metrics.
    selected = list(selected)

    if len(selected) > INITIAL_CRITICAL_WINDOWS:
        # Rank the union by a simple combined stress score.
        scores = []

        for start in selected:
            _, _, discharge, excess = outage_arrays(
                pv_availability_pu,
                start,
                duration_steps,
                served_load_mw,
                pv_capacity_mw
            )

            energy = discharge.sum() * DT_HOURS
            peak = discharge.max() if len(discharge) else 0.0
            delta = BATTERY_CHARGE_EFFICIENCY * excess * DT_HOURS - discharge * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
            deficit = max(-np.min(np.cumsum(delta)), 0.0) if len(delta) else 0.0

            score = energy + peak * float(duration_steps) * DT_HOURS + deficit
            scores.append((score, start))

        selected = [start for _, start in sorted(scores, reverse=True)[:INITIAL_CRITICAL_WINDOWS]]

    return sorted(set(selected))


# ======================================================================================
# ROBUST PYOMO BATTERY / PV MODEL
# ======================================================================================

def solve_active_window_model(pv_availability_pu, active_starts, duration_steps, served_load_mw, pv_capacity_mw, solver, label):
    if not active_starts:
        raise RuntimeError("At least one active outage window is required.")

    window_data = {}

    for window_index, start in enumerate(active_starts):
        pv_available, pv_direct, discharge, excess = outage_arrays(
            pv_availability_pu,
            int(start),
            duration_steps,
            served_load_mw,
            pv_capacity_mw
        )

        window_data[window_index] = {
            "StartIndex": int(start),
            "PVAvailableMW": pv_available,
            "PVDirectMW": pv_direct,
            "BatteryDischargeMW": discharge,
            "ExcessPVMW": excess
        }

    model = pyo.ConcreteModel()

    model.W = pyo.RangeSet(0, len(active_starts) - 1)
    model.T = pyo.RangeSet(0, duration_steps - 1)
    model.S = pyo.RangeSet(0, duration_steps)

    model.BatteryInstalledMWh = pyo.Var(within=pyo.NonNegativeReals)
    model.BatteryPowerMW = pyo.Var(within=pyo.NonNegativeReals)

    model.PVChargeMW = pyo.Var(model.W, model.T, within=pyo.NonNegativeReals)
    model.SOCMWh = pyo.Var(model.W, model.S, within=pyo.NonNegativeReals)

    def initial_soc_rule(m, w):
        return m.SOCMWh[w, 0] == BATTERY_START_SOC_FRACTION * m.BatteryInstalledMWh

    model.InitialSOC = pyo.Constraint(model.W, rule=initial_soc_rule)

    def minimum_soc_rule(m, w, s):
        return m.SOCMWh[w, s] >= BATTERY_MIN_SOC_FRACTION * m.BatteryInstalledMWh

    model.MinimumSOC = pyo.Constraint(model.W, model.S, rule=minimum_soc_rule)

    def maximum_soc_rule(m, w, s):
        return m.SOCMWh[w, s] <= BATTERY_MAX_SOC_FRACTION * m.BatteryInstalledMWh

    model.MaximumSOC = pyo.Constraint(model.W, model.S, rule=maximum_soc_rule)

    def pv_charge_availability_rule(m, w, t):
        return m.PVChargeMW[w, t] <= float(window_data[int(w)]["ExcessPVMW"][int(t)])

    model.PVChargeAvailability = pyo.Constraint(model.W, model.T, rule=pv_charge_availability_rule)

    def charge_power_rule(m, w, t):
        return m.PVChargeMW[w, t] <= m.BatteryPowerMW

    model.ChargePower = pyo.Constraint(model.W, model.T, rule=charge_power_rule)

    def discharge_power_rule(m, w, t):
        return float(window_data[int(w)]["BatteryDischargeMW"][int(t)]) <= m.BatteryPowerMW

    model.DischargePower = pyo.Constraint(model.W, model.T, rule=discharge_power_rule)

    def soc_update_rule(m, w, t):
        discharge = float(window_data[int(w)]["BatteryDischargeMW"][int(t)])

        return (
            m.SOCMWh[w, t + 1]
            ==
            m.SOCMWh[w, t]
            + BATTERY_CHARGE_EFFICIENCY * m.PVChargeMW[w, t] * DT_HOURS
            - discharge * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
        )

    model.SOCUpdate = pyo.Constraint(model.W, model.T, rule=soc_update_rule)

    # Phase 1: minimum installed/nameplate battery energy.
    model.MinimizeEnergy = pyo.Objective(expr=model.BatteryInstalledMWh, sense=pyo.minimize)

    termination_energy = solve_checked(model, solver, label + " / minimize battery MWh")
    optimum_energy_mwh = float(pyo.value(model.BatteryInstalledMWh))

    model.EnergyOptimality = pyo.Constraint(
        expr=model.BatteryInstalledMWh <= optimum_energy_mwh + OPTIMALITY_TOLERANCE
    )

    model.MinimizeEnergy.deactivate()

    # Phase 2: minimum PCS MW while retaining the energy optimum.
    model.MinimizePower = pyo.Objective(expr=model.BatteryPowerMW, sense=pyo.minimize)

    termination_power = solve_checked(model, solver, label + " / minimize battery MW")

    battery_energy_mwh = float(pyo.value(model.BatteryInstalledMWh))
    battery_power_mw = float(pyo.value(model.BatteryPowerMW))

    return {
        "BatteryInstalledMWh": battery_energy_mwh,
        "BatteryPowerMW": battery_power_mw,
        "EnergyTermination": termination_energy,
        "PowerTermination": termination_power,
        "ActiveWindowCount": len(active_starts)
    }


# ======================================================================================
# ALL-WINDOW DESIGN VALIDATION
# ======================================================================================

def validate_design_against_all_starts(pv_availability_pu, starts, duration_steps, served_load_mw, pv_capacity_mw, battery_energy_mwh, battery_power_mw):
    min_soc_mwh = BATTERY_MIN_SOC_FRACTION * float(battery_energy_mwh)
    max_soc_mwh = BATTERY_MAX_SOC_FRACTION * float(battery_energy_mwh)
    start_soc_mwh = BATTERY_START_SOC_FRACTION * float(battery_energy_mwh)

    violating_records = []
    minimum_margin_overall = np.inf
    maximum_power_deficit_overall = 0.0

    for first in range(0, len(starts), WINDOW_CHUNK_SIZE):
        chunk_starts = starts[first:first + WINDOW_CHUNK_SIZE]
        n = len(chunk_starts)

        if n == 0:
            continue

        windows = np.stack([
            pv_availability_pu[start:start + duration_steps]
            for start in chunk_starts
        ])

        if ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0:
            pv_mw = windows * float(pv_capacity_mw)
        else:
            pv_mw = np.zeros_like(windows)

        pv_direct = np.minimum(float(served_load_mw), pv_mw)
        discharge = np.maximum(float(served_load_mw) - pv_direct, 0.0)
        excess = np.maximum(pv_mw - pv_direct, 0.0)

        max_discharge = discharge.max(axis=1)
        power_deficit = np.maximum(max_discharge - float(battery_power_mw), 0.0)

        soc = np.full(n, start_soc_mwh, dtype=float)
        minimum_soc_seen = soc.copy()

        for t in range(duration_steps):
            discharge_t = discharge[:, t]
            excess_t = excess[:, t]

            # PV excess charges the battery up to both PCS MW and available SOC headroom.
            charge_t = np.minimum(excess_t, float(battery_power_mw))

            if BATTERY_CHARGE_EFFICIENCY > 0 and DT_HOURS > 0:
                charge_headroom_mw = np.maximum(
                    (max_soc_mwh - soc) / (BATTERY_CHARGE_EFFICIENCY * DT_HOURS),
                    0.0
                )
                charge_t = np.minimum(charge_t, charge_headroom_mw)

            soc = (
                soc
                + BATTERY_CHARGE_EFFICIENCY * charge_t * DT_HOURS
                - discharge_t * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
            )

            minimum_soc_seen = np.minimum(minimum_soc_seen, soc)

        soc_margin = minimum_soc_seen - min_soc_mwh
        energy_deficit = np.maximum(-soc_margin, 0.0)

        minimum_margin_overall = min(minimum_margin_overall, float(np.min(soc_margin)))
        maximum_power_deficit_overall = max(maximum_power_deficit_overall, float(np.max(power_deficit)))

        violated = (
            (energy_deficit > DESIGN_VALIDATION_TOLERANCE_MWH)
            | (power_deficit > DESIGN_VALIDATION_TOLERANCE_MW)
        )

        if np.any(violated):
            local_indices = np.where(violated)[0]

            for local_index in local_indices:
                # Convert power deficit to an approximate MWh-equivalent stress so one
                # scalar can rank violations for constraint generation.
                severity = float(energy_deficit[local_index]) + float(power_deficit[local_index]) * duration_steps * DT_HOURS

                violating_records.append({
                    "StartIndex": int(chunk_starts[local_index]),
                    "EnergyDeficitMWh": float(energy_deficit[local_index]),
                    "PowerDeficitMW": float(power_deficit[local_index]),
                    "Severity": severity,
                    "MinimumSOCMarginMWh": float(soc_margin[local_index])
                })

    violations = pd.DataFrame(violating_records)

    if not violations.empty:
        violations = violations.sort_values(
            ["Severity", "EnergyDeficitMWh", "PowerDeficitMW"],
            ascending=[False, False, False]
        ).reset_index(drop=True)

    return {
        "ViolationCount": len(violations),
        "Violations": violations,
        "MinimumSOCMarginMWh": minimum_margin_overall if np.isfinite(minimum_margin_overall) else np.nan,
        "MaximumPowerDeficitMW": maximum_power_deficit_overall
    }


# ======================================================================================
# CONSTRAINT-GENERATION ROBUST OPTIMIZATION
# ======================================================================================

def solve_robust_design(pv_availability_pu, timestamps, starts, duration_steps, served_load_mw, pv_capacity_mw, solver, label):
    active_starts = initial_critical_starts(
        pv_availability_pu=pv_availability_pu,
        starts=starts,
        duration_steps=duration_steps,
        served_load_mw=served_load_mw,
        pv_capacity_mw=pv_capacity_mw
    )

    iteration_rows = []
    last_solution = None

    for iteration in range(1, MAX_CONSTRAINT_GENERATION_ITERATIONS + 1):
        solution = solve_active_window_model(
            pv_availability_pu=pv_availability_pu,
            active_starts=active_starts,
            duration_steps=duration_steps,
            served_load_mw=served_load_mw,
            pv_capacity_mw=pv_capacity_mw,
            solver=solver,
            label=f"{label} | iteration {iteration}"
        )

        validation = validate_design_against_all_starts(
            pv_availability_pu=pv_availability_pu,
            starts=starts,
            duration_steps=duration_steps,
            served_load_mw=served_load_mw,
            pv_capacity_mw=pv_capacity_mw,
            battery_energy_mwh=solution["BatteryInstalledMWh"],
            battery_power_mw=solution["BatteryPowerMW"]
        )

        iteration_rows.append({
            "Iteration": iteration,
            "ActiveWindows": len(active_starts),
            "BatteryInstalledMWh": solution["BatteryInstalledMWh"],
            "BatteryPowerMW": solution["BatteryPowerMW"],
            "ViolatingHistoricalWindows": validation["ViolationCount"],
            "MinimumSOCMarginMWh": validation["MinimumSOCMarginMWh"],
            "MaximumPowerDeficitMW": validation["MaximumPowerDeficitMW"]
        })

        last_solution = solution

        if validation["ViolationCount"] == 0:
            break

        violations = validation["Violations"]
        additions = []

        for start_index in violations["StartIndex"].tolist():
            start_index = int(start_index)

            if start_index not in active_starts:
                additions.append(start_index)

            if len(additions) >= VIOLATING_WINDOWS_ADDED_PER_ITERATION:
                break

        if not additions:
            raise RuntimeError(
                f"{label}: design still violates historical windows but no new unique windows could be added."
            )

        active_starts = sorted(set(active_starts).union(additions))

    else:
        raise RuntimeError(
            f"{label}: robust design did not converge within {MAX_CONSTRAINT_GENERATION_ITERATIONS} iterations."
        )

    # Apply optional deterministic design margins only after finding the exact robust optimum.
    robust_energy_mwh = last_solution["BatteryInstalledMWh"] * (1.0 + BATTERY_ENERGY_MARGIN_FRACTION)
    robust_power_mw = last_solution["BatteryPowerMW"] * (1.0 + BATTERY_POWER_MARGIN_FRACTION)

    final_validation = validate_design_against_all_starts(
        pv_availability_pu=pv_availability_pu,
        starts=starts,
        duration_steps=duration_steps,
        served_load_mw=served_load_mw,
        pv_capacity_mw=pv_capacity_mw,
        battery_energy_mwh=robust_energy_mwh,
        battery_power_mw=robust_power_mw
    )

    if final_validation["ViolationCount"] != 0:
        raise RuntimeError(
            f"{label}: final design failed validation against {final_validation['ViolationCount']} historical windows."
        )

    return {
        "BatteryInstalledMWh": robust_energy_mwh,
        "BatteryPowerMW": robust_power_mw,
        "UnmarginedBatteryInstalledMWh": last_solution["BatteryInstalledMWh"],
        "UnmarginedBatteryPowerMW": last_solution["BatteryPowerMW"],
        "ActiveCriticalStarts": active_starts,
        "ConstraintGenerationIterations": len(iteration_rows),
        "IterationAudit": pd.DataFrame(iteration_rows),
        "FinalValidation": final_validation,
        "EnergyTermination": last_solution["EnergyTermination"],
        "PowerTermination": last_solution["PowerTermination"]
    }


# ======================================================================================
# HISTORICAL START-TIME ENERGY DIAGNOSTICS
# ======================================================================================

def energy_feasible_unlimited_power(pv_windows, served_load_mw, energy_mwh):
    n = pv_windows.shape[0]

    energy_mwh = np.asarray(energy_mwh, dtype=float)

    if energy_mwh.ndim == 0:
        energy_mwh = np.full(n, float(energy_mwh), dtype=float)

    soc = BATTERY_START_SOC_FRACTION * energy_mwh
    min_soc = BATTERY_MIN_SOC_FRACTION * energy_mwh
    max_soc = BATTERY_MAX_SOC_FRACTION * energy_mwh

    feasible = np.ones(n, dtype=bool)

    for t in range(pv_windows.shape[1]):
        pv_t = pv_windows[:, t]
        pv_direct = np.minimum(float(served_load_mw), pv_t)
        discharge = np.maximum(float(served_load_mw) - pv_direct, 0.0)
        excess = np.maximum(pv_t - pv_direct, 0.0)

        soc = (
            soc
            + BATTERY_CHARGE_EFFICIENCY * excess * DT_HOURS
            - discharge * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
        )

        soc = np.minimum(soc, max_soc)
        feasible &= soc >= (min_soc - 1e-10)

    return feasible


def minimum_energy_by_historical_start(pv_availability_pu, starts, duration_steps, served_load_mw, pv_capacity_mw):
    usable_fraction = BATTERY_START_SOC_FRACTION - BATTERY_MIN_SOC_FRACTION

    if usable_fraction <= 0:
        raise RuntimeError("Battery start SOC must be above minimum SOC.")

    battery_only_upper = (
        float(served_load_mw)
        * duration_steps
        * DT_HOURS
        / (BATTERY_DISCHARGE_EFFICIENCY * usable_fraction)
    )

    requirements = np.zeros(len(starts), dtype=float)

    for first in range(0, len(starts), WINDOW_CHUNK_SIZE):
        chunk_starts = starts[first:first + WINDOW_CHUNK_SIZE]

        windows = np.stack([
            pv_availability_pu[start:start + duration_steps]
            for start in chunk_starts
        ])

        if ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0:
            pv_windows = windows * float(pv_capacity_mw)
        else:
            pv_windows = np.zeros_like(windows)

        low = np.zeros(len(chunk_starts), dtype=float)
        high = np.full(len(chunk_starts), battery_only_upper, dtype=float)

        for _ in range(ENERGY_DIAGNOSTIC_BISECTION_ITERATIONS):
            mid = (low + high) / 2.0
            feasible = energy_feasible_unlimited_power(
                pv_windows=pv_windows,
                served_load_mw=served_load_mw,
                energy_mwh=mid
            )

            high = np.where(feasible, mid, high)
            low = np.where(feasible, low, mid)

        requirements[first:first + len(chunk_starts)] = high

    return requirements


# ======================================================================================
# CRITICAL-WINDOW DISPATCH
# ======================================================================================

def simulate_window_dispatch(site, profile, start_index, service_target, duration_hours, pv_capacity_mw, battery_energy_mwh, battery_power_mw):
    duration_steps = duration_to_steps(duration_hours)
    block = profile.iloc[start_index:start_index + duration_steps].copy().reset_index(drop=True)

    served_load_mw = float(site["BaseDCMW"]) * float(service_target)

    if ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0:
        block["PVAvailableMW"] = block["PVAvailabilityPU"] * float(pv_capacity_mw)
    else:
        block["PVAvailableMW"] = 0.0

    block["PVUsedDirectMW"] = np.minimum(served_load_mw, block["PVAvailableMW"])
    block["BatteryDischargeMW"] = np.maximum(served_load_mw - block["PVUsedDirectMW"], 0.0)
    block["PVExcessAvailableMW"] = np.maximum(block["PVAvailableMW"] - block["PVUsedDirectMW"], 0.0)

    min_soc_mwh = BATTERY_MIN_SOC_FRACTION * float(battery_energy_mwh)
    max_soc_mwh = BATTERY_MAX_SOC_FRACTION * float(battery_energy_mwh)
    soc = BATTERY_START_SOC_FRACTION * float(battery_energy_mwh)

    soc_start = []
    soc_end = []
    charge = []
    curtailed = []

    for row in block.itertuples(index=False):
        soc_start.append(soc)

        excess_mw = float(row.PVExcessAvailableMW)
        discharge_mw = float(row.BatteryDischargeMW)

        charge_mw = min(excess_mw, float(battery_power_mw))

        if BATTERY_CHARGE_EFFICIENCY > 0:
            charge_headroom_mw = max(
                (max_soc_mwh - soc) / (BATTERY_CHARGE_EFFICIENCY * DT_HOURS),
                0.0
            )
            charge_mw = min(charge_mw, charge_headroom_mw)

        soc = (
            soc
            + BATTERY_CHARGE_EFFICIENCY * charge_mw * DT_HOURS
            - discharge_mw * DT_HOURS / BATTERY_DISCHARGE_EFFICIENCY
        )

        charge.append(charge_mw)
        curtailed.append(max(excess_mw - charge_mw, 0.0))
        soc_end.append(soc)

    block["BatteryChargeFromPVMW"] = charge
    block["PVCurtailedMW"] = curtailed
    block["BatterySOCStartMWh"] = soc_start
    block["BatterySOCEndMWh"] = soc_end
    block["BatterySOCStartFraction"] = block["BatterySOCStartMWh"] / float(battery_energy_mwh) if battery_energy_mwh > 0 else np.nan
    block["BatterySOCEndFraction"] = block["BatterySOCEndMWh"] / float(battery_energy_mwh) if battery_energy_mwh > 0 else np.nan
    block["BatteryMinimumAllowedSOCMWh"] = min_soc_mwh
    block["ServedLoadMW"] = served_load_mw
    block["GridImportMW"] = 0.0
    block["UnservedLoadMW"] = 0.0

    block["CandidateKey"] = site["CandidateKey"]
    block["GridID"] = site["GridID"]
    block["SubstationID"] = site["SubstationID"]
    block["CandidateLineID"] = site["CandidateLineID"]
    block["BaseDCMW"] = site["BaseDCMW"]
    block["ServiceTarget"] = service_target
    block["OutageDurationHours"] = duration_hours
    block["PVCapacityMW"] = pv_capacity_mw
    block["BatteryPowerMW"] = battery_power_mw
    block["BatteryInstalledMWh"] = battery_energy_mwh
    block["TimestampLocal"] = block["TimestampUTC"].dt.tz_convert(MODEL_TIMEZONE)

    ordered = [
        "CandidateKey",
        "GridID",
        "SubstationID",
        "CandidateLineID",
        "BaseDCMW",
        "ServiceTarget",
        "OutageDurationHours",
        "PVCapacityMW",
        "BatteryPowerMW",
        "BatteryInstalledMWh",
        "TimestampUTC",
        "TimestampLocal",
        "PVAvailabilityPU",
        "PVAvailableMW",
        "PVUsedDirectMW",
        "PVExcessAvailableMW",
        "BatteryChargeFromPVMW",
        "PVCurtailedMW",
        "BatteryDischargeMW",
        "BatterySOCStartMWh",
        "BatterySOCEndMWh",
        "BatterySOCStartFraction",
        "BatterySOCEndFraction",
        "BatteryMinimumAllowedSOCMWh",
        "ServedLoadMW",
        "GridImportMW",
        "UnservedLoadMW"
    ]

    return block[ordered]


# ======================================================================================
# PARETO FLAG
# ======================================================================================

def add_pareto_flag(results):
    out = results.copy()
    out["ParetoEfficient"] = False

    group_columns = ["CandidateKey", "ServiceTarget", "OutageDurationHours"]

    for _, group in out.groupby(group_columns, sort=False):
        indices = group.index.tolist()

        for i in indices:
            row_i = out.loc[i]
            dominated = False

            for j in indices:
                if i == j:
                    continue

                row_j = out.loc[j]

                no_worse = (
                    row_j["PVCapacityMW"] <= row_i["PVCapacityMW"] + 1e-9
                    and row_j["BatteryPowerMW"] <= row_i["BatteryPowerMW"] + 1e-9
                    and row_j["BatteryInstalledMWh"] <= row_i["BatteryInstalledMWh"] + 1e-9
                )

                strictly_better = (
                    row_j["PVCapacityMW"] < row_i["PVCapacityMW"] - 1e-9
                    or row_j["BatteryPowerMW"] < row_i["BatteryPowerMW"] - 1e-9
                    or row_j["BatteryInstalledMWh"] < row_i["BatteryInstalledMWh"] - 1e-9
                )

                if no_worse and strictly_better:
                    dominated = True
                    break

            out.loc[i, "ParetoEfficient"] = not dominated

    return out


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading("GREENSBORO MILP 2 - OPTIMIZED BATTERY + PV OUTAGE RESILIENCE")

    if not (
        0.0 <= BATTERY_MIN_SOC_FRACTION
        < BATTERY_START_SOC_FRACTION
        <= BATTERY_MAX_SOC_FRACTION
        <= 1.0
    ):
        raise RuntimeError("SOC fractions must satisfy 0 <= MIN < START <= MAX <= 1.")

    if not (0 < BATTERY_CHARGE_EFFICIENCY <= 1):
        raise RuntimeError("BATTERY_CHARGE_EFFICIENCY must be in (0, 1].")

    if not (0 < BATTERY_DISCHARGE_EFFICIENCY <= 1):
        raise RuntimeError("BATTERY_DISCHARGE_EFFICIENCY must be in (0, 1].")

    sites = load_milp1_sites()
    pv_shape = load_pv_shape()
    solver_name, solver = choose_solver()

    print("MILP 1 remains the normal-state existing-grid capacity answer.")
    print("MILP 2 fixes that DC MW and sizes battery storage for explicit outage service targets.")
    print("PV serves the critical load first; excess PV may recharge the battery during the outage.")
    print("Battery charging is limited by PCS MW, SOC headroom, and charge efficiency.")
    print("The robust design is validated against every complete historical outage start in the 2024 PV profile.")
    print(f"Sites admitted: {len(sites)}")
    print(f"Service targets: {SERVICE_TARGETS}")
    print(f"Outage durations: {OUTAGE_DURATIONS_HOURS}")
    print(f"PV capacity ratios to fixed DC load: {PV_CAPACITY_RATIOS}")
    print(f"Battery start/min/max SOC: {BATTERY_START_SOC_FRACTION:.0%}/{BATTERY_MIN_SOC_FRACTION:.0%}/{BATTERY_MAX_SOC_FRACTION:.0%}")
    print(f"Battery charge/discharge efficiency: {BATTERY_CHARGE_EFFICIENCY:.0%}/{BATTERY_DISCHARGE_EFFICIENCY:.0%}")
    print(f"PV grid-forming/islanding assumption active: {ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE}")
    print(f"Solver: {solver_name}")

    scenario_rows = []
    dispatch_frames = []
    constraint_generation_frames = []
    error_rows = []

    for site_index, site in sites.iterrows():
        candidate_key = str(site["CandidateKey"])
        base_dc_mw = float(site["BaseDCMW"])
        profile = site_pv_profile(site, pv_shape)

        if profile.empty:
            raise RuntimeError(f"No PV availability profile is available for {candidate_key}.")

        timestamps = profile["TimestampUTC"].reset_index(drop=True)
        pv_availability_pu = profile["PVAvailabilityPU"].to_numpy(dtype=float)
        pv_scenarios = build_pv_scenarios(base_dc_mw)

        heading(
            f"SITE {site_index + 1}/{len(sites)} - "
            f"{site['GridID']} | {site['CandidateLineID']} | FIXED DC={base_dc_mw:.3f} MW"
        )

        total_cases = len(SERVICE_TARGETS) * len(OUTAGE_DURATIONS_HOURS) * len(pv_scenarios)
        case_number = 0

        for service_target in SERVICE_TARGETS:
            if not (0 < float(service_target) <= 1):
                raise RuntimeError(f"Invalid service target: {service_target}")

            served_load_mw = base_dc_mw * float(service_target)

            for duration_hours in OUTAGE_DURATIONS_HOURS:
                duration_steps = duration_to_steps(duration_hours)
                starts = valid_start_indices(timestamps, duration_steps)

                if len(starts) == 0:
                    raise RuntimeError(f"No complete historical outage windows exist for {duration_hours} hours.")

                for _, pv_scenario in pv_scenarios.iterrows():
                    case_number += 1

                    pv_capacity_mw = float(pv_scenario["PVCapacityMW"])
                    pv_ratio = float(pv_scenario["PVCapacityRatioToDC"])

                    print(
                        f"  Case {case_number}/{total_cases}: "
                        f"target={service_target:.0%} | duration={duration_hours:g}h | "
                        f"PV={pv_capacity_mw:.3f} MW ({pv_ratio:.0%} of DC)"
                    )

                    label = (
                        f"{candidate_key} | target={service_target:.3f} | "
                        f"duration={duration_hours:g}h | PV={pv_capacity_mw:.3f}MW"
                    )

                    try:
                        robust = solve_robust_design(
                            pv_availability_pu=pv_availability_pu,
                            timestamps=timestamps,
                            starts=starts,
                            duration_steps=duration_steps,
                            served_load_mw=served_load_mw,
                            pv_capacity_mw=pv_capacity_mw,
                            solver=solver,
                            label=label
                        )

                        energy_requirements = minimum_energy_by_historical_start(
                            pv_availability_pu=pv_availability_pu,
                            starts=starts,
                            duration_steps=duration_steps,
                            served_load_mw=served_load_mw,
                            pv_capacity_mw=pv_capacity_mw
                        )

                        worst_energy_position = int(np.argmax(energy_requirements))
                        worst_energy_start_index = int(starts[worst_energy_position])
                        worst_start_utc = timestamps.iloc[worst_energy_start_index]
                        worst_start_local = worst_start_utc.tz_convert(MODEL_TIMEZONE)

                        dispatch = simulate_window_dispatch(
                            site=site,
                            profile=profile,
                            start_index=worst_energy_start_index,
                            service_target=float(service_target),
                            duration_hours=float(duration_hours),
                            pv_capacity_mw=pv_capacity_mw,
                            battery_energy_mwh=robust["BatteryInstalledMWh"],
                            battery_power_mw=robust["BatteryPowerMW"]
                        )

                        pv_available_mwh = float(dispatch["PVAvailableMW"].sum() * DT_HOURS)
                        pv_direct_mwh = float(dispatch["PVUsedDirectMW"].sum() * DT_HOURS)
                        pv_charge_mwh_ac = float(dispatch["BatteryChargeFromPVMW"].sum() * DT_HOURS)
                        pv_curtailed_mwh = float(dispatch["PVCurtailedMW"].sum() * DT_HOURS)
                        battery_delivered_mwh = float(dispatch["BatteryDischargeMW"].sum() * DT_HOURS)
                        minimum_soc_fraction = float(dispatch["BatterySOCEndFraction"].min()) if robust["BatteryInstalledMWh"] > 0 else np.nan

                        scenario_rows.append({
                            "CandidateKey": candidate_key,
                            "GridID": site["GridID"],
                            "SubstationID": site["SubstationID"],
                            "CandidateLineID": site["CandidateLineID"],
                            "BaseDCMW": base_dc_mw,
                            "ServiceTarget": float(service_target),
                            "ServedLoadMW": served_load_mw,
                            "OutageDurationHours": float(duration_hours),
                            "PVCapacityMW": pv_capacity_mw,
                            "PVCapacityRatioToDC": pv_ratio,
                            "PVOutageCreditEnabled": ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE and pv_capacity_mw > 0,
                            "PVCanRechargeBatteryDuringOutage": True,
                            "HistoricalOutageWindowsEvaluated": int(len(starts)),
                            "BatteryPowerMW": robust["BatteryPowerMW"],
                            "BatteryInstalledMWh": robust["BatteryInstalledMWh"],
                            "UnmarginedBatteryPowerMW": robust["UnmarginedBatteryPowerMW"],
                            "UnmarginedBatteryInstalledMWh": robust["UnmarginedBatteryInstalledMWh"],
                            "BatteryUsableSOCWindowMWh": robust["BatteryInstalledMWh"] * (BATTERY_START_SOC_FRACTION - BATTERY_MIN_SOC_FRACTION),
                            "BatteryDeliverableFromStartMWh": robust["BatteryInstalledMWh"] * (BATTERY_START_SOC_FRACTION - BATTERY_MIN_SOC_FRACTION) * BATTERY_DISCHARGE_EFFICIENCY,
                            "P99EnergyOptimalBatteryInstalledMWhByHistoricalStart": percentile(energy_requirements, 0.99),
                            "P95EnergyOptimalBatteryInstalledMWhByHistoricalStart": percentile(energy_requirements, 0.95),
                            "P50EnergyOptimalBatteryInstalledMWhByHistoricalStart": percentile(energy_requirements, 0.50),
                            "MinimumEnergyOptimalBatteryInstalledMWhByHistoricalStart": float(np.min(energy_requirements)),
                            "WorstEnergyOptimalHistoricalStartMWh": float(np.max(energy_requirements)),
                            "CriticalHistoricalStartUTC": worst_start_utc,
                            "CriticalHistoricalStartLocal": worst_start_local,
                            "CriticalWindowPVAvailableMWh": pv_available_mwh,
                            "CriticalWindowPVUsedDirectMWh": pv_direct_mwh,
                            "CriticalWindowPVChargedToBatteryACMWh": pv_charge_mwh_ac,
                            "CriticalWindowPVCurtailedMWh": pv_curtailed_mwh,
                            "CriticalWindowBatteryDeliveredMWh": battery_delivered_mwh,
                            "CriticalWindowMinimumSOCFraction": minimum_soc_fraction,
                            "ConstraintGenerationIterations": robust["ConstraintGenerationIterations"],
                            "FinalCriticalWindowCount": len(robust["ActiveCriticalStarts"]),
                            "FinalHistoricalViolationCount": robust["FinalValidation"]["ViolationCount"],
                            "FinalMinimumSOCMarginMWh": robust["FinalValidation"]["MinimumSOCMarginMWh"],
                            "FinalMaximumPowerDeficitMW": robust["FinalValidation"]["MaximumPowerDeficitMW"],
                            "BatteryStartSOCFraction": BATTERY_START_SOC_FRACTION,
                            "BatteryMinSOCFraction": BATTERY_MIN_SOC_FRACTION,
                            "BatteryMaxSOCFraction": BATTERY_MAX_SOC_FRACTION,
                            "BatteryChargeEfficiency": BATTERY_CHARGE_EFFICIENCY,
                            "BatteryDischargeEfficiency": BATTERY_DISCHARGE_EFFICIENCY,
                            "EnergyTermination": robust["EnergyTermination"],
                            "PowerTermination": robust["PowerTermination"]
                        })

                        dispatch_frames.append(dispatch)

                        audit = robust["IterationAudit"].copy()
                        audit["CandidateKey"] = candidate_key
                        audit["ServiceTarget"] = float(service_target)
                        audit["OutageDurationHours"] = float(duration_hours)
                        audit["PVCapacityMW"] = pv_capacity_mw
                        constraint_generation_frames.append(audit)

                    except Exception as exc:
                        error_rows.append({
                            "CandidateKey": candidate_key,
                            "ServiceTarget": float(service_target),
                            "OutageDurationHours": float(duration_hours),
                            "PVCapacityMW": pv_capacity_mw,
                            "Error": str(exc)
                        })

                        print(f"    ERROR: {exc}")

    results = pd.DataFrame(scenario_rows)
    errors = pd.DataFrame(error_rows)

    if results.empty:
        raise RuntimeError("No resilience scenario completed successfully.")

    # ----------------------------------------------------------------------------------
    # Compare PV scenarios with the battery-only case.
    # ----------------------------------------------------------------------------------

    battery_only = results[np.isclose(results["PVCapacityMW"], 0.0)][[
        "CandidateKey",
        "ServiceTarget",
        "OutageDurationHours",
        "BatteryPowerMW",
        "BatteryInstalledMWh",
        "P99EnergyOptimalBatteryInstalledMWhByHistoricalStart",
        "P95EnergyOptimalBatteryInstalledMWhByHistoricalStart",
        "P50EnergyOptimalBatteryInstalledMWhByHistoricalStart"
    ]].copy()

    battery_only = battery_only.rename(columns={
        "BatteryPowerMW": "BatteryOnlyPowerMW",
        "BatteryInstalledMWh": "BatteryOnlyInstalledMWh",
        "P99EnergyOptimalBatteryInstalledMWhByHistoricalStart": "BatteryOnlyP99InstalledMWh",
        "P95EnergyOptimalBatteryInstalledMWhByHistoricalStart": "BatteryOnlyP95InstalledMWh",
        "P50EnergyOptimalBatteryInstalledMWhByHistoricalStart": "BatteryOnlyP50InstalledMWh"
    })

    results = results.merge(
        battery_only,
        on=["CandidateKey", "ServiceTarget", "OutageDurationHours"],
        how="left"
    )

    results["RobustBatteryEnergySavingsFromPVMWh"] = results["BatteryOnlyInstalledMWh"] - results["BatteryInstalledMWh"]
    results["RobustBatteryPowerSavingsFromPVMW"] = results["BatteryOnlyPowerMW"] - results["BatteryPowerMW"]
    results["P99BatteryEnergySavingsFromPVMWh"] = results["BatteryOnlyP99InstalledMWh"] - results["P99EnergyOptimalBatteryInstalledMWhByHistoricalStart"]
    results["P95BatteryEnergySavingsFromPVMWh"] = results["BatteryOnlyP95InstalledMWh"] - results["P95EnergyOptimalBatteryInstalledMWhByHistoricalStart"]
    results["P50BatteryEnergySavingsFromPVMWh"] = results["BatteryOnlyP50InstalledMWh"] - results["P50EnergyOptimalBatteryInstalledMWhByHistoricalStart"]

    results = add_pareto_flag(results)

    tradeoff_columns = [
        "CandidateKey",
        "GridID",
        "BaseDCMW",
        "ServiceTarget",
        "OutageDurationHours",
        "PVCapacityMW",
        "PVCapacityRatioToDC",
        "BatteryPowerMW",
        "BatteryInstalledMWh",
        "RobustBatteryEnergySavingsFromPVMWh",
        "RobustBatteryPowerSavingsFromPVMW",
        "P95BatteryEnergySavingsFromPVMWh",
        "P50BatteryEnergySavingsFromPVMWh",
        "ParetoEfficient"
    ]

    tradeoff = results[tradeoff_columns].sort_values(
        ["CandidateKey", "ServiceTarget", "OutageDurationHours", "PVCapacityMW"]
    ).reset_index(drop=True)

    battery_only_summary = results[np.isclose(results["PVCapacityMW"], 0.0)].copy()
    dispatch = pd.concat(dispatch_frames, ignore_index=True) if dispatch_frames else pd.DataFrame()
    constraint_generation = pd.concat(constraint_generation_frames, ignore_index=True) if constraint_generation_frames else pd.DataFrame()

    assumptions = pd.DataFrame([
        {
            "Item": "MILP-1 handoff",
            "Value": f"Normal-state site and DC MW are fixed from {BASE_MILP_FILE}. MILP 2 never increases the MILP-1 base load."
        },
        {
            "Item": "Outage model",
            "Value": "Utility grid import is unavailable for the full outage. Required served load equals ServiceTarget x BaseDCMW."
        },
        {
            "Item": "Historical robustness",
            "Value": "The final battery design is explicitly validated against every complete historical outage start represented by the 2024 PV profile."
        },
        {
            "Item": "Constraint generation",
            "Value": "The Pyomo model begins with critical historical windows, solves battery MWh then MW, tests all starts, adds violating windows, and repeats until zero historical violations remain."
        },
        {
            "Item": "PV capacity",
            "Value": "New PV is evaluated as explicit capacity scenarios rather than optimized without a defensible cost, land, or interconnection objective."
        },
        {
            "Item": "PV availability",
            "Value": f"PV production uses the empirical Greensboro normalized availability profile from {PV_SHAPE_FILE}; a candidate-substation shape is preferred when available."
        },
        {
            "Item": "PV dispatch",
            "Value": "PV serves critical load first. Excess PV may charge the battery up to PCS MW and SOC headroom. Only remaining excess PV is curtailed."
        },
        {
            "Item": "PV islanding requirement",
            "Value": f"PV outage credit assumes PV/BESS are on the outage-isolated load side with grid-forming/islanding capability. Active={ASSUME_GRID_FORMING_ISLANDING and PV_AVAILABLE_DURING_OUTAGE}."
        },
        {
            "Item": "Battery PCS",
            "Value": "A single symmetric BatteryPowerMW rating constrains both battery charge and discharge."
        },
        {
            "Item": "Battery SOC",
            "Value": f"Start={BATTERY_START_SOC_FRACTION:.0%}, minimum={BATTERY_MIN_SOC_FRACTION:.0%}, maximum={BATTERY_MAX_SOC_FRACTION:.0%}."
        },
        {
            "Item": "Battery efficiency",
            "Value": f"Charge={BATTERY_CHARGE_EFFICIENCY:.0%}; discharge={BATTERY_DISCHARGE_EFFICIENCY:.0%}."
        },
        {
            "Item": "Battery sizing objective",
            "Value": "Lexicographic: minimize installed/nameplate battery MWh first, then minimize PCS MW while retaining the minimum-MWh solution."
        },
        {
            "Item": "P99/P95/P50 energy diagnostics",
            "Value": "These estimate the minimum nameplate energy by historical outage start with unrestricted charge power. The robust Pyomo design remains the governing result."
        },
        {
            "Item": "Not modeled",
            "Value": "Battery degradation, temperature derating, black-start transients, inverter overload, generator start sequences, utility protection/islanding approval, land/cost constraints, and post-outage recharge are outside this deterministic stage."
        },
        {
            "Item": "Solver",
            "Value": solver_name
        }
    ])

    # ----------------------------------------------------------------------------------
    # Console headline.
    # ----------------------------------------------------------------------------------

    heading("100% SERVICE RESILIENCE SUMMARY")

    full_service = results[
        np.isclose(results["ServiceTarget"], 1.0)
    ].sort_values(
        ["OutageDurationHours", "PVCapacityMW"]
    )

    display_columns = [
        "OutageDurationHours",
        "PVCapacityMW",
        "BatteryPowerMW",
        "BatteryInstalledMWh",
        "RobustBatteryEnergySavingsFromPVMWh",
        "P95BatteryEnergySavingsFromPVMWh",
        "CriticalHistoricalStartLocal",
        "ConstraintGenerationIterations"
    ]

    print(full_service[display_columns].to_string(index=False))

    # ----------------------------------------------------------------------------------
    # Outputs.
    # ----------------------------------------------------------------------------------

    workbook_frames = {
        "Scenario_Summary": results,
        "Battery_Only": battery_only_summary,
        "PV_Battery_Tradeoff": tradeoff,
        "Critical_Window_Dispatch": dispatch,
        "Constraint_Generation": constraint_generation,
        "MILP1_Sites": sites,
        "Assumptions": assumptions,
        "Errors": errors
    }

    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
        for sheet_name, frame in workbook_frames.items():
            safe = excel_safe(frame)
            safe.to_excel(writer, sheet_name=sheet_name[:31], index=False)
            format_excel(writer, sheet_name[:31], safe)

    results.to_csv(OUTPUT_CSV, index=False)

    heading("OPTIMIZED MILP 2 BATTERY + PV RESILIENCE RUN COMPLETE")

    print(f"Workbook: {Path(OUTPUT_FILE).resolve()}")
    print(f"Scenario CSV: {Path(OUTPUT_CSV).resolve()}")
    print()
    print("Interpretation:")
    print("1. MILP 1 remains the normal-state grid-capacity answer.")
    print("2. MILP 2 holds that load fixed and sizes battery/PV outage resilience.")
    print("3. PV now serves load AND can recharge the battery during the outage.")
    print("4. Battery MW and installed MWh are optimized automatically; proposed PV is tested as explicit scenarios.")
    print("5. The final design is validated against every complete historical outage start in the 2024 PV profile.")
    print("6. The deterministic results from this script are intended to be the final input baseline before Monte Carlo uncertainty analysis.")


if __name__ == "__main__":
    main()
