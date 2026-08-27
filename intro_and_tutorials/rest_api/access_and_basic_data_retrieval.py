#!/usr/bin/env python
# coding: utf-8

# ## Overview

#  This notebook is intended to:
#  * Show how to connect to Awesense's Energy Data Model (EDM) using REST API.
#  * Use the REST API endpoints to retrieve the following:
#      * Information about active grids
#      * Information about grid elements
#      * Tracing information from specific grid element
#      * Time series for different grid elements 
# * The data returned by the REST API is typically returned in JSON format; `pandas.json_normalize` is used in this notebook to display the results as a data frame using the first-level JSON objects, which can be further expanded. Some endpoints support alternately returning a response in different formats e.g. CSV; `pandas.read_csv` is used in this notebook for parsing CSV data.
# 
# It is assumed that the user has been given credentials for accessing the Awesense Sandbox. Otherwise, please contact us at [api@awesense.com](api@awesense.com).

# ---

# ## Set up

# In[1]:


import getpass
import os
from contextlib import suppress
from io import StringIO

import pandas as pd
import requests
from dotenv import load_dotenv

pd.set_option('display.max_columns', None)


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
    print('☁️ Running in Google Colab. Using Colab Secrets for REST API connection')
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
if 'EDM_PASSWORD' not in os.environ: os.environ['EDM_PASSWORD'] = getpass.getpass('EDM password: ')

# Ensure that the server address does not specify plaintext HTTP, then build the HTTPS server origin.
server_hostname = os.environ['EDM_HOST']
assert (server_hostname.startswith('http://') == False), 'Server address uses https:// for secure communications, not http://'
if server_hostname.startswith('https://'):
    server_origin = server_hostname
else:
    server_origin = f'https://{server_hostname}'

# This auth tuple can be passed to `requests` functions as the `auth` argument.
# (An HTTP Basic Authentication header will be generated and used automatically)
auth = (os.environ['EDM_USER'], os.environ['EDM_PASSWORD'])
response = requests.get(f'{server_origin}/api/v1/grid', auth=auth, timeout=30)
response.raise_for_status()
print('✅ REST API connection parameters/credentials verified')


# ---

# #### Input Parameters
# These parameters drive the `awefice` examples below. They can be injected at runtime with [Papermill](https://papermill.readthedocs.io) (the cell below is tagged `parameters`), or entered interactively when prompted.

# In[8]:


# PAPERMILL PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None   # Target grid identifier (e.g., 'awefice')
meter_id = None  # Example meter grid element (e.g., 'm_6')
start = None     # Time series range start (ISO 8601 UTC, e.g. '2021-01-01T08:00:00.000000Z')
end = None       # Time series range end (ISO 8601 UTC, e.g. '2021-03-01T08:00:00.000000Z')


# In[9]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input('Enter grid ID: ').strip()  # awefice

# Define the grid_id time zone by matching on whether the grid_id contains a known substring.
time_zones = {
    "awefice": "America/Vancouver",
    "SAF": "America/Denver",
    "GSO": "America/New_York",
}
time_zone = next((tz for name, tz in time_zones.items() if name in grid_id), None)
if time_zone is None:
    raise ValueError(f"No time zone mapping found for grid_id '{grid_id}'.")

meter_id = meter_id or input('Enter meter grid element ID: ').strip()  # m_6
start = start or input('Enter start date (ISO 8601 UTC): ').strip()  # 2021-01-01T08:00:00.000000Z
end = end or input('Enter end date (ISO 8601 UTC): ').strip()  # 2021-03-01T08:00:00.000000Z


# ## Basic Data Retrieval

# ### Grids

# In[ ]:


# Get all the grids. 
response = requests.get(f'{server_origin}/api/v1/grid', auth=auth)
grids = pd.json_normalize(response.json())
grids


# ### Grid Elements

# In[7]:


# Get all the `grid_elements` of a specific type. 
# Types include ACLineSegment, Battery, Busbar, Cabinet, Capacitor, CircuitBreaker, Disconnector, 
# Enclosure, EVCharger, FixedSensor, Fuse, grid_data_source, Jumper, Manhole, Meter, Photovoltaic,
# Pole, ServiceDeliveryPoint, SpokeLine, Substation, Switch, Transformer, UnmeteredLoad.   
# The following example gets information about type 'Meter' in the `grid_id` set in the parameters cell.
grid_element_type = 'Meter'

params = {
        'limit': '30',
        'offset': '0', 
        'ordering': '{"direction": "ASC", "query_name": "grid_id"}',
        'filter': f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
        'export': 'false',
        'grid_element_type': grid_element_type,
}

response = requests.get(f'{server_origin}/api/v1/grid_explorer', auth=auth, params=params)
grid_elements = pd.json_normalize(response.json())
grid_elements


# In[12]:


# The above data frame contains lists of dictionaries that must be unpacked for viewing the results.
# Unpack the `results` column to view all the meters.
results = pd.json_normalize(grid_elements.to_dict('records'), record_path='results')
results.head()


# In[13]:


# Display all the Meter grid element IDs.
results.id


# In[10]:


# Similar API as in the previous cell, but with a different grid element type. Notice that the resulting columns are different. 
# Please consult the grid schema CSV file in the online documentation available from the TGI help menu to learn more 
# information about all the different properties of the various grid element types.
grid_element_type = 'Photovoltaic'

params = {
        'limit': '10',
        'offset': '0', 
        'ordering': '{"direction": "ASC", "query_name": "grid_id"}',
        'filter': f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
        'export': 'false',
        'grid_element_type': grid_element_type,
}

response = requests.get(f'{server_origin}/api/v1/grid_explorer', auth=auth, params=params)
grid_elements_pv = pd.json_normalize(response.json())
grid_elements_pv


# In[15]:


# Unpack the `results` column to view the elements.
results = pd.json_normalize(grid_elements_pv.to_dict('records'), record_path='results')
results.head()


# In[16]:


# Display all the Photovoltaic grid element IDs.
results.id


# ### Details about a grid element

# In[17]:


# Get details about a specific grid element (the meter set in the parameters cell). 
grid_element = meter_id

params = {
        'all_sources': 'true'
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element}', auth=auth, params=params)
grid_element_details = pd.json_normalize(response.json())
grid_element_details


# In[18]:


# Unpack the `grid_data_source` column containing the list of dictionaries.
# The grid_data_source ID represents the linking between time-series data and physical elements on the grid. 
# The ID is used further down in this notebook to retrieve the time series. 
pd.json_normalize(
    grid_element_details.to_dict('records'),
    record_path='grid_data_sources',
    meta=['customer_type', 'meta.address']
)


# ### Grid Tracing

# In[19]:


# The example below demonstrates the tracing of transformers from a meter in an upstream direction. The results are exported in CSV format.
grid_element_id = meter_id

params = {
        'trace_type': 'upstream',
        'output_type': 'transformer_download',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

response = requests.get(f'{server_origin}/api/v1/grid/trace', auth=auth, params=params)
grid_element_trace = pd.read_csv(StringIO(response.content.decode('utf-8')))
grid_element_trace


# In[16]:


# The example below demonstrates the tracing from transformers in a downstream direction.  
# For more information about different types of traces and outputs, please refer to the REST API documentation, 
# accessible from the TGI help (?) menu or in the api-connect developer portal.
# Note: `transformer_2` is an example element from the `awefice` grid (one of the upstream transformers
# traced above); adjust it if you point `grid_id` at a different grid.
grid_element_id = 'transformer_2'

params = {
        'trace_type': 'downstream',
        'output_type': 'summary',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

response = requests.get(f'{server_origin}/api/v1/grid/trace', auth=auth, params=params)
grid_element_trace = pd.json_normalize(response.json())
grid_element_trace


# In[21]:


# Unpack the `consumers_breakdown` column containing the list of dictionaries. 
pd.json_normalize(grid_element_trace.to_dict('records'), record_path='consumers_breakdown')


# ### Time Series
# 
# There are different endpoints for retrieving time series, each suited for different use cases. For more information, please refer to the REST API documentation accessible from the TGI help (?) menu or in the api-connect developer portal.

# In[22]:


# Get time series data associated with a given grid data source.
# Note: this `grid_data_source` ID is the kWh source for `m_6` taken from the details output above
# (valid for the `awefice` grid); replace it with an ID from your own grid element details if needed.
grid_data_source = '84e32b3f-1cf6-4b8a-9a55-f59ec574f8c1'

params = {
        'units': 'kWh',
        'group_by': 'hour',
        'start': start,
        'end': end,
        'max_points': '2000',
        'timezone': time_zone,
        'include_sign': 'false',
        'export': 'false'
}

response = requests.get(f'{server_origin}/api/v1/grid/grid_data_source/{grid_data_source}/data', auth=auth, params=params)
ts = pd.json_normalize(response.json())
ts


# In[23]:


# Unpack the time series and include the `units` column. 
grid_element_ts = pd.json_normalize(ts.to_dict('records'), record_path='series', meta=['units'])
grid_element_ts


# In[24]:


# Get consumption data for any grid element with associated grid data sources of type CONSUMER. 
# This example exports the results in CSV format. 
grid_element_id = meter_id
params = {
        'units': 'kWh',
        'group_by': 'hour',
        'start': start,
        'end': end,
        'max_points': '2000',
        'timezone': time_zone,
        'export': 'true'
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element_id}/data', auth=auth, params=params)
grid_element_ts = pd.read_csv(StringIO(response.content.decode('utf-8')))
grid_element_ts


# The following cells use the GSO_4 grid. To run the code in these cells it is necessary to have Awesense Sandbox access credentials.

# In[21]:


# Get any kind of time-series data for a grid element. 
grid_id = 'GSO_4'
grid_element_id = 'br_04_21'

params = {
        'units': 'kWh',
        'group_by': 'dynamic',
        'start': '2024-01-01T08:00:00.000000Z',
        'end': '2024-04-01T08:00:00.000000Z',
        'phases': 'ABC',
        'max_points': '1000',
        'timezone': 'America/New_York',
        'use_last_value': 'false',
        'combine_phases': 'false',
        'data_source_type': 'SENSOR',
        'only_matching_units': 'false',
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element_id}/flexible_time_series_data', auth=auth, params=params)
ts = pd.json_normalize(response.json())
ts


# In[22]:


# Unpack the time series for the different phases and include the `units`, and `source.friendly_id` columns.
phase_ts = pd.json_normalize(
    ts.to_dict('records'),
    record_path='series',
    meta=['phase', 'units', 'source.friendly_id']
)
phase_ts


# In[23]:


# Get per-phase time series data for each sensor (raptor or SCADA) associated with the given grid element.
grid_id = 'GSO_4'
grid_element_id = 'br_04_21'

params = {
        'units': 'kWh',
        'group_by': 'day',
        'start': '2024-01-01T08:00:00.000000Z',
        'end': '2024-04-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/New_York',
        'include_sign': 'false'
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element_id}/chart_data', auth=auth, params=params)
ts = pd.json_normalize(response.json())
ts


# In[24]:


# Unpack the time series for the different phases and add the `units` column. 
per_phase = pd.json_normalize(
    ts.to_dict('records'),
    record_path='series',
    meta=['phase', 'units']
)
per_phase


# ---
