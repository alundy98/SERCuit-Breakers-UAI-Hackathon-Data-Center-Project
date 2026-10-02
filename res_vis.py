from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt


BASE_DC_MW = 13.509608

DETERMINISTIC_POWER_MW = 13.509608
DETERMINISTIC_ENERGY_MWH = 71.103202

FINAL_99_POWER_MW = 14.252637
FINAL_99_ENERGY_MWH = 91.723130
FINAL_99_SUCCESS_PCT = 99.1127

FINAL_999_POWER_MW = 14.455281
FINAL_999_ENERGY_MWH = 95.278290
FINAL_999_SUCCESS_PCT = 99.9200

PV_CAPACITY_MW = BASE_DC_MW

OUTAGE_HOURS = 4.0
INTERVAL_MINUTES = 5
DT_HOURS = INTERVAL_MINUTES / 60.0
OUTAGE_STEPS = int(round(OUTAGE_HOURS / DT_HOURS))

BATTERY_MIN_SOC = 0.10
BATTERY_MAX_SOC = 1.00

START_SOC_TRIANGULAR = (0.75, 0.90, 0.95)
CAPACITY_DERATE_TRIANGULAR = (0.90, 0.97, 1.00)
CHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
DISCHARGE_EFFICIENCY_TRIANGULAR = (0.90, 0.95, 0.97)
PCS_AVAILABILITY_TRIANGULAR = (0.95, 1.00, 1.00)

TRAJECTORY_SIMULATIONS = 10000
CURVE_SIMULATIONS = 25000
RANDOM_SEED = 2026

TRAJECTORY_SUCCESS_COUNT = 35
TRAJECTORY_FAILURE_COUNT = 10

PV_SHAPE_FILE = Path("greensboro_pv_availability_shape_2024.csv.gz")

OUTPUT_TRAJECTORIES = Path("01_monte_carlo_soc_trajectories.png")
OUTPUT_RELIABILITY = Path("02_reliability_vs_storage.png")
OUTPUT_DISPATCH = Path("03_representative_outage_dispatch.png")
OUTPUT_CURVE_DATA = Path("reliability_vs_storage_data.csv")


def triangular(rng, parameters, size):
    minimum, mode, maximum = parameters

    if np.isclose(minimum, maximum):
        return np.full(size, minimum, dtype=float)

    return rng.triangular(
        minimum,
        mode,
        maximum,
        size=size
    )


def build_scenario_bank(n, seed):
    rng = np.random.default_rng(seed)

    return {
        "start_soc": triangular(rng, START_SOC_TRIANGULAR, n),
        "capacity_derate": triangular(rng, CAPACITY_DERATE_TRIANGULAR, n),
        "charge_efficiency": triangular(rng, CHARGE_EFFICIENCY_TRIANGULAR, n),
        "discharge_efficiency": triangular(rng, DISCHARGE_EFFICIENCY_TRIANGULAR, n),
        "pcs_availability": triangular(rng, PCS_AVAILABILITY_TRIANGULAR, n)
    }


def simulate_battery_only(bank, battery_power_mw, battery_energy_mwh, save_trajectories=False):
    n = len(bank["start_soc"])

    effective_capacity = battery_energy_mwh * bank["capacity_derate"]
    minimum_energy = effective_capacity * BATTERY_MIN_SOC
    maximum_energy = effective_capacity * BATTERY_MAX_SOC

    soc = effective_capacity * np.clip(
        bank["start_soc"],
        BATTERY_MIN_SOC,
        BATTERY_MAX_SOC
    )

    available_power = battery_power_mw * bank["pcs_availability"]
    discharge_efficiency = bank["discharge_efficiency"]

    unserved_energy = np.zeros(n)
    first_failure_step = np.full(n, -1, dtype=int)

    trajectories = None

    if save_trajectories:
        trajectories = np.zeros((n, OUTAGE_STEPS + 1))
        trajectories[:, 0] = soc / effective_capacity

    for step in range(OUTAGE_STEPS):
        requested_power = np.full(n, BASE_DC_MW)

        power_after_pcs = np.minimum(
            requested_power,
            available_power
        )

        usable_stored_energy = np.maximum(
            soc - minimum_energy,
            0.0
        )

        energy_limited_power = (
            usable_stored_energy
            * discharge_efficiency
            / DT_HOURS
        )

        actual_discharge = np.minimum(
            power_after_pcs,
            energy_limited_power
        )

        unserved_power = np.maximum(
            requested_power - actual_discharge,
            0.0
        )

        newly_failed = (
            (first_failure_step < 0)
            & (unserved_power > 1e-8)
        )

        first_failure_step[newly_failed] = step

        unserved_energy += (
            unserved_power
            * DT_HOURS
        )

        soc -= (
            actual_discharge
            * DT_HOURS
            / discharge_efficiency
        )

        soc = np.maximum(
            soc,
            minimum_energy
        )

        if save_trajectories:
            trajectories[:, step + 1] = soc / effective_capacity

    success = unserved_energy <= 1e-8

    return {
        "success": success,
        "success_rate": float(success.mean()),
        "unserved_energy": unserved_energy,
        "first_failure_step": first_failure_step,
        "trajectories": trajectories
    }


def create_trajectory_visual():
    bank = build_scenario_bank(
        TRAJECTORY_SIMULATIONS,
        RANDOM_SEED
    )

    result = simulate_battery_only(
        bank,
        FINAL_99_POWER_MW,
        FINAL_99_ENERGY_MWH,
        save_trajectories=True
    )

    success_idx = np.where(result["success"])[0]
    failure_idx = np.where(~result["success"])[0]

    rng = np.random.default_rng(RANDOM_SEED + 100)

    if len(success_idx) > TRAJECTORY_SUCCESS_COUNT:
        success_idx = rng.choice(
            success_idx,
            size=TRAJECTORY_SUCCESS_COUNT,
            replace=False
        )

    if len(failure_idx) > TRAJECTORY_FAILURE_COUNT:
        failure_idx = rng.choice(
            failure_idx,
            size=TRAJECTORY_FAILURE_COUNT,
            replace=False
        )

    time_hours = np.arange(
        OUTAGE_STEPS + 1
    ) * DT_HOURS

    fig, ax = plt.subplots(figsize=(11, 7))

    for idx in success_idx:
        ax.plot(
            time_hours,
            result["trajectories"][idx] * 100,
            linewidth=1.2,
            alpha=0.45,
            color="steelblue"
        )

    for idx in failure_idx:
        ax.plot(
            time_hours,
            result["trajectories"][idx] * 100,
            linewidth=1.4,
            alpha=0.75,
            color="firebrick"
        )

        failure_step = result["first_failure_step"][idx]

        if failure_step >= 0:
            failure_hour = failure_step * DT_HOURS

            ax.scatter(
                failure_hour,
                result["trajectories"][idx, failure_step] * 100,
                s=35,
                color="firebrick",
                zorder=5
            )

    ax.axhline(
        BATTERY_MIN_SOC * 100,
        linestyle="--",
        linewidth=1.8,
        color="black",
        label="Minimum usable SOC"
    )

    ax.axvline(
        0,
        linewidth=2,
        color="black"
    )

    ax.text(
        0.04,
        97,
        "GRID OUTAGE BEGINS",
        fontsize=10,
        fontweight="bold",
        va="top"
    )

    ax.text(
        2.45,
        87,
        f"Final design\n"
        f"{FINAL_99_POWER_MW:.2f} MW / {FINAL_99_ENERGY_MWH:.2f} MWh\n\n"
        f"Final validation\n"
        f"300,000 simulations\n"
        f"{FINAL_99_SUCCESS_PCT:.2f}% successful service",
        fontsize=11,
        bbox={
            "boxstyle": "round,pad=0.5",
            "facecolor": "white",
            "alpha": 0.90
        }
    )

    ax.text(
        2.45,
        50,
        "Blue: successful representative runs\n"
        "Red: representative runs with unserved load\n"
        "Red markers: first interval with unserved power",
        fontsize=9,
        bbox={
            "boxstyle": "round,pad=0.4",
            "facecolor": "white",
            "alpha": 0.85
        }
    )

    ax.set_title(
        "Four Hour Outage Resilience Under Monte Carlo Uncertainty",
        fontsize=15,
        fontweight="bold"
    )

    ax.set_xlabel("Hours since grid outage")
    ax.set_ylabel("Battery state of charge (%)")

    ax.set_xlim(0, OUTAGE_HOURS)
    ax.set_ylim(0, 100)

    ax.set_xticks([0, 1, 2, 3, 4])
    ax.grid(alpha=0.20)

    plt.tight_layout()
    plt.savefig(
        OUTPUT_TRAJECTORIES,
        dpi=300,
        bbox_inches="tight"
    )
    plt.close()

    print(
        f"Trajectory sample simulation success: "
        f"{result['success_rate'] * 100:.3f}%"
    )


def create_reliability_curve():
    bank = build_scenario_bank(
        CURVE_SIMULATIONS,
        RANDOM_SEED
    )

    energy_values = np.unique(
        np.concatenate([
            np.arange(70.0, 101.0, 1.0),
            [
                DETERMINISTIC_ENERGY_MWH,
                FINAL_99_ENERGY_MWH,
                FINAL_999_ENERGY_MWH
            ]
        ])
    )

    rows = []

    print("\nCalculating reliability curve...")

    for number, energy_mwh in enumerate(energy_values, start=1):
        result = simulate_battery_only(
            bank,
            FINAL_99_POWER_MW,
            float(energy_mwh),
            save_trajectories=False
        )

        success_pct = (
            result["success_rate"]
            * 100
        )

        rows.append({
            "BatteryEnergyMWh": energy_mwh,
            "BatteryPowerMW": FINAL_99_POWER_MW,
            "SimulatedSuccessPct": success_pct
        })

        print(
            f"[{number}/{len(energy_values)}] "
            f"{energy_mwh:.2f} MWh -> "
            f"{success_pct:.3f}%"
        )

    curve = pd.DataFrame(rows)

    curve.to_csv(
        OUTPUT_CURVE_DATA,
        index=False
    )

    deterministic_result = simulate_battery_only(
        bank,
        DETERMINISTIC_POWER_MW,
        DETERMINISTIC_ENERGY_MWH
    )

    fig, ax = plt.subplots(figsize=(11, 7))

    ax.plot(
        curve["BatteryEnergyMWh"],
        curve["SimulatedSuccessPct"],
        linewidth=2.5,
        color="steelblue",
        label=f"Monte Carlo curve at {FINAL_99_POWER_MW:.2f} MW PCS"
    )

    ax.scatter(
        DETERMINISTIC_ENERGY_MWH,
        deterministic_result["success_rate"] * 100,
        s=120,
        color="black",
        zorder=5,
        label="Deterministic MILP 2 design"
    )

    ax.scatter(
        FINAL_99_ENERGY_MWH,
        FINAL_99_SUCCESS_PCT,
        s=150,
        color="darkgreen",
        zorder=6,
        label="Final 99% design"
    )

    ax.scatter(
        FINAL_999_ENERGY_MWH,
        FINAL_999_SUCCESS_PCT,
        s=150,
        color="darkorange",
        zorder=6,
        label="Final 99.9% design"
    )

    ax.axhline(
        99.0,
        linestyle="--",
        linewidth=1.3,
        color="darkgreen",
        alpha=0.7
    )

    ax.axhline(
        99.9,
        linestyle="--",
        linewidth=1.3,
        color="darkorange",
        alpha=0.7
    )

    ax.annotate(
        f"Deterministic minimum\n"
        f"{DETERMINISTIC_POWER_MW:.2f} MW\n"
        f"{DETERMINISTIC_ENERGY_MWH:.2f} MWh",
        (
            DETERMINISTIC_ENERGY_MWH,
            deterministic_result["success_rate"] * 100
        ),
        xytext=(15, 35),
        textcoords="offset points",
        arrowprops={
            "arrowstyle": "->"
        }
    )

    ax.annotate(
        f"99% design\n"
        f"{FINAL_99_POWER_MW:.2f} MW\n"
        f"{FINAL_99_ENERGY_MWH:.2f} MWh\n"
        f"{FINAL_99_SUCCESS_PCT:.2f}% success",
        (
            FINAL_99_ENERGY_MWH,
            FINAL_99_SUCCESS_PCT
        ),
        xytext=(-145, -85),
        textcoords="offset points",
        arrowprops={
            "arrowstyle": "->"
        }
    )

    ax.annotate(
        f"99.9% design\n"
        f"{FINAL_999_POWER_MW:.2f} MW\n"
        f"{FINAL_999_ENERGY_MWH:.2f} MWh\n"
        f"{FINAL_999_SUCCESS_PCT:.2f}% success",
        (
            FINAL_999_ENERGY_MWH,
            FINAL_999_SUCCESS_PCT
        ),
        xytext=(20, -90),
        textcoords="offset points",
        arrowprops={
            "arrowstyle": "->"
        }
    )

    ax.set_title(
        "Battery Size Required as Reliability Requirements Increase",
        fontsize=15,
        fontweight="bold"
    )

    ax.set_xlabel("Installed battery energy (MWh)")
    ax.set_ylabel("Successful four hour outage service (%)")

    ax.set_xlim(
        energy_values.min() - 1,
        energy_values.max() + 1
    )

    ax.set_ylim(0, 101)

    ax.grid(alpha=0.20)
    ax.legend(loc="lower right")

    plt.tight_layout()
    plt.savefig(
        OUTPUT_RELIABILITY,
        dpi=300,
        bbox_inches="tight"
    )
    plt.close()


def load_pv_profile():
    if not PV_SHAPE_FILE.exists():
        raise FileNotFoundError(
            f"Missing {PV_SHAPE_FILE}"
        )

    pv = pd.read_csv(
        PV_SHAPE_FILE,
        compression="gzip"
    )

    if "TimestampUTC" not in pv.columns:
        raise KeyError(
            "PV profile is missing TimestampUTC."
        )

    pv["TimestampUTC"] = pd.to_datetime(
        pv["TimestampUTC"],
        utc=True,
        errors="coerce"
    )

    local_column = "PVAvailabilityPU__hvmv_1_22"

    if local_column in pv.columns:
        pv["PVAvailabilityPU"] = pd.to_numeric(
            pv[local_column],
            errors="coerce"
        )

        if "SystemPVAvailabilityPU" in pv.columns:
            system = pd.to_numeric(
                pv["SystemPVAvailabilityPU"],
                errors="coerce"
            )

            pv["PVAvailabilityPU"] = (
                pv["PVAvailabilityPU"]
                .where(
                    pv["PVAvailabilityPU"].notna(),
                    system
                )
            )

    elif "SystemPVAvailabilityPU" in pv.columns:
        pv["PVAvailabilityPU"] = pd.to_numeric(
            pv["SystemPVAvailabilityPU"],
            errors="coerce"
        )

    else:
        raise KeyError(
            "PV profile contains neither the hvmv_1_22 nor system PV availability column."
        )

    pv = (
        pv[
            [
                "TimestampUTC",
                "PVAvailabilityPU"
            ]
        ]
        .dropna()
        .drop_duplicates(
            subset=["TimestampUTC"],
            keep="last"
        )
        .sort_values("TimestampUTC")
        .reset_index(drop=True)
    )

    pv["PVAvailabilityPU"] = (
        pv["PVAvailabilityPU"]
        .clip(
            lower=0.0,
            upper=1.10
        )
    )

    return pv


def get_valid_windows(pv):
    expected_interval = pd.Timedelta(
        minutes=INTERVAL_MINUTES
    )

    valid_starts = []

    for start in range(
        0,
        len(pv) - OUTAGE_STEPS + 1
    ):
        window = pv.iloc[
            start:start + OUTAGE_STEPS
        ]

        differences = (
            window["TimestampUTC"]
            .diff()
            .dropna()
        )

        if (
            len(window) == OUTAGE_STEPS
            and differences.eq(
                expected_interval
            ).all()
        ):
            valid_starts.append(start)

    return valid_starts


def select_representative_daytime_window(pv):
    valid_starts = get_valid_windows(pv)

    rows = []

    for start in valid_starts:
        window = pv.iloc[
            start:start + OUTAGE_STEPS
        ]

        average_pv = (
            window["PVAvailabilityPU"]
            .mean()
        )

        maximum_pv = (
            window["PVAvailabilityPU"]
            .max()
        )

        if maximum_pv > 0.10:
            rows.append({
                "start": start,
                "average_pv": average_pv
            })

    if not rows:
        raise RuntimeError(
            "No usable daylight four hour PV window was found."
        )

    candidates = pd.DataFrame(rows)

    median_average = (
        candidates["average_pv"]
        .median()
    )

    candidates["distance"] = (
        candidates["average_pv"]
        - median_average
    ).abs()

    selected_start = int(
        candidates
        .sort_values("distance")
        .iloc[0]["start"]
    )

    return (
        pv.iloc[
            selected_start:
            selected_start + OUTAGE_STEPS
        ]
        .copy()
        .reset_index(drop=True)
    )


def deterministic_dispatch(window):
    battery_power_mw = FINAL_99_POWER_MW
    battery_energy_mwh = FINAL_99_ENERGY_MWH

    start_soc = 0.90
    minimum_soc = 0.10
    charge_efficiency = 0.95
    discharge_efficiency = 0.95

    soc_mwh = (
        battery_energy_mwh
        * start_soc
    )

    minimum_energy_mwh = (
        battery_energy_mwh
        * minimum_soc
    )

    maximum_energy_mwh = (
        battery_energy_mwh
    )

    rows = []

    for step, row in window.iterrows():
        pv_available_mw = (
            float(row["PVAvailabilityPU"])
            * PV_CAPACITY_MW
        )

        pv_direct_mw = min(
            BASE_DC_MW,
            pv_available_mw
        )

        remaining_load_mw = max(
            BASE_DC_MW
            - pv_direct_mw,
            0.0
        )

        excess_pv_mw = max(
            pv_available_mw
            - pv_direct_mw,
            0.0
        )

        charge_power_mw = min(
            excess_pv_mw,
            battery_power_mw
        )

        charge_headroom_mw = max(
            (
                maximum_energy_mwh
                - soc_mwh
            )
            / (
                charge_efficiency
                * DT_HOURS
            ),
            0.0
        )

        charge_power_mw = min(
            charge_power_mw,
            charge_headroom_mw
        )

        soc_mwh = min(
            soc_mwh
            + charge_power_mw
            * charge_efficiency
            * DT_HOURS,
            maximum_energy_mwh
        )

        energy_available = max(
            soc_mwh
            - minimum_energy_mwh,
            0.0
        )

        energy_limited_power = (
            energy_available
            * discharge_efficiency
            / DT_HOURS
        )

        battery_discharge_mw = min(
            remaining_load_mw,
            battery_power_mw,
            energy_limited_power
        )

        unserved_mw = max(
            remaining_load_mw
            - battery_discharge_mw,
            0.0
        )

        soc_mwh -= (
            battery_discharge_mw
            * DT_HOURS
            / discharge_efficiency
        )

        soc_mwh = max(
            soc_mwh,
            minimum_energy_mwh
        )

        rows.append({
            "Hour": step * DT_HOURS,
            "TimestampUTC": row["TimestampUTC"],
            "DataCenterDemandMW": BASE_DC_MW,
            "PVAvailableMW": pv_available_mw,
            "PVDirectMW": pv_direct_mw,
            "BatteryDischargeMW": battery_discharge_mw,
            "BatteryChargeMW": charge_power_mw,
            "GridMW": 0.0,
            "UnservedMW": unserved_mw,
            "SOCPercent": soc_mwh / battery_energy_mwh * 100
        })

    dispatch = pd.DataFrame(rows)

    final_row = dispatch.iloc[-1].copy()
    final_row["Hour"] = OUTAGE_HOURS
    final_row["PVAvailableMW"] = np.nan
    final_row["PVDirectMW"] = np.nan
    final_row["BatteryDischargeMW"] = np.nan
    final_row["BatteryChargeMW"] = np.nan

    return dispatch


def create_dispatch_visual():
    pv = load_pv_profile()

    window = select_representative_daytime_window(
        pv
    )

    dispatch = deterministic_dispatch(
        window
    )

    fig, axes = plt.subplots(
        2,
        1,
        figsize=(11, 8),
        sharex=True,
        gridspec_kw={
            "height_ratios": [2.0, 1.0]
        }
    )

    ax = axes[0]

    ax.plot(
        dispatch["Hour"],
        dispatch["DataCenterDemandMW"],
        linewidth=2.5,
        color="black",
        label="Data center demand"
    )

    ax.plot(
        dispatch["Hour"],
        dispatch["PVDirectMW"],
        linewidth=2.2,
        color="goldenrod",
        label="PV serving load"
    )

    ax.plot(
        dispatch["Hour"],
        dispatch["BatteryDischargeMW"],
        linewidth=2.2,
        color="steelblue",
        label="Battery discharge"
    )

    if (
        dispatch["BatteryChargeMW"]
        > 0
    ).any():
        ax.plot(
            dispatch["Hour"],
            dispatch["BatteryChargeMW"],
            linewidth=2.0,
            linestyle="--",
            color="darkgreen",
            label="PV charging battery"
        )

    ax.axvline(
        0,
        linewidth=2,
        color="black"
    )

    ax.text(
        0.05,
        BASE_DC_MW * 1.06,
        "GRID OUTAGE BEGINS\nGrid supply = 0 MW",
        fontsize=9,
        fontweight="bold"
    )

    ax.set_ylabel("Power (MW)")

    ax.set_title(
        "How the Resilience Model Serves the Data Center During a Four Hour Outage",
        fontsize=14,
        fontweight="bold"
    )

    ax.grid(alpha=0.20)
    ax.legend(loc="best")

    soc_ax = axes[1]

    soc_ax.plot(
        dispatch["Hour"],
        dispatch["SOCPercent"],
        linewidth=2.5,
        color="steelblue"
    )

    soc_ax.axhline(
        BATTERY_MIN_SOC * 100,
        linestyle="--",
        linewidth=1.5,
        color="black",
        label="Minimum SOC"
    )

    soc_ax.set_xlabel(
        "Hours since grid outage"
    )

    soc_ax.set_ylabel(
        "Battery SOC (%)"
    )

    soc_ax.set_xlim(
        0,
        OUTAGE_HOURS
    )

    soc_ax.set_ylim(
        0,
        100
    )

    soc_ax.set_xticks(
        [0, 1, 2, 3, 4]
    )

    soc_ax.grid(
        alpha=0.20
    )

    soc_ax.legend(
        loc="best"
    )

    start_time = (
        window.iloc[0]["TimestampUTC"]
        .tz_convert("America/New_York")
    )

    fig.text(
        0.5,
        0.01,
        f"Representative historical daytime PV window beginning "
        f"{start_time.strftime('%b %d, %Y %I:%M %p')} local time. "
        f"PV capacity shown = {PV_CAPACITY_MW:.2f} MW supplemental scenario.",
        ha="center",
        fontsize=9
    )

    plt.tight_layout(
        rect=[0, 0.04, 1, 1]
    )

    plt.savefig(
        OUTPUT_DISPATCH,
        dpi=300,
        bbox_inches="tight"
    )

    plt.close()


def main():
    print("\nGREENSBORO RESILIENCE VISUALS")
    print()

    print("1. Creating Monte Carlo SOC trajectory visual...")
    create_trajectory_visual()
    print(f"Saved: {OUTPUT_TRAJECTORIES.resolve()}")

    print("\n2. Creating reliability versus storage visual...")
    create_reliability_curve()
    print(f"Saved: {OUTPUT_RELIABILITY.resolve()}")
    print(f"Saved: {OUTPUT_CURVE_DATA.resolve()}")

    print("\n3. Creating representative outage dispatch visual...")

    try:
        create_dispatch_visual()
        print(f"Saved: {OUTPUT_DISPATCH.resolve()}")
    except Exception as exc:
        print(f"Dispatch visual skipped: {exc}")

    print("\nComplete.")


if __name__ == "__main__":
    main()