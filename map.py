import os
from pathlib import Path

import pandas as pd
import matplotlib.pyplot as plt
import psycopg2
from dotenv import load_dotenv


THERMAL_FILE = "greensboro_final_thermal_hosting_results.xlsx"
THERMAL_SHEET = "Candidate_Summary"

SELECTED_GRID_ID = "GSO_122"
SELECTED_LINE_ID = "line_5852_22"

OUTPUT_FILE = "greensboro_final_candidate_cluster_map.png"

load_dotenv()


def connect_to_edm():
    return psycopg2.connect(
        host=os.getenv("EDM_HOST"),
        user=os.getenv("EDM_USER"),
        password=os.getenv("EDM_PASSWORD"),
        dbname=os.getenv("EDM_DATABASE", "edm")
    )


def query_dataframe(conn, query, params=None):
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def load_candidates():
    df = pd.read_excel(THERMAL_FILE, sheet_name=THERMAL_SHEET)

    required = ["GridID", "CandidateLineID"]
    missing = [column for column in required if column not in df.columns]

    if missing:
        raise KeyError(f"Missing columns in {THERMAL_FILE}:{THERMAL_SHEET}: {missing}")

    capacity_columns = [
        "FirmHostingMW",
        "ScreeningHostingMW",
        "AvailableThermalHostingMW"
    ]

    capacity_col = next((column for column in capacity_columns if column in df.columns), None)

    if capacity_col is None:
        raise KeyError("Could not find a hosting-capacity column in Candidate_Summary.")

    df = df[["GridID", "CandidateLineID", capacity_col]].copy()
    df = df.rename(columns={capacity_col: "FirmHostingMW"})

    df["GridID"] = df["GridID"].astype(str).str.strip()
    df["CandidateLineID"] = df["CandidateLineID"].astype(str).str.strip()
    df["FirmHostingMW"] = pd.to_numeric(df["FirmHostingMW"], errors="coerce")

    df = df.dropna(subset=["FirmHostingMW"])
    df = df.sort_values("FirmHostingMW", ascending=False).reset_index(drop=True)

    return df


def get_candidate_coordinates(conn, candidates):
    rows = []

    for _, candidate in candidates.iterrows():
        grid_id = candidate["GridID"]
        line_id = candidate["CandidateLineID"]

        result = query_dataframe(
            conn,
            """
            SELECT
                grid_id AS "GridID",
                grid_element_id AS "CandidateLineID",
                ST_Y(ST_Centroid(geometry)) AS "Latitude",
                ST_X(ST_Centroid(geometry)) AS "Longitude"
            FROM grid_element
            WHERE grid_id = %s
              AND grid_element_id = %s
              AND geometry IS NOT NULL;
            """,
            (grid_id, line_id)
        )

        if result.empty:
            print(f"WARNING: no geometry found for {grid_id} | {line_id}")
            continue

        rows.append(result.iloc[0].to_dict())

    return pd.DataFrame(rows)


def get_candidate_feeder_lines(conn, grid_ids):
    return query_dataframe(
        conn,
        """
        SELECT
            grid_id AS "GridID",
            ST_X(ST_StartPoint(geometry)) AS "StartLongitude",
            ST_Y(ST_StartPoint(geometry)) AS "StartLatitude",
            ST_X(ST_EndPoint(geometry)) AS "EndLongitude",
            ST_Y(ST_EndPoint(geometry)) AS "EndLatitude"
        FROM grid_element
        WHERE grid_id = ANY(%s)
          AND LOWER(type) LIKE %s
          AND geometry IS NOT NULL
          AND GeometryType(geometry) = 'LINESTRING';
        """,
        (list(grid_ids), "%line%")
    )


def plot_map(candidates, feeder_lines):
    selected_mask = (
        (candidates["GridID"] == SELECTED_GRID_ID) &
        (candidates["CandidateLineID"] == SELECTED_LINE_ID)
    )

    selected = candidates[selected_mask].copy()
    others = candidates[~selected_mask].copy()

    fig, ax = plt.subplots(figsize=(11, 9))

    # plot feeder geography behind the candidate points
    if not feeder_lines.empty:
        for _, line in feeder_lines.iterrows():
            values = [
                line["StartLongitude"],
                line["StartLatitude"],
                line["EndLongitude"],
                line["EndLatitude"]
            ]

            if any(pd.isna(value) for value in values):
                continue

            ax.plot(
                [line["StartLongitude"], line["EndLongitude"]],
                [line["StartLatitude"], line["EndLatitude"]],
                linewidth=0.7,
                alpha=0.18
            )

    # other viable candidates
    ax.scatter(
        others["Longitude"],
        others["Latitude"],
        s=130,
        alpha=0.75,
        label="Final evaluated candidate sites",
        zorder=3
    )

    # recommended candidate
    if not selected.empty:
        ax.scatter(
            selected["Longitude"],
            selected["Latitude"],
            s=500,
            marker="*",
            edgecolors="black",
            linewidths=1.3,
            label="Final recommended site",
            zorder=5
        )

    # candidate labels
    for _, row in others.iterrows():
        ax.annotate(
            f"{row['GridID']}\n{row['FirmHostingMW']:.2f} MW",
            (row["Longitude"], row["Latitude"]),
            xytext=(6, 6),
            textcoords="offset points",
            fontsize=8,
            zorder=4
        )

    # larger recommendation callout
    if not selected.empty:
        row = selected.iloc[0]

        ax.annotate(
            f"RECOMMENDED SITE\n"
            f"{row['GridID']} | {row['CandidateLineID']}\n"
            f"{row['FirmHostingMW']:.2f} MW firm hosting",
            (row["Longitude"], row["Latitude"]),
            xytext=(35, 35),
            textcoords="offset points",
            fontsize=10,
            fontweight="bold",
            bbox=dict(boxstyle="round,pad=0.5", facecolor="white", alpha=0.9),
            arrowprops=dict(arrowstyle="->", linewidth=1.5),
            zorder=6
        )

    ax.set_title(
        "Greensboro Data-Center Siting\nFinal Candidate Cluster and Recommended Location",
        fontsize=15,
        fontweight="bold"
    )

    ax.set_xlabel("Longitude")
    ax.set_ylabel("Latitude")

    ax.grid(alpha=0.15)
    ax.legend(loc="best")

    # tight geographic framing around final candidates
    lon_range = candidates["Longitude"].max() - candidates["Longitude"].min()
    lat_range = candidates["Latitude"].max() - candidates["Latitude"].min()

    lon_pad = max(lon_range * 0.15, 0.005)
    lat_pad = max(lat_range * 0.15, 0.005)

    ax.set_xlim(
        candidates["Longitude"].min() - lon_pad,
        candidates["Longitude"].max() + lon_pad
    )

    ax.set_ylim(
        candidates["Latitude"].min() - lat_pad,
        candidates["Latitude"].max() + lat_pad
    )

    plt.tight_layout()
    plt.savefig(OUTPUT_FILE, dpi=300, bbox_inches="tight")
    plt.show()


def main():
    print("\nGREENSBORO FINAL CANDIDATE MAP")
    print("=" * 80)

    candidates = load_candidates()

    print(f"Candidates loaded from thermal model: {len(candidates)}")

    conn = connect_to_edm()

    try:
        coordinates = get_candidate_coordinates(conn, candidates)

        candidates = candidates.merge(
            coordinates,
            on=["GridID", "CandidateLineID"],
            how="left"
        )

        missing_coordinates = candidates[
            candidates["Latitude"].isna() |
            candidates["Longitude"].isna()
        ]

        if not missing_coordinates.empty:
            print("\nCandidates missing coordinates:")
            print(
                missing_coordinates[
                    ["GridID", "CandidateLineID"]
                ].to_string(index=False)
            )

        candidates = candidates.dropna(
            subset=["Latitude", "Longitude"]
        ).copy()

        if candidates.empty:
            raise RuntimeError(
                "No candidate coordinates could be retrieved from grid_element."
            )

        feeder_lines = get_candidate_feeder_lines(
            conn,
            candidates["GridID"].unique().tolist()
        )

    finally:
        conn.close()

    print("\nCandidate locations:")
    print(
        candidates[
            [
                "GridID",
                "CandidateLineID",
                "FirmHostingMW",
                "Latitude",
                "Longitude"
            ]
        ].to_string(index=False)
    )

    selected = candidates[
        (candidates["GridID"] == SELECTED_GRID_ID) &
        (candidates["CandidateLineID"] == SELECTED_LINE_ID)
    ]

    if selected.empty:
        print(
            f"\nWARNING: final recommended site "
            f"{SELECTED_GRID_ID} | {SELECTED_LINE_ID} was not found."
        )
    else:
        row = selected.iloc[0]

        print("\nFINAL RECOMMENDATION")
        print(
            f"{row['GridID']} | "
            f"{row['CandidateLineID']} | "
            f"{row['FirmHostingMW']:.3f} MW | "
            f"{row['Latitude']:.6f}, "
            f"{row['Longitude']:.6f}"
        )

    plot_map(candidates, feeder_lines)

    print(f"\nMap saved to: {Path(OUTPUT_FILE).resolve()}")


if __name__ == "__main__":
    main()