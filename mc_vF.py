from pathlib import Path
import numpy as np
import pandas as pd


# ======================================================================================
# GREENSBORO FINAL MONTE CARLO DESIGN RECOMMENDATION
# ======================================================================================
# PURPOSE:
# Convert the deterministic MILP results into a reliability-adjusted final BESS
# recommendation.
#
# PIPELINE POSITION:
#   MILP 1  -> selects the existing-grid data-center site and fixed DC MW.
#   MILP 2  -> calculates the minimum nominal BESS requirement for a target outage.
#   THIS SCRIPT -> holds site/load fixed, Monte Carlo stress-tests many BESS MW/MWh
#                  designs, and finds the smallest designs meeting 95%, 99%, and 99.9%
#                  simulated outage-survival targets.
#
# DEFAULT HEADLINE CASE:
#   - selected MILP-1 site
#   - 100% critical-load service
#   - 4-hour outage
#   - battery-only recommendation as the headline
#   - optional 50% and 100%-of-DC PV scenarios reported as supplemental comparisons
#
# MONTE CARLO UNCERTAINTY:
#   - historical outage start / real contiguous PV block
#   - starting battery SOC
#   - effective battery capacity derating
#   - charge efficiency
#   - discharge efficiency
#   - PCS/inverter availability
#   - optional critical-load factor
#   - optional PV-output multiplier
#
# IMPORTANT:
# - The same random scenario bank is reused for every candidate BESS design. This is
#   "common random numbers" and makes design-to-design comparisons much cleaner.
# - Historical PV values are sampled as intact blocks; 5-minute PV values are never
#   independently randomized.
# - Unresolved station capacity, feeder ties, AC voltage/reactive behavior, and
#   protection/short-circuit capability remain unresolved. They are NOT randomized.
# - The triangular engineering distributions below are scenario assumptions unless
#   independently supported by measured data.
# ======================================================================================


# ======================================================================================
# FILES
# ======================================================================================

BASE_MILP_FILE = "greensboro_final_integrated_milp_results.xlsx"
BASE_MILP_CANDIDATE_SHEET = "Candidate_Optimization"
BASE_MILP_SELECTED_SHEET = "Selected_Site"

RESILIENCE_MILP_FILE = "greensboro_milp2_battery_pv_resilience_optimized_results.xlsx"
RESILIENCE_SCENARIO_SHEET = "Scenario_Summary"

PV_SHAPE_FILE = "greensboro_pv_availability_shape_2024.csv.gz"

OUTPUT_WORKBOOK = "greensboro_final_monte_carlo_design_recommendation.xlsx"
OUTPUT_GRID_CSV = "greensboro_final_monte_carlo_design_grid.csv.gz"


# ======================================================================================
# DETERMINISTIC DESIGN HANDOFF
# ======================================================================================

DESIGN_SERVICE_TARGET = 1.00
DESIGN_OUTAGE_DURATION_HOURS = 4.0
DESIGN_PV_CAPACITY_MW = 0.0

# This final sizing stage focuses on the four-hour design basis.
# Additional durations can be added, but each duration will use the same MILP-2
# deterministic design handoff unless you change the DESIGN_* settings above.
MONTE_CARLO_OUTAGE_DURATIONS_HOURS = [4.0]


# ======================================================================================
# MONTE CARLO SETTINGS
# ======================================================================================

SIMULATIONS_PER_SCENARIO = 10000
RANDOM_SEED = 2026

# Reliability targets reported from the same Monte Carlo design grid.
RELIABILITY_TARGETS = [0.95, 0.99, 0.999]

# Headline final recommendation defaults to battery-only 99% simulated survival.
HEADLINE_RELIABILITY_TARGET = 0.99
HEADLINE_PV_RATIO = 0.00

# Supplemental PV scenarios. The battery design is allowed to resize for each PV
# scenario so we can quantify whether PV lowers the probabilistic BESS requirement.
PV_CAPACITY_RATIOS_TO_TEST = [0.00, 0.50, 1.00]
PV_CAPACITY_MW_SCENARIOS = []


# ======================================================================================
# BESS DESIGN SEARCH GRID
# ======================================================================================
# Multipliers are relative to the deterministic MILP-2 minimum design.
#
# Current deterministic four-hour result is expected to be approximately:
#   13.51 MW / 71.10 MWh
#
# The ranges below intentionally cover the stress envelope identified in the first
# fixed-design Monte Carlo. Adjust only if no passing design is found at the highest
# reliability target.
# ======================================================================================

POWER_MULTIPLIERS = np.round(np.arange(1.00, 1.251, 0.025), 3)
ENERGY_MULTIPLIERS = np.round(np.arange(1.00, 1.801, 0.05), 3)

# Recommendation policy when several designs pass the target:
#
# "MIN_MAX_RELATIVE_OVERSIZE"
#   Minimize the larger of MW oversize and MWh oversize relative to the deterministic
#   baseline. This avoids arbitrarily minimizing one dimension while oversizing the other.
#
# Ties are then broken by:
#   1. lower energy multiplier,
#   2. lower power multiplier,
#   3. lower mean unserved energy.
RECOMMENDATION_POLICY = "MIN_MAX_RELATIVE_OVERSIZE"


# ======================================================================================
# TIME / PV SETTINGS
# ======================================================================================

INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0
MODEL_TIMEZONE = "America/New_York"
PV_AVAILABILITY_CLIP_PU = 1.10

OUTAGE_START_LOCAL_HOURS = None

ASSUME_GRID_FORMING_ISLANDING = True
PV_AVAILABLE_DURING_OUTAGE = True


# ======================================================================================
# MONTE CARLO UNCERTAINTY ASSUMPTIONS
# ======================================================================================
# Tuples are (minimum, mode, maximum) triangular distributions.
# Set min = mode = max to hold the parameter deterministic.
# ======================================================================================

START_SOC_TRIANGULAR = (0.75, 0.90, 0.95)
BATTERY_CAPACITY_DERATE_TRIANGULAR = (0.90, 0.97, 1.00)
BATTERY_CHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
PCS_AVAILABILITY_TRIANGULAR = (0.95, 1.00, 1.00)

# Keep full critical load by default.
CRITICAL_LOAD_FACTOR_TRIANGULAR = (1.00, 1.00, 1.00)

# Historical PV already captures weather/day/night variation.
PV_OUTPUT_MULTIPLIER_TRIANGULAR = (1.00, 1.00, 1.00)

BATTERY_MIN_SOC_FRACTION = 0.10
BATTERY_MAX_SOC_FRACTION = 1.00


# ======================================================================================
# SUCCESS DEFINITION
# ======================================================================================

UNSERVED_ENERGY_SUCCESS_TOLERANCE_MWH = 1e-8
UNSERVED_POWER_SUCCESS_TOLERANCE_MW = 1e-8
SOC_DEPLETION_TOLERANCE_MWH = 1e-8


# ======================================================================================
# HELPERS
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


def triangular_or_constant(rng, parameters, size):
    minimum, mode, maximum = [float(value) for value in parameters]

    if minimum > mode or mode > maximum:
        raise ValueError(
            f"Invalid triangular parameters {parameters}. Required minimum <= mode <= maximum."
        )

    if np.isclose(minimum, maximum):
        return np.full(size, minimum, dtype=float)

    return rng.triangular(
        minimum,
        mode,
        maximum,
        size=size
    )


def percentile(values, q):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]
    return float(np.quantile(values, q)) if len(values) else np.nan


def duration_to_steps(duration_hours):
    steps_float = float(duration_hours) / DT_HOURS
    steps = int(round(steps_float))

    if abs(steps - steps_float) > 1e-9:
        raise RuntimeError(
            f"{duration_hours} hours is not divisible by {INTERVAL_MINUTES} minutes."
        )

    if steps < 1:
        raise RuntimeError("Outage duration must contain at least one model interval.")

    return steps


def contiguous_blocks(timestamps):
    timestamps = pd.Series(
        pd.to_datetime(
            timestamps,
            utc=True,
            errors="coerce"
        )
    ).reset_index(drop=True)

    if timestamps.isna().any():
        raise RuntimeError("PV profile contains invalid timestamps.")

    expected = pd.Timedelta(minutes=INTERVAL_MINUTES)

    block_id = (
        timestamps
        .diff()
        .ne(expected)
        .cumsum()
        .to_numpy(dtype=int)
    )

    blocks = []

    for block in np.unique(block_id):
        idx = np.where(block_id == block)[0]

        if len(idx):
            blocks.append(
                (
                    int(idx[0]),
                    int(idx[-1])
                )
            )

    return blocks


def valid_start_indices(timestamps, duration_steps):
    starts = []

    for first, last in contiguous_blocks(timestamps):
        block_length = last - first + 1

        if block_length >= duration_steps:
            starts.extend(
                range(
                    first,
                    last - duration_steps + 2
                )
            )

    starts = np.asarray(
        starts,
        dtype=int
    )

    if OUTAGE_START_LOCAL_HOURS is not None and len(starts):
        start_hour, end_hour = OUTAGE_START_LOCAL_HOURS

        local_hours = (
            pd.to_datetime(
                pd.Series(timestamps).iloc[starts],
                utc=True
            )
            .dt
            .tz_convert(MODEL_TIMEZONE)
            .dt
            .hour
            .to_numpy()
        )

        if start_hour <= end_hour:
            keep = (
                (local_hours >= start_hour)
                & (local_hours < end_hour)
            )
        else:
            keep = (
                (local_hours >= start_hour)
                | (local_hours < end_hour)
            )

        starts = starts[keep]

    return starts


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

    header_format = workbook.add_format(
        {
            "bold": True,
            "border": 1,
            "align": "center",
            "valign": "vcenter"
        }
    )

    float_format = workbook.add_format(
        {
            "num_format": "0.000"
        }
    )

    percent_format = workbook.add_format(
        {
            "num_format": "0.0%"
        }
    )

    for i, column in enumerate(dataframe.columns):
        worksheet.write(
            0,
            i,
            str(column),
            header_format
        )

        values = (
            dataframe[column].tolist()
            if not dataframe.empty
            else []
        )

        max_length = max(
            (
                len(str(value))
                for value in values
                if value is not None
            ),
            default=0
        )

        width = min(
            max(
                max_length,
                len(str(column))
            ) + 2,
            55
        )

        lower = str(column).lower()

        if "fraction" in lower or lower.endswith("rate"):
            worksheet.set_column(
                i,
                i,
                width,
                percent_format
            )

        elif pd.api.types.is_float_dtype(dataframe[column]):
            worksheet.set_column(
                i,
                i,
                width,
                float_format
            )

        else:
            worksheet.set_column(
                i,
                i,
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


# ======================================================================================
# LOAD SELECTED SITE / DETERMINISTIC BESS
# ======================================================================================

def load_selected_milp1_site():
    path = Path(BASE_MILP_FILE)

    if not path.exists():
        raise FileNotFoundError(
            f"Missing {BASE_MILP_FILE}. Run MILP 1 first."
        )

    workbook = pd.ExcelFile(path)

    if BASE_MILP_CANDIDATE_SHEET in workbook.sheet_names:
        sheet = BASE_MILP_CANDIDATE_SHEET
    elif BASE_MILP_SELECTED_SHEET in workbook.sheet_names:
        sheet = BASE_MILP_SELECTED_SHEET
    else:
        raise RuntimeError(
            f"{BASE_MILP_FILE} must contain "
            f"{BASE_MILP_CANDIDATE_SHEET} or {BASE_MILP_SELECTED_SHEET}."
        )

    candidates = pd.read_excel(
        path,
        sheet_name=sheet
    )

    required = {
        "CandidateKey",
        "GridID",
        "SubstationID",
        "CandidateLineID",
        "MaxOptimizedDCMW"
    }

    missing = required - set(candidates.columns)

    if missing:
        raise RuntimeError(
            f"MILP-1 handoff missing columns: {sorted(missing)}"
        )

    candidates["CandidateKey"] = candidates["CandidateKey"].astype(str)

    candidates["MaxOptimizedDCMW"] = pd.to_numeric(
        candidates["MaxOptimizedDCMW"],
        errors="coerce"
    )

    candidates = candidates[
        candidates["MaxOptimizedDCMW"].notna()
        & (candidates["MaxOptimizedDCMW"] > 0)
    ].copy()

    if candidates.empty:
        raise RuntimeError(
            "No positive MILP-1 candidate capacity exists."
        )

    if (
        "Selected" in candidates.columns
        and candidates["Selected"].map(truthy).any()
    ):
        selected = (
            candidates[
                candidates["Selected"].map(truthy)
            ]
            .head(1)
            .copy()
        )

    elif BASE_MILP_SELECTED_SHEET in workbook.sheet_names:
        selected_sheet = pd.read_excel(
            path,
            sheet_name=BASE_MILP_SELECTED_SHEET
        )

        selected_key = str(
            selected_sheet.iloc[0]["CandidateKey"]
        )

        selected = (
            candidates[
                candidates["CandidateKey"] == selected_key
            ]
            .head(1)
            .copy()
        )

    else:
        selected = (
            candidates
            .sort_values(
                "MaxOptimizedDCMW",
                ascending=False
            )
            .head(1)
            .copy()
        )

    if selected.empty:
        raise RuntimeError(
            "Could not identify the selected MILP-1 site."
        )

    row = selected.iloc[0].copy()

    row["BaseDCMW"] = float(
        row["MaxOptimizedDCMW"]
    )

    return row


def load_deterministic_bess(selected_site):
    path = Path(RESILIENCE_MILP_FILE)

    if not path.exists():
        raise FileNotFoundError(
            f"Missing {RESILIENCE_MILP_FILE}. Run optimized MILP 2 first."
        )

    scenarios = pd.read_excel(
        path,
        sheet_name=RESILIENCE_SCENARIO_SHEET
    )

    required = {
        "CandidateKey",
        "ServiceTarget",
        "OutageDurationHours",
        "PVCapacityMW",
        "BatteryPowerMW",
        "BatteryInstalledMWh"
    }

    missing = required - set(scenarios.columns)

    if missing:
        raise RuntimeError(
            f"MILP-2 result missing columns: {sorted(missing)}"
        )

    scenarios["CandidateKey"] = scenarios["CandidateKey"].astype(str)

    for column in [
        "ServiceTarget",
        "OutageDurationHours",
        "PVCapacityMW",
        "BatteryPowerMW",
        "BatteryInstalledMWh"
    ]:
        scenarios[column] = pd.to_numeric(
            scenarios[column],
            errors="coerce"
        )

    mask = (
        scenarios["CandidateKey"].eq(
            str(selected_site["CandidateKey"])
        )
        & np.isclose(
            scenarios["ServiceTarget"],
            DESIGN_SERVICE_TARGET
        )
        & np.isclose(
            scenarios["OutageDurationHours"],
            DESIGN_OUTAGE_DURATION_HOURS
        )
        & np.isclose(
            scenarios["PVCapacityMW"],
            DESIGN_PV_CAPACITY_MW
        )
    )

    design_rows = scenarios[mask].copy()

    if design_rows.empty:
        raise RuntimeError(
            "Requested deterministic MILP-2 design row was not found."
        )

    design = design_rows.iloc[0].copy()

    if (
        not np.isfinite(design["BatteryPowerMW"])
        or not np.isfinite(design["BatteryInstalledMWh"])
        or design["BatteryPowerMW"] <= 0
        or design["BatteryInstalledMWh"] <= 0
    ):
        raise RuntimeError(
            "MILP-2 battery design is invalid."
        )

    return design


# ======================================================================================
# PV PROFILE
# ======================================================================================

def load_pv_shape():
    path = Path(PV_SHAPE_FILE)

    if not path.exists():
        raise FileNotFoundError(
            f"Missing {PV_SHAPE_FILE}."
        )

    df = pd.read_csv(
        path,
        compression=(
            "gzip"
            if str(path).lower().endswith(".gz")
            else "infer"
        )
    )

    required = {
        "TimestampUTC",
        "SystemPVAvailabilityPU"
    }

    missing = required - set(df.columns)

    if missing:
        raise RuntimeError(
            f"{PV_SHAPE_FILE} missing columns: {sorted(missing)}"
        )

    df["TimestampUTC"] = pd.to_datetime(
        df["TimestampUTC"],
        utc=True,
        errors="coerce"
    )

    availability_columns = [
        column
        for column in df.columns
        if (
            column == "SystemPVAvailabilityPU"
            or str(column).startswith("PVAvailabilityPU__")
        )
    ]

    for column in availability_columns:
        df[column] = (
            pd.to_numeric(
                df[column],
                errors="coerce"
            )
            .clip(
                lower=0.0,
                upper=PV_AVAILABILITY_CLIP_PU
            )
        )

    df = (
        df
        .dropna(
            subset=["TimestampUTC"]
        )
        .drop_duplicates(
            subset=["TimestampUTC"],
            keep="last"
        )
        .sort_values(
            "TimestampUTC"
        )
        .reset_index(
            drop=True
        )
    )

    return df[
        ["TimestampUTC"] + availability_columns
    ]


def site_pv_profile(selected_site, pv_shape):
    substation_id = str(
        selected_site["SubstationID"]
    )

    local_column = (
        f"PVAvailabilityPU__{substation_id}"
    )

    columns = [
        "TimestampUTC",
        "SystemPVAvailabilityPU"
    ]

    if local_column in pv_shape.columns:
        columns.append(
            local_column
        )

    out = pv_shape[columns].copy()

    system = pd.to_numeric(
        out["SystemPVAvailabilityPU"],
        errors="coerce"
    )

    if local_column in out.columns:
        local = pd.to_numeric(
            out[local_column],
            errors="coerce"
        )

        out["PVAvailabilityPU"] = (
            local.where(
                local.notna(),
                system
            )
        )

    else:
        out["PVAvailabilityPU"] = system

    out["PVAvailabilityPU"] = (
        out["PVAvailabilityPU"]
        .fillna(0.0)
        .clip(
            lower=0.0,
            upper=PV_AVAILABILITY_CLIP_PU
        )
    )

    return (
        out[
            [
                "TimestampUTC",
                "PVAvailabilityPU"
            ]
        ]
        .reset_index(
            drop=True
        )
    )


def build_pv_scenarios(base_dc_mw):
    values = [
        float(base_dc_mw) * float(ratio)
        for ratio in PV_CAPACITY_RATIOS_TO_TEST
    ]

    values.extend(
        float(value)
        for value in PV_CAPACITY_MW_SCENARIOS
    )

    values = sorted(
        {
            round(
                max(value, 0.0),
                9
            )
            for value in values
        }
    )

    return pd.DataFrame(
        [
            {
                "PVCapacityMW": value,
                "PVCapacityRatioToDC": (
                    value / base_dc_mw
                    if base_dc_mw > 0
                    else np.nan
                )
            }
            for value in values
        ]
    )


# ======================================================================================
# COMMON-RANDOM-NUMBER SCENARIO BANK
# ======================================================================================

def build_scenario_bank(
    rng,
    pv_profile,
    outage_duration_hours
):
    n = int(SIMULATIONS_PER_SCENARIO)

    steps = duration_to_steps(
        outage_duration_hours
    )

    timestamps = (
        pv_profile["TimestampUTC"]
        .reset_index(
            drop=True
        )
    )

    pv_shape = (
        pv_profile["PVAvailabilityPU"]
        .to_numpy(
            dtype=float
        )
    )

    starts = valid_start_indices(
        timestamps,
        steps
    )

    if len(starts) == 0:
        raise RuntimeError(
            f"No valid {outage_duration_hours:g}-hour outage windows exist."
        )

    sampled_start_indices = rng.choice(
        starts,
        size=n,
        replace=True
    )

    window_indices = (
        sampled_start_indices[:, None]
        + np.arange(
            steps,
            dtype=int
        )[None, :]
    )

    sampled_pv_shape = (
        pv_shape[
            window_indices
        ]
    )

    sampled_start_utc = (
        timestamps
        .iloc[
            sampled_start_indices
        ]
        .reset_index(
            drop=True
        )
    )

    sampled_start_local = (
        sampled_start_utc
        .dt
        .tz_convert(
            MODEL_TIMEZONE
        )
    )

    return {
        "OutageDurationHours": float(
            outage_duration_hours
        ),
        "DurationSteps": steps,
        "SampledStartIndices": sampled_start_indices,
        "SampledStartUTC": sampled_start_utc,
        "SampledStartLocal": sampled_start_local,
        "PVShape": sampled_pv_shape,
        "StartSOCFraction": triangular_or_constant(
            rng,
            START_SOC_TRIANGULAR,
            n
        ),
        "CapacityDerate": triangular_or_constant(
            rng,
            BATTERY_CAPACITY_DERATE_TRIANGULAR,
            n
        ),
        "ChargeEfficiency": triangular_or_constant(
            rng,
            BATTERY_CHARGE_EFFICIENCY_TRIANGULAR,
            n
        ),
        "DischargeEfficiency": triangular_or_constant(
            rng,
            BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR,
            n
        ),
        "PCSAvailability": triangular_or_constant(
            rng,
            PCS_AVAILABILITY_TRIANGULAR,
            n
        ),
        "CriticalLoadFactor": triangular_or_constant(
            rng,
            CRITICAL_LOAD_FACTOR_TRIANGULAR,
            n
        ),
        "PVOutputMultiplier": triangular_or_constant(
            rng,
            PV_OUTPUT_MULTIPLIER_TRIANGULAR,
            n
        )
    }


# ======================================================================================
# ONE DESIGN EVALUATION
# ======================================================================================

def evaluate_design(
    selected_site,
    scenario_bank,
    battery_power_mw,
    battery_energy_mwh,
    pv_capacity_mw
):
    n = int(SIMULATIONS_PER_SCENARIO)

    steps = int(
        scenario_bank["DurationSteps"]
    )

    start_soc_fraction = np.clip(
        scenario_bank["StartSOCFraction"],
        BATTERY_MIN_SOC_FRACTION,
        BATTERY_MAX_SOC_FRACTION
    )

    capacity_derate = (
        scenario_bank["CapacityDerate"]
    )

    charge_efficiency = (
        scenario_bank["ChargeEfficiency"]
    )

    discharge_efficiency = (
        scenario_bank["DischargeEfficiency"]
    )

    pcs_availability = (
        scenario_bank["PCSAvailability"]
    )

    critical_load_factor = (
        scenario_bank["CriticalLoadFactor"]
    )

    pv_output_multiplier = (
        scenario_bank["PVOutputMultiplier"]
    )

    pv_shape = (
        scenario_bank["PVShape"]
    )

    base_dc_mw = float(
        selected_site["BaseDCMW"]
    )

    effective_capacity_mwh = (
        float(battery_energy_mwh)
        * capacity_derate
    )

    available_power_mw = (
        float(battery_power_mw)
        * pcs_availability
    )

    critical_load_mw = (
        base_dc_mw
        * DESIGN_SERVICE_TARGET
        * critical_load_factor
    )

    minimum_soc_mwh = (
        effective_capacity_mwh
        * BATTERY_MIN_SOC_FRACTION
    )

    maximum_soc_mwh = (
        effective_capacity_mwh
        * BATTERY_MAX_SOC_FRACTION
    )

    soc_mwh = (
        effective_capacity_mwh
        * start_soc_fraction
    )

    if (
        ASSUME_GRID_FORMING_ISLANDING
        and PV_AVAILABLE_DURING_OUTAGE
        and pv_capacity_mw > 0
    ):
        pv_available_mw = (
            pv_shape
            * float(pv_capacity_mw)
            * pv_output_multiplier[:, None]
        )

    else:
        pv_available_mw = np.zeros(
            (
                n,
                steps
            ),
            dtype=float
        )

    unserved_energy_mwh = np.zeros(
        n,
        dtype=float
    )

    peak_unserved_power_mw = np.zeros(
        n,
        dtype=float
    )

    minimum_soc_seen_mwh = (
        soc_mwh.copy()
    )

    energy_limited_any = np.zeros(
        n,
        dtype=bool
    )

    pcs_limited_any = np.zeros(
        n,
        dtype=bool
    )

    first_unserved_step = np.full(
        n,
        -1,
        dtype=int
    )

    pv_direct_mwh = np.zeros(
        n,
        dtype=float
    )

    pv_charge_mwh = np.zeros(
        n,
        dtype=float
    )

    battery_delivered_mwh = np.zeros(
        n,
        dtype=float
    )

    for t in range(steps):
        pv_t = (
            pv_available_mw[:, t]
        )

        pv_direct_mw = np.minimum(
            critical_load_mw,
            pv_t
        )

        remaining_load_mw = np.maximum(
            critical_load_mw
            - pv_direct_mw,
            0.0
        )

        excess_pv_mw = np.maximum(
            pv_t
            - pv_direct_mw,
            0.0
        )

        # --------------------------------------------------------------
        # Charge from excess PV.
        # --------------------------------------------------------------

        charge_power_mw = np.minimum(
            excess_pv_mw,
            available_power_mw
        )

        charge_headroom_mw = np.divide(
            np.maximum(
                maximum_soc_mwh
                - soc_mwh,
                0.0
            ),
            charge_efficiency
            * DT_HOURS,
            out=np.zeros(
                n,
                dtype=float
            ),
            where=(
                charge_efficiency
                * DT_HOURS
            ) > 0
        )

        charge_power_mw = np.minimum(
            charge_power_mw,
            charge_headroom_mw
        )

        soc_mwh = np.minimum(
            soc_mwh
            + charge_efficiency
            * charge_power_mw
            * DT_HOURS,
            maximum_soc_mwh
        )

        # --------------------------------------------------------------
        # Discharge into remaining load.
        # --------------------------------------------------------------

        requested_discharge_mw = (
            remaining_load_mw
        )

        pcs_limited_now = (
            requested_discharge_mw
            > available_power_mw
            + UNSERVED_POWER_SUCCESS_TOLERANCE_MW
        )

        discharge_after_pcs_mw = np.minimum(
            requested_discharge_mw,
            available_power_mw
        )

        stored_energy_available_mwh = np.maximum(
            soc_mwh
            - minimum_soc_mwh,
            0.0
        )

        energy_limited_max_discharge_mw = (
            stored_energy_available_mwh
            * discharge_efficiency
            / DT_HOURS
        )

        energy_limited_now = (
            discharge_after_pcs_mw
            > energy_limited_max_discharge_mw
            + UNSERVED_POWER_SUCCESS_TOLERANCE_MW
        )

        actual_discharge_mw = np.minimum(
            discharge_after_pcs_mw,
            energy_limited_max_discharge_mw
        )

        unserved_power_mw = np.maximum(
            requested_discharge_mw
            - actual_discharge_mw,
            0.0
        )

        soc_mwh = (
            soc_mwh
            - actual_discharge_mw
            * DT_HOURS
            / discharge_efficiency
        )

        soc_mwh = np.maximum(
            soc_mwh,
            minimum_soc_mwh
            - 1e-10
        )

        minimum_soc_seen_mwh = np.minimum(
            minimum_soc_seen_mwh,
            soc_mwh
        )

        unserved_energy_mwh += (
            unserved_power_mw
            * DT_HOURS
        )

        peak_unserved_power_mw = np.maximum(
            peak_unserved_power_mw,
            unserved_power_mw
        )

        pv_direct_mwh += (
            pv_direct_mw
            * DT_HOURS
        )

        pv_charge_mwh += (
            charge_power_mw
            * DT_HOURS
        )

        battery_delivered_mwh += (
            actual_discharge_mw
            * DT_HOURS
        )

        energy_limited_any |= (
            energy_limited_now
        )

        pcs_limited_any |= (
            pcs_limited_now
        )

        newly_failed = (
            (first_unserved_step < 0)
            & (
                unserved_power_mw
                > UNSERVED_POWER_SUCCESS_TOLERANCE_MW
            )
        )

        first_unserved_step[
            newly_failed
        ] = t

    success = (
        unserved_energy_mwh
        <= UNSERVED_ENERGY_SUCCESS_TOLERANCE_MWH
    )

    minimum_soc_fraction = np.divide(
        minimum_soc_seen_mwh,
        effective_capacity_mwh,
        out=np.full(
            n,
            np.nan
        ),
        where=(
            effective_capacity_mwh > 0
        )
    )

    endurance_hours = np.where(
        first_unserved_step >= 0,
        first_unserved_step
        * DT_HOURS,
        float(
            scenario_bank["OutageDurationHours"]
        )
    )

    return {
        "SuccessRate": float(
            success.mean()
        ),
        "FailureRate": float(
            1.0 - success.mean()
        ),
        "MeanUnservedEnergyMWh": float(
            unserved_energy_mwh.mean()
        ),
        "P95UnservedEnergyMWh": percentile(
            unserved_energy_mwh,
            0.95
        ),
        "P99UnservedEnergyMWh": percentile(
            unserved_energy_mwh,
            0.99
        ),
        "MaximumUnservedEnergyMWh": float(
            unserved_energy_mwh.max()
        ),
        "P95PeakUnservedPowerMW": percentile(
            peak_unserved_power_mw,
            0.95
        ),
        "P99PeakUnservedPowerMW": percentile(
            peak_unserved_power_mw,
            0.99
        ),
        "P05MinimumSOCFraction": percentile(
            minimum_soc_fraction,
            0.05
        ),
        "P50MinimumSOCFraction": percentile(
            minimum_soc_fraction,
            0.50
        ),
        "MinimumSOCHitRate": float(
            (
                minimum_soc_fraction
                <= BATTERY_MIN_SOC_FRACTION
                + 1e-10
            ).mean()
        ),
        "PCSConstraintEncounterRate": float(
            pcs_limited_any.mean()
        ),
        "EnergyConstraintEncounterRate": float(
            energy_limited_any.mean()
        ),
        "MeanEnduranceHours": float(
            endurance_hours.mean()
        ),
        "P05EnduranceHours": percentile(
            endurance_hours,
            0.05
        ),
        "MeanPVDirectMWh": float(
            pv_direct_mwh.mean()
        ),
        "MeanPVChargeToBatteryACMWh": float(
            pv_charge_mwh.mean()
        ),
        "MeanBatteryDeliveredMWh": float(
            battery_delivered_mwh.mean()
        )
    }


# ======================================================================================
# DESIGN GRID
# ======================================================================================

def run_design_grid(
    selected_site,
    deterministic_design,
    scenario_bank,
    pv_capacity_mw,
    pv_ratio
):
    base_power_mw = float(
        deterministic_design["BatteryPowerMW"]
    )

    base_energy_mwh = float(
        deterministic_design["BatteryInstalledMWh"]
    )

    rows = []

    total_designs = (
        len(POWER_MULTIPLIERS)
        * len(ENERGY_MULTIPLIERS)
    )

    design_number = 0

    for power_multiplier in POWER_MULTIPLIERS:
        battery_power_mw = (
            base_power_mw
            * float(power_multiplier)
        )

        for energy_multiplier in ENERGY_MULTIPLIERS:
            design_number += 1

            battery_energy_mwh = (
                base_energy_mwh
                * float(energy_multiplier)
            )

            metrics = evaluate_design(
                selected_site=selected_site,
                scenario_bank=scenario_bank,
                battery_power_mw=battery_power_mw,
                battery_energy_mwh=battery_energy_mwh,
                pv_capacity_mw=pv_capacity_mw
            )

            row = {
                "OutageDurationHours": float(
                    scenario_bank["OutageDurationHours"]
                ),
                "PVCapacityMW": float(
                    pv_capacity_mw
                ),
                "PVCapacityRatioToDC": float(
                    pv_ratio
                ),
                "PowerMultiplier": float(
                    power_multiplier
                ),
                "EnergyMultiplier": float(
                    energy_multiplier
                ),
                "BatteryPowerMW": float(
                    battery_power_mw
                ),
                "BatteryInstalledMWh": float(
                    battery_energy_mwh
                ),
                "MaxRelativeOversize": float(
                    max(
                        power_multiplier,
                        energy_multiplier
                    )
                )
            }

            row.update(
                metrics
            )

            for target in RELIABILITY_TARGETS:
                label = (
                    f"Meets_{target * 100:.1f}pct"
                    .replace(".", "_")
                )

                row[label] = (
                    metrics["SuccessRate"]
                    >= target
                )

            rows.append(
                row
            )

        print(
            f"    Power level {power_multiplier:.3f} complete "
            f"({design_number:,}/{total_designs:,} designs evaluated)"
        )

    return pd.DataFrame(
        rows
    )


# ======================================================================================
# RECOMMENDATION / PARETO LOGIC
# ======================================================================================

def add_pareto_flag(grid):
    out = grid.copy()
    out["ParetoEfficient"] = False

    for _, group in out.groupby(
        [
            "OutageDurationHours",
            "PVCapacityMW"
        ],
        sort=False
    ):
        indices = group.index.tolist()

        for i in indices:
            row_i = out.loc[i]
            dominated = False

            for j in indices:
                if i == j:
                    continue

                row_j = out.loc[j]

                no_worse = (
                    row_j["BatteryPowerMW"]
                    <= row_i["BatteryPowerMW"]
                    + 1e-9
                    and row_j["BatteryInstalledMWh"]
                    <= row_i["BatteryInstalledMWh"]
                    + 1e-9
                    and row_j["SuccessRate"]
                    >= row_i["SuccessRate"]
                    - 1e-12
                )

                strictly_better = (
                    row_j["BatteryPowerMW"]
                    < row_i["BatteryPowerMW"]
                    - 1e-9
                    or row_j["BatteryInstalledMWh"]
                    < row_i["BatteryInstalledMWh"]
                    - 1e-9
                    or row_j["SuccessRate"]
                    > row_i["SuccessRate"]
                    + 1e-12
                )

                if no_worse and strictly_better:
                    dominated = True
                    break

            out.loc[
                i,
                "ParetoEfficient"
            ] = not dominated

    return out


def choose_recommendation(
    grid,
    reliability_target
):
    passing = grid[
        grid["SuccessRate"]
        >= reliability_target
    ].copy()

    if passing.empty:
        return None

    if RECOMMENDATION_POLICY == "MIN_MAX_RELATIVE_OVERSIZE":
        passing = passing.sort_values(
            [
                "MaxRelativeOversize",
                "EnergyMultiplier",
                "PowerMultiplier",
                "MeanUnservedEnergyMWh"
            ],
            ascending=[
                True,
                True,
                True,
                True
            ]
        )

    else:
        raise RuntimeError(
            f"Unsupported RECOMMENDATION_POLICY={RECOMMENDATION_POLICY}"
        )

    return passing.iloc[0].copy()


def build_recommendation_table(grid):
    rows = []

    group_columns = [
        "OutageDurationHours",
        "PVCapacityMW",
        "PVCapacityRatioToDC"
    ]

    for group_values, group in grid.groupby(
        group_columns,
        sort=True
    ):
        outage_hours, pv_mw, pv_ratio = group_values

        for target in RELIABILITY_TARGETS:
            recommendation = choose_recommendation(
                group,
                target
            )

            if recommendation is None:
                rows.append(
                    {
                        "OutageDurationHours": outage_hours,
                        "PVCapacityMW": pv_mw,
                        "PVCapacityRatioToDC": pv_ratio,
                        "ReliabilityTarget": target,
                        "PassingDesignFound": False,
                        "RecommendedBatteryPowerMW": np.nan,
                        "RecommendedBatteryInstalledMWh": np.nan,
                        "PowerMultiplier": np.nan,
                        "EnergyMultiplier": np.nan,
                        "SimulatedSuccessRate": np.nan,
                        "MeanUnservedEnergyMWh": np.nan,
                        "P99UnservedEnergyMWh": np.nan,
                        "P05MinimumSOCFraction": np.nan,
                        "PCSConstraintEncounterRate": np.nan,
                        "EnergyConstraintEncounterRate": np.nan
                    }
                )

            else:
                rows.append(
                    {
                        "OutageDurationHours": outage_hours,
                        "PVCapacityMW": pv_mw,
                        "PVCapacityRatioToDC": pv_ratio,
                        "ReliabilityTarget": target,
                        "PassingDesignFound": True,
                        "RecommendedBatteryPowerMW": recommendation["BatteryPowerMW"],
                        "RecommendedBatteryInstalledMWh": recommendation["BatteryInstalledMWh"],
                        "PowerMultiplier": recommendation["PowerMultiplier"],
                        "EnergyMultiplier": recommendation["EnergyMultiplier"],
                        "SimulatedSuccessRate": recommendation["SuccessRate"],
                        "MeanUnservedEnergyMWh": recommendation["MeanUnservedEnergyMWh"],
                        "P99UnservedEnergyMWh": recommendation["P99UnservedEnergyMWh"],
                        "P05MinimumSOCFraction": recommendation["P05MinimumSOCFraction"],
                        "PCSConstraintEncounterRate": recommendation["PCSConstraintEncounterRate"],
                        "EnergyConstraintEncounterRate": recommendation["EnergyConstraintEncounterRate"]
                    }
                )

    return pd.DataFrame(
        rows
    )


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading(
        "GREENSBORO FINAL MONTE CARLO DESIGN RECOMMENDATION"
    )

    selected_site = load_selected_milp1_site()
    deterministic_design = load_deterministic_bess(
        selected_site
    )

    base_dc_mw = float(
        selected_site["BaseDCMW"]
    )

    deterministic_power_mw = float(
        deterministic_design["BatteryPowerMW"]
    )

    deterministic_energy_mwh = float(
        deterministic_design["BatteryInstalledMWh"]
    )

    pv_profile = site_pv_profile(
        selected_site,
        load_pv_shape()
    )

    pv_scenarios = build_pv_scenarios(
        base_dc_mw
    )

    print(
        f"Selected site: "
        f"{selected_site['GridID']} | "
        f"{selected_site['CandidateLineID']} | "
        f"{selected_site['SubstationID']}"
    )

    print(
        f"Fixed MILP-1 DC load: "
        f"{base_dc_mw:.6f} MW"
    )

    print(
        f"Nominal deterministic BESS: "
        f"{deterministic_power_mw:.6f} MW / "
        f"{deterministic_energy_mwh:.6f} MWh"
    )

    print(
        f"Deterministic source: "
        f"{DESIGN_SERVICE_TARGET:.0%} service | "
        f"{DESIGN_OUTAGE_DURATION_HOURS:g}h | "
        f"{DESIGN_PV_CAPACITY_MW:.3f} MW PV"
    )

    print(
        f"Monte Carlo simulations per scenario: "
        f"{SIMULATIONS_PER_SCENARIO:,}"
    )

    print(
        f"Reliability targets: "
        f"{RELIABILITY_TARGETS}"
    )

    print(
        f"Power search multipliers: "
        f"{POWER_MULTIPLIERS[0]:.3f} to "
        f"{POWER_MULTIPLIERS[-1]:.3f}"
    )

    print(
        f"Energy search multipliers: "
        f"{ENERGY_MULTIPLIERS[0]:.3f} to "
        f"{ENERGY_MULTIPLIERS[-1]:.3f}"
    )

    print(
        f"PV scenarios: "
        f"{PV_CAPACITY_RATIOS_TO_TEST}"
    )

    print(
        f"Random seed: "
        f"{RANDOM_SEED}"
    )

    rng = np.random.default_rng(
        RANDOM_SEED
    )

    all_grid_frames = []
    scenario_bank_audit_rows = []

    # One scenario bank per outage duration. The exact same random draws are reused
    # across every BESS and PV design for that duration.
    scenario_banks = {}

    for outage_hours in MONTE_CARLO_OUTAGE_DURATIONS_HOURS:
        scenario_bank = build_scenario_bank(
            rng=rng,
            pv_profile=pv_profile,
            outage_duration_hours=float(
                outage_hours
            )
        )

        scenario_banks[
            float(outage_hours)
        ] = scenario_bank

        scenario_bank_audit_rows.append(
            {
                "OutageDurationHours": float(
                    outage_hours
                ),
                "Simulations": SIMULATIONS_PER_SCENARIO,
                "MeanStartSOCFraction": float(
                    scenario_bank["StartSOCFraction"].mean()
                ),
                "P05StartSOCFraction": percentile(
                    scenario_bank["StartSOCFraction"],
                    0.05
                ),
                "MeanCapacityDerate": float(
                    scenario_bank["CapacityDerate"].mean()
                ),
                "P05CapacityDerate": percentile(
                    scenario_bank["CapacityDerate"],
                    0.05
                ),
                "MeanDischargeEfficiency": float(
                    scenario_bank["DischargeEfficiency"].mean()
                ),
                "P05DischargeEfficiency": percentile(
                    scenario_bank["DischargeEfficiency"],
                    0.05
                ),
                "MeanPCSAvailability": float(
                    scenario_bank["PCSAvailability"].mean()
                ),
                "P05PCSAvailability": percentile(
                    scenario_bank["PCSAvailability"],
                    0.05
                )
            }
        )

    total_cases = (
        len(MONTE_CARLO_OUTAGE_DURATIONS_HOURS)
        * len(pv_scenarios)
    )

    case_number = 0

    for outage_hours in MONTE_CARLO_OUTAGE_DURATIONS_HOURS:
        scenario_bank = scenario_banks[
            float(outage_hours)
        ]

        for _, pv_scenario in pv_scenarios.iterrows():
            case_number += 1

            pv_capacity_mw = float(
                pv_scenario["PVCapacityMW"]
            )

            pv_ratio = float(
                pv_scenario["PVCapacityRatioToDC"]
            )

            heading(
                f"DESIGN GRID {case_number}/{total_cases} | "
                f"OUTAGE={outage_hours:g}h | "
                f"PV={pv_capacity_mw:.3f} MW "
                f"({pv_ratio:.0%} OF DC)"
            )

            grid = run_design_grid(
                selected_site=selected_site,
                deterministic_design=deterministic_design,
                scenario_bank=scenario_bank,
                pv_capacity_mw=pv_capacity_mw,
                pv_ratio=pv_ratio
            )

            all_grid_frames.append(
                grid
            )

    design_grid = pd.concat(
        all_grid_frames,
        ignore_index=True
    )

    design_grid = add_pareto_flag(
        design_grid
    )

    recommendations = build_recommendation_table(
        design_grid
    )

    # ------------------------------------------------------------------
    # Headline final recommendation.
    # ------------------------------------------------------------------

    headline_mask = (
        np.isclose(
            recommendations["ReliabilityTarget"],
            HEADLINE_RELIABILITY_TARGET
        )
        & np.isclose(
            recommendations["PVCapacityRatioToDC"],
            HEADLINE_PV_RATIO
        )
        & np.isclose(
            recommendations["OutageDurationHours"],
            DESIGN_OUTAGE_DURATION_HOURS
        )
    )

    headline = recommendations[
        headline_mask
    ].copy()

    if headline.empty:
        raise RuntimeError(
            "Headline recommendation row was not found."
        )

    if not truthy(
        headline.iloc[0]["PassingDesignFound"]
    ):
        raise RuntimeError(
            "No design in the configured search grid meets the headline "
            f"{HEADLINE_RELIABILITY_TARGET:.1%} reliability target. "
            "Expand POWER_MULTIPLIERS and/or ENERGY_MULTIPLIERS."
        )

    headline_row = headline.iloc[0].copy()

    headline_output = pd.DataFrame(
        [
            {
                "CandidateKey": selected_site["CandidateKey"],
                "GridID": selected_site["GridID"],
                "SubstationID": selected_site["SubstationID"],
                "CandidateLineID": selected_site["CandidateLineID"],
                "FixedDCMW": base_dc_mw,
                "OutageDurationHours": DESIGN_OUTAGE_DURATION_HOURS,
                "ServiceTarget": DESIGN_SERVICE_TARGET,
                "ReliabilityTarget": HEADLINE_RELIABILITY_TARGET,
                "PVCapacityMW": float(
                    headline_row["PVCapacityMW"]
                ),
                "RecommendedBatteryPowerMW": float(
                    headline_row["RecommendedBatteryPowerMW"]
                ),
                "RecommendedBatteryInstalledMWh": float(
                    headline_row["RecommendedBatteryInstalledMWh"]
                ),
                "SimulatedSuccessRate": float(
                    headline_row["SimulatedSuccessRate"]
                ),
                "PowerIncreaseVsDeterministicMW": float(
                    headline_row["RecommendedBatteryPowerMW"]
                    - deterministic_power_mw
                ),
                "PowerIncreaseVsDeterministicPct": float(
                    headline_row["RecommendedBatteryPowerMW"]
                    / deterministic_power_mw
                    - 1.0
                ),
                "EnergyIncreaseVsDeterministicMWh": float(
                    headline_row["RecommendedBatteryInstalledMWh"]
                    - deterministic_energy_mwh
                ),
                "EnergyIncreaseVsDeterministicPct": float(
                    headline_row["RecommendedBatteryInstalledMWh"]
                    / deterministic_energy_mwh
                    - 1.0
                ),
                "RecommendationPolicy": RECOMMENDATION_POLICY
            }
        ]
    )

    # ------------------------------------------------------------------
    # Assumptions / reproducibility.
    # ------------------------------------------------------------------

    assumptions = pd.DataFrame(
        [
            {
                "Item": "Final analytical purpose",
                "Value": "Find reliability-adjusted BESS MW/MWh designs from the deterministic MILP-1 and MILP-2 results."
            },
            {
                "Item": "Selected site",
                "Value": (
                    f"{selected_site['GridID']} | "
                    f"{selected_site['CandidateLineID']} | "
                    f"{selected_site['SubstationID']}"
                )
            },
            {
                "Item": "Fixed DC load",
                "Value": f"{base_dc_mw:.6f} MW"
            },
            {
                "Item": "Nominal deterministic BESS",
                "Value": (
                    f"{deterministic_power_mw:.6f} MW / "
                    f"{deterministic_energy_mwh:.6f} MWh"
                )
            },
            {
                "Item": "Reliability targets",
                "Value": str(RELIABILITY_TARGETS)
            },
            {
                "Item": "Headline target",
                "Value": (
                    f"{HEADLINE_RELIABILITY_TARGET:.1%} simulated survival, "
                    f"PV ratio={HEADLINE_PV_RATIO:.0%}"
                )
            },
            {
                "Item": "Recommendation policy",
                "Value": RECOMMENDATION_POLICY
            },
            {
                "Item": "Simulations per scenario",
                "Value": SIMULATIONS_PER_SCENARIO
            },
            {
                "Item": "Random seed",
                "Value": RANDOM_SEED
            },
            {
                "Item": "Common random numbers",
                "Value": "The same sampled outage starts and engineering uncertainty draws are reused across every BESS/PV design for each outage duration."
            },
            {
                "Item": "Outage start sampling",
                "Value": "Uniform with replacement from complete historical starts in the 2024 PV profile."
            },
            {
                "Item": "PV treatment",
                "Value": "Real contiguous historical PV blocks. PV serves load first, excess PV charges the battery, and remaining excess is curtailed."
            },
            {
                "Item": "Start SOC triangular",
                "Value": str(START_SOC_TRIANGULAR)
            },
            {
                "Item": "Battery capacity derate triangular",
                "Value": str(BATTERY_CAPACITY_DERATE_TRIANGULAR)
            },
            {
                "Item": "Charge efficiency triangular",
                "Value": str(BATTERY_CHARGE_EFFICIENCY_TRIANGULAR)
            },
            {
                "Item": "Discharge efficiency triangular",
                "Value": str(BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR)
            },
            {
                "Item": "PCS availability triangular",
                "Value": str(PCS_AVAILABILITY_TRIANGULAR)
            },
            {
                "Item": "Critical load factor triangular",
                "Value": str(CRITICAL_LOAD_FACTOR_TRIANGULAR)
            },
            {
                "Item": "PV output multiplier triangular",
                "Value": str(PV_OUTPUT_MULTIPLIER_TRIANGULAR)
            },
            {
                "Item": "Distribution interpretation",
                "Value": "Triangular ranges are explicit stress-test assumptions unless independently supported by measured distributions."
            },
            {
                "Item": "Not randomized",
                "Value": "Unresolved station capacity, feeder rerouting, AC voltage/reactive behavior, and protection/short-circuit limits remain unresolved rather than being invented."
            }
        ]
    )

    scenario_bank_audit = pd.DataFrame(
        scenario_bank_audit_rows
    )

    # ------------------------------------------------------------------
    # Console output.
    # ------------------------------------------------------------------

    heading(
        "FINAL RELIABILITY-ADJUSTED RECOMMENDATIONS"
    )

    print(
        recommendations[
            [
                "OutageDurationHours",
                "PVCapacityMW",
                "ReliabilityTarget",
                "PassingDesignFound",
                "RecommendedBatteryPowerMW",
                "RecommendedBatteryInstalledMWh",
                "SimulatedSuccessRate",
                "PowerMultiplier",
                "EnergyMultiplier"
            ]
        ]
        .to_string(
            index=False
        )
    )

    heading(
        "HEADLINE FINAL RECOMMENDATION"
    )

    print(
        headline_output
        .to_string(
            index=False
        )
    )

    # ------------------------------------------------------------------
    # Outputs.
    # ------------------------------------------------------------------

    design_grid.to_csv(
        OUTPUT_GRID_CSV,
        index=False,
        compression="gzip"
    )

    workbook_frames = {
        "Headline_Recommendation": headline_output,
        "Recommendations": recommendations,
        "Design_Grid": design_grid,
        "Pareto_Designs": design_grid[
            design_grid["ParetoEfficient"]
        ].copy(),
        "Scenario_Bank_Audit": scenario_bank_audit,
        "Deterministic_Baseline": pd.DataFrame(
            [
                {
                    "CandidateKey": selected_site["CandidateKey"],
                    "GridID": selected_site["GridID"],
                    "SubstationID": selected_site["SubstationID"],
                    "CandidateLineID": selected_site["CandidateLineID"],
                    "BaseDCMW": base_dc_mw,
                    "DeterministicBatteryPowerMW": deterministic_power_mw,
                    "DeterministicBatteryInstalledMWh": deterministic_energy_mwh
                }
            ]
        ),
        "Assumptions": assumptions
    }

    with pd.ExcelWriter(
        OUTPUT_WORKBOOK,
        engine="xlsxwriter"
    ) as writer:
        for sheet_name, frame in workbook_frames.items():
            safe = excel_safe(
                frame
            )

            safe.to_excel(
                writer,
                sheet_name=sheet_name[:31],
                index=False
            )

            format_excel(
                writer,
                sheet_name[:31],
                safe
            )

    heading(
        "FINAL MONTE CARLO DESIGN RECOMMENDATION COMPLETE"
    )

    print(
        f"Recommendation workbook: "
        f"{Path(OUTPUT_WORKBOOK).resolve()}"
    )

    print(
        f"Full design-grid CSV: "
        f"{Path(OUTPUT_GRID_CSV).resolve()}"
    )

    print()
    print(
        "Interpretation:"
    )

    print(
        "1. MILP 1 remains the site / existing-grid hosting answer."
    )

    print(
        "2. MILP 2 remains the nominal minimum outage-resilience sizing answer."
    )

    print(
        "3. This Monte Carlo sweep adds reliability margin by testing larger BESS MW/MWh designs under explicit uncertainty."
    )

    print(
        "4. The headline row is the configured 99% battery-only four-hour recommendation; 95% and 99.9% alternatives are also reported."
    )

    print(
        "5. PV scenarios are supplemental comparisons. They do not replace the battery-only headline unless you deliberately change HEADLINE_PV_RATIO."
    )

    print(
        "6. The final recommendation remains conditional on later utility validation of unresolved station, AC, protection, and interconnection constraints."
    )


if __name__ == "__main__":
    main()
