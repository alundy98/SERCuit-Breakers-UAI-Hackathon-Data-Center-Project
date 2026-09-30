import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_FILE = PROJECT_ROOT / "output" / "greensboro_base_milp_results.xlsx"
OUTPUT_FILE = Path(__file__).resolve().parent / "milp_incremental_capacity.png"

df = pd.read_excel(
    INPUT_FILE,
    sheet_name="Scenario_Summary"
)

df = df[
    df["Scenario"].str.startswith("Max_")
].copy()

df = df.sort_values("MaxSites")

# Calculate capacity added by each additional site
df["IncrementalMW"] = (
    df["OptimalTotalMW"].diff()
    .fillna(df["OptimalTotalMW"])
)

fig, ax = plt.subplots(figsize=(10, 6))

bars = ax.bar(
    df["MaxSites"],
    df["IncrementalMW"]
)

for bar, value in zip(bars, df["IncrementalMW"]):
    ax.text(
        bar.get_x() + bar.get_width() / 2,
        bar.get_height() + 0.2,
        f"{value:.1f}",
        ha="center",
        va="bottom"
    )

ax.set_xlabel("Site Number")
ax.set_ylabel("Incremental Capacity Added (MW)")
ax.set_title("MILP Incremental Capacity per Additional Site")
ax.set_xticks(df["MaxSites"])

plt.tight_layout()

plt.savefig(
    OUTPUT_FILE,
    dpi=300,
    bbox_inches="tight"
)

plt.show()

print(f"Chart saved to: {OUTPUT_FILE}")
