#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook displays the mapping of meters to their associated high-voltage transformers and substations based on GIS connectivity. Users can input a specific meter for mapping or view the entire grid.

# ---

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
import plotly.express as px

pd.set_option("display.max_rows", None)


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

# ###  Meter to Substation Mapping

# #### Input Parameters

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
meter_id = None  # Meter ID (e.g., 'm_1')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice


# Get the high-voltage transformer and substation upstream of a specific meter in the grid:

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
meter_id = meter_id or input("Enter meter ID: ").strip()  # m_1


# In[ ]:


# Get the high-voltage transformers and substations upstream of a specific meter in the grid.
substation_query = """
    SELECT grid_element_id as hv_transformer,
        meta->> 'enclosure_id' as substation_id
    FROM grid_get_sources(:grid_id, :meter_id, 'true')
    WHERE grid_id = :grid_id
        AND type = 'Transformer'
        AND meta->> 'voltage_level' = 'HV/MV' ;
    """
substation = get_ipython().run_line_magic('sql', '$substation_query')

# Convert the results to a data frame and display it.
df_substation = substation.DataFrame()
df_substation['meter_id'] = meter_id

df_substation


# Get the high-voltage transformer and substations associated with every meter in the grid:

# In[ ]:


substations_query = """
    SELECT ge.grid_element_id as hv_transformer_id,
        ggd.grid_element_id as meter_id,
        ge.meta->> 'enclosure_id' as substation_id
    FROM grid_element ge
    JOIN grid_get_downstream(:grid_id, ge.grid_element_id) ggd
        ON True
    WHERE ge.grid_id = :grid_id
        AND ge.type = 'Transformer'
        AND ggd.type = 'Meter'
        AND ge.meta->> 'voltage_level' = 'HV/MV';
    """
substations = get_ipython().run_line_magic('sql', '$substations_query')

# Convert the results to a data frame and display it.
df_substations = substations.DataFrame().set_index(['substation_id', 'hv_transformer_id'])

df_substations.sort_values(by=['substation_id', 'hv_transformer_id'])


# In[ ]:


# Count the number of meters per transformer.
meters_per_transformers = (
    df_substations.reset_index()
    .groupby(by=["substation_id", "hv_transformer_id"])
    .count()
    .rename(columns={"meter_id": "number_of_meters_per_transformer"})
)
meters_per_transformers


# In[ ]:


# Count the number of transformers and meters per substation.
substations = (
    meters_per_transformers.reset_index()
    .groupby(by=["substation_id"])
    .count()
    .reset_index()
    .rename(columns={"hv_transformer_id": "number_of_transformers_per_substation"})
)
substations["number_of_meters_per_substation"] = (
    meters_per_transformers.reset_index()
    .groupby(by=["substation_id"])
    .sum()
    .reset_index()["number_of_meters_per_transformer"]
)
substations = substations.drop(columns=["number_of_meters_per_transformer"])
substations


# ---
