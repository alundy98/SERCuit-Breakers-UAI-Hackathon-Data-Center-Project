from pathlib import Path
import importlib
import math
import numpy as np
import pandas as pd


# ======================================================================================
# GREENSBORO FINAL VALIDATION CHECKS
# ======================================================================================
# PURPOSE:
# Perform the two final checks before freezing the analytical model:
#
#   1. Refine the local design grid around the 99% battery-only recommendation.
#   2. Re-test the shortlisted 99% and 99.9% designs with much larger Monte Carlo runs
#      across multiple random seeds, including a Wilson 95% confidence interval.
#
# This script does NOT change the underlying MILP-1, MILP-2, or Monte Carlo physics.
# It imports and reuses the exact scenario-generation and outage-dispatch functions from:
#
#     mc_vF.py
#
# The final result is intended to be the last analytical validation before reporting.
# ======================================================================================


# ======================================================================================
# CORE MODEL MODULE
# ======================================================================================

CORE_MODULE = "mc_vF"
core = importlib.import_module(CORE_MODULE)


# ======================================================================================
# OUTPUTS
# ======================================================================================

OUTPUT_WORKBOOK = "greensboro_final_validation_checks.xlsx"
OUTPUT_LOCAL_GRID_CSV = "greensboro_final_validation_local_99_grid.csv.gz"
OUTPUT_VERIFICATION_CSV = "greensboro_final_validation_large_run_results.csv"


# ======================================================================================
# PHASE 1 - LOCAL 99% REFINEMENT
# ======================================================================================
# Previous coarse-grid recommendation:
#   Power multiplier = 1.050
#   Energy multiplier = 1.300
#
# Refine nearby values rather than rebuilding the entire broad grid.
# ======================================================================================

LOCAL_REFINEMENT_SIMULATIONS = 25000
LOCAL_REFINEMENT_SEED = 2026
LOCAL_99_TARGET = 0.99
LOCAL_PV_RATIO = 0.00

LOCAL_POWER_MULTIPLIERS = np.round(np.arange(1.030, 1.071, 0.005), 3)
LOCAL_ENERGY_MULTIPLIERS = np.round(np.arange(1.250, 1.331, 0.010), 3)

# Number of best local 99% candidates carried into the large-run verification.
TOP_LOCAL_99_CANDIDATES_TO_VERIFY = 5


# ======================================================================================
# PHASE 2 - LARGE-RUN VERIFICATION
# ======================================================================================
# 100,000 simulations per seed x 3 independent seeds = 300,000 simulations per design.
# Common random numbers are still used within each seed so competing designs see the same
# outage starts and engineering-uncertainty draws.
# ======================================================================================

VERIFICATION_SIMULATIONS_PER_SEED = 100000
VERIFICATION_SEEDS = [2026, 2027, 2028]

# Previous coarse-grid 99.9% recommendation was 1.075 x power and 1.350 x energy.
# Test a tight neighborhood around it so we can verify the selected point and see whether
# a slightly smaller nearby design also works under the larger simulation.
VERIFY_999_TARGET = 0.999

VERIFY_999_CANDIDATE_MULTIPLIERS = [
    (1.065, 1.330),
    (1.070, 1.340),
    (1.075, 1.350),
    (1.080, 1.360),
    (1.085, 1.370)
]

# Require both the aggregate point estimate and the lower bound of a Wilson 95% confidence
# interval to meet the target before labeling a design statistically verified.
USE_WILSON_LOWER_BOUND_FOR_FINAL_VERIFICATION = True
WILSON_Z = 1.959963984540054


# ======================================================================================
# HELPERS
# ======================================================================================

def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def wilson_interval(success_count, total_count, z=WILSON_Z):
    if total_count <= 0:
        return np.nan, np.nan

    p = success_count / total_count
    denominator = 1.0 + (z ** 2) / total_count
    center = (p + (z ** 2) / (2.0 * total_count)) / denominator
    half_width = z * math.sqrt((p * (1.0 - p) / total_count) + ((z ** 2) / (4.0 * total_count ** 2))) / denominator

    return max(0.0, center - half_width), min(1.0, center + half_width)


def recommendation_sort(df):
    return df.sort_values(
        ["MaxRelativeOversize", "EnergyMultiplier", "PowerMultiplier", "MeanUnservedEnergyMWh"],
        ascending=[True, True, True, True]
    )


def evaluate_grid(selected_site, deterministic_design, scenario_bank, power_multipliers, energy_multipliers, pv_capacity_mw, pv_ratio):
    base_power_mw = float(deterministic_design["BatteryPowerMW"])
    base_energy_mwh = float(deterministic_design["BatteryInstalledMWh"])
    rows = []
    total = len(power_multipliers) * len(energy_multipliers)
    counter = 0

    for power_multiplier in power_multipliers:
        for energy_multiplier in energy_multipliers:
            counter += 1
            battery_power_mw = base_power_mw * float(power_multiplier)
            battery_energy_mwh = base_energy_mwh * float(energy_multiplier)

            metrics = core.evaluate_design(
                selected_site=selected_site,
                scenario_bank=scenario_bank,
                battery_power_mw=battery_power_mw,
                battery_energy_mwh=battery_energy_mwh,
                pv_capacity_mw=pv_capacity_mw
            )

            success_count = int(round(metrics["SuccessRate"] * core.SIMULATIONS_PER_SCENARIO))
            ci_low, ci_high = wilson_interval(success_count, core.SIMULATIONS_PER_SCENARIO)

            rows.append({
                "PVCapacityMW": float(pv_capacity_mw),
                "PVCapacityRatioToDC": float(pv_ratio),
                "PowerMultiplier": float(power_multiplier),
                "EnergyMultiplier": float(energy_multiplier),
                "BatteryPowerMW": battery_power_mw,
                "BatteryInstalledMWh": battery_energy_mwh,
                "MaxRelativeOversize": max(float(power_multiplier), float(energy_multiplier)),
                "SuccessRate": metrics["SuccessRate"],
                "SuccessCount": success_count,
                "SimulationCount": core.SIMULATIONS_PER_SCENARIO,
                "Wilson95Lower": ci_low,
                "Wilson95Upper": ci_high,
                "MeanUnservedEnergyMWh": metrics["MeanUnservedEnergyMWh"],
                "P99UnservedEnergyMWh": metrics["P99UnservedEnergyMWh"],
                "P05MinimumSOCFraction": metrics["P05MinimumSOCFraction"],
                "PCSConstraintEncounterRate": metrics["PCSConstraintEncounterRate"],
                "EnergyConstraintEncounterRate": metrics["EnergyConstraintEncounterRate"],
                "MeanEnduranceHours": metrics["MeanEnduranceHours"]
            })

        print(f"    Power multiplier {power_multiplier:.3f} complete ({counter}/{total} designs evaluated)")

    return pd.DataFrame(rows)


def build_large_run_candidate_table(local_grid, deterministic_design):
    passing_local = recommendation_sort(local_grid[local_grid["SuccessRate"] >= LOCAL_99_TARGET].copy())

    if passing_local.empty:
        raise RuntimeError(
            "No locally refined design met the 99% point-estimate target. Expand LOCAL_POWER_MULTIPLIERS and/or "
            "LOCAL_ENERGY_MULTIPLIERS before finalizing the report."
        )

    top_local = passing_local.head(TOP_LOCAL_99_CANDIDATES_TO_VERIFY).copy()
    candidate_rows = []

    for rank, (_, row) in enumerate(top_local.iterrows(), start=1):
        candidate_rows.append({
            "CandidateLabel": f"99pct_local_rank_{rank}",
            "ReliabilityTarget": LOCAL_99_TARGET,
            "PowerMultiplier": float(row["PowerMultiplier"]),
            "EnergyMultiplier": float(row["EnergyMultiplier"]),
            "Source": "Local 99% refinement"
        })

    for power_multiplier, energy_multiplier in VERIFY_999_CANDIDATE_MULTIPLIERS:
        candidate_rows.append({
            "CandidateLabel": f"999pct_P{power_multiplier:.3f}_E{energy_multiplier:.3f}",
            "ReliabilityTarget": VERIFY_999_TARGET,
            "PowerMultiplier": float(power_multiplier),
            "EnergyMultiplier": float(energy_multiplier),
            "Source": "99.9% verification neighborhood"
        })

    candidates = pd.DataFrame(candidate_rows).drop_duplicates(
        subset=["ReliabilityTarget", "PowerMultiplier", "EnergyMultiplier"],
        keep="first"
    ).reset_index(drop=True)

    candidates["BatteryPowerMW"] = float(deterministic_design["BatteryPowerMW"]) * candidates["PowerMultiplier"]
    candidates["BatteryInstalledMWh"] = float(deterministic_design["BatteryInstalledMWh"]) * candidates["EnergyMultiplier"]
    candidates["MaxRelativeOversize"] = candidates[["PowerMultiplier", "EnergyMultiplier"]].max(axis=1)

    return candidates


def run_large_verification(selected_site, pv_profile, candidates, pv_capacity_mw):
    per_seed_rows = []

    for seed in VERIFICATION_SEEDS:
        heading(f"LARGE-RUN VERIFICATION | SEED={seed} | N={VERIFICATION_SIMULATIONS_PER_SEED:,}")

        core.SIMULATIONS_PER_SCENARIO = VERIFICATION_SIMULATIONS_PER_SEED
        rng = np.random.default_rng(seed)

        scenario_bank = core.build_scenario_bank(
            rng=rng,
            pv_profile=pv_profile,
            outage_duration_hours=float(core.DESIGN_OUTAGE_DURATION_HOURS)
        )

        for _, candidate in candidates.iterrows():
            metrics = core.evaluate_design(
                selected_site=selected_site,
                scenario_bank=scenario_bank,
                battery_power_mw=float(candidate["BatteryPowerMW"]),
                battery_energy_mwh=float(candidate["BatteryInstalledMWh"]),
                pv_capacity_mw=float(pv_capacity_mw)
            )

            success_count = int(round(metrics["SuccessRate"] * VERIFICATION_SIMULATIONS_PER_SEED))
            ci_low, ci_high = wilson_interval(success_count, VERIFICATION_SIMULATIONS_PER_SEED)

            per_seed_rows.append({
                "Seed": seed,
                "CandidateLabel": candidate["CandidateLabel"],
                "ReliabilityTarget": float(candidate["ReliabilityTarget"]),
                "PowerMultiplier": float(candidate["PowerMultiplier"]),
                "EnergyMultiplier": float(candidate["EnergyMultiplier"]),
                "BatteryPowerMW": float(candidate["BatteryPowerMW"]),
                "BatteryInstalledMWh": float(candidate["BatteryInstalledMWh"]),
                "SimulationCount": VERIFICATION_SIMULATIONS_PER_SEED,
                "SuccessCount": success_count,
                "FailureCount": VERIFICATION_SIMULATIONS_PER_SEED - success_count,
                "SuccessRate": metrics["SuccessRate"],
                "Wilson95Lower": ci_low,
                "Wilson95Upper": ci_high,
                "MeanUnservedEnergyMWh": metrics["MeanUnservedEnergyMWh"],
                "P99UnservedEnergyMWh": metrics["P99UnservedEnergyMWh"],
                "P05MinimumSOCFraction": metrics["P05MinimumSOCFraction"],
                "PCSConstraintEncounterRate": metrics["PCSConstraintEncounterRate"],
                "EnergyConstraintEncounterRate": metrics["EnergyConstraintEncounterRate"],
                "MeanEnduranceHours": metrics["MeanEnduranceHours"]
            })

            print(
                f"    {candidate['CandidateLabel']}: "
                f"{metrics['SuccessRate']:.4%} success | "
                f"{success_count:,}/{VERIFICATION_SIMULATIONS_PER_SEED:,}"
            )

    per_seed = pd.DataFrame(per_seed_rows)
    aggregate_rows = []

    for candidate_label, group in per_seed.groupby("CandidateLabel", sort=False):
        success_count = int(group["SuccessCount"].sum())
        total_count = int(group["SimulationCount"].sum())
        success_rate = success_count / total_count
        ci_low, ci_high = wilson_interval(success_count, total_count)
        target = float(group["ReliabilityTarget"].iloc[0])

        point_pass = success_rate >= target
        wilson_pass = ci_low >= target
        verified = wilson_pass if USE_WILSON_LOWER_BOUND_FOR_FINAL_VERIFICATION else point_pass

        aggregate_rows.append({
            "CandidateLabel": candidate_label,
            "ReliabilityTarget": target,
            "PowerMultiplier": float(group["PowerMultiplier"].iloc[0]),
            "EnergyMultiplier": float(group["EnergyMultiplier"].iloc[0]),
            "BatteryPowerMW": float(group["BatteryPowerMW"].iloc[0]),
            "BatteryInstalledMWh": float(group["BatteryInstalledMWh"].iloc[0]),
            "TotalSimulations": total_count,
            "SuccessCount": success_count,
            "FailureCount": total_count - success_count,
            "AggregateSuccessRate": success_rate,
            "Wilson95Lower": ci_low,
            "Wilson95Upper": ci_high,
            "MinimumSeedSuccessRate": float(group["SuccessRate"].min()),
            "MaximumSeedSuccessRate": float(group["SuccessRate"].max()),
            "MeanUnservedEnergyMWhAcrossSeeds": float(group["MeanUnservedEnergyMWh"].mean()),
            "MeanP99UnservedEnergyMWhAcrossSeeds": float(group["P99UnservedEnergyMWh"].mean()),
            "PointEstimateMeetsTarget": point_pass,
            "Wilson95LowerMeetsTarget": wilson_pass,
            "StatisticallyVerified": verified,
            "MaxRelativeOversize": max(
                float(group["PowerMultiplier"].iloc[0]),
                float(group["EnergyMultiplier"].iloc[0])
            )
        })

    return per_seed, pd.DataFrame(aggregate_rows)


def select_verified_design(aggregate, reliability_target):
    passing = aggregate[
        np.isclose(aggregate["ReliabilityTarget"], reliability_target)
        & aggregate["StatisticallyVerified"]
    ].copy()

    if passing.empty:
        return None

    passing = passing.sort_values(
        ["MaxRelativeOversize", "EnergyMultiplier", "PowerMultiplier", "AggregateSuccessRate"],
        ascending=[True, True, True, False]
    )

    return passing.iloc[0].copy()


# ======================================================================================
# MAIN
# ======================================================================================

def main():
    heading("GREENSBORO FINAL VALIDATION CHECKS")

    selected_site = core.load_selected_milp1_site()
    deterministic_design = core.load_deterministic_bess(selected_site)
    pv_profile = core.site_pv_profile(selected_site, core.load_pv_shape())

    base_dc_mw = float(selected_site["BaseDCMW"])
    deterministic_power_mw = float(deterministic_design["BatteryPowerMW"])
    deterministic_energy_mwh = float(deterministic_design["BatteryInstalledMWh"])
    pv_capacity_mw = base_dc_mw * LOCAL_PV_RATIO

    print(f"Selected site: {selected_site['GridID']} | {selected_site['CandidateLineID']} | {selected_site['SubstationID']}")
    print(f"Fixed data-center load: {base_dc_mw:.6f} MW")
    print(f"Deterministic BESS: {deterministic_power_mw:.6f} MW / {deterministic_energy_mwh:.6f} MWh")
    print(f"Final validation PV case: {pv_capacity_mw:.6f} MW ({LOCAL_PV_RATIO:.0%} of DC)")
    print(f"Local refinement simulations: {LOCAL_REFINEMENT_SIMULATIONS:,}")
    print(f"Large verification: {VERIFICATION_SIMULATIONS_PER_SEED:,} simulations x {len(VERIFICATION_SEEDS)} seeds")
    print(f"Large-run aggregate simulations per design: {VERIFICATION_SIMULATIONS_PER_SEED * len(VERIFICATION_SEEDS):,}")

    # ----------------------------------------------------------------------------------
    # PHASE 1 - LOCAL 99% REFINEMENT
    # ----------------------------------------------------------------------------------

    heading("PHASE 1 - LOCAL 99% DESIGN REFINEMENT")

    core.SIMULATIONS_PER_SCENARIO = LOCAL_REFINEMENT_SIMULATIONS
    local_rng = np.random.default_rng(LOCAL_REFINEMENT_SEED)

    local_bank = core.build_scenario_bank(
        rng=local_rng,
        pv_profile=pv_profile,
        outage_duration_hours=float(core.DESIGN_OUTAGE_DURATION_HOURS)
    )

    local_grid = evaluate_grid(
        selected_site=selected_site,
        deterministic_design=deterministic_design,
        scenario_bank=local_bank,
        power_multipliers=LOCAL_POWER_MULTIPLIERS,
        energy_multipliers=LOCAL_ENERGY_MULTIPLIERS,
        pv_capacity_mw=pv_capacity_mw,
        pv_ratio=LOCAL_PV_RATIO
    )

    local_grid["Meets99PointEstimate"] = local_grid["SuccessRate"] >= LOCAL_99_TARGET
    local_grid["Meets99WilsonLower95"] = local_grid["Wilson95Lower"] >= LOCAL_99_TARGET

    point_candidates = recommendation_sort(
        local_grid[
            local_grid["Meets99PointEstimate"]
        ].copy()
    )

    if point_candidates.empty:
        raise RuntimeError(
            "No local design met the 99% point-estimate target. Expand the local refinement grid."
        )

    local_best = point_candidates.iloc[0].copy()

    print()
    print("Best locally refined 99% point-estimate design:")
    print(f"  Power multiplier: {local_best['PowerMultiplier']:.3f}")
    print(f"  Energy multiplier: {local_best['EnergyMultiplier']:.3f}")
    print(f"  Battery power: {local_best['BatteryPowerMW']:.6f} MW")
    print(f"  Battery energy: {local_best['BatteryInstalledMWh']:.6f} MWh")
    print(f"  Simulated success: {local_best['SuccessRate']:.4%}")
    print(f"  Wilson 95% interval: [{local_best['Wilson95Lower']:.4%}, {local_best['Wilson95Upper']:.4%}]")

    # ----------------------------------------------------------------------------------
    # PHASE 2 - LARGE-RUN MULTI-SEED VERIFICATION
    # ----------------------------------------------------------------------------------

    candidates = build_large_run_candidate_table(
        local_grid=local_grid,
        deterministic_design=deterministic_design
    )

    heading("PHASE 2 - LARGE-RUN MULTI-SEED VERIFICATION")

    per_seed, aggregate = run_large_verification(
        selected_site=selected_site,
        pv_profile=pv_profile,
        candidates=candidates,
        pv_capacity_mw=pv_capacity_mw
    )

    verified_99 = select_verified_design(
        aggregate=aggregate,
        reliability_target=LOCAL_99_TARGET
    )

    verified_999 = select_verified_design(
        aggregate=aggregate,
        reliability_target=VERIFY_999_TARGET
    )

    final_rows = []

    for target, selected in [
        (LOCAL_99_TARGET, verified_99),
        (VERIFY_999_TARGET, verified_999)
    ]:
        if selected is None:
            final_rows.append({
                "ReliabilityTarget": target,
                "VerifiedDesignFound": False,
                "RecommendedBatteryPowerMW": np.nan,
                "RecommendedBatteryInstalledMWh": np.nan,
                "PowerMultiplier": np.nan,
                "EnergyMultiplier": np.nan,
                "AggregateSuccessRate": np.nan,
                "Wilson95Lower": np.nan,
                "Wilson95Upper": np.nan,
                "TotalSimulations": VERIFICATION_SIMULATIONS_PER_SEED * len(VERIFICATION_SEEDS)
            })
        else:
            final_rows.append({
                "ReliabilityTarget": target,
                "VerifiedDesignFound": True,
                "RecommendedBatteryPowerMW": float(selected["BatteryPowerMW"]),
                "RecommendedBatteryInstalledMWh": float(selected["BatteryInstalledMWh"]),
                "PowerMultiplier": float(selected["PowerMultiplier"]),
                "EnergyMultiplier": float(selected["EnergyMultiplier"]),
                "AggregateSuccessRate": float(selected["AggregateSuccessRate"]),
                "Wilson95Lower": float(selected["Wilson95Lower"]),
                "Wilson95Upper": float(selected["Wilson95Upper"]),
                "TotalSimulations": int(selected["TotalSimulations"])
            })

    final_recommendations = pd.DataFrame(
        final_rows
    )

    # ----------------------------------------------------------------------------------
    # CONSOLE RESULT
    # ----------------------------------------------------------------------------------

    heading("FINAL VERIFIED RESULTS")

    print(
        final_recommendations.to_string(
            index=False
        )
    )

    if verified_99 is not None:
        print()
        print(
            f"Verified 99% recommendation: "
            f"{verified_99['BatteryPowerMW']:.3f} MW / "
            f"{verified_99['BatteryInstalledMWh']:.3f} MWh | "
            f"aggregate success={verified_99['AggregateSuccessRate']:.4%} | "
            f"Wilson lower 95%={verified_99['Wilson95Lower']:.4%}"
        )
    else:
        print()
        print(
            "No tested local design was statistically verified at the 99% target."
        )

    if verified_999 is not None:
        print(
            f"Verified 99.9% recommendation: "
            f"{verified_999['BatteryPowerMW']:.3f} MW / "
            f"{verified_999['BatteryInstalledMWh']:.3f} MWh | "
            f"aggregate success={verified_999['AggregateSuccessRate']:.4%} | "
            f"Wilson lower 95%={verified_999['Wilson95Lower']:.4%}"
        )
    else:
        print(
            "No tested 99.9% neighborhood design was statistically verified at the 99.9% target."
        )

    # ----------------------------------------------------------------------------------
    # OUTPUTS
    # ----------------------------------------------------------------------------------

    assumptions = pd.DataFrame([
        {
            "Item": "Core module",
            "Value": CORE_MODULE
        },
        {
            "Item": "Selected site",
            "Value": f"{selected_site['GridID']} | {selected_site['CandidateLineID']} | {selected_site['SubstationID']}"
        },
        {
            "Item": "Fixed DC load",
            "Value": f"{base_dc_mw:.6f} MW"
        },
        {
            "Item": "Deterministic BESS",
            "Value": f"{deterministic_power_mw:.6f} MW / {deterministic_energy_mwh:.6f} MWh"
        },
        {
            "Item": "Local refinement simulations",
            "Value": LOCAL_REFINEMENT_SIMULATIONS
        },
        {
            "Item": "Local refinement seed",
            "Value": LOCAL_REFINEMENT_SEED
        },
        {
            "Item": "Local power multipliers",
            "Value": str(LOCAL_POWER_MULTIPLIERS.tolist())
        },
        {
            "Item": "Local energy multipliers",
            "Value": str(LOCAL_ENERGY_MULTIPLIERS.tolist())
        },
        {
            "Item": "Verification simulations per seed",
            "Value": VERIFICATION_SIMULATIONS_PER_SEED
        },
        {
            "Item": "Verification seeds",
            "Value": str(VERIFICATION_SEEDS)
        },
        {
            "Item": "Aggregate simulations per verified design",
            "Value": VERIFICATION_SIMULATIONS_PER_SEED * len(VERIFICATION_SEEDS)
        },
        {
            "Item": "99.9% verification neighborhood",
            "Value": str(VERIFY_999_CANDIDATE_MULTIPLIERS)
        },
        {
            "Item": "Final verification rule",
            "Value": (
                "Aggregate point estimate plus Wilson 95% lower confidence bound must meet the reliability target."
                if USE_WILSON_LOWER_BOUND_FOR_FINAL_VERIFICATION
                else
                "Aggregate point estimate must meet the reliability target."
            )
        },
        {
            "Item": "Uncertainty assumptions",
            "Value": "Inherited unchanged from mc_vF.py."
        },
        {
            "Item": "PV case",
            "Value": f"{LOCAL_PV_RATIO:.0%} of DC; battery-only final validation."
        }
    ])

    local_grid.to_csv(
        OUTPUT_LOCAL_GRID_CSV,
        index=False,
        compression="gzip"
    )

    aggregate.to_csv(
        OUTPUT_VERIFICATION_CSV,
        index=False
    )

    workbook_frames = {
        "Final_Verified_Results": final_recommendations,
        "Local_99_Grid": local_grid,
        "Verification_Aggregate": aggregate,
        "Verification_By_Seed": per_seed,
        "Verification_Candidates": candidates,
        "Assumptions": assumptions
    }

    with pd.ExcelWriter(
        OUTPUT_WORKBOOK,
        engine="xlsxwriter"
    ) as writer:
        for sheet_name, frame in workbook_frames.items():
            safe = core.excel_safe(
                frame
            )

            safe.to_excel(
                writer,
                sheet_name=sheet_name[:31],
                index=False
            )

            core.format_excel(
                writer,
                sheet_name[:31],
                safe
            )

    heading("FINAL VALIDATION COMPLETE")

    print(
        f"Workbook: "
        f"{Path(OUTPUT_WORKBOOK).resolve()}"
    )

    print(
        f"Local refinement grid: "
        f"{Path(OUTPUT_LOCAL_GRID_CSV).resolve()}"
    )

    print(
        f"Large-run verification summary: "
        f"{Path(OUTPUT_VERIFICATION_CSV).resolve()}"
    )

    print()
    print(
        "If the 99% and 99.9% rows are statistically verified, the analytical pipeline can be frozen for the final report."
    )

    print(
        "If a target is not verified, expand only the nearby BESS MW/MWh validation neighborhood; do not redesign the core pipeline."
    )


if __name__ == "__main__":
    main()