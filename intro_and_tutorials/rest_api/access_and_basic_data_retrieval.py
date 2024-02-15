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
# The notebook uses the Awesense api-connect gateway: https://api-account.awesense.com/. The gateway contains additional documentation regarding the endpoints used in this notebook, their available parameters and output options. Please refer to it for more information. 
# 
# It is assumed that the user has been given access to it and has the necessary credentials. Otherwise, please contact us at [api@awesense.com](api@awesense.com).

# ---

# ## Set up

# In[1]:


import getpass
import requests
import pandas as pd
import base64

pd.set_option('display.max_columns', None)


# ### Helper Functions

# In[2]:


def get_rest_api(url: str, key: str, auth_key: str, format: str, params: dict = None, element_id: str = None, csv_file_prefix: str = None) -> pd.DataFrame:
    """
    The function calls a REST API endpoint with the appropriate parameters. It handles any errors that may occur during the request
    and returns the results in a data frame format. The returned data frame can be further unpacked to extract specific information as needed. 

    Parameters:
    -url: The URL of the REST API endpoint.
    -key: The subscription key (primary or secondary) from the `Profile` page on the api-connect gateway website. 
    -auth_key: The basic authentication credentials.
    -format: The format in which the request URLs provide the response. REST endpoints return differently formatted JSONs and/or binary (CSV) files.
    -params: A dictionary that may contain additional parameters to pass to the API endpoint. By default, it is set to None.
    -element_id: Name of an element to include in the name of a CSV file. By default, it is set to None.
    -csv_file_prefix: Prefix to include as part of the CSV file name. By default, it is set to None.
    
    Returns:
    -df: The results as a pandas data frame. 
    """
    # Create the request headers
    hdr = {
        'Cache-Control': 'no-cache',
        'Ocp-Apim-Subscription-Key': key,
        'Authorization': auth_key,
    }
    
    try:
        # Make the GET request to the API endpoint
        if params:
            request = requests.get(url, headers=hdr, params=params)
        else:
            request = requests.get(url, headers=hdr)
            
        # Check the response status code
        if request.status_code == 200:
            # Process the response based on the specified format
            if format == 'json_format':
                # convert the returned json object to a data frame
                df = pd.json_normalize(request.json())
            elif format == 'csv_download':
                with open(element_id + '_' + csv_file_prefix + '_csv_output.csv', 'wb') as file:
                    file.write(request.content)
                    print("CSV file downloaded successfully.")
                # Read the CSV file into a Pandas data frame
                print('Below is a display of the CSV file.')
                df = pd.read_csv(element_id + '_' + csv_file_prefix + '_csv_output.csv')
            else:
                print('This is not the correct format. Possible formats are: json_format, and csv_download.')
                df = None
        else:
            print(f'Failed to download. Server response: {request.status_code}')
            print('Response content:', request.text)  # Print the response content for more details
            df = None
        
    except requests.exceptions.RequestException as exceptions:
        print(f'An error occurred while making the HTTP request: {exceptions}')
        df = None
    except Exception as exceptions:
        print(f'An unexpected error occurred: {exceptions}')
        df = None
    
    return df


def unpack_json(df: pd.DataFrame, column_to_unpack: str, columns_to_append: list = None) -> pd.DataFrame:
    """
    Unpacks the list of dictionaries returned by the `get_rest_api` function when used with the `json_format` option. 
    Optionally display additional `columns_to_append` in a data frame. Returns the results in a data frame. 

    Parameters:
    - df: A data frame containing the list of dictionaries.
    - column_to_unpack: The name of the column containing the list of dictionaries.
    - columns_to_append: A list of additional columns to display. The default is None. 

    Return:
    - df_unpacked: The results as a pandas data frame.
    """

    # Create an empty list that will store the results.
    unpacked_dicts = []
    
    for _, row in df.iterrows():
            
            # Unpack the dictionary in the current row.
            unpack = pd.json_normalize(row[column_to_unpack])
            
            if columns_to_append is not None:
                # Loop over the list of columns to be displayed.
                for column in columns_to_append:
                    unpack[column] = row[column]
            
            # Append the unpacked dictionary to the list.
            unpacked_dicts.append(unpack)
        
    # Concatenate all the unpacked dictionaries into a single data frame.
    df_unpacked = pd.concat(unpacked_dicts, ignore_index=True)

    return df_unpacked


# ### Connection Variables

# In[3]:


# Enter the user_name, and password that you use to log into the server.
server_user_name = getpass.getpass(prompt='Username: ')


# In[4]:


server_password = getpass.getpass(prompt='Password: ')


# In[5]:


# Enter the subscription key (primary or secondary) from the `Profile` page on the api-connect gateway website. 
# If you don't have a subscription key, you will need to create one. 
# To do so, go to the `Products` page on the api-connect gateway website, click on the desired product name, enter a product description, and click `Subscribe`. 
subscription_key = getpass.getpass(prompt='Subscription Key: ')


# In[6]:


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

# In[7]:


# Get all the grids. 
url_grid = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid'
grids = get_rest_api(url_grid, subscription_key, basic_auth, format = 'json_format')
grids


# ### Grid Elements

# In[8]:


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

grid_elements = get_rest_api(url_grid_explorer, subscription_key, basic_auth, format = 'json_format', params=params)
grid_elements


# In[9]:


# The above data frame contains lists of dictionaries that must be unpacked for viewing the results.
# Unpack the `results` column to view all the meters.
results = unpack_json(grid_elements, 'results')
results.head()


# In[10]:


# Display all the Meter grid element IDs.
results.id


# In[11]:


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

grid_elements_pv = get_rest_api(url_grid_explorer, subscription_key, basic_auth, format = 'json_format', params=params)
grid_elements_pv


# In[12]:


# Unpack the `results` column to view the elements.
results = unpack_json(grid_elements_pv, 'results')
results.head()


# In[13]:


# Display all the Photovoltaic grid element IDs.
results.id


# ### Details about a grid element

# In[14]:


# Get details about specific grid elements. 
grid_id = 'awefice'
grid_element = 'm_6'
url_grid_element = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element}'

params = {
        'all_sources': 'true'
}

grid_element_details = get_rest_api(url_grid_element, subscription_key, basic_auth, format = 'json_format', params=params )
grid_element_details


# In[15]:


# Unpack the `grid_data_source` column containing the list of dictionaries.
# The grid_data_source ID represents the linking between time-series data and physical elements on the grid. 
# The ID is used further down in this notebook to retrieve the time series. 
unpack_json(grid_element_details, 'grid_data_sources', ['customer_type', 'meta.address'])


# ### Grid Tracing

# In[16]:


# The example below demonstrates the tracing of transformers from a meter in an upstream direction. The results are saved to a CSV file. 
grid_id = 'awefice'
grid_element_id = 'm_6'
end_point_name = 'upstream_tracing'
url_grid_trace = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/trace'

params = {
        'trace_type': 'upstream',
        'output_type': 'transformer_download',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

grid_element_trace = get_rest_api(url_grid_trace, subscription_key, basic_auth, format = 'csv_download', params=params, element_id=grid_element_id, csv_file_prefix=end_point_name)
grid_element_trace


# In[17]:


# The example below demonstrates the tracing from transformers in a downstream direction.  
# For more information about different types of traces and output sources, please refer to the documentation in the API-connect gateway.
grid_id = 'awefice'
grid_element_id = 'transformer_2'
url_grid_trace = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/trace'

params = {
        'trace_type': 'downstream',
        'output_type': 'summary',
        'grid_id': grid_id,
        'grid_element_id': grid_element_id,
}

grid_element_trace = get_rest_api(url_grid_trace, subscription_key, basic_auth, format = 'json_format', params=params)
grid_element_trace


# In[18]:


# Unpack the `consumers_breakdown` column containing the list of dictionaries. 
unpack_json(grid_element_trace, 'consumers_breakdown')


# ### Time Series
# 
# There are different endpoints for retrieving time series, each suited for different use cases. For more information, refer to the api-connect gateway documentation.

# In[19]:


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

ts = get_rest_api(url_grid_data_source, subscription_key, basic_auth, format = 'json_format', params=params)
ts


# In[20]:


# Unpack the time series and include the `units` column. 
grid_element_ts = unpack_json(ts, 'series', ['units'])
grid_element_ts


# In[21]:


# Get consumption data for any grid element with associated grid data sources of type CONSUMER. 
# This example exports the result to a CSV file. 
grid_id = 'awefice'
grid_element_id = 'm_6'
end_point_name = 'time_series'
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

grid_element_ts = get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth,  format = 'csv_download', params=params, element_id=grid_element_id, csv_file_prefix=end_point_name)
grid_element_ts


# In[22]:


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

ts = get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth,  format = 'json_format', params=params)
ts


# In[23]:


# Unpack the time series for the different phases and include the `units`, and `source.friendly_id` columns.
phase_ts = unpack_json(ts, 'series', ['phase', 'units', 'source.friendly_id'])
phase_ts


# In[24]:


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

ts = get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth, format = 'json_format', params=params)
ts


# In[25]:


# Unpack the time series for the different phases and add the `units` column. 
per_phase = unpack_json(ts, 'series', ['phase', 'units'])
per_phase


# ---

# In[26]:


# Delete the credential variables for security purposes.
del subscription_key, basic_auth

