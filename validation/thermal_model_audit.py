import os
import json
import math
import uuid
import decimal
from pathlib import Path
from collections import defaultdict

import numpy as np
import pandas as pd
import psycopg2
import networkx as nx
from dotenv import load_dotenv

THERMAL_RESULTS_FILE = "greensboro_final_thermal_hosting_results.xlsx"
INTERVAL_FILE = "greensboro_5min_candidate_hosting_2024.csv.gz"
OUTPUT_FILE = "greensboro_flag_resolution_audit.xlsx"

YEAR = 2024
BASE_POWER_FACTOR = 0.95
MAPPING_REVIEW_THRESHOLD_PCT = 95.0

SERIES_CLASSES = {"Line", "Transformer", "Switch", "Recloser", "Fuse", "Regulator"}

load_dotenv()
DB_HOST = os.getenv("EDM_HOST")
DB_USER = os.getenv("EDM_USER")
DB_PASSWORD = os.getenv("EDM_PASSWORD")
DB_NAME = os.getenv("EDM_DATABASE", "edm")


def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def connect_to_edm():
    missing = [name for name, value in {"EDM_HOST": DB_HOST, "EDM_USER": DB_USER, "EDM_PASSWORD": DB_PASSWORD, "EDM_DATABASE": DB_NAME}.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME)


def query_dataframe(conn, query, params=None):
    with conn.cursor() as cursor:
        cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return pd.DataFrame(rows, columns=columns)


def is_missing(value):
    if value is None:
        return True
    try:
        result = pd.isna(value)
        if isinstance(result, (bool, np.bool_)):
            return bool(result)
    except Exception:
        pass
    return False


def safe_float(value):
    if is_missing(value):
        return np.nan
    try:
        if isinstance(value, str):
            value = value.replace(",", "").strip()
        return float(value)
    except Exception:
        return np.nan


def truthy(value):
    if is_missing(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def parse_meta(value):
    if value is None:
        return {}
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            pass
    return {}


def classify_element(element_type, is_switchable=False):
    text = str(element_type).lower()
    if "breaker" in text:
        return "Breaker"
    if "transformer" in text:
        return "Transformer"
    if "recloser" in text:
        return "Recloser"
    if "fuse" in text:
        return "Fuse"
    if "regulator" in text:
        return "Regulator"
    if "line" in text:
        return "Line"
    if "switch" in text or truthy(is_switchable):
        return "Switch"
    if "meter" in text:
        return "Meter"
    return "Other"


def is_series_element(row):
    return classify_element(row.get("type"), row.get("is_switchable")) in SERIES_CLASSES


def normalized_edge(node_a, node_b):
    return tuple(sorted((str(node_a), str(node_b))))


def load_thermal_outputs():
    path = Path(THERMAL_RESULTS_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {THERMAL_RESULTS_FILE}. Run the final thermal model first.")

    candidate_summary = pd.read_excel(path, sheet_name="Candidate_Summary")
    feeder_diagnostics = pd.read_excel(path, sheet_name="Feeder_Diagnostics")
    meter_mapping = pd.read_excel(path, sheet_name="Meter_Mapping")
    path_constraints = pd.read_excel(path, sheet_name="Path_Constraints")
    pf_sensitivity = pd.read_excel(path, sheet_name="PF_Sensitivity")

    for df in [candidate_summary, feeder_diagnostics, meter_mapping, path_constraints, pf_sensitivity]:
        if "GridID" in df.columns:
            df["GridID"] = df["GridID"].astype(str)

    if "Resolved" in meter_mapping.columns:
        meter_mapping["ResolvedBool"] = meter_mapping["Resolved"].map(truthy)
    else:
        meter_mapping["ResolvedBool"] = False

    return candidate_summary, feeder_diagnostics, meter_mapping, path_constraints, pf_sensitivity


def load_interval_output():
    path = Path(INTERVAL_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {INTERVAL_FILE}. Run the final thermal model first.")

    usecols = ["TimestampUTC", "CandidateKey", "GridID", "SubstationID", "CandidateLineID", "ExistingBreakerMW", "AvailableThermalHostingMW", "LimitingElementID"]
    df = pd.read_csv(path, usecols=usecols)
    df["TimestampUTC"] = pd.to_datetime(df["TimestampUTC"], utc=True, errors="coerce")
    df["GridID"] = df["GridID"].astype(str)
    return df.dropna(subset=["TimestampUTC"])


def get_grid_elements(conn, grid_ids):
    return query_dataframe(conn, """
        SELECT grid_id, grid_element_id, type, phases, is_producer, is_consumer, is_switchable, switch_is_open,
               terminal1_cn, terminal2_cn, upstream_grid_element_id, meta
        FROM grid_element
        WHERE grid_id = ANY(%s)
        ORDER BY grid_id, grid_element_id;
    """, (list(grid_ids),))


def identify_breaker(feeder_elements):
    breakers = feeder_elements[feeder_elements["type"].astype(str).str.contains("breaker", case=False, na=False)].copy()
    if breakers.empty:
        return None
    breakers["ProducerSort"] = breakers["is_producer"].fillna(False).astype(bool)
    return breakers.sort_values(["ProducerSort", "grid_element_id"], ascending=[False, True]).iloc[0]


def build_topology_graph(feeder_elements, breaker_id):
    graph = nx.Graph()
    edge_lookup = {}
    pair_records = defaultdict(list)
    skipped_open = 0
    skipped_missing_terminal = 0

    for _, row in feeder_elements.iterrows():
        element_id = str(row["grid_element_id"])

        if element_id == str(breaker_id) or not is_series_element(row):
            continue

        if truthy(row.get("is_switchable")) and truthy(row.get("switch_is_open")):
            skipped_open += 1
            continue

        terminal1 = row.get("terminal1_cn")
        terminal2 = row.get("terminal2_cn")

        if is_missing(terminal1) or is_missing(terminal2):
            skipped_missing_terminal += 1
            continue

        terminal1 = str(terminal1)
        terminal2 = str(terminal2)

        if terminal1 == terminal2:
            continue

        edge_key = normalized_edge(terminal1, terminal2)
        record = {
            "ElementID": element_id,
            "ElementType": row.get("type"),
            "ElementClass": classify_element(row.get("type"), row.get("is_switchable")),
            "IsSwitchable": truthy(row.get("is_switchable")),
            "SwitchIsOpen": truthy(row.get("switch_is_open")),
            "UpstreamGridElementID": None if is_missing(row.get("upstream_grid_element_id")) else str(row.get("upstream_grid_element_id"))
        }

        pair_records[edge_key].append(record)
        edge_lookup[element_id] = edge_key

    for edge_key, records in pair_records.items():
        node_a, node_b = edge_key
        graph.add_edge(node_a, node_b, element_ids=[record["ElementID"] for record in records], records=records)

    diagnostics = {
        "ParallelNodePairCount": int(sum(1 for records in pair_records.values() if len(records) > 1)),
        "SkippedOpenSwitches": skipped_open,
        "SkippedMissingTerminalElements": skipped_missing_terminal
    }

    return graph, edge_lookup, pair_records, diagnostics


def choose_root_node(graph, breaker_row):
    terminals = [str(value) for value in [breaker_row.get("terminal1_cn"), breaker_row.get("terminal2_cn")] if not is_missing(value)]
    component_sizes = {terminal: len(nx.node_connected_component(graph, terminal)) if terminal in graph else 0 for terminal in terminals}

    if not component_sizes:
        return None

    root = max(component_sizes, key=component_sizes.get)
    return root if component_sizes[root] > 0 else None


def choose_candidate_node(candidate_row, edge_lookup, paths, depths):
    candidate_line_id = str(candidate_row["CandidateLineID"])
    requested_node = candidate_row.get("CandidateNode")

    if not is_missing(requested_node) and str(requested_node) in paths:
        return str(requested_node), "CandidateNodeFromThermalOutput"

    if candidate_line_id not in edge_lookup:
        return None, "CandidateLineNotFound"

    node_a, node_b = edge_lookup[candidate_line_id]
    reachable = [node for node in [node_a, node_b] if node in paths]

    if not reachable:
        return None, "CandidateLineNotReachable"

    return max(reachable, key=lambda node: depths.get(node, -1)), "CandidateLineDownstreamEndpoint"


def cycle_records(graph, grid_id):
    cycles = nx.cycle_basis(graph)
    summary_rows = []
    edge_rows = []

    for cycle_number, cycle_nodes in enumerate(cycles, start=1):
        cycle_edges = []

        for index, node_a in enumerate(cycle_nodes):
            node_b = cycle_nodes[(index + 1) % len(cycle_nodes)]
            edge_key = normalized_edge(node_a, node_b)
            edge_data = graph.get_edge_data(node_a, node_b) or {}
            element_ids = edge_data.get("element_ids", [])
            cycle_edges.append(edge_key)

            for element_id in element_ids:
                record_match = next((record for record in edge_data.get("records", []) if record["ElementID"] == element_id), {})
                edge_rows.append({
                    "GridID": grid_id,
                    "CycleNumber": cycle_number,
                    "CycleLengthEdges": len(cycle_nodes),
                    "NodeA": edge_key[0],
                    "NodeB": edge_key[1],
                    "ElementID": element_id,
                    "ElementType": record_match.get("ElementType"),
                    "ElementClass": record_match.get("ElementClass"),
                    "IsSwitchable": record_match.get("IsSwitchable"),
                    "SwitchIsOpen": record_match.get("SwitchIsOpen"),
                    "UpstreamGridElementID": record_match.get("UpstreamGridElementID")
                })

        summary_rows.append({
            "GridID": grid_id,
            "CycleNumber": cycle_number,
            "CycleLengthEdges": len(cycle_nodes),
            "CycleNodeCount": len(set(cycle_nodes)),
            "CycleNodes": " | ".join(str(node) for node in cycle_nodes),
            "CycleEdges": " | ".join(f"{edge[0]}<->{edge[1]}" for edge in cycle_edges)
        })

    return pd.DataFrame(summary_rows), pd.DataFrame(edge_rows), cycles


def audit_candidate_topology(candidate_row, feeder_elements):
    grid_id = str(candidate_row["GridID"])
    breaker_row = identify_breaker(feeder_elements)

    if breaker_row is None:
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "NoBreaker"

    breaker_id = str(breaker_row["grid_element_id"])
    graph, edge_lookup, pair_records, graph_diagnostics = build_topology_graph(feeder_elements, breaker_id)
    root_node = choose_root_node(graph, breaker_row)

    if root_node is None:
        return None, pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), "NoRoot"

    connected_nodes = nx.node_connected_component(graph, root_node)
    connected_graph = graph.subgraph(connected_nodes).copy()
    paths = nx.single_source_shortest_path(connected_graph, root_node)
    depths = {node: len(path) - 1 for node, path in paths.items()}
    cycle_rank = connected_graph.number_of_edges() - connected_graph.number_of_nodes() + 1
    is_radial = cycle_rank == 0
    bridges = {normalized_edge(node_a, node_b) for node_a, node_b in nx.bridges(connected_graph)}
    cycle_summary, cycle_edges_df, cycles = cycle_records(connected_graph, grid_id)

    candidate_node, candidate_node_method = choose_candidate_node(candidate_row, edge_lookup, paths, depths)

    if candidate_node is None:
        return None, cycle_summary, cycle_edges_df, pd.DataFrame(), candidate_node_method

    node_path = paths[candidate_node]
    path_rows = []
    path_edge_keys = []

    for index in range(len(node_path) - 1):
        node_a = node_path[index]
        node_b = node_path[index + 1]
        edge_key = normalized_edge(node_a, node_b)
        edge_data = connected_graph.get_edge_data(node_a, node_b) or {}
        path_edge_keys.append(edge_key)

        for element_id in edge_data.get("element_ids", []):
            record_match = next((record for record in edge_data.get("records", []) if record["ElementID"] == element_id), {})
            path_rows.append({
                "GridID": grid_id,
                "CandidateLineID": candidate_row["CandidateLineID"],
                "PathSequence": index + 1,
                "NodeA": edge_key[0],
                "NodeB": edge_key[1],
                "ElementID": element_id,
                "ElementType": record_match.get("ElementType"),
                "ElementClass": record_match.get("ElementClass"),
                "IsBridge": edge_key in bridges,
                "IsInAnyCycle": edge_key not in bridges,
                "ParallelElementCount": len(edge_data.get("element_ids", []))
            })

    path_df = pd.DataFrame(path_rows)
    path_edge_set = set(path_edge_keys)
    cycle_edge_set = set()

    for cycle_nodes in cycles:
        for index, node_a in enumerate(cycle_nodes):
            node_b = cycle_nodes[(index + 1) % len(cycle_nodes)]
            cycle_edge_set.add(normalized_edge(node_a, node_b))

    path_cycle_intersection = path_edge_set.intersection(cycle_edge_set)
    candidate_edge = edge_lookup.get(str(candidate_row["CandidateLineID"]))
    candidate_line_is_bridge = candidate_edge in bridges if candidate_edge else False
    candidate_line_in_cycle = candidate_edge in cycle_edge_set if candidate_edge else False
    all_path_edges_bridges = all(edge in bridges for edge in path_edge_set) if path_edge_set else False

    if is_radial:
        resolution = "CLEAR_RADIAL"
        materiality = "No mesh ambiguity remains after open-switch removal."
    elif not path_cycle_intersection and all_path_edges_bridges:
        resolution = "CLEAR_CYCLES_OFF_CANDIDATE_PATH"
        materiality = "Cycles exist elsewhere on the feeder, but every candidate-path edge is a bridge and none lies in a cycle."
    elif candidate_line_is_bridge and not candidate_line_in_cycle and not path_cycle_intersection:
        resolution = "CLEAR_CANDIDATE_PATH_UNIQUE"
        materiality = "The candidate connection path is unique even though another portion of the feeder is meshed."
    else:
        resolution = "REVIEW_MESH_AFFECTS_CANDIDATE_PATH"
        materiality = "At least one candidate-path edge participates in a cycle, so exact power sharing cannot be resolved without impedance or an operational switching state."

    result = {
        "GridID": grid_id,
        "CandidateLineID": candidate_row["CandidateLineID"],
        "CandidateNode": candidate_node,
        "CandidateNodeMethod": candidate_node_method,
        "BreakerID": breaker_id,
        "RootNode": root_node,
        "ConnectedNodes": connected_graph.number_of_nodes(),
        "ConnectedEdges": connected_graph.number_of_edges(),
        "CycleRank": cycle_rank,
        "CycleCount": len(cycles),
        "FeederIsRadial": is_radial,
        "CandidatePathEdgeCount": len(path_edge_set),
        "CandidatePathEdgesInCycles": len(path_cycle_intersection),
        "AllCandidatePathEdgesAreBridges": all_path_edges_bridges,
        "CandidateLineIsBridge": candidate_line_is_bridge,
        "CandidateLineIsInCycle": candidate_line_in_cycle,
        "ParallelNodePairCount": graph_diagnostics["ParallelNodePairCount"],
        "TopologyResolution": resolution,
        "TopologyMateriality": materiality
    }

    return result, cycle_summary, cycle_edges_df, path_df, None


def get_meter_hourly_totals(conn, grid_id, unresolved_meter_ids):
    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"
    unresolved_meter_ids = [str(value) for value in unresolved_meter_ids]

    query = """
        WITH meter_sources AS (
            SELECT ge.grid_element_id,
                   ds.grid_element_data_source_id
            FROM grid_element ge
            JOIN grid_element_data_source ds
              ON ds.grid_id = ge.grid_id
             AND ds.grid_element_id = ge.grid_element_id
            WHERE ge.grid_id = %s
              AND LOWER(ge.type) ~ 'meter'
              AND 'kWh' = ANY(ds.metrics)
        ),
        meter_hourly AS (
            SELECT ms.grid_element_id,
                   date_trunc('hour', td.timestamp) AS hour_utc,
                   SUM(td.value) AS meter_kwh
            FROM meter_sources ms
            JOIN LATERAL ts_data_source_select(
                ms.grid_element_data_source_id,
                'kWh',
                tstzrange(%s::timestamptz, %s::timestamptz, '[)')
            ) td ON TRUE
            GROUP BY ms.grid_element_id, date_trunc('hour', td.timestamp)
        )
        SELECT hour_utc,
               SUM(meter_kwh) AS total_meter_kwh,
               SUM(meter_kwh) FILTER (WHERE grid_element_id = ANY(%s)) AS unresolved_meter_kwh
        FROM meter_hourly
        GROUP BY hour_utc
        ORDER BY hour_utc;
    """

    df = query_dataframe(conn, query, (grid_id, start, end, unresolved_meter_ids))

    if df.empty:
        return df

    df["hour_utc"] = pd.to_datetime(df["hour_utc"], utc=True, errors="coerce")
    df["total_meter_kwh"] = pd.to_numeric(df["total_meter_kwh"], errors="coerce")
    df["unresolved_meter_kwh"] = pd.to_numeric(df["unresolved_meter_kwh"], errors="coerce").fillna(0.0)
    df["UnresolvedShare"] = np.where(df["total_meter_kwh"].abs() > 1e-9, df["unresolved_meter_kwh"] / df["total_meter_kwh"], 0.0)

    return df


def get_unresolved_meter_energy(conn, grid_id, unresolved_meter_ids):
    if not unresolved_meter_ids:
        return pd.DataFrame()

    start = f"{YEAR}-01-01 00:00:00+00"
    end = f"{YEAR + 1}-01-01 00:00:00+00"

    query = """
        WITH selected_sources AS (
            SELECT ge.grid_element_id,
                   ds.grid_element_data_source_id
            FROM grid_element ge
            JOIN grid_element_data_source ds
              ON ds.grid_id = ge.grid_id
             AND ds.grid_element_id = ge.grid_element_id
            WHERE ge.grid_id = %s
              AND ge.grid_element_id = ANY(%s)
              AND 'kWh' = ANY(ds.metrics)
        )
        SELECT ss.grid_element_id AS "MeterID",
               SUM(td.value) / 1000.0 AS "AnnualEnergyMWh",
               COUNT(*) AS "RawObservationCount"
        FROM selected_sources ss
        JOIN LATERAL ts_data_source_select(
            ss.grid_element_data_source_id,
            'kWh',
            tstzrange(%s::timestamptz, %s::timestamptz, '[)')
        ) td ON TRUE
        GROUP BY ss.grid_element_id
        ORDER BY "AnnualEnergyMWh" DESC;
    """

    df = query_dataframe(conn, query, (grid_id, [str(value) for value in unresolved_meter_ids], start, end))
    return df


def mapping_materiality_audit(conn, candidate_row, meter_mapping, path_constraints, interval_df):
    grid_id = str(candidate_row["GridID"])
    mapping = meter_mapping[meter_mapping["GridID"] == grid_id].copy()
    unresolved = mapping[mapping["ResolvedBool"] == False].copy()
    unresolved_ids = unresolved["MeterID"].astype(str).tolist()

    mapping_pct = safe_float(candidate_row.get("MeterTopologyMappingPct"))
    result = {
        "GridID": grid_id,
        "CandidateLineID": candidate_row["CandidateLineID"],
        "MeterTopologyMappingPct": mapping_pct,
        "MeterCountInMappingSheet": len(mapping),
        "UnresolvedMeterCount": len(unresolved_ids)
    }

    if not unresolved_ids:
        result.update({
            "UnresolvedAnnualEnergyMWh": 0.0,
            "TotalMeterAnnualEnergyMWh": np.nan,
            "UnresolvedAnnualEnergySharePct": 0.0,
            "PeakEstimatedUnresolved5MinMW": 0.0,
            "NonBreakerMinPathHeadroomMW": np.nan,
            "FirmHostingMW": candidate_row["FirmHostingMW"],
            "ConservativeNonBreakerHeadroomAfterUnresolvedPeakMW": np.nan,
            "BreakEvenUnresolvedMultiplier": np.inf,
            "MappingMaterialityStatus": "CLEAR_NO_UNRESOLVED_METERS",
            "MappingMaterialityExplanation": "Every modeled meter is topologically resolved."
        })
        return result, unresolved, pd.DataFrame()

    hourly = get_meter_hourly_totals(conn, grid_id, unresolved_ids)

    if hourly.empty:
        result.update({
            "MappingMaterialityStatus": "REVIEW_UNRESOLVED_ENERGY_QUERY_EMPTY",
            "MappingMaterialityExplanation": "Unresolved meters exist but their time-series energy could not be quantified."
        })
        return result, unresolved, pd.DataFrame()

    total_energy_mwh = hourly["total_meter_kwh"].sum() / 1000.0
    unresolved_energy_mwh = hourly["unresolved_meter_kwh"].sum() / 1000.0
    unresolved_energy_share_pct = unresolved_energy_mwh / total_energy_mwh * 100.0 if total_energy_mwh != 0 else np.nan

    candidate_intervals = interval_df[interval_df["GridID"] == grid_id].copy()
    candidate_intervals["HourUTC"] = candidate_intervals["TimestampUTC"].dt.floor("h")
    hourly_share = hourly.set_index("hour_utc")["UnresolvedShare"]
    candidate_intervals["UnresolvedShare"] = candidate_intervals["HourUTC"].map(hourly_share).fillna(0.0)
    candidate_intervals["EstimatedUnresolved5MinMW"] = candidate_intervals["ExistingBreakerMW"].abs() * candidate_intervals["UnresolvedShare"].abs()
    peak_estimated_unresolved_5min_mw = float(candidate_intervals["EstimatedUnresolved5MinMW"].max()) if not candidate_intervals.empty else np.nan

    constraints = path_constraints[
        (path_constraints["GridID"] == grid_id) &
        (path_constraints["CandidateLineID"].astype(str) == str(candidate_row["CandidateLineID"])) &
        (pd.to_numeric(path_constraints["PowerFactor"], errors="coerce").sub(BASE_POWER_FACTOR).abs() < 1e-9)
    ].copy()

    non_breaker = constraints[constraints["ElementClass"].astype(str) != "Breaker"].copy()
    non_breaker_min_headroom = pd.to_numeric(non_breaker["MinimumIncrementalHeadroomMW"], errors="coerce").min() if not non_breaker.empty else np.nan
    firm_hosting = safe_float(candidate_row["FirmHostingMW"])
    conservative_non_breaker = non_breaker_min_headroom - peak_estimated_unresolved_5min_mw if pd.notna(non_breaker_min_headroom) and pd.notna(peak_estimated_unresolved_5min_mw) else np.nan

    if pd.notna(peak_estimated_unresolved_5min_mw) and peak_estimated_unresolved_5min_mw > 0 and pd.notna(non_breaker_min_headroom):
        break_even_multiplier = max((non_breaker_min_headroom - firm_hosting) / peak_estimated_unresolved_5min_mw, 0.0)
    else:
        break_even_multiplier = np.inf

    limiter = str(candidate_row.get("MostFrequentLimitingElementID"))
    breaker_limited = limiter.lower().startswith("br_")

    if mapping_pct >= MAPPING_REVIEW_THRESHOLD_PCT:
        status = "CLEAR_MAPPING_THRESHOLD"
        explanation = f"Topology mapping is at or above the {MAPPING_REVIEW_THRESHOLD_PCT:.0f}% review threshold."
    elif breaker_limited and pd.notna(conservative_non_breaker) and conservative_non_breaker >= firm_hosting:
        status = "CLEAR_FOR_CURRENT_CANDIDATE_USING_MODELED_5MIN_UNRESOLVED_SHARE"
        explanation = "Even after conservatively placing the largest modeled five-minute unresolved-meter contribution onto the non-breaker candidate path, the breaker remains the binding firm constraint."
    else:
        status = "REVIEW_MAPPING_COULD_CHANGE_PATH_LIMIT"
        explanation = "The unresolved-meter contribution is large enough that candidate-path loading could change the binding constraint."

    result.update({
        "UnresolvedAnnualEnergyMWh": unresolved_energy_mwh,
        "TotalMeterAnnualEnergyMWh": total_energy_mwh,
        "UnresolvedAnnualEnergySharePct": unresolved_energy_share_pct,
        "PeakEstimatedUnresolved5MinMW": peak_estimated_unresolved_5min_mw,
        "NonBreakerMinPathHeadroomMW": non_breaker_min_headroom,
        "FirmHostingMW": firm_hosting,
        "ConservativeNonBreakerHeadroomAfterUnresolvedPeakMW": conservative_non_breaker,
        "BreakEvenUnresolvedMultiplier": break_even_multiplier,
        "BreakerLimited": breaker_limited,
        "MappingMaterialityStatus": status,
        "MappingMaterialityExplanation": explanation
    })

    unresolved_detail = get_unresolved_meter_energy(conn, grid_id, unresolved_ids)
    if not unresolved_detail.empty:
        unresolved_detail.insert(0, "GridID", grid_id)

    return result, unresolved, unresolved_detail


def summarize_pf_sensitivity(pf_sensitivity):
    df = pf_sensitivity.copy()
    df["PowerFactor"] = pd.to_numeric(df["PowerFactor"], errors="coerce")
    df["FirmHostingMW"] = pd.to_numeric(df.get("FirmHostingMW"), errors="coerce")
    df = df[df["Status"].astype(str).str.upper().eq("READY") & df["FirmHostingMW"].notna()].copy()

    if df.empty:
        return pd.DataFrame()

    pf_values = sorted(df["PowerFactor"].dropna().unique())
    rank_frames = []

    for pf in pf_values:
        subset = df[np.isclose(df["PowerFactor"], pf)].copy()
        subset[f"Rank_PF_{pf:.2f}"] = subset["FirmHostingMW"].rank(method="min", ascending=False).astype(int)
        rank_frames.append(subset[["CandidateKey", f"Rank_PF_{pf:.2f}"]])

    ranks = rank_frames[0]
    for frame in rank_frames[1:]:
        ranks = ranks.merge(frame, on="CandidateKey", how="outer")

    rows = []

    for candidate_key, group in df.groupby("CandidateKey"):
        group = group.sort_values("PowerFactor")
        row = {
            "CandidateKey": candidate_key,
            "GridID": group["GridID"].iloc[0],
            "SubstationID": group["SubstationID"].iloc[0] if "SubstationID" in group.columns else None,
            "CandidateLineID": group["CandidateLineID"].iloc[0],
            "MinimumTestedPF": group["PowerFactor"].min(),
            "MaximumTestedPF": group["PowerFactor"].max(),
            "FirmHostingAtMinimumPFMW": group.loc[group["PowerFactor"].idxmin(), "FirmHostingMW"],
            "FirmHostingAtBasePFMW": group.iloc[(group["PowerFactor"] - BASE_POWER_FACTOR).abs().argmin()]["FirmHostingMW"],
            "FirmHostingAtMaximumPFMW": group.loc[group["PowerFactor"].idxmax(), "FirmHostingMW"],
            "FirmHostingRangeAcrossPFMW": group["FirmHostingMW"].max() - group["FirmHostingMW"].min()
        }
        rows.append(row)

    summary = pd.DataFrame(rows).merge(ranks, on="CandidateKey", how="left")
    rank_columns = [column for column in summary.columns if column.startswith("Rank_PF_")]

    if rank_columns:
        summary["MaxRankShiftAcrossPF"] = summary[rank_columns].max(axis=1) - summary[rank_columns].min(axis=1)
        summary["PFRankingStatus"] = np.where(summary["MaxRankShiftAcrossPF"] == 0, "RANK_STABLE", "RANK_CHANGES")
    else:
        summary["MaxRankShiftAcrossPF"] = np.nan
        summary["PFRankingStatus"] = "NO_RANK_DATA"

    summary["FirmHostingPctRangeVsBase"] = np.where(
        summary["FirmHostingAtBasePFMW"].abs() > 1e-9,
        summary["FirmHostingRangeAcrossPFMW"] / summary["FirmHostingAtBasePFMW"].abs() * 100.0,
        np.nan
    )

    return summary


def build_milp_readiness(candidate_summary, topology_summary, mapping_summary, pf_summary):
    result = candidate_summary.copy()

    keep_topology = [
        "GridID", "CandidateLineID", "TopologyResolution", "TopologyMateriality", "CycleRank",
        "CandidatePathEdgesInCycles", "AllCandidatePathEdgesAreBridges", "CandidateLineIsBridge"
    ]
    result = result.merge(topology_summary[keep_topology], on=["GridID", "CandidateLineID"], how="left")

    keep_mapping = [
        "GridID", "CandidateLineID", "UnresolvedMeterCount", "UnresolvedAnnualEnergySharePct",
        "PeakEstimatedUnresolved5MinMW", "MappingMaterialityStatus", "MappingMaterialityExplanation"
    ]
    result = result.merge(mapping_summary[keep_mapping], on=["GridID", "CandidateLineID"], how="left")

    if not pf_summary.empty:
        keep_pf = [
            "CandidateKey", "FirmHostingAtMinimumPFMW", "FirmHostingAtBasePFMW", "FirmHostingAtMaximumPFMW",
            "FirmHostingRangeAcrossPFMW", "MaxRankShiftAcrossPF", "PFRankingStatus"
        ]
        result = result.merge(pf_summary[keep_pf], on="CandidateKey", how="left")

    result["TopologyClearedForBaseMILP"] = result["TopologyResolution"].astype(str).str.startswith("CLEAR_")
    result["MappingClearedForBaseMILP"] = result["MappingMaterialityStatus"].astype(str).str.startswith("CLEAR_")
    result["NoBaselineCandidatePathExceedance"] = pd.to_numeric(result["BaselinePathRatingExceedancePct"], errors="coerce").fillna(0.0).eq(0.0)
    result["ThermalCandidateModelCompleted"] = result["FirmHostingMW"].notna()

    result["ReadyForFeederBoundMILP"] = (
        result["TopologyClearedForBaseMILP"] &
        result["MappingClearedForBaseMILP"] &
        result["NoBaselineCandidatePathExceedance"] &
        result["ThermalCandidateModelCompleted"]
    )

    result["ReadyForFinalSubstationConstrainedMILP"] = False
    result["RemainingFinalMILPBlocker"] = "Explicit substation capacity not yet available"

    def readiness_note(row):
        blockers = []
        if not row["TopologyClearedForBaseMILP"]:
            blockers.append("topology")
        if not row["MappingClearedForBaseMILP"]:
            blockers.append("meter mapping")
        if not row["NoBaselineCandidatePathExceedance"]:
            blockers.append("baseline path exceedance")
        if not row["ThermalCandidateModelCompleted"]:
            blockers.append("thermal model")
        if blockers:
            return "Resolve before base MILP: " + ", ".join(blockers)
        return "Ready for feeder-bound MILP; final portfolio still needs substation capacity."

    result["MILPReadinessNote"] = result.apply(readiness_note, axis=1)

    result["SubstationCandidateCount"] = result.groupby("SubstationID")["CandidateKey"].transform("count")
    result["SubstationSumFirmHostingMW_UpperBoundOnly"] = result.groupby("SubstationID")["FirmHostingMW"].transform("sum")

    return result


def excel_safe_value(value):
    if value is None:
        return None
    if isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, decimal.Decimal):
        return float(value)
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, (dict, list, tuple, set)):
        return json.dumps(value, default=str)
    return str(value)


def make_excel_safe(df):
    if df is None:
        return pd.DataFrame()

    df = df.copy()

    for column in df.columns:
        if isinstance(df[column].dtype, pd.DatetimeTZDtype):
            df[column] = df[column].dt.tz_localize(None)
        elif pd.api.types.is_datetime64_any_dtype(df[column]):
            try:
                df[column] = pd.to_datetime(df[column]).dt.tz_localize(None)
            except Exception:
                pass
        elif df[column].dtype == "object":
            df[column] = df[column].map(excel_safe_value)

    return df


def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return

    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_format = workbook.add_format({"bold": True, "border": 1, "align": "center", "valign": "vcenter"})
    decimal_format = workbook.add_format({"num_format": "0.000"})
    integer_format = workbook.add_format({"num_format": "0"})

    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)
        values = dataframe[column_name].tolist() if not dataframe.empty else []
        max_length = max((len(str(value)) for value in values if value is not None), default=0)
        width = min(max(max_length, len(str(column_name))) + 2, 52)

        if pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, decimal_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


def assumptions_table():
    return pd.DataFrame([
        {"AuditArea": "Topology cycles", "Rule": "A feeder-level cycle is not automatically material. It is cleared for central siting when the candidate path does not intersect any cycle and all candidate-path edges are graph bridges."},
        {"AuditArea": "Topology cycles", "Rule": "If a candidate-path edge participates in a cycle, exact flow sharing is not inferred without impedance or a known operational switching state."},
        {"AuditArea": "Unmapped meters", "Rule": "Unresolved meters are quantified by energy and by their estimated five-minute feeder contribution using the same hourly spatial-share assumption as the thermal model."},
        {"AuditArea": "Unmapped meters", "Rule": "For a breaker-limited candidate, the mapping flag can be cleared for the current central-site analysis if placing the largest estimated unresolved five-minute contribution onto the non-breaker path still leaves the breaker as the firm constraint."},
        {"AuditArea": "Power factor", "Rule": "The audit reviews the already-computed PF 0.90/0.95/1.00 results for MW sensitivity and rank stability rather than inventing another PF assumption."},
        {"AuditArea": "MILP readiness", "Rule": "A candidate is ready for the first feeder-bound MILP when topology and mapping flags are materially cleared, the thermal model completed, and no baseline rating exceedance exists on the candidate path."},
        {"AuditArea": "Final MILP readiness", "Rule": "No candidate is marked ready for the final shared-substation portfolio MILP until explicit substation capacity is available. Feeder-level sums by substation are upper bounds only."}
    ])


def main():
    heading("GREENSBORO FLAG-RESOLUTION AND MILP-READINESS AUDIT")

    candidate_summary, feeder_diagnostics, meter_mapping, path_constraints, pf_sensitivity = load_thermal_outputs()
    interval_df = load_interval_output()

    candidate_summary["GridID"] = candidate_summary["GridID"].astype(str)
    target_grid_ids = candidate_summary["GridID"].unique().tolist()

    print(f"Candidates loaded: {len(candidate_summary):,}")
    print(f"Feeders represented: {len(target_grid_ids):,}")
    print("This audit resolves only topology, meter-mapping materiality, and PF sensitivity before the first MILP.")

    conn = connect_to_edm()

    topology_rows = []
    cycle_summary_frames = []
    cycle_edge_frames = []
    candidate_path_frames = []
    mapping_rows = []
    unresolved_mapping_frames = []
    unresolved_energy_frames = []
    error_rows = []

    try:
        all_elements = get_grid_elements(conn, target_grid_ids)

        heading("1. TOPOLOGY FLAG RESOLUTION")

        for _, candidate_row in candidate_summary.iterrows():
            grid_id = str(candidate_row["GridID"])
            feeder_elements = all_elements[all_elements["grid_id"].astype(str) == grid_id].copy()

            print(f"\n{grid_id} | {candidate_row['CandidateLineID']}")

            try:
                result, cycle_summary_df, cycle_edges_df, candidate_path_df, error = audit_candidate_topology(candidate_row, feeder_elements)

                if error:
                    print(f"  Topology audit error: {error}")
                    error_rows.append({"GridID": grid_id, "CandidateLineID": candidate_row["CandidateLineID"], "Stage": "Topology", "Error": error})
                    continue

                topology_rows.append(result)

                if not cycle_summary_df.empty:
                    cycle_summary_frames.append(cycle_summary_df)

                if not cycle_edges_df.empty:
                    cycle_edge_frames.append(cycle_edges_df)

                if not candidate_path_df.empty:
                    candidate_path_frames.append(candidate_path_df)

                print(f"  Cycle rank: {result['CycleRank']}")
                print(f"  Candidate-path edges in cycles: {result['CandidatePathEdgesInCycles']}")
                print(f"  All candidate-path edges are bridges: {result['AllCandidatePathEdgesAreBridges']}")
                print(f"  Resolution: {result['TopologyResolution']}")

            except Exception as exc:
                print(f"  Topology audit failed: {exc}")
                error_rows.append({"GridID": grid_id, "CandidateLineID": candidate_row["CandidateLineID"], "Stage": "Topology", "Error": str(exc)})

        topology_summary = pd.DataFrame(topology_rows)
        cycle_summary = pd.concat(cycle_summary_frames, ignore_index=True) if cycle_summary_frames else pd.DataFrame()
        cycle_edges = pd.concat(cycle_edge_frames, ignore_index=True) if cycle_edge_frames else pd.DataFrame()
        candidate_paths = pd.concat(candidate_path_frames, ignore_index=True) if candidate_path_frames else pd.DataFrame()

        heading("2. METER-MAPPING MATERIALITY")

        for _, candidate_row in candidate_summary.iterrows():
            grid_id = str(candidate_row["GridID"])

            print(f"\n{grid_id} | mapping {safe_float(candidate_row.get('MeterTopologyMappingPct')):.2f}%")

            try:
                result, unresolved_mapping, unresolved_energy = mapping_materiality_audit(conn, candidate_row, meter_mapping, path_constraints, interval_df)
                mapping_rows.append(result)

                if not unresolved_mapping.empty:
                    unresolved_mapping = unresolved_mapping.copy()
                    unresolved_mapping["CandidateLineID"] = candidate_row["CandidateLineID"]
                    unresolved_mapping_frames.append(unresolved_mapping)

                if not unresolved_energy.empty:
                    unresolved_energy["CandidateLineID"] = candidate_row["CandidateLineID"]
                    unresolved_energy_frames.append(unresolved_energy)

                print(f"  Unresolved meters: {result['UnresolvedMeterCount']}")
                if "UnresolvedAnnualEnergySharePct" in result and pd.notna(result["UnresolvedAnnualEnergySharePct"]):
                    print(f"  Unresolved annual energy share: {result['UnresolvedAnnualEnergySharePct']:.3f}%")
                if "PeakEstimatedUnresolved5MinMW" in result and pd.notna(result["PeakEstimatedUnresolved5MinMW"]):
                    print(f"  Peak modeled unresolved five-minute contribution: {result['PeakEstimatedUnresolved5MinMW']:.4f} MW")
                print(f"  Resolution: {result['MappingMaterialityStatus']}")

            except Exception as exc:
                print(f"  Mapping audit failed: {exc}")
                error_rows.append({"GridID": grid_id, "CandidateLineID": candidate_row["CandidateLineID"], "Stage": "Mapping", "Error": str(exc)})

        mapping_summary = pd.DataFrame(mapping_rows)
        unresolved_mapping_detail = pd.concat(unresolved_mapping_frames, ignore_index=True) if unresolved_mapping_frames else pd.DataFrame()
        unresolved_energy_detail = pd.concat(unresolved_energy_frames, ignore_index=True) if unresolved_energy_frames else pd.DataFrame()

        heading("3. POWER-FACTOR SENSITIVITY")
        pf_summary = summarize_pf_sensitivity(pf_sensitivity)

        if pf_summary.empty:
            print("No usable PF sensitivity rows were found.")
        else:
            display_columns = ["GridID", "CandidateLineID", "FirmHostingAtMinimumPFMW", "FirmHostingAtBasePFMW", "FirmHostingAtMaximumPFMW", "FirmHostingRangeAcrossPFMW", "MaxRankShiftAcrossPF", "PFRankingStatus"]
            print(pf_summary[display_columns].sort_values("FirmHostingAtBasePFMW", ascending=False).to_string(index=False))

        heading("4. MILP READINESS")
        milp_readiness = build_milp_readiness(candidate_summary, topology_summary, mapping_summary, pf_summary)
        display_columns = ["GridID", "CandidateLineID", "FirmHostingMW", "TopologyResolution", "MappingMaterialityStatus", "ReadyForFeederBoundMILP", "MILPReadinessNote"]
        print(milp_readiness[display_columns].to_string(index=False))

        ready_count = int(milp_readiness["ReadyForFeederBoundMILP"].sum())
        print(f"\nCandidates ready for the first feeder-bound MILP: {ready_count}/{len(milp_readiness)}")
        print("Final shared-substation portfolio capacity remains blocked only by explicit substation capacity values.")

        substation_upper_bounds = milp_readiness.groupby("SubstationID", as_index=False).agg(
            CandidateCount=("CandidateKey", "count"),
            SumIndividualFirmHostingMW_UpperBoundOnly=("FirmHostingMW", "sum"),
            AllCandidatesReadyForFeederBoundMILP=("ReadyForFeederBoundMILP", "all")
        ).sort_values("SumIndividualFirmHostingMW_UpperBoundOnly", ascending=False)

        workbook_frames = {
            "MILP_Readiness": milp_readiness,
            "Topology_Summary": topology_summary,
            "Cycle_Summary": cycle_summary,
            "Cycle_Edges": cycle_edges,
            "Candidate_Path_Bridges": candidate_paths,
            "Mapping_Summary": mapping_summary,
            "Unresolved_Mapping": unresolved_mapping_detail,
            "Unresolved_Meter_Energy": unresolved_energy_detail,
            "PF_Sensitivity_Review": pf_summary,
            "Substation_Upper_Bounds": substation_upper_bounds,
            "Errors": pd.DataFrame(error_rows),
            "Audit_Assumptions": assumptions_table()
        }

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe_frame = make_excel_safe(frame)
                safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
                format_excel(writer, sheet_name, safe_frame)

        print(f"\nAudit workbook saved to: {Path(OUTPUT_FILE).resolve()}")

        heading("NEXT STEP")
        if ready_count == len(milp_readiness):
            print("All current candidates are materially cleared for the first feeder-bound MILP.")
            print("Next script: build the base MILP using FirmHostingMW as the feeder/site ceiling, site-selection variables, optional site-count constraints, and explicit shared-substation capacity parameters when available.")
        else:
            unresolved = milp_readiness[milp_readiness["ReadyForFeederBoundMILP"] == False]
            print("Some candidates still need targeted resolution before being admitted to the base MILP:")
            print(unresolved[["GridID", "CandidateLineID", "MILPReadinessNote"]].to_string(index=False))
            print("Candidates already marked ready can still be used in the first MILP while the remaining candidates are excluded or resolved.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
