#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Provide SQL code snippets to retrieve DER installations (EV Chargers, PV installations, and Batteries) using the Awesense's Energy Data Model (EDM).
# * Visualize the counts of the retrieved DER installations in Python.
# * Offer insights for decision-making on the current distribution of DERs within a specified grid area. This information can help support new DER connection permits and provide a valuable data source for future grid upgrades and planning activities. 
# 
# The code snippets are divided into three scenarios: 
# 
# | | DER Installation Counts For | Input Parameters |
# |---|:---|:---|
# | 1. | Downstream of a transformer | `grid_id`, `transformer_id` |
# | 2. | Downstream of multiple transformers | `grid_id`, `transformer_ids` |
# | 3. | Entire grid | `grid_id` |
# 
# 
# 

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


def prep_description(str_ids, is_entire_grid=False):
    """
    Prepare a string of IDs for chart title by:
        1) capitalizing the first letter of each ID
        2) replacing "_" with " "
        3) adding "&" if there are multiple IDs
        4) adding "for" at the front for `is_entire_grid` True, else "Downstream for"
    """

    # Apply step 1 ~ 3 listed in the function description.
    str_display = " & ".join(str_ids.title().split()).replace("_", " ")

    # Prepare description based on if it's for an entire grid or not.
    if is_entire_grid:
        description = "for " + str_display
    else:
        description = "Downstream for " + str_display

    return description


def plot_count_pie(df, ids, is_entire_grid=False):
    """
    Plot a pie chart of DER type breakdown for an entire grid (if `is_entire_grid` is True)
        or for given transformer ID(s) (if `is_entire_grid` is default False).

    Print a "no DER" message if the dataframe `df` is empty.
    """

    # Prepare input IDs for title.
    description = prep_description(ids, is_entire_grid)

    # Return a message for empty dataframe.
    if df.empty:
        return "There's no DER Installation " + description

    # If a transformer is downstream of another transformer (e.g. transformer_16 and transformer_2),
    #   then ensure that no DERs are counted multiple times in pie chart.
    df_distinct = df[["der_id", "type"]].drop_duplicates()

    # Plot pie chart.
    fig = px.pie(
        df_distinct,
        names="type",
        title="Breakdown of DERs " + description,
        labels={"type": "DER Type"},
    )

    # Show all percentage, actual count and type inside the pie chart.
    fig.update_traces(textposition="inside", textinfo="percent+label+value")
    fig.show()

    return None


def plot_count_bar(df, ids, is_entire_grid=False):
    """
    Plot a bar chart of DER type breakdown for each transformer ID.
    The chart's title is prepared accordingly based on if `is_entire_grid` is True/False.

    Print a "no DER" message if the dataframe `df` is empty.
    """

    # Prepare input IDs for title.
    description = prep_description(ids, is_entire_grid)

    # Return a message for empty dataframe.
    if df.empty:
        return "There's no DER Installation " + description

    # Compute counts for each transformer and DER type.
    df_count = (
        df.groupby(["transformer_id", "type"])
        .count()
        .reset_index()
        .rename(columns={"der_id": "count"})
    )

    # Box plot of the DER counts.
    fig = px.bar(
        df_count,
        x="transformer_id",
        y="count",
        title="Breakdown of DERs " + description,
        color="type",
        text_auto=True,
        labels={
            "count": "Count",
            "transformer_id": "Transformer ID",
            "type": "DER Type",
        },
    )

    # Rotate the x-axis labels for `is_entire_grid` True, or if there are more than 2 IDs
    #   to fit all labels properly.
    if is_entire_grid or (len(ids.split()) > 2):
        fig.update_xaxes(tickangle=-90)

    fig.show()

    return None


# ---

# ## Examples 

# ### 1. DER Installation Counts Downstream of a Transformer
# Find and visualize the number of DERs in a grid downstream of a specific transformer.

# #### Input Parameters
# Enter the grid ID and transformer ID of interest. 

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
grid_element_id = None  # Transformer ID (e.g., 'transformer_2')
grid_element_ids = None  # Transformer IDs (e.g., 'transformer_2 transformer_16')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice
grid_element_id = (
    grid_element_id or input("Enter transformer ID: ").strip()
)  # transformer_2


# #### SQL Queries
# Fetch DERs downstream of a given transformer.

# In[ ]:


result = get_ipython().run_line_magic('sql', "SELECT ggd.grid_element_id as der_id,                       ggd.type                  FROM grid_get_downstream(:grid_id, :grid_element_id, 'false') ggd                  WHERE ggd.type in ('EVCharger', 'Photovoltaic', 'Battery');")

df = result.DataFrame()
df


# #### Visualization
# Pie chart of DER counts.

# In[ ]:


plot_count_pie(df, grid_element_id)


# ### 2. DER Installation Counts Downstream of Multiple Transformers
# Find and visualize the number of DERs in a grid downstream of multiple transformers.

# #### Input Parameters
# 
# Enter the grid ID and multiple trasnformer IDs of interest. 

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_element_ids = (
    grid_element_ids or input("Enter transformer IDs separated by space: ").strip()
)  # transformer_2 transformer_6


# #### SQL Queries
# Fetch DERs downstream of multiple transformers.

# In[ ]:


# Prepare the input transformer IDs to be used in SQL query by adding quotes and commas.
transformer_ids = "','".join(grid_element_ids.split())

# SQL query.
result = get_ipython().run_line_magic('sql', "SELECT ge.grid_element_id as transformer_id,                          ggd.grid_element_id as der_id,                          ggd.type                  FROM grid_element ge                  JOIN grid_get_downstream(:grid_id, ge.grid_element_id, 'false') ggd                  ON true                  WHERE ge.grid_element_id IN ('{transformer_ids}')                  AND ggd.type in ('EVCharger', 'Photovoltaic', 'Battery');")

df = result.DataFrame()
df


# #### Visualization
# Pie chart of DER counts. If a given DER is downstream of multiple transformers (e.g. because one transformer is downstream of another), the DER is counted only once.

# In[ ]:


# Pie chart of DER counts.
plot_count_pie(df, grid_element_ids)


# Bar chart of DER counts.

# In[ ]:


plot_count_bar(df, grid_element_ids)


# ### 3. DER Installation Counts of Entire Grid
# Find and visualize the number of DERs in an entire grid.

# #### SQL Queries
# Fetch DERs in an entire grid.

# In[ ]:


result = get_ipython().run_line_magic('sql', "SELECT ge.grid_element_id as transformer_id,                          ggd.grid_element_id as der_id,                          ggd.type                  FROM grid_element ge                  JOIN grid_get_downstream(:grid_id, ge.grid_element_id, 'false') ggd                  ON true                  WHERE ge.grid_id = :grid_id                  AND ge.type = 'Transformer'                 AND ggd.type in ('EVCharger', 'Photovoltaic', 'Battery');")

df = result.DataFrame()
df


# #### Visualization
# Pie chart of DER counts. Each DER is counted only once.

# In[ ]:


# Pie chart of DER counts.
plot_count_pie(df, grid_id, is_entire_grid=True)


# Bar chart of DER counts.

# In[ ]:


plot_count_bar(df, grid_id, is_entire_grid=True)


# ---
