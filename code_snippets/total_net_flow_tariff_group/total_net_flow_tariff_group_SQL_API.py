#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the yearly net flow per tariff group. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid and year of interest.
# * Sum the net flow per tariff group.
# * Present and plot the results. 
# 
# This snippet presents two methods of querying, aggregating and calculating these results. The first method uses a single SQL query to fetch the data, aggregate, and calculate the results. The second method uses a SQL query to fetch the data and Python to aggregate it and calculate the results.

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import plotly.express as px
import pandas as pd
import numpy as np
import datetime
import pytz


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

# ## Net Flow per Tariff Group

# #### Input Parameters
# Enter the grid ID and year. 

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
year = None  # Target year (e.g., 2021)


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice
year = year or input("Enter year: ").strip()  # 2021

# Define the grid_id time zone by matching on whether the grid_id contains a known substring.
time_zones = {
    "awefice": "America/Vancouver",
    "SAF": "America/Denver",
    "GSO": "America/New_York",
}
time_zone = next((tz for name, tz in time_zones.items() if name in grid_id), None)
if time_zone is None:
    raise ValueError(f"No time zone mapping found for grid_id '{grid_id}'.")


# In[ ]:


# Get the start and end dates for the year and localize them.
year_start = datetime.datetime.min.replace(year=int(year))
year_start = pytz.timezone(time_zone).localize(year_start)
year_end = datetime.datetime.max.replace(year=int(year))
year_end = pytz.timezone(time_zone).localize(year_end)

# Convert it to string.
timerange_tz = f"[ {year_start} , {year_end} ]"


# #### Sum of net flow by tariff group - Method 1 - Using only SQL to fetch, aggregate and calculate results

# In[ ]:


get_ipython().run_cell_magic('sql', 'net_tariff <<', '\nSELECT ge.meta ->> \'tariff_id\' as tariff_id,\n    ROUND(CAST(SUM(tdss_c.value - COALESCE(tdss_p.value,0)) AS NUMERIC),2) as "net_kWh"\nFROM grid_element ge\nJOIN grid_element_data_source geds_c\n    ON geds_c.grid_element_id = ge.grid_element_id\n    AND geds_c.type = \'CONSUMER\'\nJOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\', :timerange_tz) tdss_c\n    ON true\nLEFT JOIN grid_element_data_source geds_p\n    ON geds_p.grid_element_id = geds_c.grid_element_id\n    AND geds_p.type = \'PRODUCER\'\nLEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\', :timerange_tz) tdss_p\n    ON tdss_p.timestamp = tdss_c.timestamp\nWHERE ge.grid_id = :grid_id\n    AND ge.type = \'Meter\'\nGROUP BY ge.meta ->> \'tariff_id\'\nORDER BY ge.meta ->> \'tariff_id\';\n')


# In[ ]:


# Convert the results to a data frame.
df_tariff = net_tariff.DataFrame()
df_tariff["net_kWh"] = df_tariff["net_kWh"].astype(float)
df_tariff


# In[ ]:


fig = px.bar(
    df_tariff,
    x="net_kWh",
    y="tariff_id",
    orientation="h",
    color="net_kWh",
    color_continuous_scale=px.colors.diverging.Temps,
    color_continuous_midpoint=0,
    title="<b>Net Flow by Tariff Group for the year {}<b>".format(year),
    labels={"tariff_id": "Tariff Group", "net_kWh": "Net Flow (kWh)"},
)
fig.show()


# Negative net flow indicates that production from DERs (in this case, solar panels) is greater than the current consumption. 

# ---

# #### Sum of net flow by tariff group - Method 2 - Using a SQL query to fetch the data and Python to aggregate and calculate the results  

# In[ ]:


get_ipython().run_cell_magic('sql', 'net_flow <<', '\nSELECT ge.meta ->> \'tariff_id\' as tariff_id,\n    tdss.value as "kWh",\n    geds.type\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_element_id = ge.grid_element_id\nJOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\', :timerange_tz) tdss\n    ON true\nWHERE ge.grid_id = :grid_id\n    AND ge.type = \'Meter\';\n')


# In[ ]:


# Convert the results to a data frame.
df_flow = net_flow.DataFrame()


# In[ ]:


# Aggregate the consumer load.
df_consumer = (
    df_flow.loc[df_flow["type"] == "CONSUMER"]
    .groupby("tariff_id")
    .sum(numeric_only=True)
    .rename(columns={"kWh": "consumer_kWh"})
)

# Aggregate the producer load.
df_producer = (
    df_flow.loc[df_flow["type"] == "PRODUCER"]
    .groupby("tariff_id")
    .sum(numeric_only=True)
    .rename(columns={"kWh": "producer_kWh"})
)

# Concat the aggregated consumer and producer loads.
df_net = pd.concat([df_consumer, df_producer], axis=1).fillna(0).reset_index()

# Calculate the net load.
df_net["net_kWh"] = (df_net["consumer_kWh"] - df_net["producer_kWh"]).round(2)

# Display the results.
df_net.sort_values(by=["tariff_id"])


# ---
