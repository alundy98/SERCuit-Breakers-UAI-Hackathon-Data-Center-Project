#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# - Demonstrate how to access time series data for meters and SCADAs from Awesense's Energy Data Model (EDM).
# 
# Please refer to the [main_concepts.ipynb](../2_main_concepts/main_concepts.ipynb) notebook for a high-level introduction to and simpler examples of the core views and functions available in Awesense's Energy Data Model (EDM).

# ---

# ## Set up

# In[1]:


import getpass
import plotly.express as px
import plotly.io as pio
import time
import urllib.parse

# Allow charts to persist between notebook sessions.
pio.renderers.default='notebook'


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


edm_address = getpass.getpass(prompt='EDM server address: ')

print('\nEDM login information')
edm_name = getpass.getpass(prompt='Username: ')
edm_password = getpass.getpass(prompt='Password: ')
edm_password = urllib.parse.quote(edm_password)

get_ipython().run_line_magic('load_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://$edm_name:$edm_password@$edm_address/edm')
get_ipython().run_line_magic('config', 'SqlMagic.displaycon = False')
get_ipython().run_line_magic('config', 'SqlMagic.feedback = False')

# Delete the credential variables for security purpose.
del edm_name, edm_password


# ---

# ## Time Series
# Various temporary views are created below to optimize query performance within the notebook.

# ### Meter Time Series

# *High level approach*
# - Create a temporary view `grid_element_metric`, containing information on meters for grid elements and convenient access to the actual data.
# - The `grid_get_downstream` function is used to gather information of meters that are downstream of a specified grid element.
# - Time series are retrieved using the `ts_data_source_select` function.

# **Grid Element Data**
# * Create a temporary view `grid_element_metric`. 

# In[3]:


get_ipython().run_cell_magic('sql', '', '    \nCREATE OR REPLACE TEMPORARY VIEW grid_element_metric AS\n    SELECT grid_id,\n            grid_element_id,\n            phases,\n            type,\n            provider,\n            direction,\n            friendly_id,\n            metric_key AS metric,\n            valid,\n            timestamp,\n            value\n    FROM grid_element_data_source geds\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    LEFT JOIN ts_data_source_select(grid_element_data_source_id, metric_key) AS ts\n        ON true;')


# **Downstream of a Grid** 
# - Specify `grid_id` and `grid_element_id` whose downstream meter data to fetch for.

# In[4]:


grid_id = input('Grid Id: ') # awefice
grid_element_id = input('Grid Element Id: ') # line_segment_57


# Check when this grid was last updated.

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT last_updated\nFROM grid\nWHERE grid_id = '{grid_id}';")


# - Create a temporary view `meter_data_source` to make it more convenient to access the data sources for the grid elements in the trace for the specified element.

# In[6]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW meter_data_source AS\n    SELECT meter.grid_id,\n            meter.grid_element_id,\n            geds.grid_element_data_source_id,\n            geds.friendly_id,\n            geds.provider,\n            metric_key as metric,\n            lower(geds.valid) as start_time,\n            upper(geds.valid) as end_time\n    FROM grid_get_downstream('{grid_id}', '{grid_element_id}') AS meter\n    LEFT JOIN grid_element_data_source geds\n        ON meter.grid_element_id = geds.grid_element_id\n        AND meter.grid_id = geds.grid_id\n        AND geds.type = 'CONSUMER'\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    WHERE meter.type = 'Meter';")


# **Consumption Data**
# - Create a temporary view `meter_consumption` with the meter readings.

# In[7]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW meter_consumption AS\nSELECT meter.grid_id,\n        meter.grid_element_id,\n        meter.friendly_id,\n        timestamp,\n        value AS kWh\nFROM meter_data_source meter\nLEFT JOIN grid_element_metric gem\n    ON gem.grid_id = meter.grid_id\n    AND gem.grid_element_id = meter.grid_element_id\nWHERE gem.metric = 'kWh'\n   AND gem.type = 'CONSUMER';")


# **Summary**
# - Return a high-level summary of the time series data.

# In[8]:


get_ipython().run_cell_magic('sql', '', "\nWITH ts_stats AS (\n    SELECT SUM(kWh) AS kWh, MIN(timestamp) AS start_timerange, MAX(timestamp) AS end_timerange\n    FROM meter_consumption\n)\nSELECT name, value FROM (\n    SELECT 1 AS idx, 'Meters Found' AS name, (SELECT COUNT(DISTINCT grid_element_id) FROM meter_data_source)::text AS value\n    UNION\n    SELECT 2, 'Meters w/ Datasources', (SELECT COUNT(DISTINCT grid_element_id) FROM meter_data_source WHERE grid_element_data_source_id IS NOT NULL)::text\n    UNION\n    SELECT 3, 'Common DS Timerange', (SELECT CONCAT(MAX(start_time), ' - ',  MIN(end_time)) FROM meter_data_source)::text\n    UNION\n    SELECT 4, 'Common Timeseries Timerange', (SELECT CONCAT(start_timerange, ' - ', end_timerange) FROM ts_stats)::text\n    UNION\n    SELECT 5, 'Total Consumption', (SELECT kwh FROM ts_stats)::text\n) x ORDER BY idx\n;")


# **Monthly Time Series**
# - Aggregate `meter_consumption` data by month and create a data frame `df_meter` for average monthly meter consumption.

# In[9]:


# Save to a Python variable first.
monthly_meter = get_ipython().run_line_magic('sql', "SELECT friendly_id,                             date_trunc('month', timestamp)::date AS month,                             AVG(kWh) AS kwh                         FROM meter_consumption                         GROUP BY friendly_id, month;")
                    
# Sort the data by date saved as `month`.
df_meter = monthly_meter.DataFrame().sort_values('month')


# **Visualization**
# - Visualize monthly total consumption for each `grid_element_data_source_id`.

# In[10]:


# Plot graph
px.line(df_meter, x='month', y='kwh', 
        title='Average Hourly Consumption by Meter',
        color='friendly_id')


# ---

# ### SCADA Time Series

# *High level approach*
# - Create a temporary view `grid_element_metric`, containing information on SCADAs for grid elements and convenient access to the actual data.
# - The `grid_get_sources` function is used for switches that are top feeders of a specified element.
# - Time series are retrieved using the `ts_source_select` function.

# **Grid Element Data**
# * Create a temporary view `grid_element_metric`.
# 
# *Please note that the below is the same view as the one from the **Meters Time Series** section. No need to re-run this `Grid Element Data` section if it was already done above.*

# In[11]:


get_ipython().run_cell_magic('sql', '', '\nCREATE OR REPLACE TEMPORARY VIEW grid_element_metric AS\n    SELECT grid_id,\n            grid_element_id,\n            phases,\n            type,\n            provider,\n            direction,\n            friendly_id,\n            metric_key AS metric,\n            valid,\n            timestamp,\n            value\n    FROM grid_element_data_source geds\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    LEFT JOIN ts_data_source_select(grid_element_data_source_id, metric_key) AS ts\n        ON true;')


# **Sources of a Grid** 
# - Fetch the SCADA data for the specified `grid_id` and `grid_element_id`.

# In[12]:


grid_id = input('Grid Id: ') # awefice
grid_element_id = input('Grid Element Id: ') # line_segment_57


# - Create a temporary view `scada_data_source` to make it more convenient to access the data sources for the source grid elements in the trace for the specified element.

# In[13]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW scada_data_source AS\n    SELECT scada.grid_element_id,\n            scada.grid_id,\n            geds.friendly_id,\n            geds.provider,\n            metric_key as metric,\n            geds.grid_element_data_source_id,\n            lower(geds.valid) as start_time,\n            upper(geds.valid) as end_time\n    FROM grid_get_sources('{grid_id}', '{grid_element_id}', 'true') AS scada\n        LEFT JOIN grid_element_data_source geds\n            ON scada.grid_element_id = geds.grid_element_id\n            AND scada.grid_id = geds.grid_id\n            AND geds.type = 'SENSOR'\n    JOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n        ON true\n    WHERE scada.type = 'CircuitBreaker';")


# **SCADA Time Series**
# - Create a temporary view `scada_time_series` with the SCADA readings.

# In[14]:


get_ipython().run_cell_magic('sql', '', "\nCREATE OR REPLACE TEMPORARY VIEW scada_time_series AS\nSELECT gem.type, \n        gem.grid_id,\n        gem.grid_element_id ,\n        scada.friendly_id,\n        timestamp,\n        value AS kWh\nFROM scada_data_source scada\nLEFT JOIN grid_element_metric gem\n    ON gem.grid_id = scada.grid_id\n    AND gem.grid_element_id = scada.grid_element_id\nWHERE gem.metric = 'kWh'\n   AND gem.type = 'SENSOR';")


# **Summary**
# - Return a high-level summary of the time series data.

# In[15]:


get_ipython().run_cell_magic('sql', '', "\nWITH ts_stats AS (\n    SELECT SUM(kWh) AS kWh, MIN(timestamp) AS start_timerange, MAX(timestamp) AS end_timerange\n    FROM scada_time_series\n)\nSELECT name, value FROM (\n    SELECT 1 AS idx, 'SCADAs Found' AS name, (SELECT COUNT(DISTINCT grid_element_id) FROM scada_data_source)::text AS value\n    UNION\n    SELECT 2, 'SCADAs w/ Datasources', (SELECT COUNT(DISTINCT grid_element_id) FROM scada_data_source WHERE grid_element_data_source_id IS NOT NULL)::text\n    UNION\n    SELECT 3, 'Common DS Timerange', (SELECT CONCAT(MAX(start_time), MIN(end_time)) FROM scada_data_source)::text\n    UNION\n    SELECT 4, 'Common Timeseries Timerange', (SELECT CONCAT(start_timerange, ' - ', end_timerange) FROM ts_stats)::text\n    UNION\n    SELECT 5, 'Total Distribution', (SELECT kWh FROM ts_stats)::text\n) x\nORDER BY idx")


# **Monthly Time Series**
# - Aggregate `scada_time_series` data by month and create a data frame `df_scada` for average monthly SCADA distribution.

# In[16]:


# Save to a Python variable first.
monthly_scada = get_ipython().run_line_magic('sql', "SELECT friendly_id,                             date_trunc('month', timestamp)::date AS month,                             AVG(kWh) AS kwh                         FROM scada_time_series                         GROUP BY friendly_id, month;")
                    
# Sort the data by date saved as `month`.
df_scada = monthly_scada.DataFrame().sort_values('month')


# **Visualization**
# - Visualize monthly total distribution for each SCADA.

# In[17]:


# Plot graph
px.line(df_scada, x='month', y='kwh', 
        title='Average Hourly Distribution by SCADA',
        color='friendly_id')


# ---
