import subprocess
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent

DOWNSTREAM = [
    ("MILP 2 battery and PV resilience", "greensboro_milp2_battery_pv_resilience_optimized.py"),
    ("Monte Carlo design sizing", "mc_vF.py"),
    ("Final Monte Carlo validation", "mc_validation.py"),
    ("Final resilience visuals", "res_vis.py"),
    ("Final candidate map", "map.py"),
]

SAFE_METADATA_COLUMNS = {
    "CandidateKey",
    "GridID",
    "SubstationID",
    "CandidateLineID",
}


def check_files():
    required = ["milp_final_data.py"] + [script for _, script in DOWNSTREAM]
    missing = [name for name in required if not (ROOT / name).exists()]

    if missing:
        print("\nMissing required scripts:")
        for name in missing:
            print(f"  {name}")
        raise SystemExit("\nCannot start until the missing scripts are present.")


def run_milp1_with_duplicate_insert_fix():
    print("\n" + "=" * 100)
    print("STEP 1: MILP 1 SITE AND LOAD OPTIMIZATION")
    print("Running milp_final_data.py with duplicate metadata insert protection")
    print("=" * 100)

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


def run_script(step_number, total_steps, name, script):
    print("\n" + "=" * 100)
    print(f"STEP {step_number}/{total_steps}: {name}")
    print(f"Running {script}")
    print("=" * 100)

    subprocess.run(
        [sys.executable, str(ROOT / script)],
        cwd=ROOT,
        check=True
    )


def main():
    print("\nGREENSBORO PIPELINE DEBUG RUN")
    print("Starting at the failing MILP 1 stage")
    print(f"Repository: {ROOT}")

    check_files()

    try:
        run_milp1_with_duplicate_insert_fix()
    except Exception as exc:
        print("\n" + "=" * 100)
        print("MILP 1 FAILED")
        print(f"{type(exc).__name__}: {exc}")
        print("=" * 100)
        raise

    total_steps = len(DOWNSTREAM) + 1

    for number, (name, script) in enumerate(DOWNSTREAM, start=2):
        try:
            run_script(number, total_steps, name, script)
        except subprocess.CalledProcessError as exc:
            print("\n" + "=" * 100)
            print("PIPELINE STOPPED")
            print(f"Failed step: {name}")
            print(f"Script: {script}")
            print(f"Exit code: {exc.returncode}")
            print("=" * 100)
            raise SystemExit(exc.returncode)

    print("\n" + "=" * 100)
    print("PIPELINE COMPLETE")
    print("=" * 100)


if __name__ == "__main__":
    main()
