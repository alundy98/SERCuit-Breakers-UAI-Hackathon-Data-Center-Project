from pathlib import Path
import numpy as np
import pandas as pd


# ======================================================================================
# GREENSBORO MONTE CARLO - FIXED-DESIGN RESILIENCE STRESS TEST
# ======================================================================================
# PURPOSE:
# Validate the deterministic MILP-2 recommendation WITHOUT resizing the battery on every
# simulation. The default design is read from the optimized MILP-2 100% service / 4-hour /
# 0-MW-PV case, expected to be approximately 13.51 MW / 71.10 MWh at GSO_122.
#
# MONTE CARLO:
# - Samples complete historical 2024 PV blocks with replacement.
# - Randomizes explicit engineering scenario assumptions:
#       starting SOC,
#       effective battery-capacity derating,
#       charge/discharge efficiency,
#       PCS/inverter availability,
#       optional critical-load factor,
#       optional PV-output multiplier.
# - Simulates five-minute islanded dispatch:
#       PV -> critical load
#       excess PV -> battery
#       battery -> remaining load
# - Reports probability of full survival, unserved energy, SOC stress, endurance, and
#   whether failures are driven by battery energy/SOC or PCS power.
#
# IMPORTANT:
# The uncertainty distributions below are scenario assumptions unless independently
# supported by measured data. Unresolved station capacity, feeder rerouting, AC power flow,
# and protection constraints are NOT converted into random variables.
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

OUTPUT_WORKBOOK = "greensboro_monte_carlo_fixed_design_resilience_results.xlsx"
OUTPUT_SIMULATION_CSV = "greensboro_monte_carlo_fixed_design_resilience_simulations.csv.gz"


# ======================================================================================
# FIXED DESIGN TO VALIDATE
# ======================================================================================

DESIGN_SERVICE_TARGET = 1.00
DESIGN_OUTAGE_DURATION_HOURS = 4.0
DESIGN_PV_CAPACITY_MW = 0.0

# Default: validate the four-hour recommendation only.
# You may later change this to [1.0, 2.0, 4.0, 8.0] while holding the SAME battery fixed.
MONTE_CARLO_OUTAGE_DURATIONS_HOURS = [4.0]

# Compare optional PV while holding the deterministic battery MW/MWh fixed.
PV_CAPACITY_RATIOS_TO_TEST = [0.00, 0.50, 1.00]
PV_CAPACITY_MW_SCENARIOS = []

SIMULATIONS_PER_SCENARIO = 10000
RANDOM_SEED = 2026


# ======================================================================================
# TIME / PV SETTINGS
# ======================================================================================

INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0
MODEL_TIMEZONE = "America/New_York"
PV_AVAILABILITY_CLIP_PU = 1.10

# None = sample any complete historical start with equal probability.
# Example: (8, 20) would restrict starts to 08:00 through 19:59 local time.
OUTAGE_START_LOCAL_HOURS = None

ASSUME_GRID_FORMING_ISLANDING = True
PV_AVAILABLE_DURING_OUTAGE = True


# ======================================================================================
# UNCERTAINTY ASSUMPTIONS
# ======================================================================================
# Tuples are (minimum, mode, maximum) triangular distributions.
# Set min = mode = max to hold an input deterministic.
# ======================================================================================

START_SOC_TRIANGULAR = (0.75, 0.90, 0.95)
BATTERY_CAPACITY_DERATE_TRIANGULAR = (0.90, 0.97, 1.00)
BATTERY_CHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
PCS_AVAILABILITY_TRIANGULAR = (0.95, 1.00, 1.00)

# Keep full critical load by default. Change only as an explicit scenario.
CRITICAL_LOAD_FACTOR_TRIANGULAR = (1.00, 1.00, 1.00)

# Historical PV already captures actual weather/day/night variation.
PV_OUTPUT_MULTIPLIER_TRIANGULAR = (1.00, 1.00, 1.00)

BATTERY_MIN_SOC_FRACTION = 0.10
BATTERY_MAX_SOC_FRACTION = 1.00

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
            f"Invalid triangular parameters {parameters}. Required min <= mode <= max."
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
    values = np.asarray(
        values,
        dtype=float
    )

    values = values[
        np.isfinite(values)
    ]

    return (
        float(np.quantile(values, q))
        if len(values)
        else np.nan
    )


def duration_to_steps(duration_hours):
    steps_float = (
        float(duration_hours)
        / DT_HOURS
    )

    steps = int(
        round(steps_float)
    )

    if abs(steps - steps_float) > 1e-9:
        raise RuntimeError(
            f"{duration_hours} hours is not divisible by "
            f"{INTERVAL_MINUTES} minutes."
        )

    if steps < 1:
        raise RuntimeError(
            "Outage duration must contain at least one interval."
        )

    return steps


def contiguous_blocks(timestamps):
    timestamps = pd.Series(
        pd.to_datetime(
            timestamps,
            utc=True,
            errors="coerce"
        )
    ).reset_index(
        drop=True
    )

    if timestamps.isna().any():
        raise RuntimeError(
            "PV profile contains invalid timestamps."
        )

    expected = pd.Timedelta(
        minutes=INTERVAL_MINUTES
    )

    block_id = (
        timestamps
        .diff()
        .ne(expected)
        .cumsum()
        .to_numpy(dtype=int)
    )

    blocks = []

    for block in np.unique(block_id):
        idx = np.where(
            block_id == block
        )[0]

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
        if last - first + 1 >= duration_steps:
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
        start_hour, end_hour = (
            OUTAGE_START_LOCAL_HOURS
        )

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

        starts = starts[
            keep
        ]

    return starts


def excel_safe(df):
    out = df.copy()

    for column in out.columns:
        if isinstance(
            out[column].dtype,
            pd.DatetimeTZDtype
        ):
            out[column] = (
                out[column]
                .dt
                .tz_localize(None)
            )

    return out


def format_excel(writer, sheet_name, dataframe):
    if dataframe.shape[1] == 0:
        return

    workbook = writer.book
    worksheet = writer.sheets[
        sheet_name
    ]

    header_format = workbook.add_format(
        {
            "bold": True,
            "border": 1,
            "align": "center"
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

    for i, column in enumerate(
        dataframe.columns
    ):
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

        if (
            "fraction" in lower
            or lower.endswith("rate")
        ):
            worksheet.set_column(
                i,
                i,
                width,
                percent_format
            )

        elif pd.api.types.is_float_dtype(
            dataframe[column]
        ):
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

    worksheet.freeze_panes(
        1,
        0
    )

    if not dataframe.empty:
        worksheet.autofilter(
            0,
            0,
            len(dataframe),
            len(dataframe.columns) - 1
        )


# ======================================================================================
# LOAD DETERMINISTIC DESIGN
# ======================================================================================

def load_selected_milp1_site():
    path = Path(
        BASE_MILP_FILE
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Missing {BASE_MILP_FILE}. Run MILP 1 first."
        )

    workbook = pd.ExcelFile(
        path
    )

    sheet = (
        BASE_MILP_CANDIDATE_SHEET
        if BASE_MILP_CANDIDATE_SHEET in workbook.sheet_names
        else BASE_MILP_SELECTED_SHEET
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

    missing = (
        required
        - set(candidates.columns)
    )

    if missing:
        raise RuntimeError(
            f"MILP-1 handoff missing columns: "
            f"{sorted(missing)}"
        )

    candidates["CandidateKey"] = (
        candidates["CandidateKey"]
        .astype(str)
    )

    candidates["MaxOptimizedDCMW"] = (
        pd.to_numeric(
            candidates["MaxOptimizedDCMW"],
            errors="coerce"
        )
    )

    candidates = candidates[
        candidates["MaxOptimizedDCMW"].notna()
        & (
            candidates["MaxOptimizedDCMW"]
            > 0
        )
    ].copy()

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
            selected_sheet.iloc[0][
                "CandidateKey"
            ]
        )

        selected = (
            candidates[
                candidates["CandidateKey"]
                == selected_key
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
            "Could not identify the MILP-1 selected site."
        )

    row = selected.iloc[0].copy()

    row["BaseDCMW"] = float(
        row["MaxOptimizedDCMW"]
    )

    return row


def load_fixed_resilience_design(selected_site):
    path = Path(
        RESILIENCE_MILP_FILE
    )

    if not path.exists():
        raise FileNotFoundError(
            f"Missing {RESILIENCE_MILP_FILE}. "
            "Run optimized MILP 2 first."
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

    missing = (
        required
        - set(scenarios.columns)
    )

    if missing:
        raise RuntimeError(
            f"MILP-2 result missing columns: "
            f"{sorted(missing)}"
        )

    scenarios["CandidateKey"] = (
        scenarios["CandidateKey"]
        .astype(str)
    )

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
            str(
                selected_site["CandidateKey"]
            )
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

    design_rows = (
        scenarios[
            mask
        ]
        .copy()
    )

    if design_rows.empty:
        raise RuntimeError(
            "Requested deterministic MILP-2 design row was not found."
        )

    design = (
        design_rows
        .iloc[0]
        .copy()
    )

    if (
        design["BatteryPowerMW"] <= 0
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
    path = Path(
        PV_SHAPE_FILE
    )

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

    missing = (
        required
        - set(df.columns)
    )

    if missing:
        raise RuntimeError(
            f"{PV_SHAPE_FILE} missing columns: "
            f"{sorted(missing)}"
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
            column
            == "SystemPVAvailabilityPU"
            or str(column).startswith(
                "PVAvailabilityPU__"
            )
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

    return (
        df
        .dropna(
            subset=["TimestampUTC"]
        )
        .drop_duplicates(
            "TimestampUTC",
            keep="last"
        )
        .sort_values(
            "TimestampUTC"
        )
        .reset_index(
            drop=True
        )[
            ["TimestampUTC"]
            + availability_columns
        ]
    )


def site_pv_profile(
    selected_site,
    pv_shape
):
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

    out = (
        pv_shape[
            columns
        ]
        .copy()
    )

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
        out["PVAvailabilityPU"] = (
            system
        )

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
        float(base_dc_mw)
        * float(ratio)
        for ratio in PV_CAPACITY_RATIOS_TO_TEST
    ]

    values.extend(
        float(value)
        for value in PV_CAPACITY_MW_SCENARIOS
    )

    values = sorted(
        {
            round(
                max(
                    value,
                    0.0
                ),
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
# MONTE CARLO ENGINE
# ======================================================================================

def run_monte_carlo_scenario(
    rng,
    selected_site,
    fixed_design,
    pv_profile,
    outage_duration_hours,
    pv_capacity_mw,
    pv_capacity_ratio
):
    n = int(
        SIMULATIONS_PER_SCENARIO
    )

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

    valid_starts = valid_start_indices(
        timestamps,
        steps
    )

    if len(valid_starts) == 0:
        raise RuntimeError(
            f"No valid "
            f"{outage_duration_hours:g}-hour "
            "outage windows exist."
        )

    sampled_start_indices = (
        rng.choice(
            valid_starts,
            size=n,
            replace=True
        )
    )

    window_indices = (
        sampled_start_indices[:, None]
        + np.arange(
            steps
        )[None, :]
    )

    sampled_pv_shape = (
        pv_shape[
            window_indices
        ]
    )

    start_soc_fraction = triangular_or_constant(
        rng,
        START_SOC_TRIANGULAR,
        n
    )

    capacity_derate = triangular_or_constant(
        rng,
        BATTERY_CAPACITY_DERATE_TRIANGULAR,
        n
    )

    charge_efficiency = triangular_or_constant(
        rng,
        BATTERY_CHARGE_EFFICIENCY_TRIANGULAR,
        n
    )

    discharge_efficiency = triangular_or_constant(
        rng,
        BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR,
        n
    )

    pcs_availability = triangular_or_constant(
        rng,
        PCS_AVAILABILITY_TRIANGULAR,
        n
    )

    critical_load_factor = triangular_or_constant(
        rng,
        CRITICAL_LOAD_FACTOR_TRIANGULAR,
        n
    )

    pv_output_multiplier = triangular_or_constant(
        rng,
        PV_OUTPUT_MULTIPLIER_TRIANGULAR,
        n
    )

    base_dc_mw = float(
        selected_site["BaseDCMW"]
    )

    installed_battery_mwh = float(
        fixed_design["BatteryInstalledMWh"]
    )

    installed_battery_power_mw = float(
        fixed_design["BatteryPowerMW"]
    )

    effective_capacity_mwh = (
        installed_battery_mwh
        * capacity_derate
    )

    available_power_mw = (
        installed_battery_power_mw
        * pcs_availability
    )

    critical_load_mw = (
        base_dc_mw
        * DESIGN_SERVICE_TARGET
        * critical_load_factor
    )

    min_soc_mwh = (
        effective_capacity_mwh
        * BATTERY_MIN_SOC_FRACTION
    )

    max_soc_mwh = (
        effective_capacity_mwh
        * BATTERY_MAX_SOC_FRACTION
    )

    start_soc_fraction = np.clip(
        start_soc_fraction,
        BATTERY_MIN_SOC_FRACTION,
        BATTERY_MAX_SOC_FRACTION
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
            sampled_pv_shape
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

    unserved_energy = np.zeros(
        n
    )

    peak_unserved_power = np.zeros(
        n
    )

    minimum_soc_seen = (
        soc_mwh.copy()
    )

    pv_direct_energy = np.zeros(
        n
    )

    pv_charge_energy = np.zeros(
        n
    )

    pv_curtailed_energy = np.zeros(
        n
    )

    battery_delivered_energy = np.zeros(
        n
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

    for t in range(steps):
        pv_t = (
            pv_available_mw[:, t]
        )

        pv_direct = np.minimum(
            critical_load_mw,
            pv_t
        )

        remaining_load = np.maximum(
            critical_load_mw
            - pv_direct,
            0.0
        )

        excess_pv = np.maximum(
            pv_t
            - pv_direct,
            0.0
        )

        charge_power = np.minimum(
            excess_pv,
            available_power_mw
        )

        charge_headroom = np.divide(
            np.maximum(
                max_soc_mwh
                - soc_mwh,
                0.0
            ),
            charge_efficiency
            * DT_HOURS,
            out=np.zeros(n),
            where=(
                charge_efficiency
                * DT_HOURS
            ) > 0
        )

        charge_power = np.minimum(
            charge_power,
            charge_headroom
        )

        curtailed = np.maximum(
            excess_pv
            - charge_power,
            0.0
        )

        soc_mwh = np.minimum(
            soc_mwh
            + charge_efficiency
            * charge_power
            * DT_HOURS,
            max_soc_mwh
        )

        requested_discharge = (
            remaining_load
        )

        pcs_limited_now = (
            requested_discharge
            > available_power_mw
            + UNSERVED_POWER_SUCCESS_TOLERANCE_MW
        )

        after_pcs = np.minimum(
            requested_discharge,
            available_power_mw
        )

        stored_energy_available = np.maximum(
            soc_mwh
            - min_soc_mwh,
            0.0
        )

        energy_limited_max_discharge = (
            stored_energy_available
            * discharge_efficiency
            / DT_HOURS
        )

        energy_limited_now = (
            after_pcs
            > energy_limited_max_discharge
            + UNSERVED_POWER_SUCCESS_TOLERANCE_MW
        )

        discharge_actual = np.minimum(
            after_pcs,
            energy_limited_max_discharge
        )

        unserved_power = np.maximum(
            requested_discharge
            - discharge_actual,
            0.0
        )

        soc_mwh -= (
            discharge_actual
            * DT_HOURS
            / discharge_efficiency
        )

        soc_mwh = np.maximum(
            soc_mwh,
            min_soc_mwh
            - 1e-10
        )

        minimum_soc_seen = np.minimum(
            minimum_soc_seen,
            soc_mwh
        )

        unserved_energy += (
            unserved_power
            * DT_HOURS
        )

        peak_unserved_power = np.maximum(
            peak_unserved_power,
            unserved_power
        )

        pv_direct_energy += (
            pv_direct
            * DT_HOURS
        )

        pv_charge_energy += (
            charge_power
            * DT_HOURS
        )

        pv_curtailed_energy += (
            curtailed
            * DT_HOURS
        )

        battery_delivered_energy += (
            discharge_actual
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
                unserved_power
                > UNSERVED_POWER_SUCCESS_TOLERANCE_MW
            )
        )

        first_unserved_step[
            newly_failed
        ] = t

    success = (
        unserved_energy
        <= UNSERVED_ENERGY_SUCCESS_TOLERANCE_MWH
    )

    depleted = (
        minimum_soc_seen
        <= min_soc_mwh
        + SOC_DEPLETION_TOLERANCE_MWH
    )

    minimum_soc_fraction = np.divide(
        minimum_soc_seen,
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
        float(outage_duration_hours)
    )

    failure_driver = np.full(
        n,
        "SUCCESS",
        dtype=object
    )

    failed = (
        ~success
    )

    failure_driver[
        failed
        & energy_limited_any
        & pcs_limited_any
    ] = "ENERGY_AND_PCS"

    failure_driver[
        failed
        & energy_limited_any
        & ~pcs_limited_any
    ] = "ENERGY_SOC"

    failure_driver[
        failed
        & pcs_limited_any
        & ~energy_limited_any
    ] = "PCS_POWER"

    failure_driver[
        failed
        & ~energy_limited_any
        & ~pcs_limited_any
    ] = "OTHER"

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

    return pd.DataFrame(
        {
            "Simulation": np.arange(
                1,
                n + 1
            ),
            "CandidateKey": str(
                selected_site["CandidateKey"]
            ),
            "GridID": str(
                selected_site["GridID"]
            ),
            "SubstationID": str(
                selected_site["SubstationID"]
            ),
            "CandidateLineID": str(
                selected_site["CandidateLineID"]
            ),
            "BaseDCMW": base_dc_mw,
            "OutageDurationHours": float(
                outage_duration_hours
            ),
            "PVCapacityMW": float(
                pv_capacity_mw
            ),
            "PVCapacityRatioToDC": float(
                pv_capacity_ratio
            ),
            "SampledOutageStartUTC": sampled_start_utc,
            "SampledOutageStartLocal": sampled_start_local,
            "InstalledBatteryPowerMW": installed_battery_power_mw,
            "InstalledBatteryMWh": installed_battery_mwh,
            "StartSOCFraction": start_soc_fraction,
            "BatteryCapacityDerate": capacity_derate,
            "EffectiveBatteryCapacityMWh": effective_capacity_mwh,
            "ChargeEfficiency": charge_efficiency,
            "DischargeEfficiency": discharge_efficiency,
            "PCSAvailabilityFraction": pcs_availability,
            "AvailableBatteryPowerMW": available_power_mw,
            "CriticalLoadFactor": critical_load_factor,
            "CriticalLoadMW": critical_load_mw,
            "PVOutputMultiplier": pv_output_multiplier,
            "PVDirectMWh": pv_direct_energy,
            "PVChargeToBatteryACMWh": pv_charge_energy,
            "PVCurtailedMWh": pv_curtailed_energy,
            "BatteryDeliveredMWh": battery_delivered_energy,
            "MinimumSOCFraction": minimum_soc_fraction,
            "BatteryReachedMinimumSOC": depleted,
            "PCSConstraintEncountered": pcs_limited_any,
            "EnergyConstraintEncountered": energy_limited_any,
            "UnservedEnergyMWh": unserved_energy,
            "PeakUnservedPowerMW": peak_unserved_power,
            "EnduranceHours": endurance_hours,
            "Success": success,
            "FailureDriver": failure_driver
        }
    )


# ======================================================================================
# SUMMARY
# ======================================================================================

def summarize_scenario(df):
    total = len(df)

    success_count = int(
        df["Success"].sum()
    )

    failures = (
        df.loc[
            ~df["Success"],
            "FailureDriver"
        ]
        .value_counts()
    )

    return {
        "CandidateKey": df["CandidateKey"].iloc[0],
        "GridID": df["GridID"].iloc[0],
        "BaseDCMW": float(
            df["BaseDCMW"].iloc[0]
        ),
        "OutageDurationHours": float(
            df["OutageDurationHours"].iloc[0]
        ),
        "PVCapacityMW": float(
            df["PVCapacityMW"].iloc[0]
        ),
        "PVCapacityRatioToDC": float(
            df["PVCapacityRatioToDC"].iloc[0]
        ),
        "InstalledBatteryPowerMW": float(
            df["InstalledBatteryPowerMW"].iloc[0]
        ),
        "InstalledBatteryMWh": float(
            df["InstalledBatteryMWh"].iloc[0]
        ),
        "Simulations": total,
        "SuccessCount": success_count,
        "FailureCount": total - success_count,
        "SuccessRate": success_count / total,
        "FailureRate": (
            1.0
            - success_count / total
        ),
        "MeanUnservedEnergyMWh": float(
            df["UnservedEnergyMWh"].mean()
        ),
        "P95UnservedEnergyMWh": percentile(
            df["UnservedEnergyMWh"],
            0.95
        ),
        "P99UnservedEnergyMWh": percentile(
            df["UnservedEnergyMWh"],
            0.99
        ),
        "MaximumUnservedEnergyMWh": float(
            df["UnservedEnergyMWh"].max()
        ),
        "P05MinimumSOCFraction": percentile(
            df["MinimumSOCFraction"],
            0.05
        ),
        "P50MinimumSOCFraction": percentile(
            df["MinimumSOCFraction"],
            0.50
        ),
        "BatteryMinimumSOCHitRate": float(
            df["BatteryReachedMinimumSOC"].mean()
        ),
        "PCSConstraintEncounterRate": float(
            df["PCSConstraintEncountered"].mean()
        ),
        "EnergyConstraintEncounterRate": float(
            df["EnergyConstraintEncountered"].mean()
        ),
        "MeanEnduranceHours": float(
            df["EnduranceHours"].mean()
        ),
        "P05EnduranceHours": percentile(
            df["EnduranceHours"],
            0.05
        ),
        "MeanPVDirectMWh": float(
            df["PVDirectMWh"].mean()
        ),
        "MeanPVChargeToBatteryACMWh": float(
            df["PVChargeToBatteryACMWh"].mean()
        ),
        "MeanPVCurtailedMWh": float(
            df["PVCurtailedMWh"].mean()
        ),
        "MeanBatteryDeliveredMWh": float(
            df["BatteryDeliveredMWh"].mean()
        ),
        "EnergySOCFailureCount": int(
            failures.get(
                "ENERGY_SOC",
                0
            )
        ),
        "PCSPowerFailureCount": int(
            failures.get(
                "PCS_POWER",
                0
            )
        ),
        "EnergyAndPCSFailureCount": int(
            failures.get(
                "ENERGY_AND_PCS",
                0
            )
        ),
        "OtherFailureCount": int(
            failures.get(
                "OTHER",
                0
            )
        )
    }


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading(
        "GREENSBORO MONTE CARLO - FIXED-DESIGN OUTAGE RESILIENCE"
    )

    selected_site = (
        load_selected_milp1_site()
    )

    fixed_design = (
        load_fixed_resilience_design(
            selected_site
        )
    )

    pv_profile = (
        site_pv_profile(
            selected_site,
            load_pv_shape()
        )
    )

    base_dc_mw = float(
        selected_site["BaseDCMW"]
    )

    battery_power_mw = float(
        fixed_design["BatteryPowerMW"]
    )

    battery_energy_mwh = float(
        fixed_design["BatteryInstalledMWh"]
    )

    pv_scenarios = (
        build_pv_scenarios(
            base_dc_mw
        )
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
        f"Fixed deterministic battery design: "
        f"{battery_power_mw:.6f} MW / "
        f"{battery_energy_mwh:.6f} MWh"
    )

    print(
        f"Design source: "
        f"{DESIGN_SERVICE_TARGET:.0%} service | "
        f"{DESIGN_OUTAGE_DURATION_HOURS:g}h | "
        f"{DESIGN_PV_CAPACITY_MW:.3f} MW PV"
    )

    print(
        f"Monte Carlo outage durations: "
        f"{MONTE_CARLO_OUTAGE_DURATIONS_HOURS}"
    )

    print(
        f"PV ratios tested with same battery: "
        f"{PV_CAPACITY_RATIOS_TO_TEST}"
    )

    print(
        f"Simulations per scenario: "
        f"{SIMULATIONS_PER_SCENARIO:,}"
    )

    print(
        f"Random seed: "
        f"{RANDOM_SEED}"
    )

    rng = np.random.default_rng(
        RANDOM_SEED
    )

    simulation_frames = []
    summary_rows = []
    scenario_number = 0

    total_scenarios = (
        len(
            MONTE_CARLO_OUTAGE_DURATIONS_HOURS
        )
        * len(
            pv_scenarios
        )
    )

    for outage_hours in MONTE_CARLO_OUTAGE_DURATIONS_HOURS:
        for _, pv_scenario in pv_scenarios.iterrows():
            scenario_number += 1

            pv_mw = float(
                pv_scenario["PVCapacityMW"]
            )

            pv_ratio = float(
                pv_scenario["PVCapacityRatioToDC"]
            )

            heading(
                f"SCENARIO {scenario_number}/{total_scenarios} | "
                f"{outage_hours:g}h | "
                f"PV={pv_mw:.3f} MW "
                f"({pv_ratio:.0%} OF DC)"
            )

            simulations = (
                run_monte_carlo_scenario(
                    rng,
                    selected_site,
                    fixed_design,
                    pv_profile,
                    float(outage_hours),
                    pv_mw,
                    pv_ratio
                )
            )

            summary = (
                summarize_scenario(
                    simulations
                )
            )

            simulation_frames.append(
                simulations
            )

            summary_rows.append(
                summary
            )

            print(
                f"Success rate: "
                f"{summary['SuccessRate']:.2%}"
            )

            print(
                f"Mean unserved energy: "
                f"{summary['MeanUnservedEnergyMWh']:.4f} MWh"
            )

            print(
                f"P99 unserved energy: "
                f"{summary['P99UnservedEnergyMWh']:.4f} MWh"
            )

            print(
                f"P05 minimum SOC: "
                f"{summary['P05MinimumSOCFraction']:.2%}"
            )

            print(
                f"Minimum-SOC hit rate: "
                f"{summary['BatteryMinimumSOCHitRate']:.2%}"
            )

            print(
                f"PCS constraint encounter rate: "
                f"{summary['PCSConstraintEncounterRate']:.2%}"
            )

    all_simulations = pd.concat(
        simulation_frames,
        ignore_index=True
    )

    scenario_summary = (
        pd.DataFrame(
            summary_rows
        )
        .sort_values(
            [
                "OutageDurationHours",
                "PVCapacityMW"
            ]
        )
        .reset_index(
            drop=True
        )
    )

    battery_only = (
        scenario_summary[
            np.isclose(
                scenario_summary["PVCapacityMW"],
                0.0
            )
        ][
            [
                "OutageDurationHours",
                "SuccessRate",
                "MeanUnservedEnergyMWh",
                "P99UnservedEnergyMWh",
                "MeanEnduranceHours"
            ]
        ]
        .rename(
            columns={
                "SuccessRate": "BatteryOnlySuccessRate",
                "MeanUnservedEnergyMWh": "BatteryOnlyMeanUnservedEnergyMWh",
                "P99UnservedEnergyMWh": "BatteryOnlyP99UnservedEnergyMWh",
                "MeanEnduranceHours": "BatteryOnlyMeanEnduranceHours"
            }
        )
    )

    scenario_summary = (
        scenario_summary
        .merge(
            battery_only,
            on="OutageDurationHours",
            how="left"
        )
    )

    scenario_summary[
        "SuccessRateImprovementFromPV"
    ] = (
        scenario_summary["SuccessRate"]
        - scenario_summary["BatteryOnlySuccessRate"]
    )

    scenario_summary[
        "MeanUnservedEnergyReductionFromPVMWh"
    ] = (
        scenario_summary["BatteryOnlyMeanUnservedEnergyMWh"]
        - scenario_summary["MeanUnservedEnergyMWh"]
    )

    scenario_summary[
        "P99UnservedEnergyReductionFromPVMWh"
    ] = (
        scenario_summary["BatteryOnlyP99UnservedEnergyMWh"]
        - scenario_summary["P99UnservedEnergyMWh"]
    )

    scenario_summary[
        "MeanEnduranceImprovementFromPVHours"
    ] = (
        scenario_summary["MeanEnduranceHours"]
        - scenario_summary["BatteryOnlyMeanEnduranceHours"]
    )

    failure_drivers = (
        all_simulations
        .groupby(
            [
                "OutageDurationHours",
                "PVCapacityMW",
                "FailureDriver"
            ]
        )
        .size()
        .reset_index(
            name="SimulationCount"
        )
    )

    scenario_totals = (
        all_simulations
        .groupby(
            [
                "OutageDurationHours",
                "PVCapacityMW"
            ]
        )
        .size()
        .reset_index(
            name="ScenarioSimulationCount"
        )
    )

    failure_drivers = (
        failure_drivers
        .merge(
            scenario_totals,
            on=[
                "OutageDurationHours",
                "PVCapacityMW"
            ],
            how="left"
        )
    )

    failure_drivers[
        "FractionOfScenario"
    ] = (
        failure_drivers["SimulationCount"]
        / failure_drivers["ScenarioSimulationCount"]
    )

    assumptions = pd.DataFrame(
        [
            {
                "Item": "Purpose",
                "Value": (
                    "Fixed-design Monte Carlo validation. "
                    "The battery is never resized inside the simulation."
                )
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
                "Value": (
                    f"{base_dc_mw:.6f} MW"
                )
            },
            {
                "Item": "Fixed BESS",
                "Value": (
                    f"{battery_power_mw:.6f} MW / "
                    f"{battery_energy_mwh:.6f} MWh"
                )
            },
            {
                "Item": "Design source",
                "Value": (
                    f"{DESIGN_SERVICE_TARGET:.0%} service | "
                    f"{DESIGN_OUTAGE_DURATION_HOURS:g}h | "
                    f"{DESIGN_PV_CAPACITY_MW:.3f} MW PV"
                )
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
                "Item": "Outage start sampling",
                "Value": (
                    "Uniform with replacement from all complete "
                    "historical 2024 starts in the PV profile."
                )
            },
            {
                "Item": "PV treatment",
                "Value": (
                    "Real contiguous historical PV blocks. "
                    "PV serves load first, excess PV charges battery, "
                    "remainder is curtailed."
                )
            },
            {
                "Item": "Start SOC triangular",
                "Value": str(
                    START_SOC_TRIANGULAR
                )
            },
            {
                "Item": "Capacity derate triangular",
                "Value": str(
                    BATTERY_CAPACITY_DERATE_TRIANGULAR
                )
            },
            {
                "Item": "Charge efficiency triangular",
                "Value": str(
                    BATTERY_CHARGE_EFFICIENCY_TRIANGULAR
                )
            },
            {
                "Item": "Discharge efficiency triangular",
                "Value": str(
                    BATTERY_DISCHARGE_EFFICIENCY_TRIANGULAR
                )
            },
            {
                "Item": "PCS availability triangular",
                "Value": str(
                    PCS_AVAILABILITY_TRIANGULAR
                )
            },
            {
                "Item": "Critical-load factor triangular",
                "Value": str(
                    CRITICAL_LOAD_FACTOR_TRIANGULAR
                )
            },
            {
                "Item": "PV-output multiplier triangular",
                "Value": str(
                    PV_OUTPUT_MULTIPLIER_TRIANGULAR
                )
            },
            {
                "Item": "Interpretation",
                "Value": (
                    "Triangular ranges are scenario assumptions unless "
                    "supported by measured data; they are not claimed "
                    "empirical probability distributions."
                )
            },
            {
                "Item": "Not randomized",
                "Value": (
                    "Unresolved station capacity, feeder ties, "
                    "AC voltage/reactive limits, and protection limits "
                    "remain unresolved rather than being invented."
                )
            }
        ]
    )

    heading(
        "MONTE CARLO SUMMARY"
    )

    print(
        scenario_summary[
            [
                "OutageDurationHours",
                "PVCapacityMW",
                "InstalledBatteryPowerMW",
                "InstalledBatteryMWh",
                "SuccessRate",
                "MeanUnservedEnergyMWh",
                "P99UnservedEnergyMWh",
                "P05MinimumSOCFraction",
                "BatteryMinimumSOCHitRate",
                "PCSConstraintEncounterRate",
                "SuccessRateImprovementFromPV"
            ]
        ]
        .to_string(
            index=False
        )
    )

    all_simulations.to_csv(
        OUTPUT_SIMULATION_CSV,
        index=False,
        compression="gzip"
    )

    workbook_frames = {
        "Scenario_Summary": scenario_summary,
        "Failure_Drivers": failure_drivers,
        "Deterministic_Design": pd.DataFrame(
            [
                {
                    "CandidateKey": selected_site["CandidateKey"],
                    "GridID": selected_site["GridID"],
                    "SubstationID": selected_site["SubstationID"],
                    "CandidateLineID": selected_site["CandidateLineID"],
                    "BaseDCMW": base_dc_mw,
                    "BatteryPowerMW": battery_power_mw,
                    "BatteryInstalledMWh": battery_energy_mwh
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
        "MONTE CARLO RUN COMPLETE"
    )

    print(
        f"Summary workbook: "
        f"{Path(OUTPUT_WORKBOOK).resolve()}"
    )

    print(
        f"Simulation-level CSV: "
        f"{Path(OUTPUT_SIMULATION_CSV).resolve()}"
    )

    print(
        "SuccessRate = fraction of simulations with effectively "
        "zero unserved critical-load energy."
    )

    print(
        "Failure drivers distinguish ENERGY_SOC, PCS_POWER, "
        "ENERGY_AND_PCS, and OTHER."
    )

    print(
        "PV scenarios use the same fixed battery, so any improvement "
        "is supplemental PV resilience value."
    )


if __name__ == "__main__":
    main()