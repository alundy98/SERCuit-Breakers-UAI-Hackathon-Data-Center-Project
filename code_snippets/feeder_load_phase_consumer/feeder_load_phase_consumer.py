#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the net flow of a specific feeder over the past day, month, and year from a given date defined by the user. The results are broken down by the meters' phases and consumer types. 
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Define the feeder of interest.
# * Define the date.
# * Display the net flow by phase over the past day, month, and year relative to the end date.
# * Display the net flow by consumer type over the past day, month, and year. 
# 
# This snippet presents two methods of querying, aggregating and calculating results. The first method uses SQL queries to fetch the data for each time range (past day, month, and year), aggregate, and calculate the results. The second method uses a single SQL query to fetch the data over the available time range and Python to aggregate and filter the results to the appropriate time range (past day, month, or year).

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
from datetime import datetime, timedelta
from dateutil.relativedelta import relativedelta
import pandas as pd
import plotly.express as px
import numpy as np
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


# #### Custom Function 

# In[ ]:


def net_flow_bar(df, title):
    """
    Plot a bar chart of net flow by consumer type
    """
    fig = px.bar(
        df,
        x="consumer_type",
        y="kWh",
        title=title,
        color="consumer_type",
        labels={"consumer_type": "Consumer Type", "net_kWh": "Net Flow (kWh)"},
    )
    fig.update_layout(showlegend=False)

    fig.show()


# ---

# ## Net Flow of a Feeder by Phase and Consumer Type

# #### Input Parameters 

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
grid_element_id = None  # Transformer ID (e.g., 'transformer_2')
end_date = None  # Net flow up to this end date (e.g., 2022-11-15 00:00:00)


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


# Feeders are modelled as having a top transformer that is marked as a producer of energy. This denotes a substation where power shifts from generation and transmission networks to a distribution network.
# 
# Fetch the grid's feeders.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT grid_element_id as feeder_id\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Transformer'\n    AND is_producer=True;\n")


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_element_id = (
    grid_element_id or input("Enter transformer ID: ").strip()
)  # transformer_2
end_date = end_date or input("Enter end date: ").strip()  # 2022-11-15 00:00:00


# In[ ]:


# Convert the end date to datetime, round down to the hour, and localize it.
end_date = datetime.strptime(end_date, "%Y-%m-%d %H:%M:%S").replace(
    microsecond=0, second=0, minute=0
)
end_date = pytz.timezone(time_zone).localize(end_date)

# Define the timestamp for the past day, month, and year.
past_day = end_date - timedelta(days=1)
past_month = end_date - relativedelta(months=1)
past_year = end_date - relativedelta(years=1)


# Create a time range to use as an input argument.
timerange_day = f"[ {past_day} , {end_date} ]"
timerange_month = f"[ {past_month} , {end_date} ]"
timerange_year = f"[ {past_year} , {end_date} ]"


# #### Net flow by phase - Method 1 - Using SQL to fetch, aggregate and calculate results

# Display the total net flow by phase over the past day. 

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT ggd.phases,\n    ROUND(CAST(SUM(tdss_c.value - COALESCE(tdss_p.value, 0)) AS NUMERIC), 2) as "total_kWh"\nFROM grid_element ge\nJOIN grid_get_downstream(:grid_id, ge.grid_element_id, \'false\') ggd\n    ON ggd.grid_id = ge.grid_id\nJOIN grid_element_data_source geds_c\n    ON geds_c.grid_element_id = ggd.grid_element_id\n        AND geds_c.type = \'CONSUMER\'\nJOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\', :timerange_day) tdss_c\n    ON true\nLEFT JOIN grid_element_data_source geds_p\n    ON geds_p.grid_element_id = geds_c.grid_element_id\n        AND geds_p.type = \'PRODUCER\'\nLEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\', :timerange_day) tdss_p\n    ON tdss_p.timestamp = tdss_c.timestamp\nWHERE ge.grid_element_id = :grid_element_id\n    AND ggd.type = \'Meter\'\nGROUP BY ggd.phases\nORDER by 2;\n')


# Display the total net flow by phase over the past month.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT ggd.phases,\n    ROUND(CAST(SUM(tdss_c.value - COALESCE(tdss_p.value, 0)) AS NUMERIC), 2) as "total_kWh"\nFROM grid_element ge\nJOIN grid_get_downstream(:grid_id, ge.grid_element_id, \'false\') ggd\n    ON ggd.grid_id = ge.grid_id\nJOIN grid_element_data_source geds_c\n    ON geds_c.grid_element_id = ggd.grid_element_id\n        AND geds_c.type = \'CONSUMER\'\nJOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\', :timerange_month) tdss_c\n    ON true\nLEFT JOIN grid_element_data_source geds_p\n    ON geds_p.grid_element_id = geds_c.grid_element_id\n        AND geds_p.type = \'PRODUCER\'\nLEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\', :timerange_month) tdss_p\n    ON tdss_p.timestamp = tdss_c.timestamp\nWHERE ge.grid_element_id = :grid_element_id\n    AND ggd.type = \'Meter\'\nGROUP BY ggd.phases\nORDER by 2;\n')


# Display the total net flow by phase over the past year.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT ggd.phases,\n    ROUND(CAST(SUM(tdss_c.value - COALESCE(tdss_p.value, 0)) AS NUMERIC), 2) as "total_kWh"\nFROM grid_element ge\nJOIN grid_get_downstream(:grid_id, ge.grid_element_id, \'false\') ggd\n    ON ggd.grid_id = ge.grid_id\nJOIN grid_element_data_source geds_c\n    ON geds_c.grid_element_id = ggd.grid_element_id\n        AND geds_c.type = \'CONSUMER\'\nJOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\', :timerange_year) tdss_c\n    ON true\nLEFT JOIN grid_element_data_source geds_p\n    ON geds_p.grid_element_id = geds_c.grid_element_id\n        AND geds_p.type = \'PRODUCER\'\nLEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\', :timerange_year) tdss_p\n    ON tdss_p.timestamp = tdss_c.timestamp\nWHERE ge.grid_element_id = :grid_element_id\n    AND ggd.type = \'Meter\'\nGROUP BY ggd.phases\nORDER by 2;\n')


# #### Net flow by consumer type - Method 2 - Using SQL query to fetch the data and Python to aggregate and filter the results

# In[ ]:


get_ipython().run_cell_magic('sql', 'feeder_flow <<', '\nSELECT ggd.meta ->> \'type_of_consumer\' as consumer_type,\n    tdss.value as "kWh",\n    geds.type,\n    tdss.timestamp\nFROM grid_element ge\nJOIN grid_get_downstream(:grid_id, ge.grid_element_id, \'false\') ggd\n    ON ggd.grid_id = ge.grid_id\nJOIN grid_element_data_source geds\n    ON geds.grid_element_id = ggd.grid_element_id\nJOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\') tdss\n    ON true\nWHERE ge.grid_element_id = :grid_element_id\n    AND ggd.type = \'Meter\';\n')


# In[ ]:


# Convert the results to a data frame.
feeder_flow = feeder_flow.DataFrame()


# In[ ]:


# Change the values of PRODUCER to negative values.
feeder_flow["kWh"] = np.where(
    feeder_flow["type"].isin(["PRODUCER"]), -feeder_flow["kWh"], feeder_flow["kWh"]
)

# Group the results by timestamp and consumer types and sum the net flow.
feeder_flow = (
    feeder_flow.groupby(["timestamp", "consumer_type"])
    .sum(numeric_only=True)
    .reset_index()
)


# In[ ]:


# Convert to string.
past_day_str = f"{past_day}"
end_date_str = f"{end_date}"

# Filter the results to include the net flow from the past day and aggregate it by consumer type.
df_past_day = (
    feeder_flow.loc[
        (feeder_flow["timestamp"] >= past_day_str)
        & (feeder_flow["timestamp"] <= end_date),
        ["kWh", "consumer_type"],
    ]
    .groupby("consumer_type")
    .sum()
    .round(2)
    .reset_index()
)

# Display the results.
df_past_day


# Display the net flow by consumer type over the past day.

# In[ ]:


# Plot the results.
net_flow_bar(
    df_past_day,
    title="<b>Net Flow of Feeder {} by Consumer Type Over the Past Day<b>".format(
        grid_element_id
    ),
)


# Display the net flow by consumer type over the past month.

# In[ ]:


# Convert to string.
past_month_str = f"{past_month}"

# Filter the results to include the net flow from the past month and aggregate it by consumer type.
df_past_month = (
    feeder_flow.loc[
        (feeder_flow["timestamp"] >= past_month_str)
        & (feeder_flow["timestamp"] <= end_date),
        ["kWh", "consumer_type"],
    ]
    .groupby("consumer_type")
    .sum()
    .round(2)
    .reset_index()
)

# Display the results.
df_past_month


# In[ ]:


# Plot the results.
net_flow_bar(
    df_past_month,
    title="<b>Net Flow of Feeder {} by Consumer Type Over the Past Month<b>".format(
        grid_element_id
    ),
)


# Display the net flow by consumer type over the past year.

# In[ ]:


# Convert to string.
past_year_str = f"{past_year}"

# Filter the results to include the net flow from the past year and aggregate it by consumer type.
df_past_year = (
    feeder_flow.loc[
        (feeder_flow["timestamp"] >= past_year_str)
        & (feeder_flow["timestamp"] <= end_date),
        ["kWh", "consumer_type"],
    ]
    .groupby("consumer_type")
    .sum()
    .round(2)
    .reset_index()
)

# Display the results.
df_past_year


# In[ ]:


# Plot the results.
net_flow_bar(
    df_past_year,
    title="<b>Net Flow of Feeder {} by Consumer Type Over the Past Year<b>".format(
        grid_element_id
    ),
)


# ---
