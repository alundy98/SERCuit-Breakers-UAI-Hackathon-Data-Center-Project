#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# - Demonstrate how to access time series data for meters and SCADAs using Awesense's Energy Data Model (EDM) SQL API.
# 
# Please refer to the [main_concepts.ipynb](../2_main_concepts/main_concepts.ipynb) notebook for a high-level introduction to and simpler examples of the core views and functions available in Awesense's Energy Data Model (EDM).

# ---

# ## Set up

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import plotly.express as px
import time


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


# ---

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
grid_element_id = None  # Target element ID (e.g., 'line_segment_57')


# ## Time Series
# Various temporary views are created below to optimize query performance within the notebook.

# ### Meter Time Series

# *High level approach*
# - Create a temporary view `grid_element_metric`, containing information on meters for grid elements and convenient access to the actual data.
# - The `grid_get_downstream()` function is used to gather information of meters that are downstream of a specified grid element.
# - Time series are retrieved using the `ts_data_source_select()` function.

# **Grid Element Data**
# * Create a temporary view `grid_element_metric`. 

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nCREATE OR REPLACE TEMPORARY VIEW grid_element_metric AS\n    SELECT grid_id,\n            grid_element_id,\n            phases,\n            type,\n            provider,\n            direction,\n            friendly_id,\n            metric_key AS metric,\n            valid,\n            timestamp,\n            value\n    FROM grid_element_data_source geds\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    LEFT JOIN ts_data_source_select(grid_element_data_source_id, metric_key) AS ts\n        ON true;\n')


# **Downstream of a Grid** 
# - Specify `grid_id` and `grid_element_id` whose downstream meter data to fetch for.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice
grid_element_id = (
    grid_element_id or input("Grid Element Id: ").strip()
)  # line_segment_57


# Check when this grid was last updated.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT last_updated\nFROM grid\nWHERE grid_id = :grid_id;\n')


# - Create a temporary view `meter_data_source` to make it more convenient to access the data sources for the grid elements in the trace for the specified element.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW meter_data_source AS\n    SELECT meter.grid_id,\n            meter.grid_element_id,\n            geds.grid_element_data_source_id,\n            geds.friendly_id,\n            geds.provider,\n            metric_key as metric,\n            lower(geds.valid) as start_time,\n            upper(geds.valid) as end_time\n    FROM grid_get_downstream(:grid_id, :grid_element_id) AS meter\n    LEFT JOIN grid_element_data_source geds\n        ON meter.grid_element_id = geds.grid_element_id\n        AND meter.grid_id = geds.grid_id\n        AND geds.type = 'CONSUMER'\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    WHERE meter.type = 'Meter';\n")


# **Consumption Data**
# - Create a temporary view `meter_consumption` with the meter readings.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW meter_consumption AS\nSELECT meter.grid_id,\n        meter.grid_element_id,\n        meter.friendly_id,\n        timestamp,\n        value AS kWh\nFROM meter_data_source meter\nLEFT JOIN grid_element_metric gem\n    ON gem.grid_id = meter.grid_id\n    AND gem.grid_element_id = meter.grid_element_id\nWHERE gem.metric = 'kWh'\n   AND gem.type = 'CONSUMER';\n")


# **Summary**
# - Return a high-level summary of the time series data.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nWITH ts_stats AS (\n    SELECT SUM(kWh) AS kWh, MIN(timestamp) AS start_timerange, MAX(timestamp) AS end_timerange\n    FROM meter_consumption\n)\nSELECT name, value FROM (\n    SELECT 1 AS idx, 'Meters Found' AS name, (SELECT COUNT(DISTINCT grid_element_id) FROM meter_data_source)::text AS value\n    UNION\n    SELECT 2, 'Meters w/ Datasources', (SELECT COUNT(DISTINCT grid_element_id) FROM meter_data_source WHERE grid_element_data_source_id IS NOT NULL)::text\n    UNION\n    SELECT 3, 'Common DS Timerange', (SELECT CONCAT(MAX(start_time), ' - ',  MIN(end_time)) FROM meter_data_source)::text\n    UNION\n    SELECT 4, 'Common Timeseries Timerange', (SELECT CONCAT(start_timerange, ' - ', end_timerange) FROM ts_stats)::text\n    UNION\n    SELECT 5, 'Total Consumption', (SELECT kwh FROM ts_stats)::text\n) x ORDER BY idx\n;\n")


# **Monthly Time Series**
# - Aggregate `meter_consumption` data by month and create a data frame `df_meter` for average monthly meter consumption.

# In[ ]:


# Save to a Python variable first.
monthly_meter = get_ipython().run_line_magic('sql', "SELECT friendly_id,                              date_trunc('month', timestamp)::date AS month,                              AVG(kWh) AS kwh                          FROM meter_consumption                          GROUP BY friendly_id, month;")

# Sort the data by date saved as `month`.
df_meter = monthly_meter.DataFrame().sort_values('month')


# **Visualization**
# - Visualize monthly total consumption for each `grid_element_data_source_id`.

# In[ ]:


# Plot graph
px.line(
    df_meter,
    x="month",
    y="kwh",
    title="Average Hourly Consumption by Meter",
    color="friendly_id",
)


# ---

# ### SCADA Time Series

# *High level approach*
# - Create a temporary view `grid_element_metric`, containing information on SCADAs for grid elements and convenient access to the actual data.
# - The `grid_get_sources()` function is used for switches that are top feeders of a specified element.
# - Time series are retrieved using the `ts_data_source_select()` function.

# **Grid Element Data**
# * Create a temporary view `grid_element_metric`.
# 
# *Please note that the below is the same view as the one from the **Meters Time Series** section. No need to re-run this `Grid Element Data` section if it was already done above.*

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nCREATE OR REPLACE TEMPORARY VIEW grid_element_metric AS\n    SELECT grid_id,\n            grid_element_id,\n            phases,\n            type,\n            provider,\n            direction,\n            friendly_id,\n            metric_key AS metric,\n            valid,\n            timestamp,\n            value\n    FROM grid_element_data_source geds\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    LEFT JOIN ts_data_source_select(grid_element_data_source_id, metric_key) AS ts\n        ON true;\n')


# **Sources of a Grid** 
# - Fetch the SCADA data for the specified `grid_id` and `grid_element_id`.

# - Create a temporary view `scada_data_source` to make it more convenient to access the data sources for the source grid elements in the trace for the specified element.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW scada_data_source AS\n    SELECT scada.grid_element_id,\n            scada.grid_id,\n            geds.friendly_id,\n            geds.provider,\n            metric_key as metric,\n            geds.grid_element_data_source_id,\n            lower(geds.valid) as start_time,\n            upper(geds.valid) as end_time\n    FROM grid_get_sources(:grid_id, :grid_element_id, 'true') AS scada\n        LEFT JOIN grid_element_data_source geds\n            ON scada.grid_element_id = geds.grid_element_id\n            AND scada.grid_id = geds.grid_id\n            AND geds.type = 'SENSOR'\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    WHERE scada.type = 'CircuitBreaker';\n")


# **SCADA Time Series**
# - Create a temporary view `scada_time_series` with the SCADA readings.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW scada_time_series AS\nSELECT gem.type,\n        gem.grid_id,\n        gem.grid_element_id ,\n        scada.friendly_id,\n        timestamp,\n        value AS kWh\nFROM scada_data_source scada\nLEFT JOIN grid_element_metric gem\n    ON gem.grid_id = scada.grid_id\n    AND gem.grid_element_id = scada.grid_element_id\nWHERE gem.metric = 'kWh'\n   AND gem.type = 'SENSOR';\n")


# **Summary**
# - Return a high-level summary of the time series data.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nWITH ts_stats AS (\n    SELECT SUM(kWh) AS kWh, MIN(timestamp) AS start_timerange, MAX(timestamp) AS end_timerange\n    FROM scada_time_series\n)\nSELECT name, value FROM (\n    SELECT 1 AS idx, 'SCADAs Found' AS name, (SELECT COUNT(DISTINCT grid_element_id) FROM scada_data_source)::text AS value\n    UNION\n    SELECT 2, 'SCADAs w/ Datasources', (SELECT COUNT(DISTINCT grid_element_id) FROM scada_data_source WHERE grid_element_data_source_id IS NOT NULL)::text\n    UNION\n    SELECT 3, 'Common DS Timerange', (SELECT CONCAT(MAX(start_time), MIN(end_time)) FROM scada_data_source)::text\n    UNION\n    SELECT 4, 'Common Timeseries Timerange', (SELECT CONCAT(start_timerange, ' - ', end_timerange) FROM ts_stats)::text\n    UNION\n    SELECT 5, 'Total Distribution', (SELECT kWh FROM ts_stats)::text\n) x\nORDER BY idx\n")


# **Monthly Time Series**
# - Aggregate `scada_time_series` data by month and create a data frame `df_scada` for average monthly SCADA distribution.

# In[ ]:


# Save to a Python variable first.
monthly_scada = get_ipython().run_line_magic('sql', "SELECT friendly_id,                              date_trunc('month', timestamp)::date AS month,                              AVG(kWh) AS kwh                          FROM scada_time_series                          GROUP BY friendly_id, month;")

# Sort the data by date saved as `month`.
df_scada = monthly_scada.DataFrame().sort_values('month')


# **Visualization**
# - Visualize monthly total distribution for each SCADA.

# In[ ]:


# Plot graph
px.line(
    df_scada,
    x="month",
    y="kwh",
    title="Average Hourly Distribution by SCADA",
    color="friendly_id",
)


# ---
