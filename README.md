Greensboro Data Center Optimization Pipeline User Guide
Purpose
This repository contains the Greensboro data center optimization workflow. The pipeline screens candidate feeder locations, validates thermal hosting capacity, selects one centralized data center site, sizes battery and solar resilience resources, and validates the final battery design with Monte Carlo simulation.
Before Running
Place the repository files in one folder and run commands from that folder. A working Python installation is required.
The pipeline also expects a .env file with the Awesense EDM connection information and the existing greensboro_flag_resolution_audit.xlsx workbook. Do not commit real database credentials to a public repository.
Install the required Python packages:
python -m pip install -r requirements.txt
Run the Full Pipeline
To run the complete workflow from the first feeder analysis through the final visuals and map:
python -m run_pipeline
The runner executes each script in order and stops if a script returns an error. To restart from a later stage, use the --start-at option. For example:
python -m run_pipeline --start-at thermal_model.py
If restarting at a later stage, the output files created by the earlier stages must already be present.
Pipeline Scripts
hours_near_peakMW.py: Analyzes 2024 feeder loading for the shortlisted Greensboro feeders and creates the peak-duration workbook used by the location screen.
location_screen.py: Screens candidate line locations on the shortlisted feeders and estimates available hosting capacity.
location_rank_w_subs.py: Adds substation context and ranks the screened candidate locations. It creates greensboro_substation_constrained_location_rankings.xlsx, which is the input to the thermal model.
thermal_model.py: Runs the detailed five-minute 2024 thermal validation for the strongest candidate locations and creates the final thermal hosting results and interval-level hosting file.
substation_milp_constraint.py: Builds five-minute substation loading profiles and refreshes the substation capacity input file used by the optimization.
resource_flex.py: Builds the solar PV availability and EV flexibility profiles used by the optimization and resilience stages.
milp_final_data.py: Runs MILP 1. It selects one centralized data center location and determines the maximum modeled supportable load under the available constraints.
greensboro_milp2_battery_pv_resilience_optimized.py: Runs MILP 2. It holds the selected site and load fixed, sizes battery storage for outage service, and evaluates solar PV as a supplemental resilience resource.
mc_vF.py: Runs the Monte Carlo battery design search under uncertainty and identifies reliability-adjusted battery designs.
mc_validation.py: Performs the final large Monte Carlo validation of the selected reliability-adjusted battery designs.
res_vis.py: Creates the final resilience and Monte Carlo presentation visuals.
map.py: Creates the final map showing the evaluated candidate locations and selected Greensboro site.
Main Outputs
The main final result files are greensboro_final_integrated_milp_results.xlsx, greensboro_milp2_battery_pv_resilience_optimized_results.xlsx, the Monte Carlo design and validation workbooks produced by mc_vF.py and mc_validation.py, and the final visual files produced by res_vis.py and map.py.
If the Pipeline Stops
Read the last error shown in the terminal. The runner reports the script that failed, so that script can be corrected and the pipeline can then be restarted with --start-at using that script name. Do not rerun the entire pipeline unless the earlier inputs need to be regenerated.
