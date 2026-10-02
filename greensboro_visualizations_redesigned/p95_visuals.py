from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib.pyplot as plt
import matplotlib.patheffects as path_effects
import seaborn as sns

INPUT_FILE = "greensboro_substation_constrained_location_rankings.xlsx"
RANKING_SHEET = "Final_Location_Ranking"
OUTPUT_DIR = Path("greensboro_visualizations_redesigned")
FIG_DPI = 220

LOW_THRESHOLD = 80.0
HIGH_THRESHOLD = 95.0
KDE_BANDWIDTH = 2.5

SHOW_PLOTS = True
SAVE_PLOTS = True

AVG_COLOR = "#4C78A8"
P95_COLOR = "#F2A541"
PEAK_COLOR = "#D1495B"

FEEDER_COLOR = "#2A9D8F"
SUBSTATION_COLOR = "#E76F51"
FINAL_COLOR = "#264653"

LOW_BAND = "#FDECEC"
MEDIUM_BAND = "#FFF3D6"
HIGH_BAND = "#E7F5EC"

GRID_COLOR = "#D8DEE8"
TEXT_COLOR = "#263238"

sns.set_theme(style="whitegrid", context="talk", font_scale=0.95)
plt.rcParams["figure.facecolor"] = "white"
plt.rcParams["axes.facecolor"] = "white"
plt.rcParams["axes.edgecolor"] = "#B8C1CC"
plt.rcParams["axes.labelcolor"] = TEXT_COLOR
plt.rcParams["text.color"] = TEXT_COLOR
plt.rcParams["xtick.color"] = TEXT_COLOR
plt.rcParams["ytick.color"] = TEXT_COLOR


def numeric_series(df, column):
    if column not in df.columns:
        return pd.Series(np.nan, index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def bool_series(df, column):
    if column not in df.columns:
        return pd.Series(False, index=df.index, dtype=bool)
    return df[column].astype(str).str.strip().str.lower().isin({"true", "1", "yes"})


def require_columns(df, columns):
    missing = [column for column in columns if column not in df.columns]
    if missing:
        raise RuntimeError(f"{RANKING_SHEET} is missing required columns: {missing}")


def load_data():
    path = Path(INPUT_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {INPUT_FILE}: {path.resolve()}")

    df = pd.read_excel(path, sheet_name=RANKING_SHEET)

    require_columns(df, [
        "GridID",
        "CandidateLineID",
        "SubstationID",
        "EstimatedBaseHostingMW",
        "FeederPathHostingMW",
        "CandidateConfidence",
        "LoadPointReachabilityPct",
        "Substation_AverageLoadMW",
        "Substation_P95LoadMW",
        "Substation_PeakLoadMW",
        "Substation_DataCoveragePct",
        "Substation_LoadedFeederCount",
        "Substation_ExpectedFeederCount",
        "Substation_CapacityResolutionStatus"
    ])

    numeric_columns = [
        "GlobalRank",
        "RankWithinFeeder",
        "EstimatedBaseHostingMW",
        "FeederPathHostingMW",
        "FinalBaseHostingMW",
        "LoadPointReachabilityPct",
        "BreakerCapacityMW",
        "LimitingElementCapacityMW",
        "CandidateLineCapacityMW",
        "Substation_AverageLoadMW",
        "Substation_P95LoadMW",
        "Substation_PeakLoadMW",
        "Substation_DataCoveragePct",
        "Substation_LoadedFeederCount",
        "Substation_ExpectedFeederCount",
        "Substation_AvailableCapacityMW"
    ]

    for column in numeric_columns:
        df[column] = numeric_series(df, column)

    df["VisualHostingMW"] = df["FinalBaseHostingMW"].combine_first(df["FeederPathHostingMW"]).combine_first(df["EstimatedBaseHostingMW"])
    df["HostingBasis"] = np.select(
        [df["FinalBaseHostingMW"].notna(), df["FeederPathHostingMW"].notna()],
        ["Substation-constrained hosting", "Feeder/path thermal hosting"],
        default="Original thermal hosting"
    )

    df["AllSubstationFeedersLoaded"] = bool_series(df, "Substation_AllMappedFeedersLoaded")

    df["SubstationP95UtilizationPct"] = np.where(
        df["Substation_AvailableCapacityMW"] > 0,
        df["Substation_P95LoadMW"] / df["Substation_AvailableCapacityMW"] * 100.0,
        np.nan
    )

    return df


def choose_best_per_feeder(df):
    usable = df[df["VisualHostingMW"].notna()].copy()
    if usable.empty:
        raise RuntimeError("No candidate rows contain usable hosting capacity")

    sort_columns = ["GridID", "VisualHostingMW"]
    ascending = [True, False]

    if "GlobalRank" in usable.columns:
        sort_columns.append("GlobalRank")
        ascending.append(True)

    best = usable.sort_values(sort_columns, ascending=ascending, na_position="last").groupby("GridID", as_index=False).head(1).copy()
    best = best.sort_values(["VisualHostingMW", "GlobalRank"], ascending=[False, True], na_position="last").reset_index(drop=True)
    best["FeederVisualRank"] = np.arange(1, len(best) + 1)

    return best


def build_substation_table(df):
    columns = [
        "SubstationID",
        "Substation_AverageLoadMW",
        "Substation_P95LoadMW",
        "Substation_PeakLoadMW",
        "Substation_DataCoveragePct",
        "Substation_LoadedFeederCount",
        "Substation_ExpectedFeederCount",
        "Substation_CapacityResolutionStatus",
        "Substation_AvailableCapacityMW"
    ]

    substations = df[columns].drop_duplicates(subset=["SubstationID"]).copy()
    substations = substations.rename(columns={
        "Substation_AverageLoadMW": "AverageLoadMW",
        "Substation_P95LoadMW": "P95LoadMW",
        "Substation_PeakLoadMW": "PeakLoadMW",
        "Substation_DataCoveragePct": "DataCoveragePct",
        "Substation_LoadedFeederCount": "LoadedFeederCount",
        "Substation_ExpectedFeederCount": "ExpectedFeederCount",
        "Substation_CapacityResolutionStatus": "CapacityResolutionStatus",
        "Substation_AvailableCapacityMW": "AvailableCapacityMW"
    })

    substations = substations[substations["SubstationID"].notna()].copy()
    return substations


def save_show_close(fig, filename):
    output = OUTPUT_DIR / filename

    if SAVE_PLOTS:
        fig.savefig(output, dpi=FIG_DPI, bbox_inches="tight", facecolor="white")

    if SHOW_PLOTS:
        plt.show()

    plt.close(fig)
    return output


def add_value_label(ax, x, y, text, color, x_offset=7, y_offset=0, ha="left"):
    label = ax.annotate(
        text,
        xy=(x, y),
        xytext=(x_offset, y_offset),
        textcoords="offset points",
        ha=ha,
        va="center",
        fontsize=10,
        fontweight="semibold",
        color=color
    )
    label.set_path_effects([path_effects.withStroke(linewidth=3, foreground="white")])
    return label


def plot_substation_p95(df):
    substations = build_substation_table(df)
    substations = substations.dropna(subset=["AverageLoadMW", "P95LoadMW", "PeakLoadMW"]).copy()

    if substations.empty:
        raise RuntimeError("No substations contain Average, P95, and Peak load values")

    substations = substations.sort_values("P95LoadMW", ascending=True).reset_index(drop=True)
    y_positions = np.arange(len(substations))

    fig_height = max(6.5, 1.0 + len(substations) * 0.8)
    fig, ax = plt.subplots(figsize=(13.5, fig_height))

    for y, row in substations.iterrows():
        ax.hlines(y, row["AverageLoadMW"], row["PeakLoadMW"], color="#CBD5E1", linewidth=8, alpha=0.55, zorder=1)
        ax.hlines(y, row["AverageLoadMW"], row["P95LoadMW"], color="#AFC6E9", linewidth=8, alpha=0.75, zorder=2)

    avg_points = pd.DataFrame({"SubstationID": substations["SubstationID"], "MW": substations["AverageLoadMW"], "Y": y_positions})
    p95_points = pd.DataFrame({"SubstationID": substations["SubstationID"], "MW": substations["P95LoadMW"], "Y": y_positions})
    peak_points = pd.DataFrame({"SubstationID": substations["SubstationID"], "MW": substations["PeakLoadMW"], "Y": y_positions})

    sns.scatterplot(data=avg_points, x="MW", y="Y", s=150, marker="o", color=AVG_COLOR, edgecolor="white", linewidth=1.4, label="Average", ax=ax, zorder=4)
    sns.scatterplot(data=p95_points, x="MW", y="Y", s=190, marker="D", color=P95_COLOR, edgecolor="white", linewidth=1.4, label="P95", ax=ax, zorder=5)
    sns.scatterplot(data=peak_points, x="MW", y="Y", s=190, marker="X", color=PEAK_COLOR, edgecolor="white", linewidth=1.4, label="Peak", ax=ax, zorder=6)

    for y, row in substations.iterrows():
        add_value_label(ax, row["AverageLoadMW"], y, f"{row['AverageLoadMW']:.2f}", AVG_COLOR, x_offset=-8, ha="right")
        add_value_label(ax, row["P95LoadMW"], y, f"{row['P95LoadMW']:.2f}", P95_COLOR, x_offset=0, y_offset=14, ha="center")
        add_value_label(ax, row["PeakLoadMW"], y, f"{row['PeakLoadMW']:.2f}", PEAK_COLOR, x_offset=8, ha="left")

    ax.set_yticks(y_positions)
    ax.set_yticklabels(substations["SubstationID"])
    ax.set_title("Substation Average, P95, and Peak Load", fontsize=22, fontweight="bold", pad=18)
    ax.set_xlabel("Load (MW)", fontsize=13, fontweight="semibold")
    ax.set_ylabel("")
    ax.grid(axis="x", color=GRID_COLOR, linewidth=0.9, alpha=0.8)
    ax.grid(axis="y", visible=False)
    ax.margins(x=0.12)
    ax.legend(title="", frameon=False, ncol=3, loc="upper center", bbox_to_anchor=(0.5, -0.10))
    sns.despine(ax=ax, left=True, bottom=False)
    fig.tight_layout()

    return save_show_close(fig, "01_substation_average_p95_peak.png"), substations


def score_feeder_confidence(best_feeders):
    df = best_feeders.copy()

    reachability = df["LoadPointReachabilityPct"].clip(0, 100).fillna(0)

    breaker_present = df["BreakerCapacityMW"].notna().astype(float) * 100.0
    limiting_present = df["LimitingElementCapacityMW"].notna().astype(float) * 100.0
    candidate_rating_present = df["CandidateLineCapacityMW"].notna().astype(float) * 100.0

    rating_completeness = (breaker_present + limiting_present + candidate_rating_present) / 3.0

    df["FeederConfidenceScore"] = 0.70 * reachability + 0.30 * rating_completeness
    df["FeederConfidenceScore"] = df["FeederConfidenceScore"].clip(0, 100)

    return df


def score_substation_confidence(substations):
    df = substations.copy()

    coverage = df["DataCoveragePct"].clip(0, 100).fillna(0)

    feeder_completeness = np.where(
        df["ExpectedFeederCount"] > 0,
        df["LoadedFeederCount"] / df["ExpectedFeederCount"] * 100.0,
        0.0
    )
    feeder_completeness = np.clip(feeder_completeness, 0, 100)

    status = df["CapacityResolutionStatus"].astype(str).str.strip().str.lower()
    capacity_score = np.select(
        [status.eq("resolved"), status.eq("ambiguous")],
        [100.0, 50.0],
        default=0.0
    )

    df["SubstationFeederCompletenessPct"] = feeder_completeness
    df["SubstationCapacityResolutionScore"] = capacity_score
    df["SubstationConfidenceScore"] = 0.45 * coverage + 0.30 * feeder_completeness + 0.25 * capacity_score
    df["SubstationConfidenceScore"] = df["SubstationConfidenceScore"].clip(0, 100)

    return df


def classify_score(score):
    if pd.isna(score):
        return "Low"
    if score >= HIGH_THRESHOLD:
        return "High"
    if score >= LOW_THRESHOLD:
        return "Medium"
    return "Low"


def build_confidence_data(df):
    best_feeders = score_feeder_confidence(choose_best_per_feeder(df))
    substations = score_substation_confidence(build_substation_table(df))

    substation_lookup = substations.set_index("SubstationID")["SubstationConfidenceScore"].to_dict()
    best_feeders["SubstationConfidenceScore"] = best_feeders["SubstationID"].map(substation_lookup)
    best_feeders["FinalLocationConfidenceScore"] = np.minimum(best_feeders["FeederConfidenceScore"], best_feeders["SubstationConfidenceScore"])

    best_feeders["FeederConfidenceBand"] = best_feeders["FeederConfidenceScore"].apply(classify_score)
    best_feeders["SubstationConfidenceBand"] = best_feeders["SubstationConfidenceScore"].apply(classify_score)
    best_feeders["FinalLocationConfidenceBand"] = best_feeders["FinalLocationConfidenceScore"].apply(classify_score)

    return best_feeders, substations


def kernel_density(values, x_grid, bandwidth=KDE_BANDWIDTH):
    values = np.asarray(values, dtype=float)
    values = values[np.isfinite(values)]

    if len(values) == 0:
        return np.zeros_like(x_grid, dtype=float)

    differences = (x_grid[:, None] - values[None, :]) / bandwidth
    kernels = np.exp(-0.5 * differences ** 2) / (bandwidth * np.sqrt(2 * np.pi))
    return kernels.mean(axis=1)


def plot_confidence_threshold_curves(df):
    best_feeders, substations = build_confidence_data(df)

    feeder_values = best_feeders["FeederConfidenceScore"].dropna().to_numpy()
    substation_values = substations["SubstationConfidenceScore"].dropna().to_numpy()
    final_values = best_feeders["FinalLocationConfidenceScore"].dropna().to_numpy()

    x_grid = np.linspace(0, 100, 700)
    feeder_density = kernel_density(feeder_values, x_grid)
    substation_density = kernel_density(substation_values, x_grid)
    final_density = kernel_density(final_values, x_grid)

    density_df = pd.DataFrame({
        "Confidence score": np.tile(x_grid, 3),
        "Density": np.concatenate([feeder_density, substation_density, final_density]),
        "Level": np.repeat(["Feeder", "Substation", "Final location"], len(x_grid))
    })

    palette = {"Feeder": FEEDER_COLOR, "Substation": SUBSTATION_COLOR, "Final location": FINAL_COLOR}

    fig, ax = plt.subplots(figsize=(13.5, 7.5))

    ax.axvspan(0, LOW_THRESHOLD, color=LOW_BAND, alpha=0.72, zorder=0)
    ax.axvspan(LOW_THRESHOLD, HIGH_THRESHOLD, color=MEDIUM_BAND, alpha=0.72, zorder=0)
    ax.axvspan(HIGH_THRESHOLD, 100, color=HIGH_BAND, alpha=0.72, zorder=0)

    sns.lineplot(data=density_df, x="Confidence score", y="Density", hue="Level", palette=palette, linewidth=3.2, ax=ax)

    for level, values, color in [
        ("Feeder", feeder_values, FEEDER_COLOR),
        ("Substation", substation_values, SUBSTATION_COLOR),
        ("Final location", final_values, FINAL_COLOR)
    ]:
        median = float(np.nanmedian(values))
        density_at_median = float(kernel_density(values, np.array([median]))[0])
        ax.scatter(median, density_at_median, s=95, color=color, edgecolor="white", linewidth=1.2, zorder=5)
        ax.annotate(f"{level} median {median:.1f}", (median, density_at_median), xytext=(7, 8), textcoords="offset points", fontsize=10, fontweight="semibold", color=color)

    ax.axvline(LOW_THRESHOLD, color="#B7791F", linestyle="--", linewidth=2)
    ax.axvline(HIGH_THRESHOLD, color="#2F855A", linestyle="--", linewidth=2)

    ymax = max(feeder_density.max(), substation_density.max(), final_density.max())
    ax.text(LOW_THRESHOLD / 2, ymax * 1.05, "LOW", ha="center", va="bottom", fontsize=11, fontweight="bold", color="#9B2C2C")
    ax.text((LOW_THRESHOLD + HIGH_THRESHOLD) / 2, ymax * 1.05, "MEDIUM", ha="center", va="bottom", fontsize=11, fontweight="bold", color="#975A16")
    ax.text((HIGH_THRESHOLD + 100) / 2, ymax * 1.05, "HIGH", ha="center", va="bottom", fontsize=11, fontweight="bold", color="#276749")

    ax.set_xlim(0, 100)
    ax.set_ylim(bottom=0, top=ymax * 1.14)
    ax.set_title("Confidence Score Distributions and Thresholds", fontsize=22, fontweight="bold", pad=18)
    ax.set_xlabel("Confidence score", fontsize=13, fontweight="semibold")
    ax.set_ylabel("Relative density", fontsize=13, fontweight="semibold")
    ax.grid(axis="y", color=GRID_COLOR, linewidth=0.9, alpha=0.75)
    ax.grid(axis="x", visible=False)
    ax.legend(title="", frameon=False, loc="upper left")
    sns.despine(ax=ax)
    fig.tight_layout()

    output = save_show_close(fig, "02_confidence_threshold_curves.png")

    return output, best_feeders, substations


def choose_scatter_y(best_feeders):
    resolved = best_feeders["SubstationP95UtilizationPct"].notna()

    if resolved.sum() >= max(3, int(np.ceil(len(best_feeders) * 0.50))):
        return "SubstationP95UtilizationPct", "Substation P95 utilization (%)", "Hosting Capacity vs Substation P95 Utilization"

    return "Substation_P95LoadMW", "Substation P95 load (MW)", "Hosting Capacity vs Substation P95 Load"


def plot_rank_scatter(df):
    best_feeders, _ = build_confidence_data(df)
    y_field, y_label, title = choose_scatter_y(best_feeders)

    plot_df = best_feeders[best_feeders["VisualHostingMW"].notna() & best_feeders[y_field].notna()].copy()

    if plot_df.empty:
        raise RuntimeError("No best-per-feeder rows have both hosting capacity and P95 data")

    plot_df = plot_df.sort_values("FeederVisualRank").reset_index(drop=True)

    substations = sorted(plot_df["SubstationID"].dropna().unique().tolist())
    colors = sns.color_palette("husl", n_colors=max(len(substations), 1))
    substation_palette = {substation: colors[index] for index, substation in enumerate(substations)}

    fig, ax = plt.subplots(figsize=(14.5, 9))

    sns.scatterplot(
        data=plot_df,
        x="VisualHostingMW",
        y=y_field,
        hue="SubstationID",
        palette=substation_palette,
        s=190,
        edgecolor="white",
        linewidth=1.6,
        alpha=0.95,
        ax=ax
    )

    x_median = float(plot_df["VisualHostingMW"].median())
    y_median = float(plot_df[y_field].median())

    ax.axvline(x_median, color="#7A869A", linestyle="--", linewidth=1.3, alpha=0.8)
    ax.axhline(y_median, color="#7A869A", linestyle="--", linewidth=1.3, alpha=0.8)

    x_min, x_max = plot_df["VisualHostingMW"].min(), plot_df["VisualHostingMW"].max()
    y_min, y_max = plot_df[y_field].min(), plot_df[y_field].max()

    x_pad = max((x_max - x_min) * 0.10, 0.5)
    y_pad = max((y_max - y_min) * 0.10, 0.2)

    ax.set_xlim(x_min - x_pad, x_max + x_pad)
    ax.set_ylim(max(0, y_min - y_pad), y_max + y_pad * 1.6)

    grouped_offsets = {}

    for _, row in plot_df.iterrows():
        key = row["SubstationID"]
        grouped_offsets[key] = grouped_offsets.get(key, 0) + 1
        occurrence = grouped_offsets[key]

        if occurrence % 4 == 1:
            offset = (8, 10)
        elif occurrence % 4 == 2:
            offset = (8, -18)
        elif occurrence % 4 == 3:
            offset = (-8, 12)
        else:
            offset = (-8, -20)

        ha = "left" if offset[0] > 0 else "right"
        label = f"#{int(row['FeederVisualRank'])} {row['GridID']}"

        annotation = ax.annotate(
            label,
            (row["VisualHostingMW"], row[y_field]),
            xytext=offset,
            textcoords="offset points",
            ha=ha,
            va="center",
            fontsize=9.5,
            fontweight="bold",
            color=TEXT_COLOR,
            arrowprops=dict(arrowstyle="-", color="#9AA5B1", linewidth=0.7, alpha=0.75)
        )
        annotation.set_path_effects([path_effects.withStroke(linewidth=3, foreground="white")])

    ax.text(
        x_max + x_pad * 0.85,
        max(0, y_min - y_pad * 0.65),
        "Higher hosting\nLower P95",
        ha="right",
        va="bottom",
        fontsize=10,
        fontweight="bold",
        color="#2F855A"
    )

    ax.set_title(title, fontsize=22, fontweight="bold", pad=18)
    ax.set_xlabel("Usable hosting estimate (MW)", fontsize=13, fontweight="semibold")
    ax.set_ylabel(y_label, fontsize=13, fontweight="semibold")
    ax.grid(color=GRID_COLOR, linewidth=0.9, alpha=0.75)
    ax.legend(title="Substation", frameon=False, bbox_to_anchor=(1.02, 1), loc="upper left")
    sns.despine(ax=ax)
    fig.tight_layout()

    output = save_show_close(fig, "03_rank_scatter_best_feeder_locations.png")
    return output, plot_df


def write_audit(p95_data, feeder_confidence, substation_confidence, scatter_data):
    output = OUTPUT_DIR / "visualization_audit.xlsx"

    rules = pd.DataFrame([
        ["P95 visual", "One row per substation with Average P95 and annual Peak load"],
        ["Scatter candidate set", "Only the best hosting candidate on each feeder is plotted so repeated line candidates do not obscure the chart"],
        ["Visual hosting value", "FinalBaseHostingMW when available otherwise FeederPathHostingMW otherwise EstimatedBaseHostingMW"],
        ["Feeder confidence score", "70 percent LoadPointReachabilityPct plus 30 percent equipment rating completeness"],
        ["Equipment rating completeness", "Equal availability checks for breaker capacity limiting element capacity and candidate line capacity"],
        ["Substation confidence score", "45 percent SCADA coverage plus 30 percent loaded feeder completeness plus 25 percent explicit capacity resolution"],
        ["Capacity resolution score", "Resolved equals 100 Ambiguous equals 50 Unresolved equals 0"],
        ["Final location confidence score", "Minimum of feeder confidence and the serving substation confidence"],
        ["Low confidence", f"Score below {LOW_THRESHOLD:.0f}"],
        ["Medium confidence", f"Score from {LOW_THRESHOLD:.0f} to below {HIGH_THRESHOLD:.0f}"],
        ["High confidence", f"Score at least {HIGH_THRESHOLD:.0f}"],
        ["Current substation capacity limitation", "When substation capacity is unresolved the scatter uses P95 load MW instead of P95 utilization percent"]
    ], columns=["Item", "Definition"])

    with pd.ExcelWriter(output, engine="xlsxwriter") as writer:
        p95_data.to_excel(writer, sheet_name="P95_Substations", index=False)
        feeder_confidence.to_excel(writer, sheet_name="Feeder_Final_Confidence", index=False)
        substation_confidence.to_excel(writer, sheet_name="Substation_Confidence", index=False)
        scatter_data.to_excel(writer, sheet_name="Scatter_Best_Feeders", index=False)
        rules.to_excel(writer, sheet_name="Confidence_Rules", index=False)

    return output


def print_summary(df, feeder_confidence, substation_confidence, scatter_data):
    print("\nVISUALIZATION INPUT SUMMARY")
    print("-" * 80)
    print(f"Candidate rows loaded: {len(df):,}")
    print(f"Feeders represented: {df['GridID'].nunique():,}")
    print(f"Substations represented: {df['SubstationID'].nunique():,}")
    print(f"Best-per-feeder scatter points: {len(scatter_data):,}")
    print(f"Rows with resolved substation capacity: {df['Substation_AvailableCapacityMW'].notna().sum():,}")

    print("\nCONFIDENCE SUMMARY")
    print("-" * 80)
    print(f"Feeder score median: {feeder_confidence['FeederConfidenceScore'].median():.2f}")
    print(f"Substation score median: {substation_confidence['SubstationConfidenceScore'].median():.2f}")
    print(f"Final location score median: {feeder_confidence['FinalLocationConfidenceScore'].median():.2f}")

    print("\nBEST FEEDER LOCATIONS USED IN SCATTER")
    print("-" * 80)
    columns = ["FeederVisualRank", "GridID", "CandidateLineID", "SubstationID", "VisualHostingMW", "Substation_P95LoadMW", "FeederConfidenceScore", "SubstationConfidenceScore", "FinalLocationConfidenceScore"]
    print(scatter_data[columns].to_string(index=False))

def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Loading ranking workbook...")
    df = load_data()

    print("Creating substation Average / P95 / Peak visual...")
    p95_plot, p95_data = plot_substation_p95(df)

    print("Creating confidence threshold distribution visual...")
    confidence_plot, feeder_confidence, substation_confidence = plot_confidence_threshold_curves(df)

    print("Creating clean best-per-feeder rank scatterplot...")
    scatter_plot, scatter_data = plot_rank_scatter(df)

    audit_file = write_audit(p95_data, feeder_confidence, substation_confidence, scatter_data)
    print_summary(df, feeder_confidence, substation_confidence, scatter_data)

    print("\nOUTPUTS")
    print("-" * 80)
    print(f"P95 visual: {p95_plot.resolve()}")
    print(f"Confidence visual: {confidence_plot.resolve()}")
    print(f"Rank scatterplot: {scatter_plot.resolve()}")
    print(f"Audit workbook: {audit_file.resolve()}")


if __name__ == "__main__":
    main()
