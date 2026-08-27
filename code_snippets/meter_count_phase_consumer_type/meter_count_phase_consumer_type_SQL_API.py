#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the number of meters per phase in a given grid.
# * Display the number of meters per consumer type in a given grid.
# * Display the number of meters per  phase and consumer type combination in a given grid. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid of interest.
# * Fetch the number of meters per phase and consumer type
# * Display the results.
# 
# The insights derived from these results may be helpful in multiple use cases ranging from Data Quality Improvement to Grid Planning or Customer Analytics

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

# ## Meter Count per Phase and per Consumer Type

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


# #### Meter Count by Phase
# First, fetch the number of meters per phase using a query snippet without saving the results.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases as phase\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY phases;\n")


# Second, save the query results to a dataframe for visualization purposes. 

# In[ ]:


get_ipython().run_cell_magic('sql', 'meters <<', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases as phase\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY phases\nORDER BY LENGTH(phases) desc, phases;\n")


# In[ ]:


# Convert the results to a data frame.
df_meters = meters.DataFrame()

# Plot the number of meters per phase.
fig = px.pie(
    df_meters,
    values="number_of_meters",
    names="phase",
    title="<b>Breakdown of Meters by Phase<b>",
    labels={"phase": "Meter Phase", "number_of_meters": "Meter Count"},
)

# Show meter phase, meter count and percent inside the pie chart.
fig.update_traces(
    textposition="inside",
    texttemplate="Meter Phase: %{label} <br> Count: %{value:} <br>(%{percent})",
)

fig.show()


# #### Meter Count by Consumer Type
# First, fetch the number of meters per consumer type using a query snippet without saving the results.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "SELECT COUNT(grid_element_id) as number_of_meters,\n    meta ->> 'type_of_consumer' as consumer_type\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer')\nORDER BY number_of_meters desc;\n")


# Second, save the query results to a dataframe for visualization purposes. 

# In[ ]:


get_ipython().run_cell_magic('sql', 'meters <<', "SELECT COUNT(grid_element_id) as number_of_meters,\n    meta ->> 'type_of_consumer' as consumer_type\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer')\nORDER BY number_of_meters desc;\n")


# In[ ]:


# Convert the results to a data frame.
df_meters = meters.DataFrame()

# Plot the number of meters per consumer type.
fig = px.pie(
    df_meters,
    values="number_of_meters",
    names="consumer_type",
    title="<b>Breakdown of Meters by Consumer Type<b>",
    color_discrete_sequence=px.colors.qualitative.G10,
    labels={"consumer_type": "Consumer Type", "number_of_meters": "Meter Count"},
)

# Show consumer type, meter count and percent inside the pie chart.
fig.update_traces(
    textposition="inside",
    texttemplate="Type: %{label} <br> Count: %{value:} <br>(%{percent})",
)
fig.show()


# #### Meter Count by both Phase and Consumer Type
# First, fetch the number of meters per phase and consumer type using a query snippet without saving the results.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases,\n    meta ->> 'type_of_consumer' as type_consumer\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer'), phases\nORDER BY LENGTH(phases) desc, phases, type_consumer;\n")


# Second, save the query results to a dataframe for visualization purposes.

# In[ ]:


get_ipython().run_cell_magic('sql', 'meters <<', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases,\n    meta ->> 'type_of_consumer' as type_consumer\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer'), phases\nORDER BY LENGTH(phases) desc, phases, type_consumer;\n")


# In[ ]:


# Convert the results to a data frame.
df_meters = meters.DataFrame()

# Plot the breakdown of meters per phase and per consumer type.
fig = px.bar(
    df_meters,
    x="type_consumer",
    y="number_of_meters",
    color="phases",
    title="<b>Meters Count by Consumer Type and Phase<b>",
    labels={
        "type_consumer": "Consumer Type",
        "number_of_meters": "Number of Meters",
        "phases": "Meter Phase",
    },
)
fig.show()


# ---
