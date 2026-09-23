import math
from pathlib import Path

import numpy as np
import pandas as pd

try:
    import pyomo.environ as pyo
except ImportError as exc:
    raise ImportError("Pyomo is required. Install with: pip install pyomo highspy") from exc

AUDIT_FILE = "greensboro_flag_resolution_audit.xlsx"
AUDIT_SHEET = "MILP_Readiness"
SUBSTATION_CAPACITY_FILE = "greensboro_substation_capacity_inputs.xlsx"
SUBSTATION_CAPACITY_SHEET = "Substation_Capacity"
OUTPUT_FILE = "greensboro_base_milp_results.xlsx"

CAPACITY_COLUMN = "FirmHostingMW"
REQUIRE_READY_CANDIDATES = True
ONE_SITE_PER_FEEDER = True
MIN_SITE_MW = 0.0
MAX_SITE_MW = None
SUBSTATION_CAPACITY_FACTOR = 1.0
UNRESOLVED_SUBSTATION_POLICY = "ALLOW_AS_UPPER_BOUND"
RUN_SITE_COUNT_FRONTIER = True
OPTIMALITY_TOLERANCE_MW = 1e-6

SOLVER_CANDIDATES = ["appsi_highs", "highs", "cbc", "glpk"]


def heading(text):
    print("\n" + "=" * 118)
    print(text)
    print("=" * 118)


def truthy(value):
    if pd.isna(value):
        return False
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "yes", "y", "t"}


def safe_float(value):
    if pd.isna(value):
        return np.nan
    try:
        return float(value)
    except Exception:
        return np.nan


def load_candidates():
    path = Path(AUDIT_FILE)
    if not path.exists():
        raise FileNotFoundError(f"Could not find {AUDIT_FILE}. Run the flag-resolution audit first.")

    df = pd.read_excel(path, sheet_name=AUDIT_SHEET)

    required = {"CandidateKey", "GridID", "SubstationID", "CandidateLineID", CAPACITY_COLUMN, "ReadyForFeederBoundMILP"}
    missing = required - set(df.columns)
    if missing:
        raise RuntimeError(f"{AUDIT_FILE} is missing required columns: {sorted(missing)}")

    df["GridID"] = df["GridID"].astype(str)
    df["SubstationID"] = df["SubstationID"].astype(str)
    df["CandidateKey"] = df["CandidateKey"].astype(str)
    df[CAPACITY_COLUMN] = pd.to_numeric(df[CAPACITY_COLUMN], errors="coerce")
    df["ReadyForFeederBoundMILP"] = df["ReadyForFeederBoundMILP"].map(truthy)

    if REQUIRE_READY_CANDIDATES:
        df = df[df["ReadyForFeederBoundMILP"]].copy()

    df = df[df[CAPACITY_COLUMN].notna() & (df[CAPACITY_COLUMN] > 0)].copy()

    if df.empty:
        raise RuntimeError("No MILP-ready candidates with positive firm hosting capacity were found.")

    if "FirmHostingAtMinimumPFMW" in df.columns:
        df["FirmHostingAtMinimumPFMW"] = pd.to_numeric(df["FirmHostingAtMinimumPFMW"], errors="coerce")

    df["SiteCapacityMW"] = df[CAPACITY_COLUMN]

    if MAX_SITE_MW is not None:
        df["SiteCapacityMW"] = np.minimum(df["SiteCapacityMW"], float(MAX_SITE_MW))

    df["SiteCapacityMW"] = df["SiteCapacityMW"].clip(lower=0.0)

    return df.reset_index(drop=True)


def create_substation_capacity_template(candidates):
    path = Path(SUBSTATION_CAPACITY_FILE)

    if path.exists():
        return

    rows = []

    for substation_id, group in candidates.groupby("SubstationID"):
        rows.append({
            "SubstationID": substation_id,
            "TotalCapacityMW": np.nan,
            "ExistingCoincidentPeakMW": np.nan,
            "AvailableAdditionalCapacityMW": np.nan,
            "CapacitySource": "Review",
            "Notes": "Populate either AvailableAdditionalCapacityMW directly, or TotalCapacityMW and ExistingCoincidentPeakMW."
        })

    template = pd.DataFrame(rows).sort_values("SubstationID").reset_index(drop=True)

    with pd.ExcelWriter(path, engine="xlsxwriter") as writer:
        template.to_excel(writer, sheet_name=SUBSTATION_CAPACITY_SHEET, index=False)
        worksheet = writer.sheets[SUBSTATION_CAPACITY_SHEET]
        worksheet.freeze_panes(1, 0)
        worksheet.autofilter(0, 0, len(template), len(template.columns) - 1)

        for column_index, column_name in enumerate(template.columns):
            width = min(max(len(column_name) + 2, 18), 55)
            worksheet.set_column(column_index, column_index, width)

    print(f"Created substation-capacity template: {path.resolve()}")


def load_substation_capacities(candidates):
    create_substation_capacity_template(candidates)
    path = Path(SUBSTATION_CAPACITY_FILE)

    capacity_df = pd.read_excel(path, sheet_name=SUBSTATION_CAPACITY_SHEET)

    required = {"SubstationID"}
    missing = required - set(capacity_df.columns)
    if missing:
        raise RuntimeError(f"{SUBSTATION_CAPACITY_FILE} is missing required columns: {sorted(missing)}")

    for column in ["TotalCapacityMW", "ExistingCoincidentPeakMW", "AvailableAdditionalCapacityMW"]:
        if column not in capacity_df.columns:
            capacity_df[column] = np.nan
        capacity_df[column] = pd.to_numeric(capacity_df[column], errors="coerce")

    if "CapacitySource" not in capacity_df.columns:
        capacity_df["CapacitySource"] = "Review"
    if "Notes" not in capacity_df.columns:
        capacity_df["Notes"] = ""

    capacity_df["SubstationID"] = capacity_df["SubstationID"].astype(str)
    capacity_df = capacity_df.drop_duplicates(subset=["SubstationID"], keep="last")

    rows = []

    for substation_id in sorted(candidates["SubstationID"].unique()):
        match = capacity_df[capacity_df["SubstationID"] == substation_id]

        if match.empty:
            total_capacity = np.nan
            existing_peak = np.nan
            direct_headroom = np.nan
            capacity_source = "Missing"
            notes = ""
        else:
            row = match.iloc[0]
            total_capacity = safe_float(row["TotalCapacityMW"])
            existing_peak = safe_float(row["ExistingCoincidentPeakMW"])
            direct_headroom = safe_float(row["AvailableAdditionalCapacityMW"])
            capacity_source = row.get("CapacitySource", "Review")
            notes = row.get("Notes", "")

        if pd.notna(direct_headroom) and direct_headroom >= 0:
            applied_headroom = direct_headroom
            method = "DirectAvailableAdditionalCapacity"
        elif pd.notna(total_capacity) and pd.notna(existing_peak):
            applied_headroom = SUBSTATION_CAPACITY_FACTOR * total_capacity - existing_peak
            applied_headroom = max(applied_headroom, 0.0)
            method = f"{SUBSTATION_CAPACITY_FACTOR:.3f}xTotalCapacityMinusCoincidentPeak"
        else:
            applied_headroom = np.nan
            method = "Unresolved"

        rows.append({
            "SubstationID": substation_id,
            "TotalCapacityMW": total_capacity,
            "ExistingCoincidentPeakMW": existing_peak,
            "AvailableAdditionalCapacityMW_Input": direct_headroom,
            "AppliedAdditionalHeadroomMW": applied_headroom,
            "CapacityResolved": pd.notna(applied_headroom),
            "CapacityMethod": method,
            "CapacitySource": capacity_source,
            "Notes": notes
        })

    return pd.DataFrame(rows)


def choose_solver():
    for solver_name in SOLVER_CANDIDATES:
        try:
            solver = pyo.SolverFactory(solver_name)
            if solver is not None and solver.available(exception_flag=False):
                return solver_name, solver
        except Exception:
            continue

    raise RuntimeError("No MILP solver was found. Recommended installation: pip install pyomo highspy")


def build_model(candidates, substation_capacities, max_sites=None, unresolved_policy="ALLOW_AS_UPPER_BOUND"):
    candidate_keys = candidates["CandidateKey"].tolist()
    candidate_lookup = candidates.set_index("CandidateKey").to_dict("index")
    feeder_groups = candidates.groupby("GridID")["CandidateKey"].apply(list).to_dict()
    substation_groups = candidates.groupby("SubstationID")["CandidateKey"].apply(list).to_dict()
    substation_lookup = substation_capacities.set_index("SubstationID").to_dict("index")

    model = pyo.ConcreteModel()
    model.CANDIDATES = pyo.Set(initialize=candidate_keys, ordered=True)
    model.x = pyo.Var(model.CANDIDATES, within=pyo.Binary)
    model.p = pyo.Var(model.CANDIDATES, within=pyo.NonNegativeReals)

    def site_upper_rule(m, candidate_key):
        return m.p[candidate_key] <= float(candidate_lookup[candidate_key]["SiteCapacityMW"]) * m.x[candidate_key]

    model.SiteUpper = pyo.Constraint(model.CANDIDATES, rule=site_upper_rule)

    if MIN_SITE_MW > 0:
        def site_lower_rule(m, candidate_key):
            return m.p[candidate_key] >= float(MIN_SITE_MW) * m.x[candidate_key]

        model.SiteLower = pyo.Constraint(model.CANDIDATES, rule=site_lower_rule)

    if ONE_SITE_PER_FEEDER:
        model.FEEDERS = pyo.Set(initialize=list(feeder_groups.keys()), ordered=True)

        def one_site_per_feeder_rule(m, feeder):
            return sum(m.x[candidate_key] for candidate_key in feeder_groups[feeder]) <= 1

        model.OneSitePerFeeder = pyo.Constraint(model.FEEDERS, rule=one_site_per_feeder_rule)

    if max_sites is not None:
        model.MaxSites = pyo.Constraint(expr=sum(model.x[candidate_key] for candidate_key in model.CANDIDATES) <= int(max_sites))

    resolved_substations = []
    unresolved_substations = []

    for substation_id, candidate_list in substation_groups.items():
        record = substation_lookup.get(substation_id, {})
        headroom = safe_float(record.get("AppliedAdditionalHeadroomMW"))

        if pd.notna(headroom):
            resolved_substations.append(substation_id)
        else:
            unresolved_substations.append(substation_id)

    model.RESOLVED_SUBSTATIONS = pyo.Set(initialize=resolved_substations, ordered=True)

    if resolved_substations:
        def substation_capacity_rule(m, substation_id):
            headroom = float(substation_lookup[substation_id]["AppliedAdditionalHeadroomMW"])
            return sum(m.p[candidate_key] for candidate_key in substation_groups[substation_id]) <= headroom

        model.SubstationCapacity = pyo.Constraint(model.RESOLVED_SUBSTATIONS, rule=substation_capacity_rule)

    if unresolved_policy.upper() == "EXCLUDE" and unresolved_substations:
        unresolved_candidate_keys = [candidate_key for substation_id in unresolved_substations for candidate_key in substation_groups[substation_id]]
        model.UNRESOLVED_CANDIDATES = pyo.Set(initialize=unresolved_candidate_keys, ordered=True)

        def unresolved_exclusion_rule(m, candidate_key):
            return m.x[candidate_key] == 0

        model.UnresolvedSubstationExclusion = pyo.Constraint(model.UNRESOLVED_CANDIDATES, rule=unresolved_exclusion_rule)

    model.TotalMW = pyo.Expression(expr=sum(model.p[candidate_key] for candidate_key in model.CANDIDATES))
    model.SiteCount = pyo.Expression(expr=sum(model.x[candidate_key] for candidate_key in model.CANDIDATES))
    model.MaximizeMW = pyo.Objective(expr=model.TotalMW, sense=pyo.maximize)

    return model, candidate_lookup, feeder_groups, substation_groups, substation_lookup, resolved_substations, unresolved_substations


def solve_lexicographic(model, solver):
    first_result = solver.solve(model, tee=False)
    termination = str(first_result.solver.termination_condition)

    if termination.lower() not in {"optimal", "locallyoptimal", "globallyoptimal"}:
        raise RuntimeError(f"MILP did not solve optimally. Termination condition: {termination}")

    optimum_mw = float(pyo.value(model.TotalMW))

    model.MWFloor = pyo.Constraint(expr=model.TotalMW >= max(optimum_mw - OPTIMALITY_TOLERANCE_MW, 0.0))
    model.MaximizeMW.deactivate()
    model.MinimizeSites = pyo.Objective(expr=model.SiteCount, sense=pyo.minimize)

    second_result = solver.solve(model, tee=False)
    second_termination = str(second_result.solver.termination_condition)

    if second_termination.lower() not in {"optimal", "locallyoptimal", "globallyoptimal"}:
        raise RuntimeError(f"Secondary MILP did not solve optimally. Termination condition: {second_termination}")

    return {
        "PrimaryTermination": termination,
        "SecondaryTermination": second_termination,
        "OptimalTotalMW": float(pyo.value(model.TotalMW)),
        "SelectedSiteCount": int(round(pyo.value(model.SiteCount)))
    }


def extract_solution(model, candidates, substation_capacities, scenario_name, max_sites, model_status):
    candidate_lookup = candidates.set_index("CandidateKey").to_dict("index")
    rows = []

    for candidate_key in model.CANDIDATES:
        selected = pyo.value(model.x[candidate_key])
        installed_mw = pyo.value(model.p[candidate_key])
        record = candidate_lookup[candidate_key]

        rows.append({
            "Scenario": scenario_name,
            "MaxSites": max_sites,
            "CandidateKey": candidate_key,
            "GridID": record["GridID"],
            "SubstationID": record["SubstationID"],
            "CandidateLineID": record["CandidateLineID"],
            "Selected": bool(selected >= 0.5),
            "InstalledDCMW": float(installed_mw),
            "FirmHostingCeilingMW": float(record[CAPACITY_COLUMN]),
            "UnusedFeederHostingMW": float(record[CAPACITY_COLUMN]) - float(installed_mw),
            "ModelStatus": model_status
        })

    solution = pd.DataFrame(rows)
    selected_solution = solution[solution["Selected"]].copy().sort_values("InstalledDCMW", ascending=False)

    substation_usage_rows = []
    capacity_lookup = substation_capacities.set_index("SubstationID").to_dict("index")

    for substation_id, group in solution.groupby("SubstationID"):
        installed = float(group["InstalledDCMW"].sum())
        record = capacity_lookup.get(substation_id, {})
        headroom = safe_float(record.get("AppliedAdditionalHeadroomMW"))
        resolved = pd.notna(headroom)

        substation_usage_rows.append({
            "Scenario": scenario_name,
            "MaxSites": max_sites,
            "SubstationID": substation_id,
            "InstalledDCMW": installed,
            "AppliedAdditionalHeadroomMW": headroom,
            "RemainingSubstationHeadroomMW": headroom - installed if resolved else np.nan,
            "AddedHeadroomUtilizationPct": installed / headroom * 100.0 if resolved and headroom > 0 else np.nan,
            "CapacityResolved": resolved,
            "CapacityMethod": record.get("CapacityMethod", "Unresolved"),
            "CapacitySource": record.get("CapacitySource", "Missing"),
            "ModelStatus": model_status
        })

    substation_usage = pd.DataFrame(substation_usage_rows)

    return solution, selected_solution, substation_usage


def classify_model_status(substation_capacities):
    resolved = int(substation_capacities["CapacityResolved"].sum())
    total = len(substation_capacities)

    if resolved == 0:
        return "FEEDER_CONSTRAINED_UPPER_BOUND"
    if resolved < total:
        return "PARTIAL_SUBSTATION_CONSTRAINTS"
    return "FULL_SUBSTATION_CONSTRAINED"


def run_scenario(candidates, substation_capacities, solver, scenario_name, max_sites=None):
    model_status = classify_model_status(substation_capacities)

    model, candidate_lookup, feeder_groups, substation_groups, substation_lookup, resolved_substations, unresolved_substations = build_model(
        candidates=candidates,
        substation_capacities=substation_capacities,
        max_sites=max_sites,
        unresolved_policy=UNRESOLVED_SUBSTATION_POLICY
    )

    solve_info = solve_lexicographic(model, solver)
    solution, selected_solution, substation_usage = extract_solution(
        model=model,
        candidates=candidates,
        substation_capacities=substation_capacities,
        scenario_name=scenario_name,
        max_sites=max_sites,
        model_status=model_status
    )

    scenario_summary = {
        "Scenario": scenario_name,
        "MaxSites": max_sites,
        "OptimalTotalMW": solve_info["OptimalTotalMW"],
        "SelectedSiteCount": solve_info["SelectedSiteCount"],
        "ResolvedSubstations": len(resolved_substations),
        "UnresolvedSubstations": len(unresolved_substations),
        "ModelStatus": model_status,
        "UnresolvedSubstationPolicy": UNRESOLVED_SUBSTATION_POLICY,
        "PrimaryTermination": solve_info["PrimaryTermination"],
        "SecondaryTermination": solve_info["SecondaryTermination"]
    }

    return scenario_summary, solution, selected_solution, substation_usage


def build_assumptions(candidates, substation_capacities, solver_name):
    return pd.DataFrame([
        {"Item": "Optimization purpose", "Value": "Maximize firm centralized data-center MW across thermally validated candidate sites."},
        {"Item": "Candidate capacity basis", "Value": CAPACITY_COLUMN},
        {"Item": "Site decision", "Value": "Binary x_j selects candidate j; continuous p_j is installed data-center MW."},
        {"Item": "Site thermal constraint", "Value": "0 <= p_j <= FirmHostingMW_j * x_j."},
        {"Item": "Feeder duplication rule", "Value": f"At most one selected candidate per feeder = {ONE_SITE_PER_FEEDER}."},
        {"Item": "Minimum selected site MW", "Value": MIN_SITE_MW},
        {"Item": "Maximum selected site MW", "Value": MAX_SITE_MW if MAX_SITE_MW is not None else "No additional cap beyond thermal hosting."},
        {"Item": "Substation constraint", "Value": "For resolved substations, sum of installed candidate MW behind the substation cannot exceed AppliedAdditionalHeadroomMW."},
        {"Item": "Substation capacity factor", "Value": SUBSTATION_CAPACITY_FACTOR},
        {"Item": "Missing substation capacity policy", "Value": UNRESOLVED_SUBSTATION_POLICY},
        {"Item": "Missing substation interpretation", "Value": "If allowed, candidates behind unresolved substations remain in the model and the result is explicitly labeled an upper bound rather than final portfolio capacity."},
        {"Item": "Objective", "Value": "Primary: maximize total installed MW. Secondary: among MW-optimal solutions, minimize selected site count."},
        {"Item": "Site-count frontier", "Value": f"Enabled = {RUN_SITE_COUNT_FRONTIER}. Solves maximum firm MW with 1 through N available sites."},
        {"Item": "Power factor", "Value": "FirmHostingMW comes from the validated PF 0.95 thermal model; PF 0.90/1.00 remain sensitivity cases, not the base MILP constraint."},
        {"Item": "Voltage/substation limitations", "Value": "MILP does not create missing voltage or substation physics. It only optimizes over validated thermal candidate ceilings plus any explicit substation headroom supplied."},
        {"Item": "Solver", "Value": solver_name},
        {"Item": "Candidate count", "Value": len(candidates)},
        {"Item": "Substation count", "Value": candidates["SubstationID"].nunique()},
        {"Item": "Resolved substation capacities", "Value": int(substation_capacities["CapacityResolved"].sum())}
    ])


def make_excel_safe(df):
    if df is None:
        return pd.DataFrame()

    df = df.copy()

    for column in df.columns:
        if isinstance(df[column].dtype, pd.DatetimeTZDtype):
            df[column] = df[column].dt.tz_localize(None)

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
        width = min(max(max_length, len(str(column_name))) + 2, 48)

        if pd.api.types.is_float_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, decimal_format)
        elif pd.api.types.is_integer_dtype(dataframe[column_name]):
            worksheet.set_column(column_index, column_index, width, integer_format)
        else:
            worksheet.set_column(column_index, column_index, width)

    worksheet.freeze_panes(1, 0)

    if not dataframe.empty:
        worksheet.autofilter(0, 0, len(dataframe), len(dataframe.columns) - 1)


def main():
    heading("GREENSBORO BASE DATA-CENTER MILP")

    candidates = load_candidates()
    substation_capacities = load_substation_capacities(candidates)
    solver_name, solver = choose_solver()
    model_status = classify_model_status(substation_capacities)

    print(f"Candidates admitted to MILP: {len(candidates)}")
    print(f"Feeders represented: {candidates['GridID'].nunique()}")
    print(f"Substations represented: {candidates['SubstationID'].nunique()}")
    print(f"Resolved substation capacities: {int(substation_capacities['CapacityResolved'].sum())}/{len(substation_capacities)}")
    print(f"Solver: {solver_name}")
    print(f"Current model status: {model_status}")

    if model_status == "FEEDER_CONSTRAINED_UPPER_BOUND":
        print("\nIMPORTANT: No explicit substation headroom values are populated.")
        print("The current MILP result is therefore a feeder-constrained upper bound, not final simultaneous Greensboro hosting capacity.")
        print(f"Populate {SUBSTATION_CAPACITY_FILE} and rerun the same script to activate shared-substation constraints.")

    heading("1. UNRESTRICTED SITE-COUNT SOLUTION")

    base_summary, base_solution, base_selected, base_substation_usage = run_scenario(
        candidates=candidates,
        substation_capacities=substation_capacities,
        solver=solver,
        scenario_name="Base_MaxFirmMW",
        max_sites=None
    )

    print(f"Optimal total firm DC MW: {base_summary['OptimalTotalMW']:.3f}")
    print(f"Selected sites: {base_summary['SelectedSiteCount']}")
    print("\nSelected locations:")
    print(base_selected[["GridID", "SubstationID", "CandidateLineID", "InstalledDCMW", "FirmHostingCeilingMW"]].to_string(index=False))

    scenario_summary_rows = [base_summary]
    all_solution_frames = [base_solution]
    selected_solution_frames = [base_selected]
    substation_usage_frames = [base_substation_usage]
    frontier_rows = []

    if RUN_SITE_COUNT_FRONTIER:
        heading("2. SITE-COUNT FRONTIER")

        for max_sites in range(1, len(candidates) + 1):
            scenario_name = f"Max_{max_sites}_Sites"
            summary, solution, selected_solution, substation_usage = run_scenario(
                candidates=candidates,
                substation_capacities=substation_capacities,
                solver=solver,
                scenario_name=scenario_name,
                max_sites=max_sites
            )

            scenario_summary_rows.append(summary)
            all_solution_frames.append(solution)
            selected_solution_frames.append(selected_solution)
            substation_usage_frames.append(substation_usage)

            frontier_rows.append({
                "MaxSites": max_sites,
                "OptimalFirmDCMW": summary["OptimalTotalMW"],
                "SelectedSiteCount": summary["SelectedSiteCount"],
                "IncrementalMWVsPreviousSiteLimit": np.nan
            })

            print(f"Max {max_sites:2d} site(s): {summary['OptimalTotalMW']:.3f} MW using {summary['SelectedSiteCount']} selected site(s)")

        frontier = pd.DataFrame(frontier_rows)
        frontier["IncrementalMWVsPreviousSiteLimit"] = frontier["OptimalFirmDCMW"].diff()
        if not frontier.empty:
            frontier.loc[frontier.index[0], "IncrementalMWVsPreviousSiteLimit"] = frontier.loc[frontier.index[0], "OptimalFirmDCMW"]
    else:
        frontier = pd.DataFrame()

    scenario_summary_df = pd.DataFrame(scenario_summary_rows)
    all_solutions_df = pd.concat(all_solution_frames, ignore_index=True)
    selected_solutions_df = pd.concat(selected_solution_frames, ignore_index=True)
    substation_usage_df = pd.concat(substation_usage_frames, ignore_index=True)

    candidate_input_columns = [
        column for column in [
            "CandidateKey", "GridID", "SubstationID", "CandidateLineID", "FirmHostingMW",
            "FirmHostingAtMinimumPFMW", "FirmHostingAtBasePFMW", "FirmHostingAtMaximumPFMW",
            "ReadyForFeederBoundMILP", "TopologyResolution", "MappingMaterialityStatus",
            "NoBaselineCandidatePathExceedance"
        ] if column in candidates.columns
    ]
    candidate_inputs = candidates[candidate_input_columns].copy()

    assumptions = build_assumptions(candidates, substation_capacities, solver_name)

    workbook_frames = {
        "Scenario_Summary": scenario_summary_df,
        "Base_Selected_Sites": base_selected,
        "Site_Count_Frontier": frontier,
        "Selected_All_Scenarios": selected_solutions_df,
        "All_Candidate_Solutions": all_solutions_df,
        "Substation_Usage": substation_usage_df,
        "Candidate_Inputs": candidate_inputs,
        "Substation_Capacities": substation_capacities,
        "Assumptions": assumptions
    }

    with pd.ExcelWriter(OUTPUT_FILE, engine="xlsxwriter") as writer:
        for sheet_name, frame in workbook_frames.items():
            safe_frame = make_excel_safe(frame)
            safe_frame.to_excel(writer, sheet_name=sheet_name, index=False)
            format_excel(writer, sheet_name, safe_frame)

    heading("MODEL COMPLETE")
    print(f"Results workbook saved to: {Path(OUTPUT_FILE).resolve()}")
    print(f"Substation input template: {Path(SUBSTATION_CAPACITY_FILE).resolve()}")

    if model_status == "FEEDER_CONSTRAINED_UPPER_BOUND":
        print("\nInterpretation:")
        print("The optimizer has solved the maximum simultaneous firm MW allowed by the validated feeder/site thermal ceilings only.")
        print("Because shared-substation capacities are unresolved, this is an upper bound and must not yet be reported as final system hosting capacity.")
    elif model_status == "PARTIAL_SUBSTATION_CONSTRAINTS":
        print("\nInterpretation:")
        print("Some shared-substation constraints are active, but unresolved substations remain unconstrained under the current policy.")
        print("The result is still an upper bound until every candidate substation has a resolved capacity.")
    else:
        print("\nInterpretation:")
        print("Every candidate substation has an explicit additional-capacity constraint, so the MILP is fully substation-constrained for the currently admitted candidate set.")

    print("\nNext modeling step after reviewing this output:")
    print("Expand the candidate pool while keeping the same MILP architecture, then add time-dependent battery/DR/flexible-compute variables and finally Monte Carlo stress testing.")


if __name__ == "__main__":
    main()
