#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the source transformer and the closest transformer of a specific meter. 
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Define the meter of interest.
# * Display the meter's source transformer.
# * Calculate and display the closest transformer to the meter.
# * Plot the distance between the meter and all the transformers in the grid. 
# 
# The gewspecial insights derived from these results may be helpful in multiple use cases ranging from Data Quality Improvement to Grid Planning.

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
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

# ## Source Transformer and Closest Transformer

# #### Input Parameters
# Enter the grid ID of interest.

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
meter_id = None  # Target meter ID (e.g., 'm_10')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice


# Display all the Meters in the Grid

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT grid_element_id\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter' limit 20;\n")


# Enter the meter ID of interest.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
meter_id = meter_id or input("Enter meter ID: ").strip()  # m_10


# Fetch the meter's source transformer.

# In[ ]:


meter = get_ipython().run_line_magic('sql', " SELECT :meter_id as meter_id,      ggs.grid_element_id AS source_transformer_id  FROM grid_element ge  JOIN grid_get_sources(:grid_id, :meter_id, true) ggs      ON :meter_id = ge.grid_element_id  LEFT JOIN grid_get_same_voltage(:grid_id, ggs.grid_element_id) ggsv      ON true  WHERE ggs.type = 'Transformer'      AND ggsv.grid_element_id = :meter_id;")

meter


# Fetch the closest transformer to the meter and display the results.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "SELECT ge_m.grid_element_id as meter_id,\n    ge_t.grid_element_id as closest_transformer_id,\n    ge_t.geometry <-> ge_m.geometry ::geography as distance_m\nFROM grid_element ge_m\nJOIN grid_element ge_t\n    ON true\nWHERE ge_m.grid_id = :grid_id\n    AND ge_m.grid_element_id = :meter_id\n    AND ge_t.type = 'Transformer'\nORDER BY distance_m\nLIMIT 1;\n")


# Plot the distance between the meter of interest and all the transformers in the grid. 

# In[ ]:


get_ipython().run_cell_magic('sql', 'transformer_distance <<', "SELECT ge_m.grid_element_id as meter_id,\n    ge_t.grid_element_id as transformer_id,\n    ge_t.geometry <-> ge_m.geometry ::geography as distance_m\nFROM grid_element ge_m\nJOIN grid_element ge_t\n    ON true\nWHERE ge_m.grid_id = :grid_id\n    AND ge_t.grid_id = :grid_id\n    AND ge_m.grid_element_id = :meter_id\n    AND ge_t.type = 'Transformer'\nORDER BY distance_m;\n")


# In[ ]:


# Convert the results to data frames.
df_meter = meter.DataFrame()
df_transformers = transformer_distance.DataFrame()


# Define a different colour for the closest transformer.
df_transformers["color"] = "red"
df_transformers["color"] = df_transformers["color"].where(
    df_transformers["transformer_id"] == df_meter.loc[0, "source_transformer_id"],
    "blue",
)

# Plot the distance.
fig = px.bar(
    df_transformers,
    x="distance_m",
    y="transformer_id",
    color="color",
    color_discrete_sequence=df_transformers.color.unique(),
    labels={"transformer_id": "Transformer ID", "distance_m": "Distance (m)"},
    title="<b>Distance of Transformers from Meter {} </b> \
            <br> The source transformer is depicted in red <br>".format(meter_id),
)

# Do not show the legend.
fig.update_layout(showlegend=False)

# Plot the bars in descending order.
fig.update_layout(barmode="stack", yaxis={"categoryorder": "total ascending"})

fig.show()


# ---
