import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

MILP_FILE = PROJECT_ROOT / "output" / "greensboro_base_milp_results.xlsx"
THERMAL_FILE = PROJECT_ROOT / "output" / "greensboro_hourly_thermal_hosting_results.xlsx"

OUTPUT_FILE = Path(__file__).resolve().parent / "milp_vs_thermal.png"

# Read MILP selected sites
milp = pd.read_excel(
    MILP_FILE,
    sheet_name="Base_Selected_Sites"
)

milp = milp[milp["Selected"] == True].copy()

milp = milp[
    ["CandidateLineID", "InstalledDCMW"]
].rename(
    columns={"InstalledDCMW": "MILPInstalledMW"}
)

# Read thermal results
thermal = pd.read_excel(
    THERMAL_FILE,
    sheet_name="Candidate_Summary"
)

thermal = thermal[
    ["CandidateLineID", "FirmHostingMW"]
]

# Combine datasets
df = milp.merge(
    thermal,
    on="CandidateLineID",
    how="left"
)

df = df.sort_values("MILPInstalledMW", ascending=True)

# Create chart
fig, ax = plt.subplots(figsize=(12, 7))

y = range(len(df))
bar_height = 0.35

ax.barh(
    [i - bar_height / 2 for i in y],
    df["MILPInstalledMW"],
    height=bar_height,
    label="MILP Installed Capacity"
)

ax.barh(
    [i + bar_height / 2 for i in y],
    df["FirmHostingMW"],
    height=bar_height,
    label="Thermal Firm Hosting"
)

ax.set_yticks(list(y))
ax.set_yticklabels(df["CandidateLineID"])

ax.set_xlabel("Capacity (MW)")
ax.set_ylabel("Candidate Feeder")
ax.set_title(
    "MILP Selected Sites — Screening vs. Thermal Firm Hosting Capacity"
)

ax.legend()

plt.tight_layout()

plt.savefig(
    OUTPUT_FILE,
    dpi=300,
    bbox_inches="tight"
)

plt.show()

print(f"Chart saved to: {OUTPUT_FILE}")