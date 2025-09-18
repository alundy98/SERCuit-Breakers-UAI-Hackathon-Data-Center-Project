#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook allows users to generate a histogram from time series data associated with a specific electric meter (grid element of type Meter), based on a selected metric and time range. The data is retrieved from the Awesense Energy Data Model (EDM) via the ts_data_source_select function.
# 
# The user specifies:
# * Grid ID
# * Meter ID
# * Time range (start and end)
# * Metric (e.g., 'V', 'kWh')
# 
# The notebook:
# * Resolves the UUID of the associated data source
# * Checks available metrics
# * Retrieves and filters data in the requested interval
# * Displays a histogram of the selected metric

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import plotly.express as px


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


edm_address = getpass.getpass(prompt='EDM server address: ')

print('\nEDM login information')
edm_name = getpass.getpass(prompt='Username: ')
edm_password = getpass.getpass(prompt='Password: ')
edm_password = urllib.parse.quote(edm_password)

get_ipython().run_line_magic('load_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://$edm_name:$edm_password@$edm_address/edm')
get_ipython().run_line_magic('config', 'SqlMagic.displaycon = False')
get_ipython().run_line_magic('config', 'SqlMagic.feedback = False')

# Delete the credential variables for security purposes.
del edm_name, edm_password


# ---

# #### Input Parameters

# In[7]:


# User inputs
grid_id = input("Enter grid ID (e.g. 'awefice or North Central Zone'): ")
grid_element_id = input("Enter meter ID (e.g. 'm_1 or 10AY3ZA'): ")
start_time = input("Enter start timestamp (e.g. 2024-06-01T00:00:00): ")
end_time = input("Enter end timestamp (e.g. 2024-06-30T23:59:59): ")
desired_metric = input("Enter desired_metric (e.g. 'kWh'): ")  # Change to 'V', 'active_power', etc. as needed


# #### SQL Queries

# In[9]:


# Query data sources for the selected meter
ds_query = f"""
SELECT grid_element_data_source_id, metrics, valid
FROM grid_element_data_source
WHERE grid_element_id = '{grid_element_id}' AND grid_id = '{grid_id}';
"""
ds_result = get_ipython().run_line_magic('sql', '{ds_query}')
ds_df = ds_result.DataFrame()

# Match metric to the correct UUID
matching = ds_df[ds_df['metrics'].apply(lambda m: desired_metric in m)]

if matching.empty:
    print(f"No data source with metric '{desired_metric}' found for meter '{grid_element_id}'.")
else:
    data_source_id = matching['grid_element_data_source_id'].iloc[0]
    validity = matching['valid'].iloc[0]
    print(f"Data source UUID: {data_source_id}")
    print(f"Validity range: {validity}")

    # Query time-series data
    ts_query = f"""
    SELECT value
    FROM ts_data_source_select('{data_source_id}', '{desired_metric}', tstzrange('{start_time}', '{end_time}', '[]'));
    """
    ts_result = get_ipython().run_line_magic('sql', '{ts_query}')
    ts_df = ts_result.DataFrame()

    # Plot histogram using Plotly
    if ts_df.empty:
        print("No time-series data available for the specified time range.")
    else:
        values = ts_df['value'].dropna().to_frame(name=desired_metric)


        fig = px.histogram(
            values,
            nbins=20,
            title=f'Histogram of {desired_metric} values<br>Meter: {grid_element_id} | {start_time[:10]} to {end_time[:10]}',
            labels={'value': desired_metric},
            opacity=0.75
        )
        fig.update_layout(
            xaxis_title=desired_metric,
            yaxis_title='Frequency',
            bargap=0.05,
            template='plotly_white'
        )
        fig.show()


# ---
