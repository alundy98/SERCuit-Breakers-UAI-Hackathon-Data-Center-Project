import os
import json
import re
from pathlib import Path
from collections import defaultdict, deque

import numpy as np
import pandas as pd
import psycopg2
import networkx as nx
from dotenv import load_dotenv

SUBSTATION_LOAD_FILE = "greensboro_substation_load_analysis.xlsx"
SUBSTATION_SUMMARY_SHEET = "Substation_Summary"
CAPACITY_INPUT_FILE = "greensboro_substation_capacity_inputs.xlsx"
CAPACITY_INPUT_SHEET = "Substation_Capacity"
AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
AUDIT_SHEET = "MILP_Readiness"

OUTPUT_FILE = "greensboro_substation_capacity_discovery_audit.xlsx"

MAX_GRAPH_HOPS = 4
SCAN_ALL_SUBSTATIONS = True

CAPACITY_KEY_PATTERNS = [
    r"rating.*kva", r"kva.*rating", r"rating.*mva", r"mva.*rating",
    r"capacity", r"nameplate", r"thermal", r"normal.*rating", r"emergency.*rating",
    r"continuous.*rating", r"rated.*power", r"power.*rating", r"ampacity",
    r"rating.*a", r"rated.*a"
]

TRANSFORMER_TYPE_PATTERN = re.compile(r"transformer", re.IGNORECASE)
BREAKER_TYPE_PATTERN = re.compile(r"breaker", re.IGNORECASE)
SUBSTATION_TYPE_PATTERN = re.compile(r"substation", re.IGNORECASE)
SOURCE_LIKE_PATTERN = re.compile(r"source|substation|transformer|breaker|regulator", re.IGNORECASE)

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


def safe_query_dataframe(conn, query, params=None):
    savepoint = "capacity_discovery_query"
    try:
        with conn.cursor() as cursor:
            cursor.execute(f"SAVEPOINT {savepoint}")
        df = query_dataframe(conn, query, params)
        with conn.cursor() as cursor:
            cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        return df, None
    except Exception as exc:
        try:
            with conn.cursor() as cursor:
                cursor.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
                cursor.execute(f"RELEASE SAVEPOINT {savepoint}")
        except Exception:
            conn.rollback()
        return pd.DataFrame(), str(exc)


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


def flatten_meta(data, prefix=""):
    output = {}
    if not isinstance(data, dict):
        return output

    for key, value in data.items():
        full_key = f"{prefix}.{key}" if prefix else str(key)

        if isinstance(value, dict):
            output.update(flatten_meta(value, full_key))
        elif isinstance(value, list):
            output[full_key] = json.dumps(value, default=str)
        else:
            output[full_key] = value

    return output


def capacity_like_key(key):
    text = str(key).lower()
    return any(re.search(pattern, text) for pattern in CAPACITY_KEY_PATTERNS)


def normalize_voltage_kv(value):
    value = safe_float(value)
    if np.isnan(value) or value <= 0:
        return np.nan
    return value / 1000.0 if value > 100 else value


def load_substation_scope():
    substations = set()
    current_candidate_substations = set()

    if Path(SUBSTATION_LOAD_FILE).exists():
        try:
            summary = pd.read_excel(SUBSTATION_LOAD_FILE, sheet_name=SUBSTATION_SUMMARY_SHEET)
            if "SubstationID" in summary.columns:
                substations.update(summary["SubstationID"].dropna().astype(str).tolist())
        except Exception:
            pass

    if Path(AUDIT_FILE).exists():
        try:
            audit = pd.read_excel(AUDIT_FILE, sheet_name=AUDIT_SHEET)
            if "SubstationID" in audit.columns:
                current_candidate_substations.update(audit["SubstationID"].dropna().astype(str).tolist())
                substations.update(current_candidate_substations)
        except Exception:
            pass

    if Path(CAPACITY_INPUT_FILE).exists():
        try:
            capacity = pd.read_excel(CAPACITY_INPUT_FILE, sheet_name=CAPACITY_INPUT_SHEET)
            if "SubstationID" in capacity.columns:
                substations.update(capacity["SubstationID"].dropna().astype(str).tolist())
        except Exception:
            pass

    return sorted(substations), current_candidate_substations


def get_breaker_substation_map(conn):
    return query_dataframe(conn, """
        SELECT DISTINCT
               b.grid_id AS "GridID",
               b.grid_element_id AS "BreakerID",
               b.meta->>'enclosure_id' AS "SubstationID",
               b.terminal1_cn AS "BreakerTerminal1",
               b.terminal2_cn AS "BreakerTerminal2",
               b.upstream_grid_element_id AS "BreakerUpstreamElementID",
               b.meta AS "BreakerMeta"
        FROM grid_element b
        WHERE b.grid_id ~ '^GSO_[0-9]+$'
          AND LOWER(b.type) ~ 'breaker'
          AND b.meta ? 'enclosure_id'
          AND NULLIF(TRIM(b.meta->>'enclosure_id'), '') IS NOT NULL
        ORDER BY b.meta->>'enclosure_id', b.grid_id, b.grid_element_id;
    """)


def get_substation_entities(conn, substation_ids):
    if not substation_ids:
        return pd.DataFrame()

    return query_dataframe(conn, """
        SELECT grid_id AS "GridID",
               grid_element_id AS "SubstationID",
               type AS "ElementType",
               phases AS "Phases",
               terminal1_cn AS "Terminal1",
               terminal2_cn AS "Terminal2",
               upstream_grid_element_id AS "UpstreamElementID",
               meta AS "Meta"
        FROM grid_element
        WHERE LOWER(type) = 'substation'
          AND grid_element_id = ANY(%s)
        ORDER BY grid_element_id, grid_id;
    """, (substation_ids,))


def get_elements_for_grids(conn, grid_ids):
    if not grid_ids:
        return pd.DataFrame()

    return query_dataframe(conn, """
        SELECT grid_id AS "GridID",
               grid_element_id AS "ElementID",
               type AS "ElementType",
               phases AS "Phases",
               is_producer AS "IsProducer",
               is_consumer AS "IsConsumer",
               is_switchable AS "IsSwitchable",
               switch_is_open AS "SwitchIsOpen",
               terminal1_cn AS "Terminal1",
               terminal2_cn AS "Terminal2",
               upstream_grid_element_id AS "UpstreamElementID",
               meta AS "Meta"
        FROM grid_element
        WHERE grid_id = ANY(%s)
        ORDER BY grid_id, grid_element_id;
    """, (grid_ids,))


def get_source_trace(conn, grid_id, breaker_id):
    df, error = safe_query_dataframe(conn, """
        SELECT *
        FROM grid_get_sources(%s, %s, false);
    """, (grid_id, breaker_id))

    if error:
        return pd.DataFrame(), error

    return df, None


def get_connected_trace(conn, grid_id, breaker_id):
    df, error = safe_query_dataframe(conn, """
        SELECT *
        FROM grid_get_connected(%s, %s);
    """, (grid_id, breaker_id))

    if error:
        return pd.DataFrame(), error

    return df, None


def extract_capacity_meta_rows(element_row, relationship, substation_id, grid_id, breaker_id=None, hop_distance=np.nan):
    rows = []
    flat = flatten_meta(parse_meta(element_row.get("Meta")))

    for key, value in flat.items():
        if not capacity_like_key(key):
            continue

        numeric_value = safe_float(value)

        rows.append({
            "SubstationID": substation_id,
            "GridID": grid_id,
            "BreakerID": breaker_id,
            "Relationship": relationship,
            "HopDistance": hop_distance,
            "ElementID": element_row.get("ElementID", element_row.get("SubstationID")),
            "ElementType": element_row.get("ElementType"),
            "MetaKey": key,
            "MetaValue": value,
            "NumericValue": numeric_value,
            "Phases": element_row.get("Phases"),
            "Terminal1": element_row.get("Terminal1"),
            "Terminal2": element_row.get("Terminal2"),
            "UpstreamElementID": element_row.get("UpstreamElementID")
        })

    return rows


def build_connectivity_graph(elements):
    graph = nx.Graph()
    edge_records = defaultdict(list)

    for _, row in elements.iterrows():
        terminal1 = row.get("Terminal1")
        terminal2 = row.get("Terminal2")

        if is_missing(terminal1) or is_missing(terminal2):
            continue

        terminal1 = str(terminal1)
        terminal2 = str(terminal2)

        if terminal1 == terminal2:
            continue

        if truthy(row.get("IsSwitchable")) and truthy(row.get("SwitchIsOpen")):
            continue

        graph.add_edge(terminal1, terminal2)

        key = tuple(sorted((terminal1, terminal2)))
        edge_records[key].append({
            "ElementID": str(row["ElementID"]),
            "ElementType": row.get("ElementType"),
            "Meta": row.get("Meta"),
            "Phases": row.get("Phases"),
            "Terminal1": terminal1,
            "Terminal2": terminal2,
            "UpstreamElementID": row.get("UpstreamElementID"),
            "IsProducer": row.get("IsProducer"),
            "IsConsumer": row.get("IsConsumer")
        })

    return graph, edge_records


def breaker_start_nodes(breaker_map_row):
    nodes = []

    for value in [breaker_map_row.get("BreakerTerminal1"), breaker_map_row.get("BreakerTerminal2")]:
        if not is_missing(value):
            nodes.append(str(value))

    return nodes


def graph_neighborhood_elements(graph, edge_records, start_nodes, max_hops):
    node_distance = {}

    queue = deque()

    for node in start_nodes:
        if node in graph and node not in node_distance:
            node_distance[node] = 0
            queue.append(node)

    while queue:
        node = queue.popleft()
        distance = node_distance[node]

        if distance >= max_hops:
            continue

        for neighbor in graph.neighbors(node):
            if neighbor not in node_distance:
                node_distance[neighbor] = distance + 1
                queue.append(neighbor)

    element_rows = []

    for (node_a, node_b), records in edge_records.items():
        distances = [node_distance[node] for node in [node_a, node_b] if node in node_distance]

        if not distances:
            continue

        hop_distance = min(distances)

        if hop_distance > max_hops:
            continue

        for record in records:
            element_rows.append({**record, "HopDistance": hop_distance})

    return pd.DataFrame(element_rows)


def relationship_rank(relationship):
    ordering = {
        "DIRECT_SUBSTATION_META": 1,
        "ENCLOSED_ASSET": 2,
        "EXPLICIT_UPSTREAM_ASSET": 3,
        "SOURCE_TRACE_ASSET": 4,
        "CONNECTED_TRACE_ASSET": 5,
        "GRAPH_NEIGHBOR_ASSET": 6,
        "GRID_RATING_ASSET": 7
    }
    return ordering.get(str(relationship), 99)


def evidence_strength(relationship, element_type, meta_key):
    relationship = str(relationship)
    element_type = str(element_type)
    meta_key = str(meta_key).lower()

    if relationship == "DIRECT_SUBSTATION_META":
        return "DIRECT"

    if relationship == "ENCLOSED_ASSET" and TRANSFORMER_TYPE_PATTERN.search(element_type):
        return "STRONG_ASSOCIATION"

    if relationship in {"EXPLICIT_UPSTREAM_ASSET", "SOURCE_TRACE_ASSET"} and TRANSFORMER_TYPE_PATTERN.search(element_type):
        return "MODERATE_ASSOCIATION"

    if "rating" in meta_key or "capacity" in meta_key or "nameplate" in meta_key:
        return "CONTEXT_ONLY"

    return "WEAK_CONTEXT"


def classify_capacity_candidate(meta_key, numeric_value):
    key = str(meta_key).lower()

    if pd.isna(numeric_value) or numeric_value <= 0:
        return "NON_NUMERIC_OR_NONPOSITIVE"

    if "mva" in key:
        return "MVA_LIKE"
    if "kva" in key:
        return "KVA_LIKE"
    if "capacity" in key or "nameplate" in key or "ratedpower" in key.replace("_", ""):
        return "POWER_CAPACITY_LIKE"
    if "rating_a" in key or "rated_a" in key or "ampacity" in key:
        return "CURRENT_LIKE"

    return "OTHER_RATING_LIKE"


def inferred_mva_from_metadata(meta_key, numeric_value):
    key = str(meta_key).lower()

    if pd.isna(numeric_value) or numeric_value <= 0:
        return np.nan

    if "mva" in key:
        return numeric_value

    if "kva" in key:
        return numeric_value / 1000.0

    return np.nan


def find_enclosed_assets(elements, substation_id):
    rows = []

    for _, row in elements.iterrows():
        meta = parse_meta(row.get("Meta"))
        enclosure_id = meta.get("enclosure_id")

        if enclosure_id is not None and str(enclosure_id) == str(substation_id):
            rows.append(row.to_dict())

    return pd.DataFrame(rows)


def find_explicit_upstream_assets(elements, breaker_row):
    upstream_id = breaker_row.get("BreakerUpstreamElementID")

    if is_missing(upstream_id):
        return pd.DataFrame()

    return elements[elements["ElementID"].astype(str) == str(upstream_id)].copy()


def normalize_trace_element_ids(trace_df):
    if trace_df.empty:
        return []

    candidate_columns = [
        column for column in trace_df.columns
        if str(column).lower() in {
            "grid_element_id", "element_id", "source_grid_element_id",
            "grid_element", "id"
        }
    ]

    ids = []

    for column in candidate_columns:
        ids.extend(trace_df[column].dropna().astype(str).tolist())

    return list(dict.fromkeys(ids))


def summarize_substation_evidence(substation_id, evidence_df, breaker_group, current_candidate_substations):
    subset = evidence_df[evidence_df["SubstationID"].astype(str) == str(substation_id)].copy()

    if subset.empty:
        return {
            "SubstationID": substation_id,
            "CurrentMILPCandidateSubstation": substation_id in current_candidate_substations,
            "MappedFeederCount": len(breaker_group),
            "EvidenceRowCount": 0,
            "DirectSubstationCapacityEvidence": False,
            "EnclosedTransformerCapacityEvidence": False,
            "SourceOrUpstreamTransformerEvidence": False,
            "BestEvidenceStrength": "NONE",
            "BestRelationship": "NONE",
            "BestElementID": None,
            "BestElementType": None,
            "BestMetaKey": None,
            "BestNumericValue": np.nan,
            "BestInferredMVA": np.nan,
            "CapacityDiscoveryStatus": "NO_DEFENSIBLE_CAPACITY_FOUND",
            "CapacityDiscoveryExplanation": "No capacity-like metadata was found in the scanned substation or associated equipment."
        }

    subset["RelationshipRank"] = subset["Relationship"].map(relationship_rank)
    strength_order = {"DIRECT": 1, "STRONG_ASSOCIATION": 2, "MODERATE_ASSOCIATION": 3, "CONTEXT_ONLY": 4, "WEAK_CONTEXT": 5}
    subset["StrengthRank"] = subset["EvidenceStrength"].map(strength_order).fillna(99)
    subset["NumericRank"] = np.where(subset["NumericValue"].notna() & (subset["NumericValue"] > 0), 0, 1)
    subset = subset.sort_values(["StrengthRank", "RelationshipRank", "NumericRank", "HopDistance"], na_position="last")

    best = subset.iloc[0]

    direct = bool((subset["EvidenceStrength"] == "DIRECT").any())
    enclosed_transformer = bool(((subset["Relationship"] == "ENCLOSED_ASSET") & subset["ElementType"].astype(str).str.contains("transformer", case=False, na=False)).any())
    source_transformer = bool((subset["Relationship"].isin(["EXPLICIT_UPSTREAM_ASSET", "SOURCE_TRACE_ASSET"])) & subset["ElementType"].astype(str).str.contains("transformer", case=False, na=False)).any()

    if direct and pd.notna(best["InferredMVA"]):
        status = "DIRECT_CAPACITY_EVIDENCE_FOUND"
        explanation = "A numeric MVA/kVA-like capacity field was found directly on the Substation entity. Review the metadata semantics before using it."
    elif direct:
        status = "DIRECT_RATING_METADATA_FOUND_REVIEW"
        explanation = "Capacity-like metadata exists directly on the Substation entity, but it does not resolve cleanly to a numeric MVA value."
    elif enclosed_transformer:
        status = "ENCLOSED_TRANSFORMER_EVIDENCE_FOUND_REVIEW"
        explanation = "A transformer enclosed by the substation has capacity-like metadata. Verify whether it represents the shared upstream station transformer before use."
    elif source_transformer:
        status = "SOURCE_TRANSFORMER_EVIDENCE_FOUND_REVIEW"
        explanation = "A transformer on an explicit/source-side relationship has capacity-like metadata. Verify topology and station-equipment semantics before use."
    else:
        status = "NO_DEFENSIBLE_CAPACITY_FOUND"
        explanation = "Only contextual or nearby rating-bearing equipment was found; no direct substation capacity should be inferred."

    return {
        "SubstationID": substation_id,
        "CurrentMILPCandidateSubstation": substation_id in current_candidate_substations,
        "MappedFeederCount": len(breaker_group),
        "EvidenceRowCount": len(subset),
        "DirectSubstationCapacityEvidence": direct,
        "EnclosedTransformerCapacityEvidence": enclosed_transformer,
        "SourceOrUpstreamTransformerEvidence": source_transformer,
        "BestEvidenceStrength": best["EvidenceStrength"],
        "BestRelationship": best["Relationship"],
        "BestElementID": best["ElementID"],
        "BestElementType": best["ElementType"],
        "BestMetaKey": best["MetaKey"],
        "BestNumericValue": best["NumericValue"],
        "BestInferredMVA": best["InferredMVA"],
        "CapacityDiscoveryStatus": status,
        "CapacityDiscoveryExplanation": explanation
    }


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
            df[column] = df[column].map(lambda value: json.dumps(value, default=str) if isinstance(value, (dict, list, tuple, set)) else value)

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
        {"Item": "Purpose", "Assumption": "Search the EDM for defensible substation-capacity evidence without manufacturing a capacity from unrelated feeder or breaker ratings."},
        {"Item": "Direct evidence", "Assumption": "Capacity-like metadata on the Substation entity is the strongest available evidence but still requires semantic review before use in the MILP."},
        {"Item": "Enclosed transformer evidence", "Assumption": "A transformer with meta.enclosure_id equal to the SubstationID is treated as strong association evidence, not automatically as the total station capacity."},
        {"Item": "Source/upstream transformer evidence", "Assumption": "A transformer explicitly upstream or returned by grid_get_sources is treated as moderate association evidence and must be verified before use."},
        {"Item": "Connected/nearby evidence", "Assumption": "Rating-bearing connected or nearby elements are context only and are never summed or promoted automatically to substation capacity."},
        {"Item": "Breaker ratings", "Assumption": "Breaker rating_kVA or current ratings are not summed to infer station transformer capacity."},
        {"Item": "Parallel/shared equipment", "Assumption": "If multiple transformers are found, the script does not assume series/parallel configuration or calculate combined capacity without explicit topology semantics."},
        {"Item": "Metadata key search", "Assumption": "Nested JSON metadata keys containing rating, capacity, nameplate, thermal, kVA, MVA, ampacity, normal, emergency, or similar terms are surfaced for review."},
        {"Item": "Graph neighborhood", "Assumption": f"Connectivity-node graph scanning is limited to {MAX_GRAPH_HOPS} hops from each mapped feeder breaker and is used only for evidence discovery."},
        {"Item": "Capacity inference", "Assumption": "Only explicit MVA/kVA-like metadata is converted to an inferred MVA display value. Current-only values are not converted to MVA without a defensible voltage/equipment interpretation."},
        {"Item": "MILP use", "Assumption": "No discovered value should be written into TotalCapacityMW automatically. Any candidate capacity must be manually reviewed and sourced before activating the shared-substation MILP constraint."}
    ])


def main():
    heading("GREENSBORO SUBSTATION CAPACITY DISCOVERY AUDIT")

    scoped_substations, current_candidate_substations = load_substation_scope()

    conn = connect_to_edm()

    evidence_rows = []
    source_trace_rows = []
    connected_trace_rows = []
    error_rows = []

    try:
        breaker_map = get_breaker_substation_map(conn)

        if breaker_map.empty:
            raise RuntimeError("No Greensboro breaker-to-substation mappings were found.")

        if not SCAN_ALL_SUBSTATIONS and scoped_substations:
            breaker_map = breaker_map[breaker_map["SubstationID"].astype(str).isin(scoped_substations)].copy()

        substation_ids = sorted(breaker_map["SubstationID"].dropna().astype(str).unique().tolist())
        grid_ids = sorted(breaker_map["GridID"].dropna().astype(str).unique().tolist())

        print(f"Mapped feeders in audit: {len(breaker_map):,}")
        print(f"Substations in audit: {len(substation_ids):,}")
        print(f"Current MILP candidate substations: {len(current_candidate_substations):,}")

        substation_entities = get_substation_entities(conn, substation_ids)
        all_elements = get_elements_for_grids(conn, grid_ids)

        if all_elements.empty:
            raise RuntimeError("No grid elements were returned for the mapped Greensboro feeder grids.")

        elements_by_grid = {grid_id: group.copy() for grid_id, group in all_elements.groupby("GridID")}

        heading("1. DIRECT SUBSTATION METADATA")

        for substation_id in substation_ids:
            rows = substation_entities[substation_entities["SubstationID"].astype(str) == substation_id]

            if rows.empty:
                continue

            for _, row in rows.iterrows():
                grid_id = str(row["GridID"])
                evidence_rows.extend(extract_capacity_meta_rows(row, "DIRECT_SUBSTATION_META", substation_id, grid_id))

        print(f"Direct capacity-like metadata rows found: {sum(1 for row in evidence_rows if row['Relationship'] == 'DIRECT_SUBSTATION_META'):,}")

        heading("2. ASSOCIATED EQUIPMENT")

        for substation_number, substation_id in enumerate(substation_ids, start=1):
            breaker_group = breaker_map[breaker_map["SubstationID"].astype(str) == substation_id].copy()
            print(f"\n[{substation_number}/{len(substation_ids)}] {substation_id} | feeders: {len(breaker_group)}")

            for _, breaker_row in breaker_group.iterrows():
                grid_id = str(breaker_row["GridID"])
                breaker_id = str(breaker_row["BreakerID"])
                elements = elements_by_grid.get(grid_id, pd.DataFrame())

                if elements.empty:
                    error_rows.append({
                        "SubstationID": substation_id,
                        "GridID": grid_id,
                        "BreakerID": breaker_id,
                        "Stage": "Elements",
                        "Error": "No elements loaded for feeder"
                    })
                    continue

                enclosed = find_enclosed_assets(elements, substation_id)

                for _, element_row in enclosed.iterrows():
                    evidence_rows.extend(extract_capacity_meta_rows(
                        element_row, "ENCLOSED_ASSET", substation_id, grid_id, breaker_id, hop_distance=0
                    ))

                explicit_upstream = find_explicit_upstream_assets(elements, breaker_row)

                for _, element_row in explicit_upstream.iterrows():
                    evidence_rows.extend(extract_capacity_meta_rows(
                        element_row, "EXPLICIT_UPSTREAM_ASSET", substation_id, grid_id, breaker_id, hop_distance=0
                    ))

                source_trace, source_error = get_source_trace(conn, grid_id, breaker_id)

                if source_error:
                    error_rows.append({
                        "SubstationID": substation_id,
                        "GridID": grid_id,
                        "BreakerID": breaker_id,
                        "Stage": "grid_get_sources",
                        "Error": source_error
                    })
                elif not source_trace.empty:
                    source_trace_copy = source_trace.copy()
                    source_trace_copy.insert(0, "BreakerID", breaker_id)
                    source_trace_copy.insert(0, "SubstationID", substation_id)
                    source_trace_copy.insert(0, "GridID_Audit", grid_id)
                    source_trace_rows.append(source_trace_copy)

                    source_ids = normalize_trace_element_ids(source_trace)

                    if source_ids:
                        source_assets = elements[elements["ElementID"].astype(str).isin(source_ids)].copy()

                        for _, element_row in source_assets.iterrows():
                            evidence_rows.extend(extract_capacity_meta_rows(
                                element_row, "SOURCE_TRACE_ASSET", substation_id, grid_id, breaker_id
                            ))

                connected_trace, connected_error = get_connected_trace(conn, grid_id, breaker_id)

                if connected_error:
                    error_rows.append({
                        "SubstationID": substation_id,
                        "GridID": grid_id,
                        "BreakerID": breaker_id,
                        "Stage": "grid_get_connected",
                        "Error": connected_error
                    })
                elif not connected_trace.empty:
                    connected_trace_copy = connected_trace.copy()
                    connected_trace_copy.insert(0, "BreakerID", breaker_id)
                    connected_trace_copy.insert(0, "SubstationID", substation_id)
                    connected_trace_copy.insert(0, "GridID_Audit", grid_id)
                    connected_trace_rows.append(connected_trace_copy)

                    connected_ids = normalize_trace_element_ids(connected_trace)

                    if connected_ids:
                        connected_assets = elements[elements["ElementID"].astype(str).isin(connected_ids)].copy()

                        for _, element_row in connected_assets.iterrows():
                            evidence_rows.extend(extract_capacity_meta_rows(
                                element_row, "CONNECTED_TRACE_ASSET", substation_id, grid_id, breaker_id
                            ))

                graph, edge_records = build_connectivity_graph(elements)
                neighborhood = graph_neighborhood_elements(graph, edge_records, breaker_start_nodes(breaker_row), MAX_GRAPH_HOPS)

                if not neighborhood.empty:
                    seen_ids = set()

                    for _, element_row in neighborhood.sort_values("HopDistance").iterrows():
                        element_id = str(element_row["ElementID"])

                        if element_id in seen_ids:
                            continue

                        seen_ids.add(element_id)

                        evidence_rows.extend(extract_capacity_meta_rows(
                            element_row, "GRAPH_NEIGHBOR_ASSET", substation_id, grid_id, breaker_id, hop_distance=element_row["HopDistance"]
                        ))

                rating_assets = []

                for _, element_row in elements.iterrows():
                    flat = flatten_meta(parse_meta(element_row.get("Meta")))

                    if any(capacity_like_key(key) for key in flat):
                        rating_assets.append(element_row)

                for element_row in rating_assets:
                    evidence_rows.extend(extract_capacity_meta_rows(
                        element_row, "GRID_RATING_ASSET", substation_id, grid_id, breaker_id
                    ))

        evidence_df = pd.DataFrame(evidence_rows)

        if evidence_df.empty:
            evidence_df = pd.DataFrame(columns=[
                "SubstationID", "GridID", "BreakerID", "Relationship", "HopDistance",
                "ElementID", "ElementType", "MetaKey", "MetaValue", "NumericValue",
                "Phases", "Terminal1", "Terminal2", "UpstreamElementID"
            ])

        evidence_df["EvidenceStrength"] = evidence_df.apply(
            lambda row: evidence_strength(row["Relationship"], row["ElementType"], row["MetaKey"]), axis=1
        )
        evidence_df["CapacityValueType"] = evidence_df.apply(
            lambda row: classify_capacity_candidate(row["MetaKey"], row["NumericValue"]), axis=1
        )
        evidence_df["InferredMVA"] = evidence_df.apply(
            lambda row: inferred_mva_from_metadata(row["MetaKey"], row["NumericValue"]), axis=1
        )
        evidence_df["RelationshipRank"] = evidence_df["Relationship"].map(relationship_rank)

        evidence_df = evidence_df.drop_duplicates(
            subset=["SubstationID", "GridID", "BreakerID", "Relationship", "ElementID", "MetaKey", "MetaValue"]
        ).sort_values(
            ["SubstationID", "RelationshipRank", "HopDistance", "ElementID", "MetaKey"],
            na_position="last"
        ).reset_index(drop=True)

        heading("3. SUBSTATION EVIDENCE SUMMARY")

        summary_rows = []

        for substation_id in substation_ids:
            breaker_group = breaker_map[breaker_map["SubstationID"].astype(str) == substation_id]
            summary_rows.append(summarize_substation_evidence(
                substation_id, evidence_df, breaker_group, current_candidate_substations
            ))

        summary_df = pd.DataFrame(summary_rows)

        current_candidate_summary = summary_df[summary_df["CurrentMILPCandidateSubstation"] == True].copy()
        current_candidate_summary = current_candidate_summary.sort_values("SubstationID")

        print("\nCurrent candidate substations:")
        if current_candidate_summary.empty:
            print("No current candidate substations were identified from prior outputs.")
        else:
            display_columns = [
                "SubstationID", "MappedFeederCount", "EvidenceRowCount", "BestEvidenceStrength",
                "BestRelationship", "BestElementID", "BestElementType", "BestMetaKey",
                "BestNumericValue", "BestInferredMVA", "CapacityDiscoveryStatus"
            ]
            print(current_candidate_summary[display_columns].to_string(index=False))

        direct_df = evidence_df[evidence_df["EvidenceStrength"] == "DIRECT"].copy()
        strong_df = evidence_df[evidence_df["EvidenceStrength"] == "STRONG_ASSOCIATION"].copy()
        source_transformer_df = evidence_df[
            (evidence_df["EvidenceStrength"] == "MODERATE_ASSOCIATION") &
            evidence_df["ElementType"].astype(str).str.contains("transformer", case=False, na=False)
        ].copy()
        transformer_evidence_df = evidence_df[
            evidence_df["ElementType"].astype(str).str.contains("transformer", case=False, na=False)
        ].copy()

        source_trace_df = pd.concat(source_trace_rows, ignore_index=True) if source_trace_rows else pd.DataFrame()
        connected_trace_df = pd.concat(connected_trace_rows, ignore_index=True) if connected_trace_rows else pd.DataFrame()
        errors_df = pd.DataFrame(error_rows)

        workbook_frames = {
            "Substation_Summary": summary_df,
            "Current_Candidate_Subs": current_candidate_summary,
            "Direct_Substation_Meta": direct_df,
            "Strong_Association": strong_df,
            "Source_Transformer": source_transformer_df,
            "All_Transformer_Evidence": transformer_evidence_df,
            "All_Capacity_Evidence": evidence_df,
            "Source_Trace_Raw": source_trace_df,
            "Connected_Trace_Raw": connected_trace_df,
            "Breaker_Substation_Map": breaker_map,
            "Substation_Entities": substation_entities,
            "Errors": errors_df,
            "Assumptions": assumptions_table()
        }

        with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
            for sheet_name, frame in workbook_frames.items():
                safe_frame = make_excel_safe(frame)
                safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
                format_excel(writer, sheet_name, safe_frame)

        heading("CAPACITY DISCOVERY COMPLETE")

        direct_count = int((summary_df["DirectSubstationCapacityEvidence"] == True).sum())
        enclosed_transformer_count = int((summary_df["EnclosedTransformerCapacityEvidence"] == True).sum())
        source_transformer_count = int((summary_df["SourceOrUpstreamTransformerEvidence"] == True).sum())

        print(f"Substations audited: {len(summary_df)}")
        print(f"Substations with direct capacity-like metadata: {direct_count}")
        print(f"Substations with enclosed transformer capacity evidence: {enclosed_transformer_count}")
        print(f"Substations with source/upstream transformer evidence: {source_transformer_count}")
        print(f"Audit workbook saved to: {Path(OUTPUT_FILE).resolve()}")

        if direct_count == 0 and enclosed_transformer_count == 0 and source_transformer_count == 0:
            print("\nNo defensible EDM substation capacity was discovered.")
            print("That would confirm TotalCapacityMW must remain an external planning input or scenario parameter.")
        else:
            print("\nPotential capacity evidence was found.")
            print("Do not copy a value into TotalCapacityMW automatically; review the relationship and metadata semantics in the workbook first.")

    finally:
        conn.close()


if __name__ == "__main__":
    main()
