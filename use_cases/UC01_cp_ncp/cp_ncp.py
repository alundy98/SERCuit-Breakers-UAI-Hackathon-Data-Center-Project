#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * analyze Coincident Peak (CP) and Non-Coincident Peak (NCP) throughout the distribution area and various customer segments using Awesense's Energy Data Model (EDM). In particular, this notebook will cover examples of this analysis on:
#     * entire grid
#     * feeder level topology
#     * tariff group
# 
# Insights originating from this type of analysis are essential for regulatory reporting, and allow utilities to understand consumer behaviour and improve rate design to increase the satisfaction and retention of consumers. 
# 
# For a comprehensive list of topological, hierarchical, geographical and customer segments that can be used for the CP & NCP analysis using the Awesense Platform, please refer to the [UC01 - Coincident Peak and Non-Coincident Peak Analysis](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC01%20-%20Coincident%20Peak%20and%20Non-Coincident%20Peak%20Analysis.pdf) document.

# ---

# ## Setup

# In[ ]:


import getpass
import pandas as pd
import plotly.express as px
import os
from dotenv import load_dotenv
import psycopg2
import datetime
import pytz


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[ ]:


# Checks for Google Colab: If detected,
# it bypasses reading credentials from local files and securely draws from Colab Secrets.
try:
    from google.colab import userdata
    from google.colab.userdata import SecretNotFoundError
    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    from contextlib import suppress
    print('☁️ Running in Google Colab. Using Colab Secrets for SQLAPI connection')
    # Look for Colab Secrets, fall back to interactive prompts if missing
    with suppress(SecretNotFoundError): os.environ['EDM_HOST'] = userdata.get('EDM_HOST')
    with suppress(SecretNotFoundError): os.environ['EDM_USER'] = userdata.get('EDM_USER')
    with suppress(SecretNotFoundError): os.environ['EDM_PASSWORD'] = userdata.get('EDM_PASSWORD')
else:
    # If running locally, loads connection parameters from a `.env` file (in this directory or parent directories)
    load_dotenv()

# Prompt the user to manually enter any missing connection parameters not found in `.env` file / Google Colab Secrets
if 'EDM_HOST' not in os.environ: os.environ['EDM_HOST'] = input('EDM server address: ').strip()
if 'EDM_USER' not in os.environ: os.environ['EDM_USER'] = input('EDM username: ')

# psycopg2/libpq automatically read the PG* environment variables, so map the shared EDM_* names onto them.
# (The password is only mapped if provided via `.env`/Colab; otherwise libpq falls back to `~/.pgpass`.)
os.environ['PGHOST'] = os.environ['EDM_HOST']
os.environ['PGUSER'] = os.environ['EDM_USER']
os.environ.setdefault('PGDATABASE', 'edm')
if 'EDM_PASSWORD' in os.environ: os.environ['PGPASSWORD'] = os.environ['EDM_PASSWORD']

print('Verifying SQLAPI connection parameters/credentials with connection attempt')

try:
    # Test database connection
    conn = psycopg2.connect('')
    conn.close()
    print('✅ SQLAPI connection parameters/credentials verified')
except psycopg2.OperationalError:
    print('⚠️ SQLAPI credentials check failed (assume missing/incorrect password, but double-check `.env` / secrets!)')
    # Prompt user securely for password
    os.environ['PGPASSWORD'] = getpass.getpass('Enter EDM Password manually: ')

# Keeps `%sql` result tables rendering correctly on newer versions of prettytable
import prettytable
if 'DEFAULT' not in vars(prettytable): prettytable.DEFAULT = prettytable.TableStyle.DEFAULT

# Load the SQL extension and connect
get_ipython().run_line_magic('reload_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://')


# **Custom Functions**

# In[ ]:


def plot_line(df, df_agg, title, value):
    """
    Compute and plot a line chart for hourly aggregated load `df_agg`.
    """

    # Plot a line chart.
    fig = px.line(
        df_agg,
        x=df_agg.index,
        y="value",
        title=title,
        labels={"value": value, "timestamp": "Timestamp"},
    )

    # Format the chart - black line and manually fix y-axis to be from 0 to 105% of max y value.
    fig.update_traces(line_color="black")
    fig.update_yaxes(range=[0, df_agg["value"].max() * 1.05])

    fig.show()

    return None


def plot_line_bar(df, df_agg, df_cp, title):
    """
    Plot a stacked bar chart for meter load at the maximum system load timestamp `df_cp`
        using the meter-level raw data `df` for full time range indices,
        and add a line chart of the hourly aggregated load `df_agg`.
    """

    # Left join `df` with `df_cp` to fill in timestamps outside of the max system load with Nulls.
    #  This is needed to overlay two charts in one figure properly by sharing the same x-axis range.
    df_full = df.merge(df_cp, how="left", on=["timestamp", "grid_element_id"]).rename(
        columns={"value_y": "value_cp"}
    )

    # Order the resulting dataframe to display stacked bar properly.
    df_full = df_full.sort_values("value_cp", ascending=False)

    # Plot a stacked bar chart first.
    fig = px.bar(
        df_full,
        x="timestamp",
        y="value_cp",
        barmode="stack",
        title=title,
        color="grid_element_id",
        text_auto=True,
        labels={"value_cp": "Load (kW)", "timestamp": "Timestamp"},
    )

    # Make the stacked bar chart text labels to show horizontally only.
    fig.update_traces(textangle=0)

    # Overlay a line chart using the `add_scatter()` for the hourly system aggregated loads.
    fig.add_scatter(x=df_agg.index, y=df_agg["value"], line_color="black")

    fig.show()

    return None


def plot_pie(df_cp, title):
    """
    Plot a pie chart of load breakdown at Coincident Peak (CP) `df_cp`.
    """

    # Plot a pie chart.
    fig = px.pie(df_cp, values="value", names="grid_element_id", title=title)

    # Make the chart text to be inside the pie.
    #  Otherwise, the chart looks messy when there are many breakdowns.
    fig.update_traces(textposition="inside", textinfo="percent+label")
    fig.show()

    return None


def map_it(df, meters, title):
    """
    Plot a map of meters using custom Awesense tiles.
    """

    # Convert the SQL result set to dataframe, and cast the longitude and latitude to floats.
    df_geo = meters.DataFrame().astype({"longitude": float, "latitude": float})

    # Add the geo-location information to the main dataframe.
    df_geo = df.merge(df_geo, on="grid_element_id")

    # Plot a map.
    fig = px.scatter_map(
        df_geo,
        lat="latitude",
        lon="longitude",
        hover_name="grid_element_id",
        title=title,
        color="value",
        size="value",
        zoom=15,
        labels={"value": "Load (kW)"},
    )

    # Use custom (raster) tile server
    fig.update_layout(
        map_style="white-bg",
        map_layers=[
            {
                "below": "traces",
                "sourcetype": "raster",
                "source": [
                    "https://d.tile.awesense.com/{z}/{x}/{y}.png",
                    "https://e.tile.awesense.com/{z}/{x}/{y}.png",
                    "https://f.tile.awesense.com/{z}/{x}/{y}.png",
                ],
            }
        ],
        margin={"r": 0, "t": 60, "l": 40, "b": 0},
    )

    fig.show()

    return None


def compute_ncp(df):
    """
    Return a dataframe of Non-Coincident Peak and the corresponding timestamp for each grid element.
    """

    # Calculate the max meter load regardless regardless of time.
    df_ncp = df.groupby("grid_element_id").max(numeric_only=True).reset_index()

    # Find timestamps of NCP values by merging `df` and `df_ncp`.
    df_ncp = df.merge(df_ncp, on=["grid_element_id", "value"])

    # If NCP values occur multiple times, then take the first occurrence.
    df_ncp = df_ncp.groupby("grid_element_id").first().reset_index()

    return df_ncp


def plot_scatter(df_ncp, title):
    """
    Plot a scatter chart of meter loads, with their colors and marker sizes dictated by the field called "value".
    """

    # Plot a scatter chart.
    fig = px.scatter(
        df_ncp,
        x="timestamp",
        y="value",
        color="value",
        size="value",
        title=title,
        hover_name="grid_element_id",
        labels={"value": "Load (kW)", "timestamp": "Timestamp"},
    )

    fig.show()

    return None


# ---

# #### Input Parameters

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
feeder_id = None  # Downstream feeder transformer name (e.g., 'transformer_6')
tariff_id = None  # Customer billing rate group (e.g., 'res_basic')
start = None  # Time series range start date (YYYY-MM-DD HH:MM:SS)
end = None  # Time series range end date (YYYY-MM-DD HH:MM:SS)


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice

# Define the grid_id time zone by matching on whether the grid_id contains a known substring.
time_zones = {
    "awefice": "America/Vancouver",
    "SAF": "America/Denver",
    "GSO": "America/New_York",
}
time_zone = next((tz for name, tz in time_zones.items() if name in grid_id), None)
if time_zone is None:
    raise ValueError(f"No time zone mapping found for grid_id '{grid_id}'.")

start = (
    start or input("Enter start date (YYYY-MM-DD HH:MM:SS): ").strip()
)  # 2022-03-01 00:00:00
end = (
    end or input("Enter end date (YYYY-MM-DD HH:MM:SS): ").strip()
)  # 2022-04-01 00:00:00

date_format = "%Y-%m-%d %H:%M:%S"
local_timezone = pytz.timezone(time_zone)
start_tz = local_timezone.localize(datetime.datetime.strptime(start, date_format))
end_tz = local_timezone.localize(datetime.datetime.strptime(end, date_format))

timerange = f"{start_tz} ~ {end_tz}"  # time range label for chart titles


# ## System

# #### Data: Hourly Consumption Load Time Series 

# In[ ]:


get_ipython().run_cell_magic('sql', 'result_system <<', "\nSELECT tdss.timestamp at time zone :time_zone as timestamp,\n        ge.grid_element_id,\n        tdss.value\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_id = ge.grid_id\n    AND geds.grid_element_id = ge.grid_element_id\nJOIN ts_data_source_select(\n        geds.grid_element_data_source_id, 'kWh', tstzrange(:start_tz, :end_tz)\n    ) tdss ON TRUE\nWHERE geds.grid_id = :grid_id\n    AND ge.type = 'Meter'\n    AND geds.type = 'CONSUMER'\nORDER BY tdss.timestamp;\n")


# #### Visualization

# In[ ]:


# Convert the SQL result to Python dataframe.
df_system = result_system.DataFrame()

# Aggregate loads for each hour.
df_system_agg = df_system.groupby("timestamp").sum(numeric_only=True)

# Plot the system time series.
plot_line(
    df_system,
    df_system_agg,
    title="Hourly System Load Time Series",
    value="Hourly System Load (kW)",
)


# In[ ]:


# Find the max load and the corresponding timestamp.
system_max_load = df_system_agg["value"].max()
system_max_time = df_system_agg["value"].idxmax()

print(
    "System peak load of {} kW occurring at {}.".format(
        system_max_load, system_max_time
    )
)


# ---
# ## Entire Grid

# ### Coincident Peak (CP)

# In[ ]:


# Filter the raw data `df_system` to the given system max load timestamp for Coincident Peak (CP).
df_grid_cp = df_system.loc[df_system["timestamp"] == system_max_time].copy()

# Plot the system load and grid load breakdown.
plot_line_bar(
    df_system,
    df_system_agg,
    df_grid_cp,
    title="Hourly System Load & Grid Load Breakdown at {}".format(system_max_time),
)


# In[ ]:


# Plot the breakdown of the grid load at system peak.
plot_pie(df_grid_cp, "Breakdown of Grid Load at System Peak {}".format(system_max_time))


# In[ ]:


# Find the max load and the corresponding meter.
grid_max_load = df_grid_cp["value"].max()
grid_max_user = df_grid_cp.set_index("grid_element_id")["value"].idxmax()

print(
    "Meter {} with the largest contribution to CP at {} kW.".format(
        grid_max_user, grid_max_load
    )
)


# #### Map View

# In[ ]:


# Prepare a string of meters to use in SQL.
meters = "','".join(df_grid_cp["grid_element_id"].unique())


# #### Data: Geo-locations

# In[ ]:


get_ipython().run_cell_magic('sql', 'grid_meters <<', "\nSELECT grid_element_id,\n    ST_Y(geometry) as latitude,\n    ST_X(geometry) as longitude\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND grid_element_id IN ('{meters}')\n")


# In[ ]:


# Plot the meters on a map.
map_it(
    df_grid_cp,
    grid_meters,
    title="Map View of Meter Load at System Peak {}".format(system_max_time),
)


# ### Non-Coincident Peak

# In[ ]:


# Compute NCP for each meter.
df_grid_ncp = compute_ncp(df_system)

# Plot NCP meter loads.
plot_scatter(df_grid_ncp, title="Max Load for Each Meter Between {}".format(timerange))


# In[ ]:


# Table view of the NCP meter data.
df_grid_ncp.sort_values("timestamp").set_index("timestamp")


# ---
# ## Feeder Level Topology

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
feeder_id = feeder_id or input("Enter feeder ID: ").strip()  # transformer_6


# #### Data: Downstream Meters & Geo-locations

# In[ ]:


get_ipython().run_cell_magic('sql', 'feeder_meters <<', "SELECT ggd.grid_element_id, ge.geometry, ge.type,\n    ST_Y(ge.geometry) as latitude,\n    ST_X(ge.geometry) as longitude\nFROM grid_get_downstream(:grid_id, :feeder_id) ggd\nJOIN grid_element ge\n    ON ge.grid_id = ggd.grid_id\n    AND ge.grid_element_id = ggd.grid_element_id\nWHERE ge.grid_element_id NOT LIKE ('line_segment%')\n    AND ge.grid_element_id NOT LIKE ('busbar%');\n")


# In[ ]:


# Convert the sql resultset from above to list.
feeder_meters_ls = feeder_meters.DataFrame()["grid_element_id"].to_list()

# Filter the system load time series data to applicable meters only.
df_feeder = df_system.loc[df_system["grid_element_id"].isin(feeder_meters_ls), :]


# ### Coincident Peak (CP)

# In[ ]:


# Filter the raw data `df_feeder` to the given feeder max load timestamp for Coincident Peak (CP).
df_feeder_cp = df_feeder.loc[df_feeder["timestamp"] == system_max_time].copy()

# Plot the system load and feeder load breakdown.
plot_line_bar(
    df_feeder,
    df_system_agg,
    df_feeder_cp,
    title="Hourly System Load & {} Load Breakdown at {}".format(
        feeder_id, system_max_time
    ),
)


# In[ ]:


# Plot the breakdown of the feeder load at the system peak.
plot_pie(
    df_feeder_cp,
    title="Breakdown of {} Load at System Peak {}".format(feeder_id, system_max_time),
)


# In[ ]:


# Find the max load and the corresponding meter.
feeder_max_load = df_feeder_cp["value"].max()
feeder_max_user = df_feeder_cp.set_index("grid_element_id")["value"].idxmax()

print(
    "Meter {} with the largest contribution to CP at {} kW.".format(
        feeder_max_user, feeder_max_load
    )
)


# #### Map View

# In[ ]:


# Plot the meters on map.
map_it(
    df_feeder_cp,
    feeder_meters,
    title="Map View of {} Downstream Meter Load at {}".format(
        feeder_id, system_max_time
    ),
)


# ### Non-Coincident Peak (NCP)

# In[ ]:


# Compute NCP for each meter.
df_feeder_ncp = compute_ncp(df_feeder)

# Plot NCP meter loads.
plot_scatter(
    df_feeder_ncp,
    title="Max Meter Load Downstream of {} Between {}".format(feeder_id, timerange),
)


# In[ ]:


# Table view of the NCP meter data.
df_feeder_ncp.sort_values("timestamp").set_index("timestamp")


# ---
# ## Customer Tariff Groups

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
tariff_id = tariff_id or input("Enter tariff ID: ").strip()  # res_basic


# #### Data: Tariff Group Meters & Geo-locations

# In[ ]:


get_ipython().run_cell_magic('sql', 'tariff_meters <<', "\nSELECT grid_element_id,\n    ST_Y(geometry) as latitude,\n    ST_X(geometry) as longitude\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\n    AND meta->>'tariff_id' = :tariff_id;\n")


# In[ ]:


# Convert the sql resultset from above to list.
tariff_meters_ls = tariff_meters.DataFrame()["grid_element_id"].to_list()

# Filter the system load time series data to applicable meters only.
df_tariff = df_system.loc[df_system["grid_element_id"].isin(tariff_meters_ls), :]


# ### Coincident Peak

# In[ ]:


# Filter the raw data `df_tariff` to the given tariff max load timestamp for Coincident Peak (CP).
df_tariff_cp = df_tariff.loc[df_tariff["timestamp"] == system_max_time].copy()

# Plot the system load and tariff group load breakdown.
plot_line_bar(
    df_tariff,
    df_system_agg,
    df_tariff_cp,
    title="Hourly System Load & {} Load Breakdown at {}".format(
        tariff_id, system_max_time
    ),
)


# In[ ]:


# Plot the breakdown of the tariff group load.
plot_pie(
    df_tariff_cp, title="Breakdown of {} Load at {}".format(feeder_id, system_max_time)
)


# In[ ]:


# Find the max load and the corresponding meter.
tariff_max_load = df_tariff_cp["value"].max()
tariff_max_user = df_tariff_cp.set_index("grid_element_id")["value"].idxmax()

print(
    "Meter {} with the largest contribution to CP at {} kW.".format(
        tariff_max_user, tariff_max_load
    )
)


# #### Map View

# In[ ]:


# Plot the meters on map.
map_it(
    df_tariff_cp,
    tariff_meters,
    title="Map View of {} Meter Load at {}".format(tariff_id, system_max_time),
)


# ### Non-Coincident Peak

# In[ ]:


# Compute NCP for each meter.
df_tariff_ncp = compute_ncp(df_tariff)

# Plot NCP meter loads.
plot_scatter(
    df_tariff_ncp,
    title="Max Meter Load for Tariff {} Between {}".format(tariff_id, timerange),
)


# In[ ]:


# Table view of the NCP meter data.
df_tariff_ncp.sort_values("timestamp").set_index("timestamp")


# ---
