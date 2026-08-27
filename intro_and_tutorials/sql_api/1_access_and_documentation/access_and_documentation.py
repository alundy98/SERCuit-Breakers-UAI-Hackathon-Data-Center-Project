#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Show how to connect to Awesense's Energy Data Model (EDM) using SQL API.
# * Access various documentations on the available functions and views. 
# 
# Please refer to the [main_concepts.ipynb](../2_main_concepts/main_concepts.ipynb) notebook for more details about the core views such as `grid`, `grid_element`, and `grid_element_data_source`.

# ---

# ## Set up

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2


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


# ---

# ## Documentation

# Use the `get_class_documentation()` function to return the information for all views, tables, and types in the schema.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM get_class_documentation();\n')


# Please note that the `ts_data_source_double` class is the type that the `ts_data_source_select()` function mentioned below returns. 
# 
# Use the `get_function_documentation()` function to return the details of all available functions.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT function_name, function_args, description\nFROM get_function_documentation();\n')


# For both `get_class_documentation()` and `get_function_documentation()` functions, their input arguments can be used to do a wildcard matching. For example, the input argument of 'grid' returns the results that contain the word 'grid' in them.

# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT class_name, column_name, description\nFROM get_class_documentation('grid');\n")


# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT function_name, function_args, description\nFROM get_function_documentation('grid');\n")

