import os
import json
import math
import uuid
import decimal
from pathlib import Path
import numpy as np
import pandas as pd
import psycopg2
import networkx as nx
from dotenv import load_dotenv
OUTPUT_FILE = 'greensboro_top15_location_hosting_screen.xlsx'
PEAK_LOAD_FILE = 'greensboro_top15_peak_duration_analysis.xlsx'
PEAK_LOAD_SHEET = 'Feeder_Summary'
YEAR = 2024
POWER_FACTOR = 0.95
PRIMARY_ONLY = True
MIN_HOSTING_MW = 0.1
TOP_CANDIDATES_PER_FEEDER = 20
TOP_GLOBAL_CANDIDATES = 100
PATH_AUDIT_CANDIDATES_PER_FEEDER = 10
LOAD_WEIGHT_FIELD = 'source_rating_kw'
TARGET_FEEDERS = ['GSO_122', 'GSO_124', 'GSO_123', 'GSO_121', 'GSO_144', 'GSO_141', 'GSO_145', 'GSO_142', 'GSO_18', 'GSO_127', 'GSO_73', 'GSO_128', 'GSO_126', 'GSO_55', 'GSO_125']
SERIES_TYPE_TERMS = ['line', 'transformer', 'switch', 'breaker', 'recloser', 'fuse', 'regulator']
load_dotenv()
DB_HOST = os.getenv('EDM_HOST')
DB_USER = os.getenv('EDM_USER')
DB_PASSWORD = os.getenv('EDM_PASSWORD')
DB_NAME = os.getenv('EDM_DATABASE', 'edm')

def connect_to_edm():
    missing = [name for name, value in {'EDM_HOST': DB_HOST, 'EDM_USER': DB_USER, 'EDM_PASSWORD': DB_PASSWORD, 'EDM_DATABASE': DB_NAME}.items() if not value]
    if missing:
        raise RuntimeError(f"Missing required .env values: {', '.join(missing)}")
    return psycopg2.connect(host=DB_HOST, user=DB_USER, password=DB_PASSWORD, dbname=DB_NAME)

def query_dataframe(conn, query, params=None):
    with conn.cursor() as cursor:
        if params is None:
            cursor.execute(query)
        else:
            cursor.execute(query, params)
        rows = cursor.fetchall()
        columns = [description[0] for description in cursor.description]
    return pd.DataFrame(rows, columns=columns)

def safe_query_dataframe(conn, query, params=None):
    savepoint = 'hosting_screen_query'
    try:
        with conn.cursor() as cursor:
            cursor.execute(f'SAVEPOINT {savepoint}')
        df = query_dataframe(conn, query, params)
        with conn.cursor() as cursor:
            cursor.execute(f'RELEASE SAVEPOINT {savepoint}')
        return (df, None)
    except Exception as exc:
        try:
            with conn.cursor() as cursor:
                cursor.execute(f'ROLLBACK TO SAVEPOINT {savepoint}')
                cursor.execute(f'RELEASE SAVEPOINT {savepoint}')
        except Exception:
            conn.rollback()
        return (pd.DataFrame(), str(exc))

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
            value = value.replace(',', '').strip()
        return float(value)
    except Exception:
        return np.nan

def truthy(value):
    if is_missing(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {'true', '1', 'yes', 'y', 't'}

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

def flatten_dict(data, prefix=''):
    output = {}
    if not isinstance(data, dict):
        return output
    for key, value in data.items():
        full_key = f'{prefix}.{key}' if prefix else str(key)
        if isinstance(value, dict):
            output.update(flatten_dict(value, full_key))
        else:
            output[full_key] = value
    return output

def normalize_key(value):
    return ''.join((character for character in str(value).lower() if character.isalnum()))

def meta_find(meta, candidate_keys):
    flattened = flatten_dict(parse_meta(meta))
    normalized_candidates = [normalize_key(key) for key in candidate_keys]
    for key, value in flattened.items():
        normalized_key = normalize_key(key)
        for candidate in normalized_candidates:
            if normalized_key.endswith(candidate):
                return value
    return None

def normalize_voltage_kv(value):
    value = safe_float(value)
    if np.isnan(value) or value <= 0:
        return np.nan
    if value > 100:
        return value / 1000.0
    return value

def classify_element(element_type, is_switchable=False):
    text = str(element_type).lower()
    if 'breaker' in text:
        return 'Breaker'
    if 'transformer' in text:
        return 'Transformer'
    if 'line' in text:
        return 'Line'
    if 'switch' in text or truthy(is_switchable):
        return 'Switch'
    if 'recloser' in text:
        return 'Recloser'
    if 'fuse' in text:
        return 'Fuse'
    if 'regulator' in text:
        return 'Regulator'
    if 'meter' in text:
        return 'Meter'
    if 'solar' in text or 'pv' in text:
        return 'PV'
    if 'ev' in text:
        return 'EV'
    return 'Other'

def is_series_element(row):
    element_class = classify_element(row.get('type'), row.get('is_switchable'))
    return element_class in {'Line', 'Transformer', 'Switch', 'Recloser', 'Fuse', 'Regulator'}

def line_capacity(row):
    meta = row.get('meta')
    rating_a = safe_float(meta_find(meta, ['rating_a', 'rated_a', 'ampacity', 'current_rating']))
    voltage_kv = normalize_voltage_kv(meta_find(meta, ['ph_ph_voltage', 'phase_phase_voltage', 'line_voltage', 'voltage_level']))
    if np.isnan(rating_a) or np.isnan(voltage_kv) or rating_a <= 0 or (voltage_kv <= 0):
        return (np.nan, rating_a, voltage_kv)
    apparent_mva = math.sqrt(3) * voltage_kv * rating_a / 1000.0
    real_power_mw = apparent_mva * POWER_FACTOR
    return (real_power_mw, rating_a, voltage_kv)

def transformer_capacity(row):
    rating_kva = safe_float(meta_find(row.get('meta'), ['rating_kva', 'rated_kva', 'kva_rating']))
    if np.isnan(rating_kva) or rating_kva <= 0:
        return (np.nan, rating_kva)
    return (rating_kva / 1000.0 * POWER_FACTOR, rating_kva)

def breaker_capacity(row):
    rating_kva = safe_float(meta_find(row.get('meta'), ['rating_kva', 'rated_kva', 'kva_rating']))
    if np.isnan(rating_kva) or rating_kva <= 0:
        return (np.nan, rating_kva)
    return (rating_kva / 1000.0 * POWER_FACTOR, rating_kva)

def element_capacity(row):
    element_class = classify_element(row.get('type'), row.get('is_switchable'))
    if element_class == 'Line':
        capacity_mw, rating_a, voltage_kv = line_capacity(row)
        return {'CapacityMW': capacity_mw, 'RatingA': rating_a, 'VoltageKV': voltage_kv, 'RatingKVA': np.nan}
    if element_class == 'Transformer':
        capacity_mw, rating_kva = transformer_capacity(row)
        return {'CapacityMW': capacity_mw, 'RatingA': np.nan, 'VoltageKV': np.nan, 'RatingKVA': rating_kva}
    return {'CapacityMW': np.nan, 'RatingA': np.nan, 'VoltageKV': np.nan, 'RatingKVA': np.nan}

def load_peak_results():
    path = Path(PEAK_LOAD_FILE)
    if not path.exists():
        raise FileNotFoundError(f'Could not find {PEAK_LOAD_FILE}. Place the SCADA peak-duration workbook in the same folder as this script.')
    peak_df = pd.read_excel(path, sheet_name=PEAK_LOAD_SHEET)
    required = {'GridID', 'PeakLoadMW'}
    missing = required - set(peak_df.columns)
    if missing:
        raise RuntimeError(f'{PEAK_LOAD_FILE} is missing required columns: {sorted(missing)}')
    peak_df = peak_df[peak_df['GridID'].isin(TARGET_FEEDERS)].copy()
    return peak_df

def get_target_elements(conn):
    placeholders = ','.join(['%s'] * len(TARGET_FEEDERS))
    spatial_query = f'\n        SELECT\n            grid_id,\n            grid_element_id,\n            type,\n            customer_type,\n            phases,\n            is_underground,\n            is_producer,\n            is_consumer,\n            is_switchable,\n            switch_is_open,\n            terminal1_cn,\n            terminal2_cn,\n            power_flow_direction,\n            upstream_grid_element_id,\n            meta,\n            CASE\n                WHEN geometry IS NOT NULL\n                THEN ST_X(ST_Centroid(geometry))\n            END AS centroid_x,\n            CASE\n                WHEN geometry IS NOT NULL\n                THEN ST_Y(ST_Centroid(geometry))\n            END AS centroid_y\n        FROM grid_element\n        WHERE grid_id IN ({placeholders})\n        ORDER BY grid_id, grid_element_id;\n    '
    elements, error = safe_query_dataframe(conn, spatial_query, tuple(TARGET_FEEDERS))
    if error is None:
        return elements
    print('Spatial centroid query was unavailable. Continuing without coordinates.')
    fallback_query = f'\n        SELECT\n            grid_id,\n            grid_element_id,\n            type,\n            customer_type,\n            phases,\n            is_underground,\n            is_producer,\n            is_consumer,\n            is_switchable,\n            switch_is_open,\n            terminal1_cn,\n            terminal2_cn,\n            power_flow_direction,\n            upstream_grid_element_id,\n            meta\n        FROM grid_element\n        WHERE grid_id IN ({placeholders})\n        ORDER BY grid_id, grid_element_id;\n    '
    elements = query_dataframe(conn, fallback_query, tuple(TARGET_FEEDERS))
    elements['centroid_x'] = np.nan
    elements['centroid_y'] = np.nan
    return elements

def identify_breaker(feeder_elements):
    breakers = feeder_elements[feeder_elements['type'].astype(str).str.contains('breaker', case=False, na=False)].copy()
    if breakers.empty:
        return None
    breakers['ProducerSort'] = breakers['is_producer'].fillna(False).astype(bool)
    breakers = breakers.sort_values(['ProducerSort', 'grid_element_id'], ascending=[False, True])
    return breakers.iloc[0]

def build_network_graph(feeder_elements, breaker_id):
    graph = nx.Graph()
    duplicate_edge_count = 0
    skipped_open_switches = 0
    skipped_missing_terminals = 0
    for _, row in feeder_elements.iterrows():
        element_id = str(row['grid_element_id'])
        if element_id == str(breaker_id):
            continue
        if not is_series_element(row):
            continue
        if truthy(row.get('is_switchable')) and truthy(row.get('switch_is_open')):
            skipped_open_switches += 1
            continue
        terminal1 = row.get('terminal1_cn')
        terminal2 = row.get('terminal2_cn')
        if is_missing(terminal1) or is_missing(terminal2):
            skipped_missing_terminals += 1
            continue
        terminal1 = str(terminal1)
        terminal2 = str(terminal2)
        if terminal1 == terminal2:
            continue
        element_class = classify_element(row.get('type'), row.get('is_switchable'))
        capacity = element_capacity(row)
        edge_data = {'element_id': element_id, 'element_type': row.get('type'), 'element_class': element_class, 'capacity_mw': capacity['CapacityMW'], 'rating_a': capacity['RatingA'], 'voltage_kv': capacity['VoltageKV'], 'rating_kva': capacity['RatingKVA'], 'centroid_x': row.get('centroid_x'), 'centroid_y': row.get('centroid_y')}
        if graph.has_edge(terminal1, terminal2):
            duplicate_edge_count += 1
            existing = graph[terminal1][terminal2]
            existing_capacity = safe_float(existing.get('capacity_mw'))
            new_capacity = safe_float(edge_data.get('capacity_mw'))
            if np.isnan(existing_capacity):
                graph[terminal1][terminal2].update(edge_data)
            elif not np.isnan(new_capacity) and new_capacity < existing_capacity:
                graph[terminal1][terminal2].update(edge_data)
        else:
            graph.add_edge(terminal1, terminal2, **edge_data)
    return (graph, duplicate_edge_count, skipped_open_switches, skipped_missing_terminals)

def choose_root_node(graph, breaker_row):
    terminals = []
    for value in [breaker_row.get('terminal1_cn'), breaker_row.get('terminal2_cn')]:
        if not is_missing(value):
            terminals.append(str(value))
    if not terminals:
        return (None, {})
    component_sizes = {}
    for terminal in terminals:
        if terminal in graph:
            component_sizes[terminal] = len(nx.node_connected_component(graph, terminal))
        else:
            component_sizes[terminal] = 0
    root_node = max(component_sizes, key=component_sizes.get)
    if component_sizes[root_node] == 0:
        return (None, component_sizes)
    return (root_node, component_sizes)

def get_load_points(feeder_elements):
    meters = feeder_elements[feeder_elements['type'].astype(str).str.contains('meter', case=False, na=False)].copy()
    if not meters.empty:
        return (meters, 'Meter')
    consumers = feeder_elements[feeder_elements['is_consumer'].fillna(False).astype(bool)].copy()
    consumers = consumers[~consumers.apply(is_series_element, axis=1)]
    return (consumers, 'ConsumerFallback')

def extract_load_weight(row):
    value = safe_float(meta_find(row.get('meta'), [LOAD_WEIGHT_FIELD, 'source_rating_kw', 'rating_kw', 'rated_kw']))
    if np.isnan(value) or value <= 0:
        return np.nan
    return value

def choose_load_node(row, paths):
    candidate_nodes = []
    for value in [row.get('terminal1_cn'), row.get('terminal2_cn')]:
        if is_missing(value):
            continue
        node = str(value)
        if node in paths:
            candidate_nodes.append(node)
    if not candidate_nodes:
        return None
    return max(candidate_nodes, key=lambda node: len(paths[node]))

def get_path_edge_records(graph, node_path):
    records = []
    for index in range(len(node_path) - 1):
        node_a = node_path[index]
        node_b = node_path[index + 1]
        if not graph.has_edge(node_a, node_b):
            continue
        edge = graph[node_a][node_b]
        records.append({'FromNode': node_a, 'ToNode': node_b, 'ElementID': edge.get('element_id'), 'ElementType': edge.get('element_type'), 'ElementClass': edge.get('element_class'), 'CapacityMW': edge.get('capacity_mw'), 'RatingA': edge.get('rating_a'), 'VoltageKV': edge.get('voltage_kv'), 'RatingKVA': edge.get('rating_kva')})
    return records

def allocate_peak_load(feeder_elements, graph, root_node, peak_mw):
    paths = nx.single_source_shortest_path(graph, root_node)
    load_points, load_method = get_load_points(feeder_elements)
    allocation_rows = []
    for _, row in load_points.iterrows():
        node = choose_load_node(row, paths)
        allocation_rows.append({'GridElementID': str(row['grid_element_id']), 'ElementType': row['type'], 'LoadNode': node, 'Reachable': node is not None, 'RawWeightKW': extract_load_weight(row)})
    allocation = pd.DataFrame(allocation_rows)
    if allocation.empty:
        return ({}, allocation, {'LoadMethod': load_method, 'LoadPointCount': 0, 'ReachableLoadPoints': 0, 'ReachabilityPct': 0.0, 'PositiveWeightCount': 0, 'WeightFillKW': np.nan, 'TotalWeightKW': 0.0})
    reachable = allocation[allocation['Reachable'] == True].copy()
    reachability_pct = len(reachable) / len(allocation) * 100.0 if len(allocation) > 0 else 0.0
    positive_weights = reachable[reachable['RawWeightKW'].notna() & (reachable['RawWeightKW'] > 0)]['RawWeightKW']
    if not positive_weights.empty:
        fill_weight = float(positive_weights.median())
    else:
        fill_weight = 1.0
    reachable['FinalWeightKW'] = reachable['RawWeightKW'].fillna(fill_weight)
    reachable.loc[reachable['FinalWeightKW'] <= 0, 'FinalWeightKW'] = fill_weight
    total_weight = reachable['FinalWeightKW'].sum()
    if total_weight <= 0:
        reachable['FinalWeightKW'] = 1.0
        total_weight = reachable['FinalWeightKW'].sum()
    reachable['AllocatedPeakMW'] = peak_mw * reachable['FinalWeightKW'] / total_weight
    allocation = allocation.merge(reachable[['GridElementID', 'FinalWeightKW', 'AllocatedPeakMW']], on='GridElementID', how='left')
    edge_load_mw = {}
    for _, load in reachable.iterrows():
        load_node = load['LoadNode']
        allocated_mw = load['AllocatedPeakMW']
        node_path = paths.get(load_node)
        if not node_path:
            continue
        path_edges = get_path_edge_records(graph, node_path)
        for edge in path_edges:
            element_id = edge['ElementID']
            edge_load_mw[element_id] = edge_load_mw.get(element_id, 0.0) + allocated_mw
    diagnostics = {'LoadMethod': load_method, 'LoadPointCount': len(allocation), 'ReachableLoadPoints': len(reachable), 'ReachabilityPct': reachability_pct, 'PositiveWeightCount': len(positive_weights), 'WeightFillKW': fill_weight, 'TotalWeightKW': total_weight}
    return (edge_load_mw, allocation, diagnostics)

def get_breaker_capacity(breaker_row):
    capacity_mw, rating_kva = breaker_capacity(breaker_row)
    return (capacity_mw, rating_kva)

def build_candidates(grid_id, graph, root_node, breaker_row, peak_mw, edge_load_mw, load_diagnostics, hours_within_10_pct=None, pct_time_within_10_pct=None):
    paths = nx.single_source_shortest_path(graph, root_node)
    breaker_capacity_mw, breaker_rating_kva = get_breaker_capacity(breaker_row)
    if np.isnan(breaker_capacity_mw):
        breaker_residual_mw = np.nan
    else:
        breaker_residual_mw = breaker_capacity_mw - peak_mw
    candidate_rows = []
    excluded_rows = []
    path_record_lookup = {}
    for node_a, node_b, edge in graph.edges(data=True):
        if edge.get('element_class') != 'Line':
            continue
        line_capacity_mw = safe_float(edge.get('capacity_mw'))
        if np.isnan(line_capacity_mw) or line_capacity_mw <= 0:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'CandidateLineMissingCapacity'})
            continue
        if node_a not in paths or node_b not in paths:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'CandidateLineNotReachableFromBreaker'})
            continue
        distance_a = len(paths[node_a]) - 1
        distance_b = len(paths[node_b]) - 1
        if distance_a == distance_b:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'LoopOrAmbiguousLineDirection'})
            continue
        downstream_node = node_a if distance_a > distance_b else node_b
        node_path = paths[downstream_node]
        path_edges = get_path_edge_records(graph, node_path)
        transformers_on_path = [path_edge for path_edge in path_edges if path_edge['ElementClass'] == 'Transformer']
        if PRIMARY_ONLY and transformers_on_path:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'DownstreamOfTransformer'})
            continue
        thermal_constraints = []
        if not np.isnan(breaker_capacity_mw):
            thermal_constraints.append({'ElementID': str(breaker_row['grid_element_id']), 'ElementType': breaker_row['type'], 'ElementClass': 'Breaker', 'CapacityMW': breaker_capacity_mw, 'ExistingLoadMW': peak_mw, 'ResidualMW': breaker_residual_mw})
        missing_capacity_elements = []
        for path_edge in path_edges:
            element_class = path_edge['ElementClass']
            if element_class not in {'Line', 'Transformer'}:
                continue
            capacity_mw = safe_float(path_edge['CapacityMW'])
            if np.isnan(capacity_mw) or capacity_mw <= 0:
                missing_capacity_elements.append(path_edge['ElementID'])
                continue
            existing_load_mw = edge_load_mw.get(path_edge['ElementID'], 0.0)
            residual_mw = capacity_mw - existing_load_mw
            thermal_constraints.append({'ElementID': path_edge['ElementID'], 'ElementType': path_edge['ElementType'], 'ElementClass': element_class, 'CapacityMW': capacity_mw, 'ExistingLoadMW': existing_load_mw, 'ResidualMW': residual_mw})
        if missing_capacity_elements:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'MissingCapacityOnPath', 'MissingElementIDs': ', '.join(missing_capacity_elements)})
            continue
        if not thermal_constraints:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'NoUsableThermalConstraints'})
            continue
        limiting_constraint = min(thermal_constraints, key=lambda item: item['ResidualMW'])
        estimated_hosting_mw = max(0.0, limiting_constraint['ResidualMW'])
        candidate_line_existing_load = edge_load_mw.get(edge.get('element_id'), 0.0)
        candidate_line_residual = line_capacity_mw - candidate_line_existing_load
        if estimated_hosting_mw < MIN_HOSTING_MW:
            excluded_rows.append({'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'Reason': 'HostingBelowMinimum', 'EstimatedHostingMW': estimated_hosting_mw})
            continue
        reachability_pct = load_diagnostics['ReachabilityPct']
        if reachability_pct >= 95.0 and (not np.isnan(breaker_capacity_mw)):
            confidence = 'High'
        elif reachability_pct >= 80.0 and (not np.isnan(breaker_capacity_mw)):
            confidence = 'Medium'
        else:
            confidence = 'Low'
        candidate_key = f"{grid_id}|{edge.get('element_id')}"
        candidate_rows.append({'CandidateKey': candidate_key, 'GridID': grid_id, 'CandidateLineID': edge.get('element_id'), 'CandidateLineType': edge.get('element_type'), 'CandidateNode': downstream_node, 'CentroidX': edge.get('centroid_x'), 'CentroidY': edge.get('centroid_y'), 'NominalVoltageKV': edge.get('voltage_kv'), 'CandidateLineRatingA': edge.get('rating_a'), 'CandidateLineCapacityMW': line_capacity_mw, 'CandidateLineExistingLoadMW': candidate_line_existing_load, 'CandidateLineResidualMW': candidate_line_residual, 'FeederPeakMW': peak_mw, 'BreakerCapacityMW': breaker_capacity_mw, 'BreakerRatingKVA': breaker_rating_kva, 'BreakerResidualMW': breaker_residual_mw, 'EstimatedBaseHostingMW': estimated_hosting_mw, 'LimitingElementID': limiting_constraint['ElementID'], 'LimitingElementType': limiting_constraint['ElementType'], 'LimitingElementClass': limiting_constraint['ElementClass'], 'LimitingElementCapacityMW': limiting_constraint['CapacityMW'], 'LimitingElementExistingLoadMW': limiting_constraint['ExistingLoadMW'], 'LimitingElementResidualMW': limiting_constraint['ResidualMW'], 'ElectricalDistanceHops': len(node_path) - 1, 'PathLineCount': sum((1 for item in path_edges if item['ElementClass'] == 'Line')), 'PathTransformerCount': len(transformers_on_path), 'LoadPointReachabilityPct': reachability_pct, 'LoadAllocationMethod': load_diagnostics['LoadMethod'], 'HoursWithin10PctOfPeak': hours_within_10_pct, 'PctTimeWithin10PctOfPeak': pct_time_within_10_pct, 'CandidateConfidence': confidence, 'PathElementIDs': ' -> '.join((item['ElementID'] for item in path_edges))})
        path_record_lookup[candidate_key] = thermal_constraints
    return (pd.DataFrame(candidate_rows), pd.DataFrame(excluded_rows), path_record_lookup)

def build_element_capacity_audit(feeder_elements, edge_load_mw):
    rows = []
    for _, row in feeder_elements.iterrows():
        element_class = classify_element(row.get('type'), row.get('is_switchable'))
        if element_class not in {'Line', 'Transformer'}:
            continue
        capacity = element_capacity(row)
        capacity_mw = capacity['CapacityMW']
        existing_load_mw = edge_load_mw.get(str(row['grid_element_id']), 0.0)
        residual_mw = capacity_mw - existing_load_mw if not np.isnan(capacity_mw) else np.nan
        rows.append({'GridID': row['grid_id'], 'GridElementID': str(row['grid_element_id']), 'ElementType': row['type'], 'ElementClass': element_class, 'CapacityMW': capacity_mw, 'AllocatedPeakLoadMW': existing_load_mw, 'ResidualMW': residual_mw, 'RatingA': capacity['RatingA'], 'VoltageKV': capacity['VoltageKV'], 'RatingKVA': capacity['RatingKVA'], 'CentroidX': row.get('centroid_x'), 'CentroidY': row.get('centroid_y')})
    return pd.DataFrame(rows)

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
        elif df[column].dtype == 'object':
            df[column] = df[column].map(excel_safe_value)
    return df

def format_excel(writer, sheet_name, dataframe):
    if dataframe is None or dataframe.shape[1] == 0:
        return
    workbook = writer.book
    worksheet = writer.sheets[sheet_name]
    header_format = workbook.add_format({'bold': True, 'border': 1, 'align': 'center', 'valign': 'vcenter'})
    number_format = workbook.add_format({'num_format': '0.000'})
    integer_format = workbook.add_format({'num_format': '0'})
    percent_format = workbook.add_format({'num_format': '0.00'})
    for column_index, column_name in enumerate(dataframe.columns):
        worksheet.write(0, column_index, str(column_name), header_format)
        if dataframe.empty:
            max_length = 0
        else:
            max_length = max((len(str(value)) for value in dataframe[column_name].tolist() if value is not None and (not (isinstance(value, float) and np.isnan(value)))), default=0)
        width = min(max(max_length, len(str(column_name))) + 2, 45)
        lower_name = str(column_name).lower()
        if 'rank' in lower_name or 'count' in lower_name or 'hops' in lower_name:
            worksheet.set_column(column_index, column_index, width, integer_format)
        elif 'pct' in lower_name or 'percent' in lower_name:
            worksheet.set_column(column_index, column_index, width, percent_format)
        elif pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, number_format)
        else:
            worksheet.set_column(column_index, column_index, width)
    worksheet.freeze_panes(1, 0)
    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)

def build_assumptions_table():
    return pd.DataFrame([{'Item': 'Model boundary', 'Assumption': 'Feeder breaker/source is treated as the upstream boundary because the Greensboro EDM does not explicitly represent substations.'}, {'Item': 'Power factor', 'Assumption': f'{POWER_FACTOR:.2f} used to convert apparent equipment ratings to screening real-power capacity.'}, {'Item': 'Existing feeder demand', 'Assumption': 'Measured 2024 annual SCADA peak load is used as the feeder peak condition.'}, {'Item': 'Load allocation', 'Assumption': 'Feeder peak demand is distributed across reachable downstream meters in proportion to source_rating_kw.'}, {'Item': 'Missing meter weights', 'Assumption': 'Meters without source_rating_kw receive the median positive source_rating_kw for that feeder.'}, {'Item': 'Candidate locations', 'Assumption': 'Candidate locations are rated primary-distribution line segments reachable from the feeder breaker.'}, {'Item': 'Primary-only screen', 'Assumption': 'Candidates downstream of a distribution transformer are excluded.'}, {'Item': 'Hosting capacity', 'Assumption': 'Estimated hosting capacity equals the smallest residual thermal capacity along the breaker-to-candidate path.'}, {'Item': 'Switches/protection', 'Assumption': 'Closed switches, fuses, reclosers, and regulators are retained for connectivity but are not treated as thermal constraints unless represented as line or transformer ratings.'}, {'Item': 'Open switches', 'Assumption': 'Open switchable elements are excluded from the active network graph.'}, {'Item': 'Parallel elements', 'Assumption': 'If parallel elements connect the same topology nodes, the script conservatively retains the lower-capacity representation.'}, {'Item': 'Interpretation', 'Assumption': 'Results are thermal screening estimates and require later voltage/power-flow validation before being treated as interconnection capacity.'}])

def main():
    print('=' * 110)
    print('GREENSBORO TOP-15 SITE-SPECIFIC THERMAL HOSTING-CAPACITY SCREEN')
    print('=' * 110)
    print()
    print('Purpose:')
    print('  Move from feeder-level headroom to candidate line locations inside the strongest feeders.')
    print()
    print('Loading 2024 SCADA peak results...')
    peak_df = load_peak_results()
    peak_lookup = peak_df.set_index('GridID').to_dict('index')
    print(f'SCADA peak results loaded for {len(peak_df):,} shortlisted feeders.')
    print()
    conn = connect_to_edm()
    try:
        print('Loading shortlisted feeder assets and topology...')
        all_elements = get_target_elements(conn)
        print(f'Grid elements loaded: {len(all_elements):,}')
        print()
        all_candidate_frames = []
        excluded_candidate_frames = []
        feeder_summary_rows = []
        load_allocation_frames = []
        capacity_audit_frames = []
        all_path_records = {}
        for feeder_index, grid_id in enumerate(TARGET_FEEDERS, start=1):
            print('-' * 110)
            print(f'[{feeder_index}/{len(TARGET_FEEDERS)}] Processing {grid_id}')
            feeder_elements = all_elements[all_elements['grid_id'] == grid_id].copy()
            if feeder_elements.empty:
                print('  WARNING: no grid elements found.')
                continue
            breaker_row = identify_breaker(feeder_elements)
            if breaker_row is None:
                print('  WARNING: no breaker found.')
                continue
            breaker_id = str(breaker_row['grid_element_id'])
            print(f'  Breaker: {breaker_id}')
            peak_record = peak_lookup.get(grid_id)
            if peak_record is None:
                print('  WARNING: no SCADA peak record found.')
                continue
            peak_mw = safe_float(peak_record.get('PeakLoadMW'))
            if np.isnan(peak_mw):
                print('  WARNING: invalid peak load.')
                continue
            hours_within_10_pct = peak_record.get('HoursWithin10PctOfPeak', np.nan)
            pct_time_within_10_pct = peak_record.get('PctObservedTimeWithin10PctOfPeak', np.nan)
            graph, duplicate_edges, open_switches, missing_terminal_elements = build_network_graph(feeder_elements, breaker_id)
            root_node, breaker_terminal_components = choose_root_node(graph, breaker_row)
            if root_node is None:
                print('  WARNING: could not identify downstream breaker terminal.')
                continue
            print(f'  Graph nodes: {graph.number_of_nodes():,}')
            print(f'  Graph edges: {graph.number_of_edges():,}')
            print(f'  Downstream breaker node: {root_node}')
            edge_load_mw, load_allocation, load_diagnostics = allocate_peak_load(feeder_elements=feeder_elements, graph=graph, root_node=root_node, peak_mw=peak_mw)
            if not load_allocation.empty:
                load_allocation.insert(0, 'GridID', grid_id)
                load_allocation_frames.append(load_allocation)
            print(f'  Peak load: {peak_mw:.4f} MW')
            print(f"  Load points: {load_diagnostics['LoadPointCount']:,}")
            print(f"  Reachable load points: {load_diagnostics['ReachableLoadPoints']:,}")
            print(f"  Load-point reachability: {load_diagnostics['ReachabilityPct']:.2f}%")
            candidates, excluded_candidates, path_lookup = build_candidates(grid_id=grid_id, graph=graph, root_node=root_node, breaker_row=breaker_row, peak_mw=peak_mw, edge_load_mw=edge_load_mw, load_diagnostics=load_diagnostics, hours_within_10_pct=hours_within_10_pct, pct_time_within_10_pct=pct_time_within_10_pct)
            if not candidates.empty:
                candidates = candidates.sort_values(['EstimatedBaseHostingMW', 'LoadPointReachabilityPct', 'ElectricalDistanceHops'], ascending=[False, False, True]).reset_index(drop=True)
                candidates['RankWithinFeeder'] = np.arange(1, len(candidates) + 1)
                all_candidate_frames.append(candidates)
                all_path_records.update(path_lookup)
            if not excluded_candidates.empty:
                excluded_candidate_frames.append(excluded_candidates)
            capacity_audit = build_element_capacity_audit(feeder_elements, edge_load_mw)
            capacity_audit_frames.append(capacity_audit)
            breaker_capacity_mw, breaker_rating_kva = get_breaker_capacity(breaker_row)
            breaker_residual = breaker_capacity_mw - peak_mw if not np.isnan(breaker_capacity_mw) else np.nan
            cycle_count = len(nx.cycle_basis(graph))
            top_hosting = candidates['EstimatedBaseHostingMW'].max() if not candidates.empty else np.nan
            top_candidate = candidates.iloc[0]['CandidateLineID'] if not candidates.empty else None
            feeder_summary_rows.append({'GridID': grid_id, 'BreakerID': breaker_id, 'RootConnectivityNode': root_node, 'GraphNodes': graph.number_of_nodes(), 'GraphEdges': graph.number_of_edges(), 'GraphCycles': cycle_count, 'ParallelEdgesSimplified': duplicate_edges, 'OpenSwitchesExcluded': open_switches, 'SeriesElementsMissingTerminals': missing_terminal_elements, 'PeakLoadMW': peak_mw, 'HoursWithin10PctOfPeak': hours_within_10_pct, 'PctTimeWithin10PctOfPeak': pct_time_within_10_pct, 'BreakerRatingKVA': breaker_rating_kva, 'BreakerCapacityMW': breaker_capacity_mw, 'BreakerResidualMW': breaker_residual, 'LoadAllocationMethod': load_diagnostics['LoadMethod'], 'LoadPointCount': load_diagnostics['LoadPointCount'], 'ReachableLoadPoints': load_diagnostics['ReachableLoadPoints'], 'LoadPointReachabilityPct': load_diagnostics['ReachabilityPct'], 'PositiveMeterWeightCount': load_diagnostics['PositiveWeightCount'], 'MedianFillWeightKW': load_diagnostics['WeightFillKW'], 'CandidateLocationCount': len(candidates), 'BestCandidateLineID': top_candidate, 'BestEstimatedHostingMW': top_hosting})
            print(f'  Candidate primary lines: {len(candidates):,}')
            if not candidates.empty:
                print(f"  Best candidate: {candidates.iloc[0]['CandidateLineID']}")
                print(f"  Estimated base hosting: {candidates.iloc[0]['EstimatedBaseHostingMW']:.4f} MW")
                print(f"  Limiting element: {candidates.iloc[0]['LimitingElementID']} ({candidates.iloc[0]['LimitingElementClass']})")
        print()
        print('=' * 110)
        print('BUILDING FINAL LOCATION RANKINGS')
        print('=' * 110)
        all_candidates = pd.concat(all_candidate_frames, ignore_index=True) if all_candidate_frames else pd.DataFrame()
        excluded_candidates = pd.concat(excluded_candidate_frames, ignore_index=True) if excluded_candidate_frames else pd.DataFrame()
        load_allocation_df = pd.concat(load_allocation_frames, ignore_index=True) if load_allocation_frames else pd.DataFrame()
        capacity_audit_df = pd.concat(capacity_audit_frames, ignore_index=True) if capacity_audit_frames else pd.DataFrame()
        feeder_summary_df = pd.DataFrame(feeder_summary_rows)
        if not all_candidates.empty:
            all_candidates = all_candidates.sort_values(['EstimatedBaseHostingMW', 'LoadPointReachabilityPct', 'HoursWithin10PctOfPeak', 'ElectricalDistanceHops'], ascending=[False, False, True, True]).reset_index(drop=True)
            all_candidates['GlobalRank'] = np.arange(1, len(all_candidates) + 1)
            preferred_order = ['GlobalRank', 'RankWithinFeeder', 'GridID', 'CandidateLineID', 'CandidateNode', 'EstimatedBaseHostingMW', 'CandidateConfidence', 'NominalVoltageKV', 'CandidateLineCapacityMW', 'CandidateLineExistingLoadMW', 'CandidateLineResidualMW', 'FeederPeakMW', 'BreakerCapacityMW', 'BreakerResidualMW', 'LimitingElementID', 'LimitingElementType', 'LimitingElementClass', 'LimitingElementCapacityMW', 'LimitingElementExistingLoadMW', 'LimitingElementResidualMW', 'ElectricalDistanceHops', 'PathLineCount', 'PathTransformerCount', 'LoadPointReachabilityPct', 'HoursWithin10PctOfPeak', 'PctTimeWithin10PctOfPeak', 'CentroidX', 'CentroidY', 'PathElementIDs', 'CandidateKey']
            remaining_columns = [column for column in all_candidates.columns if column not in preferred_order]
            all_candidates = all_candidates[preferred_order + remaining_columns]
        if not all_candidates.empty:
            top_by_feeder = all_candidates.sort_values(['GridID', 'RankWithinFeeder']).groupby('GridID', group_keys=False).head(TOP_CANDIDATES_PER_FEEDER).copy()
            global_top = all_candidates.head(TOP_GLOBAL_CANDIDATES).copy()
        else:
            top_by_feeder = pd.DataFrame()
            global_top = pd.DataFrame()
        path_audit_rows = []
        if not all_candidates.empty:
            audit_candidates = all_candidates.sort_values(['GridID', 'RankWithinFeeder']).groupby('GridID', group_keys=False).head(PATH_AUDIT_CANDIDATES_PER_FEEDER)
            for _, candidate in audit_candidates.iterrows():
                candidate_key = candidate['CandidateKey']
                constraints = all_path_records.get(candidate_key, [])
                for path_order, constraint in enumerate(constraints, start=1):
                    path_audit_rows.append({'GridID': candidate['GridID'], 'RankWithinFeeder': candidate['RankWithinFeeder'], 'CandidateLineID': candidate['CandidateLineID'], 'CandidateKey': candidate_key, 'PathConstraintOrder': path_order, 'ElementID': constraint['ElementID'], 'ElementType': constraint['ElementType'], 'ElementClass': constraint['ElementClass'], 'CapacityMW': constraint['CapacityMW'], 'ExistingLoadMW': constraint['ExistingLoadMW'], 'ResidualMW': constraint['ResidualMW'], 'IsLimitingElement': constraint['ElementID'] == candidate['LimitingElementID']})
        path_audit_df = pd.DataFrame(path_audit_rows)
        assumptions_df = build_assumptions_table()
        print()
        if not global_top.empty:
            print('TOP 25 CANDIDATE LOCATIONS')
            print('-' * 110)
            print(global_top[['GlobalRank', 'GridID', 'CandidateLineID', 'EstimatedBaseHostingMW', 'LimitingElementID', 'LimitingElementClass', 'NominalVoltageKV', 'CandidateConfidence']].head(25).to_string(index=False))
        output_frames = {'Global_Top_Candidates': global_top, 'Top_By_Feeder': top_by_feeder, 'All_Candidates': all_candidates, 'Feeder_Summary': feeder_summary_df, 'Top_Path_Audit': path_audit_df, 'Element_Capacities': capacity_audit_df, 'Load_Allocation': load_allocation_df, 'Excluded_Candidates': excluded_candidates, 'Assumptions': assumptions_df}
        output_frames = {sheet_name: make_excel_safe(dataframe) for sheet_name, dataframe in output_frames.items()}
        print()
        print('Writing Excel workbook...')
        with pd.ExcelWriter(OUTPUT_FILE, engine='xlsxwriter') as writer:
            for sheet_name, dataframe in output_frames.items():
                dataframe.to_excel(writer, sheet_name=sheet_name[:31], index=False)
                format_excel(writer, sheet_name[:31], dataframe)
        print()
        print('=' * 110)
        print('HOSTING-CAPACITY SCREEN COMPLETE')
        print('=' * 110)
        print(f'Output: {Path(OUTPUT_FILE).resolve()}')
        print()
        if not all_candidates.empty:
            print(f'Usable candidate line locations: {len(all_candidates):,}')
            print(f"Feeders represented: {all_candidates['GridID'].nunique():,}")
            print(f"Maximum estimated base hosting capacity: {all_candidates['EstimatedBaseHostingMW'].max():.4f} MW")
        print()
        print('Review these sheets first:')
        print('  1. Global_Top_Candidates')
        print('  2. Top_By_Feeder')
        print('  3. Feeder_Summary')
        print('  4. Top_Path_Audit')
        print()
        print('The EstimatedBaseHostingMW value is a thermal screening result.')
        print('It represents the minimum remaining rated capacity from the feeder breaker to that candidate line.')
        print('It should not yet be presented as final interconnection capacity until voltage/power-flow validation is completed.')
    finally:
        conn.close()
if __name__ == '__main__':
    main()
