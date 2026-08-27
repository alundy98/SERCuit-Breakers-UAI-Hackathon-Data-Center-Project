#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the number of transformers in a given grid.
# * Display the number of transformers in a given grid older than twenty years.
# * Display the breakdown of transformers older than twenty years by feeders.
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Display the number of transformers in the grid.
# * Display the number of transformers older than twenty years in the grid and aggregate them by feeders.
# * Plot the results.
# 
# Feeders are modelled as having a top transformer that is marked as a producer of energy. This denotes a substation where power shifts from generation and transmission networks to a distribution network.
# 
# The insights derived from these results may be helpful in multiple analytics use cases for areas like Asset Management, Grid Planning or Grid Maintenance.

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

# ## Transformer Age

# #### Input Parameters
# Enter the grid ID of interest.

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice


# #### Transformer Count per Grid
# Fetch the number of transformers in the entire grid.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_transformers\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Transformer';\n")


# Fetch the number of transformers that are older than 20 years.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "SELECT COUNT(grid_element_id) as number_of_transformers\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Transformer'\n    AND (meta ->> 'commission_date')::timestamp < (SELECT current_date  - interval '20 year');\n")


# Fetch the transformers older than 20 years, their commission date, and their top feeders. Top feeders are obtained by getting the transformers' main power source (the producer on the path obtained by tracing to the highest_source of power) using the `grid_get_sources` function.
# 
# First, use a single query snippet without saving the results.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ge.grid_element_id as transformer,\n    ge.meta ->> 'commission_date' as commission_date,\n    ggd.grid_element_id as feeder_top_element\nFROM grid_element ge\nLEFT JOIN grid_get_sources(:grid_id, ge.grid_element_id , 'true') ggd\nON true\nWHERE ge.type = 'Transformer'\n    AND ggd.is_producer = 'True'\n    AND (ge.meta ->> 'commission_date')::timestamp < (SELECT current_date - interval '20 year');\n")


# Second, aggregate the number of transformers per feeder. Save the query results to a dataframe for visualization purposes.

# In[ ]:


get_ipython().run_cell_magic('sql', 'feeders <<', "\nSELECT ggd.grid_element_id as feeder_top_element,\n    COUNT(ge.grid_element_id) as number_of_transformers\nFROM grid_element ge\nLEFT JOIN grid_get_sources(:grid_id, ge.grid_element_id , 'true') ggd\nON true\nWHERE ge.type = 'Transformer'\n    AND ggd.is_producer = 'True'\n    AND (ge.meta ->> 'commission_date')::timestamp < (SELECT current_date - interval '20 year')\nGROUP BY ggd.grid_element_id;\n")


# In[ ]:


# Convert the results to a data frame and display them.
df_feeder = feeders.DataFrame()
df_feeder


# In[ ]:


# Plot the results using a bar chart.
fig = px.bar(
    df_feeder.reset_index(),
    x="feeder_top_element",
    y="number_of_transformers",
    title="<b> Number of Transformers Older than 20 Years by Feeder<b>",
    labels={
        "feeder_top_element": "Top-feeder Elements",
        "number_of_transformers": "Number of Transformers",
    },
)
fig.show()


# ---
