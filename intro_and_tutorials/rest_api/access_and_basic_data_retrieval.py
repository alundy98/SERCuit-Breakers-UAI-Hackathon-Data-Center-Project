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
# * The data returned by the REST API is in JSON format. To extract and display the information more clearly, different formats such as 'json_format', and 'csv_download' are used in the notebook. The 'json_format' is the default and works all the time. It displays a data frame using the first-level JSON objects, which can be further expanded. 
# 
# The notebook uses the Awesense api-connect developer portal: https://api-account.awesense.com/. The developer portal contains additional documentation regarding the endpoints used in this notebook, their available parameters and output options. Please refer to it for more information. 
# 
# It is assumed that the user has been given access to the Awesense api-connect developer portal along with the necessary credentials for accessing Sandbox tier 1. Otherwise, please contact us at [api@awesense.com](api@awesense.com).

# ---

# ## Set up

# In[1]:


import getpass
import pandas as pd
import base64
import callrestapi as cr

pd.set_option('display.max_columns', None)


# ### Connection Variables

# In[2]:


# Enter the user_name, and password that you use to log into the server.
server_user_name = getpass.getpass(prompt='Username: ')


# In[3]:


server_password = getpass.getpass(prompt='Password: ')


# In[4]:


# Enter the subscription key (primary or secondary) from the `Profile` page on the api-connect developer portal website. 
# If you don't have a subscription key, you will need to create one. 
# To do so, go to the `Products` page on the api-connect developer portal website, click on the desired product name, enter a product description, and click `Subscribe`. 
subscription_key = getpass.getpass(prompt='Subscription Key: ')


# In[5]:


# Create BasicAuth credential based on user_name and password.  
auth_str = server_user_name + ':' + server_password
byte_str = auth_str.encode('ascii')
encoded_data = base64.b64encode(byte_str)
basic_auth = 'Basic ' + str(encoded_data, encoding='utf-8')

# Delete the credential variables for security purposes.
del server_user_name, server_password, auth_str, byte_str


# ---

# ## Basic Data Retrieval

# ### Grids

# In[6]:


# Get all the grids. 
url_grid = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid'
grids = cr.get_rest_api(url_grid, subscription_key, basic_auth, format = 'json_format')
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
url_grid_explorer = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid_explorer'

params = {
        'limit': '30',
        'offset': '0', 
        'ordering': '{"direction": "ASC", "query_name": "grid_id"}',
        'filter': f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
        'export': 'false',
        'grid_element_type': grid_element_type,
}

grid_elements = cr.get_rest_api(url_grid_explorer, subscription_key, basic_auth, format = 'json_format', params=params)
grid_elements


# In[8]:


# The above data frame contains lists of dictionaries that must be unpacked for viewing the results.
# Unpack the `results` column to view all the meters.
results = cr.unpack_json(grid_elements, 'results')
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
url_grid_explorer = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid_explorer'

params = {
        'limit': '10',
        'offset': '0', 
        'ordering': '{"direction": "ASC", "query_name": "grid_id"}',
        'filter': f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
        'export': 'false',
        'grid_element_type': grid_element_type,
}

grid_elements_pv = cr.get_rest_api(url_grid_explorer, subscription_key, basic_auth, format = 'json_format', params=params)
grid_elements_pv


# In[11]:


# Unpack the `results` column to view the elements.
results = cr.unpack_json(grid_elements_pv, 'results')
results.head()


# In[12]:


# Display all the Photovoltaic grid element IDs.
results.id


# ### Details about a grid element

# In[13]:


# Get details about specific grid elements. 
grid_id = 'awefice'
grid_element = 'm_6'
url_grid_element = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element}'

params = {
        'all_sources': 'true'
}

grid_element_details = cr.get_rest_api(url_grid_element, subscription_key, basic_auth, format = 'json_format', params=params )
grid_element_details


# In[14]:


# Unpack the `grid_data_source` column containing the list of dictionaries.
# The grid_data_source ID represents the linking between time-series data and physical elements on the grid. 
# The ID is used further down in this notebook to retrieve the time series. 
cr.unpack_json(grid_element_details, 'grid_data_sources', ['customer_type', 'meta.address'])


# ### Grid Tracing

# In[15]:


# The example below demonstrates the tracing of transformers from a meter in an upstream direction. The results are saved to a CSV file. 
grid_id = 'awefice'
grid_element_id = 'm_6'
prefix_name = 'upstream_tracing'
url_grid_trace = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/trace'

params = {
        'trace_type': 'upstream',
        'output_type': 'transformer_download',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

grid_element_trace = cr.get_rest_api(url_grid_trace, subscription_key, basic_auth, format = 'csv_download', params=params, element_id=grid_element_id, csv_file_prefix=prefix_name)
grid_element_trace


# In[16]:


# The example below demonstrates the tracing from transformers in a downstream direction.  
# For more information about different types of traces and output sources, please refer to the documentation in the API-connect developer portal.
grid_id = 'awefice'
grid_element_id = 'transformer_2'
url_grid_trace = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/trace'

params = {
        'trace_type': 'downstream',
        'output_type': 'summary',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

grid_element_trace = cr.get_rest_api(url_grid_trace, subscription_key, basic_auth, format = 'json_format', params=params)
grid_element_trace


# In[17]:


# Unpack the `consumers_breakdown` column containing the list of dictionaries. 
cr.unpack_json(grid_element_trace, 'consumers_breakdown')


# ### Time Series
# 
# There are different endpoints for retrieving time series, each suited for different use cases. For more information, refer to the api-connect developer portal documentation.

# In[18]:


# Get time series data associated with a given grid data source.
grid_id = 'awefice'
grid_data_source = '84e32b3f-1cf6-4b8a-9a55-f59ec574f8c1' # From the details grid element above
url_grid_data_source = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/grid_data_source/{grid_data_source}/data'

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

ts = cr.get_rest_api(url_grid_data_source, subscription_key, basic_auth, format = 'json_format', params=params)
ts


# In[19]:


# Unpack the time series and include the `units` column. 
grid_element_ts = cr.unpack_json(ts, 'series', ['units'])
grid_element_ts


# In[20]:


# Get consumption data for any grid element with associated grid data sources of type CONSUMER. 
# This example exports the result to a CSV file. 
grid_id = 'awefice'
grid_element_id = 'm_6'
prefix_name = 'time_series'
url_grid_ts_consumer = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element_id}/data'

params = {
        'units': 'kWh',
        'group_by': 'hour',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-03-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/Vancouver',
        'export': 'true'
}

grid_element_ts = cr.get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth,  format = 'csv_download', params=params, element_id=grid_element_id, csv_file_prefix=prefix_name)
grid_element_ts


# The Following cells use the North Central Zone grid. To run the code in these cells it in necessary to have access credentials and point the notebook to the Sandbox tier 2&3 server

# In[21]:


# Get any kind of time-series data for a grid element. 
grid_id = 'North Central Zone'
grid_element_id = '7676'
url_grid_ts_consumer = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element_id}/flexible_time_series_data'

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

ts = cr.get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth,  format = 'json_format', params=params)
ts


# In[22]:


# Unpack the time series for the different phases and include the `units`, and `source.friendly_id` columns.
phase_ts = cr.unpack_json(ts, 'series', ['phase', 'units', 'source.friendly_id'])
phase_ts


# In[23]:


# Get per-phase time series data for each sensor (raptor or SCADA) associated with the given grid element.
grid_id = 'North Central Zone'
grid_element_id = '10680'
url_grid_ts_consumer = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element_id}/chart_data'

params = {
        'units': 'kWh',
        'group_by': 'day',
        'start': '2021-01-01T08:00:00.000000Z',
        'end': '2021-04-01T08:00:00.000000Z',
        'max_points': '2000',
        'timezone': 'America/Vancouver',
        'include_sign': 'false'
}

ts = cr.get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth, format = 'json_format', params=params)
ts


# In[24]:


# Unpack the time series for the different phases and add the `units` column. 
per_phase = cr.unpack_json(ts, 'series', ['phase', 'units'])
per_phase


# ---

# In[25]:


# Delete the credential variables for security purposes.
del subscription_key, basic_auth

