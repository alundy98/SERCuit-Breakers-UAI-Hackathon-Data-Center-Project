#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Analyze the maximum possible cumulative load from existing EV chargers downstream of a specific transformer.
# 
# Context:
# 
# * Some transformers may have different numbers of EV Chargers downstream from them. Usually, these chargers may not all be in use at the same time, and when in use, they may not provide maximum power at the maximum rate. We would like to know the total power drawn by EV Chargers downstream of a transformer if all EV Chargers were in use at the same time and all at their maximum rate, i.e. the maximum possible cumulative load on a transformer due to EV Chargers alone. This metric can be used for various planning and reporting purposes.
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Define the transformer of interest.
# * Sum the active charging power of all the EV chargers downstream of this transformer.
# * Fetch all the EV chargers in the grid, their active charging power, parent transformers, and top feeders.
# * Aggregate the EV chargers by transformers and sum the active charging power.
# * Plot the results to show the sum of active charging power per transformer.

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
import plotly.express as px
from natsort import natsort_keygen


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

# ###  Sum of Active Charging Power 

# #### Input Parameters

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
grid_element_id = None  # Transformer ID (e.g., 'transformer_2')


# #### Analysis of Specific Transformer in a grid

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice
grid_element_id = (
    grid_element_id or input("Enter transformer ID: ").strip()
)  # transformer_2


# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT SUM(CAST(meta ->> 'active_charging_power' AS float)/1000.00) as sum_active_charging_power_kW\nFROM grid_get_downstream(:grid_id, :grid_element_id, 'false')\nWHERE type = 'EVCharger';\n")


# #### Analysis of All Transformers with Downstream EV Chargers
# Fetch all the EV chargers in the grid, their active charging power, parent transformers, and top feeders. 

# In[ ]:


get_ipython().run_cell_magic('sql', 'result_ev_chargers <<', "\nSELECT ge.grid_element_id as ev_charger,\n    CAST(ge.meta ->> 'active_charging_power' AS float)/1000.00 as sum_active_charging_power_kW,\n    ggs.grid_element_id as transformer_id,\n    ggs.is_producer as top_feeder\nFROM grid_element ge\nJOIN grid_get_sources(:grid_id, ge.grid_element_id, 'true') ggs\n    ON true\nWHERE ge.type = 'EVCharger'\n    AND ggs.type = 'Transformer'\nORDER BY ggs.grid_element_id, ge.grid_element_id;\n")


# In[ ]:


# Convert the results to a data frame.
df_evchargers = result_ev_chargers.DataFrame()

df_evchargers = df_evchargers.sort_values(
    by=["transformer_id", "ev_charger"], key=natsort_keygen()
)

# Set up a multi-index data frame.
df_evchargers = df_evchargers.set_index(["transformer_id", "ev_charger"])

# Display the results.
df_evchargers


# In[ ]:


# Aggregate the sum of the active charging power based on parent transformers and top feeders.
df_evchargers_sum = (
    df_evchargers.groupby("transformer_id")
    .sum()
    .rename(columns={"top_feeder": "number_ev_chargers"})
    .replace(0, 1)
)

# Sort the results based on the parent/feeder transformer_id
df_evchargers_sum = df_evchargers_sum.sort_index(key=natsort_keygen())

# df_evchargers_sum = df_evchargers_sum.sort_index(key=lambda x: (x.to_series().str[12:].astype(int)))

# Display the results.
df_evchargers_sum


# #### Visualization

# In[ ]:


# Plot the results.
fig = px.bar(
    df_evchargers_sum.reset_index(),
    x="transformer_id",
    y="sum_active_charging_power_kw",
    color="number_ev_chargers",
    color_continuous_scale="Portland",
    title="Sum of Active Power per Transformer and the Number of EV Chargers Downstream from the Transformer",
    text="number_ev_chargers",
    labels={
        "transformer_id": "Transformer ID",
        "active_charging_power_kw": "Sum of Active Charging Power (kW)",
        "number_ev_chargers": "Number of Chargers",
    },
)
# Rotate the x-axis ticks.
fig.update_xaxes(tickangle=-90)

# Update the text's parameters in the graph.
fig.update_traces(
    textfont_size=12, textangle=0, textposition="outside", cliponaxis=False
)

# Include a sentence explaining the value on top of the bars.
fig.add_annotation(
    dict(
        x=0.37,
        y=0.6,
        ax=0,
        ay=0,
        xref="paper",
        yref="paper",
        text="The value above the bars indicates the number of <br> EV chargers downstream from this transformer",
    )
)
fig.show()


# ---
