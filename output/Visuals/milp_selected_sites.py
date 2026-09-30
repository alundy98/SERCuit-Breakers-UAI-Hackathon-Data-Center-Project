import pandas as pd
import matplotlib.pyplot as plt
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent

INPUT_FILE = PROJECT_ROOT / "output" / "greensboro_base_milp_results.xlsx"
OUTPUT_FILE = Path(__file__).resolve().parent / "milp_selected_sites.png"

df = pd.read_excel(
    INPUT_FILE,
    sheet_name="Base_Selected_Sites"
)

df = df[df["Selected"] == True].copy()
df = df.sort_values("InstalledDCMW", ascending=True)

fig, ax = plt.subplots(figsize=(11, 7))

ax.barh(
    df["CandidateLineID"],
    df["InstalledDCMW"]
)

for i, value in enumerate(df["InstalledDCMW"]):
    ax.text(
        value + 0.2,
        i,
        f"{value:.1f} MW",
        va="center"
    )

ax.set_xlabel("Installed Data Center Capacity (MW)")
ax.set_ylabel("Candidate Feeder")
ax.set_title("MILP Selected Sites — Installed Data Center Capacity")

plt.tight_layout()

plt.savefig(
    OUTPUT_FILE,
    dpi=300,
    bbox_inches="tight"
)

plt.show()

print(f"Chart saved to: {OUTPUT_FILE}")