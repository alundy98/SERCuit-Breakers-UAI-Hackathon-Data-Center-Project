#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook allows users to generate a histogram from time series data associated with a specific electric meter (grid element of type Meter), based on a selected metric and time range. The data is retrieved from the Awesense Energy Data Model (EDM) via the ts_data_source_select function.
# 
# The user specifies:
# * Grid ID
# * Meter ID
# * Time range (start and end)
# * Metric (e.g., 'V', 'kWh')
# 
# The notebook:
# * Resolves the UUID of the associated data source
# * Checks available metrics
# * Retrieves and filters data in the requested interval
# * Displays a histogram of the selected metric

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import plotly.express as px


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com.
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

# #### Input Parameters

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
grid_element_id = None  # Target meter ID (e.g., 'm_1')
start_time = None  # Start time (e.g., 2024-06-01T00:00:00)
end_time = None  # End time (e.g., 2024-06-30T00:00:00)
desired_metric = None  # Target matrics (e.g., 'kWh')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice
grid_element_id = (
    grid_element_id or input("Enter meter ID (e.g. 'm_1'): ").strip()
)  # m_1
start_time = (
    start_time or input("Enter start timestamp (e.g. 2024-06-01T00:00:00): ").strip()
)  # 2024-06-01T00:00:00
end_time = (
    end_time or input("Enter end timestamp (e.g. 2024-06-30T23:59:59): ").strip()
)  # 2024-06-30T23:59:59
desired_metric = (
    desired_metric or input("Enter desired_metric (e.g. 'kWh'): ").strip()
)  # Change to 'V', 'active_power', etc. as needed


# #### SQL Queries

# In[ ]:


# Query data sources for the selected meter
ds_query = f"""
SELECT grid_element_data_source_id, metrics, valid
FROM grid_element_data_source
WHERE grid_element_id = :grid_element_id AND grid_id = :grid_id;
"""
ds_result = get_ipython().run_line_magic('sql', '{ds_query}')
ds_df = ds_result.DataFrame()

# Match metric to the correct UUID
matching = ds_df[ds_df['metrics'].apply(lambda m: desired_metric in m)]

if matching.empty:
    print(f"No data source with metric '{desired_metric}' found for meter '{grid_element_id}'.")
else:
    data_source_id = matching['grid_element_data_source_id'].iloc[0]
    validity = matching['valid'].iloc[0]
    print(f"Data source UUID: {data_source_id}")
    print(f"Validity range: {validity}")

    # Query time-series data
    ts_query = f"""
    SELECT value
    FROM ts_data_source_select(:data_source_id, :desired_metric, tstzrange(:start_time, :end_time, '[]'));
    """
    ts_result = get_ipython().run_line_magic('sql', '{ts_query}')
    ts_df = ts_result.DataFrame()

    # Plot histogram using Plotly
    if ts_df.empty:
        print("No time-series data available for the specified time range.")
    else:
        values = ts_df['value'].dropna().to_frame(name=desired_metric)


        fig = px.histogram(
            values,
            nbins=20,
            title=f'Histogram of {desired_metric} values<br>Meter: {grid_element_id} | {start_time[:10]} to {end_time[:10]}',
            labels={'value': desired_metric},
            opacity=0.75
        )
        fig.update_layout(
            xaxis_title=desired_metric,
            yaxis_title='Frequency',
            bargap=0.05,
            template='plotly_white'
        )
        fig.show()


# ---
