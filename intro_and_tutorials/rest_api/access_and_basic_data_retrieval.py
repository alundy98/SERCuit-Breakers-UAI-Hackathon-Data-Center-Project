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
# It is assumed that the user has been given credentials for accessing Awesense Sandbox tiers 2 or 3. Otherwise, please contact us at [api@awesense.com](api@awesense.com).

# ---

# ## Set up

# In[1]:


import getpass
from io import StringIO

import pandas as pd
import requests

pd.set_option('display.max_columns', None)


# ### Connection
# 
# Enter the login credentials provided by Awesense. If you do not have credentials or have any trouble connecting, please contact [api@awesense.com](api@awesense.com).
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


# Enter the REST server origin or hostname (optionally without "https://")
server_hostname = getpass.getpass(prompt='REST server address: ')
# Ensure that the server address does not specify plaintext HTTP
assert (server_hostname.startswith('http://') == False), 'Server address uses https:// for secure communications, not http://'

# Support the case where the specified address already included `https://`, otherwise add it
if server_hostname.startswith('https://'):
    server_origin = server_hostname
else:
    server_origin = f'https://{server_hostname}'


# In[3]:


# Enter the username that you use to log into the server
server_user_name = getpass.getpass(prompt='Username: ')


# In[4]:


# Enter the password that you use to log into the server
server_password = getpass.getpass(prompt='Password: ')


# In[5]:


# This auth tuple can be passed to `requests` functions as the `auth` argument.
# (An HTTP Basic Authentication header will be generated and used automatically)
auth = (server_user_name, server_password)


# ---

# ## Basic Data Retrieval

# ### Grids

# In[6]:


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
# The following example gets information about type 'Meter'.
grid_id = 'awefice'
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


# In[8]:


# The above data frame contains lists of dictionaries that must be unpacked for viewing the results.
# Unpack the `results` column to view all the meters.
results = pd.json_normalize(grid_elements.to_dict('records'), record_path='results')
results.head()


# In[9]:


# Display all the Meter grid element IDs.
results.id


# In[10]:


# Similar API as in the previous cell, but with a different grid element type. Notice that the resulting columns are different. 
# Please consult the grid schema CSV file in the online documentation available from the TGI help menu to learn more 
# information about all the different properties of the various grid element types.
grid_id = 'awefice'
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


# In[11]:


# Unpack the `results` column to view the elements.
results = pd.json_normalize(grid_elements_pv.to_dict('records'), record_path='results')
results.head()


# In[12]:


# Display all the Photovoltaic grid element IDs.
results.id


# ### Details about a grid element

# In[13]:


# Get details about specific grid elements. 
grid_id = 'awefice'
grid_element = 'm_6'

params = {
        'all_sources': 'true'
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element}', auth=auth, params=params)
grid_element_details = pd.json_normalize(response.json())
grid_element_details


# In[14]:


# Unpack the `grid_data_source` column containing the list of dictionaries.
# The grid_data_source ID represents the linking between time-series data and physical elements on the grid. 
# The ID is used further down in this notebook to retrieve the time series. 
pd.json_normalize(
    grid_element_details.to_dict('records'),
    record_path='grid_data_sources',
    meta=['customer_type', 'meta.address']
)


# ### Grid Tracing

# In[15]:


# The example below demonstrates the tracing of transformers from a meter in an upstream direction. The results are exported in CSV format.
grid_id = 'awefice'
grid_element_id = 'm_6'

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
grid_id = 'awefice'
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


# In[17]:


# Unpack the `consumers_breakdown` column containing the list of dictionaries. 
pd.json_normalize(grid_element_trace.to_dict('records'), record_path='consumers_breakdown')


# ### Time Series
# 
# There are different endpoints for retrieving time series, each suited for different use cases. For more information, please refer to the REST API documentation accessible from the TGI help (?) menu or in the api-connect developer portal.

# In[18]:


# Get time series data associated with a given grid data source.
grid_id = 'awefice'
grid_data_source = '84e32b3f-1cf6-4b8a-9a55-f59ec574f8c1' # From the details grid element above

params = {
        'units': 'kWh',
        'group_by': 'hour',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-03-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/Vancouver',
        'include_sign': 'false',
        'export': 'false'
}

response = requests.get(f'{server_origin}/api/v1/grid/grid_data_source/{grid_data_source}/data', auth=auth, params=params)
ts = pd.json_normalize(response.json())
ts


# In[19]:


# Unpack the time series and include the `units` column. 
grid_element_ts = pd.json_normalize(ts.to_dict('records'), record_path='series', meta=['units'])
grid_element_ts


# In[20]:


# Get consumption data for any grid element with associated grid data sources of type CONSUMER. 
# This example exports the results in CSV format. 
grid_id = 'awefice'
grid_element_id = 'm_6'
params = {
        'units': 'kWh',
        'group_by': 'hour',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-03-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/Vancouver',
        'export': 'true'
}

response = requests.get(f'{server_origin}/api/v1/grid/{grid_id}/element/{grid_element_id}/data', auth=auth, params=params)
grid_element_ts = pd.read_csv(StringIO(response.content.decode('utf-8')))
grid_element_ts


# The following cells use the North Central Zone grid. To run the code in these cells it in necessary to have access credentials and point the notebook to the Sandbox tier 2&3 server.

# In[21]:


# Get any kind of time-series data for a grid element. 
grid_id = 'North Central Zone'
grid_element_id = '7676'

params = {
        'units': 'kW',
        'group_by': 'dynamic',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-04-01T08:00:00.000000Z',
        'phases': 'ABC',
        'max_points': '1000',
        'timezone': 'America/Vancouver',
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
grid_id = 'North Central Zone'
grid_element_id = '10680'

params = {
        'units': 'kWh',
        'group_by': 'day',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-04-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/Vancouver',
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
