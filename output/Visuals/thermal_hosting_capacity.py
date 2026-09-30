import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ---------------------------------------------------------
# File locations
# ---------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_FILE = PROJECT_ROOT / "output" / "greensboro_hourly_thermal_hosting_results.xlsx"
OUTPUT_FILE = Path(__file__).resolve().parent / "thermal_hosting_capacity.png"

# ---------------------------------------------------------
# Read candidate summary
# ---------------------------------------------------------
df = pd.read_excel(
    INPUT_FILE,
    sheet_name="Candidate_Summary"
)

# ---------------------------------------------------------
# Keep top 10 thermal-ranked candidates
# ---------------------------------------------------------
df = (
    df.sort_values("ThermalRank")
      .head(10)
      .copy()
)

# Short candidate labels
df["Candidate"] = (
    df["CandidateLineID"]
    .astype(str)
    .str.replace("line_", "", regex=False)
)

# ---------------------------------------------------------
# Create chart
# ---------------------------------------------------------
fig, ax = plt.subplots(figsize=(12, 7))

y = range(len(df))
bar_height = 0.25

ax.barh(
    [i - bar_height for i in y],
    df["OriginalScreeningHostingMW"],
    height=bar_height,
    label="Original Screening"
)

ax.barh(
    y,
    df["FirmHostingMW"],
    height=bar_height,
    label="Firm Hosting"
)

ax.barh(
    [i + bar_height for i in y],
    df["HostingAvailable95PctHoursMW"],
    height=bar_height,
    label="95% Availability"
)

# ---------------------------------------------------------
# Formatting
# ---------------------------------------------------------
ax.set_yticks(list(y))
ax.set_yticklabels(df["Candidate"])

ax.invert_yaxis()

ax.set_xlabel("Hosting Capacity (MW)")
ax.set_ylabel("Candidate Location")

ax.set_title(
    "Top Candidate Locations — Thermal Hosting Capacity"
)

ax.legend()

plt.tight_layout()

# ---------------------------------------------------------
# Save
# ---------------------------------------------------------
plt.savefig(
    OUTPUT_FILE,
    dpi=300,
    bbox_inches="tight"
)

plt.show()

print(f"Chart saved to: {OUTPUT_FILE}")
