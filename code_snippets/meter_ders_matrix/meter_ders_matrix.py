#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to provide some snippets of code summarizing the distribution of DERs of various types across meters of various types, including the combinations in which DERs appear together behind meters.

# ## Setup

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
import numpy as np


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


# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'GSO_4')


# ## DER combinations behind meters

# **Input Parameters**
# 
# Enter the grid ID of interest.
# 
# The example below uses the GSO_4 grid.  

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # GSO_4


# **Retrieve all meter-DER pairs where the DER is downstream of (behind) the meter.**

# In[ ]:


get_ipython().run_cell_magic('sql', 'meter_ders << SELECT ggs.grid_element_id as meter_id,', "    ggs.meta ->> 'type_of_consumer' as meter_type,\n    ge.type as der_type,\n    ge.grid_element_id as der_id\nFROM grid_element ge\nJOIN grid_get_sources(:grid_id, ge.grid_element_id, True) ggs ON True\nWHERE ge.grid_id = :grid_id AND ggs.type = 'Meter'\n    AND (ge.type='EVCharger' OR ge.type='Photovoltaic' OR ge.type='Battery')\nORDER BY meter_type, meter_id;\n")


# In[ ]:


# Convert the results to a data frame.
df_meter_ders = meter_ders.DataFrame()
df_meter_ders


# **Find the DER combinations**

# In[ ]:


# Pivotal the table by DER type
df_matrix = df_meter_ders.pivot(
    index=["meter_type", "meter_id"], columns="der_type", values="der_id"
)
df_matrix


# In[ ]:


# Label the DER combinations
df_matrix["DERs Present"] = (
    (
        df_matrix["Battery"].isnull().map({True: "_", False: "B"})
        if np.any(df_matrix.columns == "Battery")
        else "_"
    )
    + (
        df_matrix["EVCharger"].isnull().map({True: "_", False: "E"})
        if np.any(df_matrix.columns == "EVCharger")
        else "_"
    )
    + (
        df_matrix["Photovoltaic"].isnull().map({True: "_", False: "P"})
        if np.any(df_matrix.columns == "Photovoltaic")
        else "_"
    )
)
df_matrix


# In[ ]:


# Pivot again to list and count the meters for each DER combination, grouped by meter type.
df_matrix.rename_axis(None, axis=1).reset_index().pivot_table(
    index=["meter_type"],
    columns="DERs Present",
    values="meter_id",
    aggfunc=lambda x: str(len(x)) + ": " + ", ".join(x.astype(str)),
)

