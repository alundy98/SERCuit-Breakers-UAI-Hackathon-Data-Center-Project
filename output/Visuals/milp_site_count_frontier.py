import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

# ---------------------------------------------------------
# File locations
# ---------------------------------------------------------
PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_FILE = PROJECT_ROOT / "output" / "greensboro_base_milp_results.xlsx"
OUTPUT_FILE = Path(__file__).resolve().parent / "milp_site_count_frontier.png"

# ---------------------------------------------------------
# Read MILP scenario summary
# ---------------------------------------------------------
df = pd.read_excel(
    INPUT_FILE,
    sheet_name="Scenario_Summary"
)

# Keep only Max N Sites scenarios
df = df[df["Scenario"].str.startswith("Max_")].copy()

# Sort by number of sites
df = df.sort_values("MaxSites")

# ---------------------------------------------------------
# Create chart
# ---------------------------------------------------------
plt.figure(figsize=(10, 6))

plt.plot(
    df["MaxSites"],
    df["OptimalTotalMW"],
    marker="o",
    linewidth=2
)

# Add MW labels
for x, y in zip(df["MaxSites"], df["OptimalTotalMW"]):
    plt.annotate(
        f"{y:.1f}",
        (x, y),
        xytext=(0, 8),
        textcoords="offset points",
        ha="center"
    )

# ---------------------------------------------------------
# Formatting
# ---------------------------------------------------------
plt.xlabel("Maximum Number of Sites")
plt.ylabel("Optimized Total MW")

plt.title(
    "MILP-Optimized Total MW by Number of Sites"
)

plt.xticks(df["MaxSites"])

plt.grid(
    axis="y",
    alpha=0.3
)

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