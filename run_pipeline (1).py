import argparse
import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

PIPELINE = [
    ("Feeder peak analysis", "hours_near_peakMW.py"),
    ("Candidate location screening", "location_screen.py"),
    ("Substation constrained ranking", "location_rank_w_subs.py"),
    ("Five minute thermal validation", "thermal_model.py"),
    ("Substation five minute profiles", "substation_milp_constraint.py"),
    ("PV and EV resource profiles", "resource_flex.py"),
    ("MILP 1 site and load optimization", "milp_final_data.py"),
    ("MILP 2 battery and PV resilience", "greensboro_milp2_battery_pv_resilience_optimized.py"),
    ("Monte Carlo design sizing", "mc_vF.py"),
    ("Final Monte Carlo validation", "mc_validation.py"),
    ("Final resilience visuals", "res_vis.py"),
    ("Final candidate map", "map.py"),
]

REQUIRED_STARTING_FILES = [
    ".env",
    "greensboro_flag_resolution_audit.xlsx",
]

SAFE_METADATA_COLUMNS = {
    "CandidateKey",
    "GridID",
    "SubstationID",
    "CandidateLineID",
}


def check_required_files():
    missing_scripts = [
        script
        for _, script in PIPELINE
        if not (ROOT / script).exists()
    ]

    missing_inputs = [
        filename
        for filename in REQUIRED_STARTING_FILES
        if not (ROOT / filename).exists()
    ]

    if missing_scripts:
        print("\nMissing pipeline scripts:")
        for script in missing_scripts:
            print(f"  {script}")

    if missing_inputs:
        print("\nMissing required starting files:")
        for filename in missing_inputs:
            print(f"  {filename}")

    if missing_scripts or missing_inputs:
        raise SystemExit(
            "\nPipeline cannot start until the missing files above are present."
        )


def find_start_index(start_at):
    if start_at is None:
        return 0

    start_at = start_at.strip().lower()

    for index, (name, script) in enumerate(PIPELINE):
        if start_at in {name.lower(), script.lower()}:
            return index

    valid = "\n".join(f"  {script}" for _, script in PIPELINE)

    raise SystemExit(
        f"\nUnknown --start-at value: {start_at}\n\n"
        f"Valid script names are:\n{valid}"
    )


def run_normal_script(step_number, total_steps, name, script):
    print("\n" + "=" * 100)
    print(f"STEP {step_number}/{total_steps}: {name}")
    print(f"Running {script}")
    print("=" * 100)

    subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=ROOT,
        check=True
    )


def run_milp1_with_duplicate_insert_fix(step_number, total_steps):
    print("\n" + "=" * 100)
    print(f"STEP {step_number}/{total_steps}: MILP 1 site and load optimization")
    print("Running milp_final_data.py with duplicate metadata insert protection")
    print("=" * 100)

    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))

    import milp_final_data

    original_insert = pd.DataFrame.insert

    def safe_insert(self, loc, column, value, allow_duplicates=False):
        if column in SAFE_METADATA_COLUMNS and column in self.columns:
            existing = self[column].copy()
            self[column] = value

            try:
                same = existing.astype(str).equals(self[column].astype(str))
            except Exception:
                same = False

            if same:
                print(f"  duplicate metadata column already present and matched: {column}")
            else:
                print(f"  duplicate metadata column already present and refreshed: {column}")

            return None

        return original_insert(
            self,
            loc,
            column,
            value,
            allow_duplicates=allow_duplicates
        )

    pd.DataFrame.insert = safe_insert

    try:
        milp_final_data.main()
    finally:
        pd.DataFrame.insert = original_insert


def run_step(step_number, total_steps, name, script):
    if script == "milp_final_data.py":
        run_milp1_with_duplicate_insert_fix(
            step_number,
            total_steps
        )
    else:
        run_normal_script(
            step_number,
            total_steps,
            name,
            script
        )


def main():
    parser = argparse.ArgumentParser(
        description="Run the Greensboro data center optimization pipeline."
    )

    parser.add_argument(
        "--start-at",
        default=None,
        help="Optional script name to restart from, for example thermal_model.py"
    )

    args = parser.parse_args()

    print("\nGREENSBORO DATA CENTER OPTIMIZATION PIPELINE")
    print(f"Repository: {ROOT}")

    check_required_files()

    start_index = find_start_index(args.start_at)
    steps = PIPELINE[start_index:]

    if args.start_at:
        print(f"Starting at: {steps[0][1]}")
    else:
        print("Starting from the beginning.")

    for step_number, (name, script) in enumerate(steps, start=1):
        try:
            run_step(
                step_number,
                len(steps),
                name,
                script
            )
        except subprocess.CalledProcessError as exc:
            print("\n" + "=" * 100)
            print("PIPELINE STOPPED")
            print(f"Failed step: {name}")
            print(f"Script: {script}")
            print(f"Exit code: {exc.returncode}")
            print("=" * 100)
            raise SystemExit(exc.returncode)
        except Exception as exc:
            print("\n" + "=" * 100)
            print("PIPELINE STOPPED")
            print(f"Failed step: {name}")
            print(f"Script: {script}")
            print(f"{type(exc).__name__}: {exc}")
            print("=" * 100)
            raise

    print("\n" + "=" * 100)
    print("PIPELINE COMPLETE")
    print("=" * 100)
    print("All configured pipeline scripts completed successfully.")


if __name__ == "__main__":
    main()
