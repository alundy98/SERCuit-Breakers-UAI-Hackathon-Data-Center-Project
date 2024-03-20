#!/usr/bin/env python
# coding: utf-8

# ## Overview

# 
# This notebook is intended to:
# 
# * Display the yearly net flow per tariff group. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid and year of interest.
# * Call the REST API endpoint to get all the meters in the grid of interest. 
# * Loop over all the meters and sum each meter's net flow over the defined year of interest.
# * Aggregate the net flow by tariff groups.
# * Display and plot the results. 
# 
# It is assumed that the user has been given access to the Awesense api-connect developer portal along with the necessary credentials for accessing Sandbox tier 1. Otherwise, please contact us at [api@awesense.com](api@awesense.com). 
# 
# You can find more information about how to use Awesense's api-connect developer portal in the [access_and_basic_data_retrieval](https://github.com/Awesense/edm-app-examples/blob/master/intro_and_tutorials/rest_api/access_and_basic_data_retrieval.ipynb) notebook.

# ---

# ## Setup

# In[1]:


import getpass
import plotly.express as px
import pandas as pd
import base64
from datetime import datetime
import pytz
import callrestapi as cr

pd.set_option('display.max_columns', None)


# **Connection**
# 
# Enter the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


# Enter your username and password to log into the server.
server_user_name = getpass.getpass(prompt='Username: ')


# In[3]:


server_password = getpass.getpass(prompt='Password: ')


# In[4]:


# Enter the subscription key (primary or secondary) from the `Profile` page on the api-connect developer portal website. 
# If you don't have a subscription key, you will need to create one. 
# To do so, go to the `Products` page on the api-connect developer portal website, click on the desired product name, enter a product description, and click `Subscribe`. 
subscription_key = getpass.getpass(prompt='Subscription Key: ')


# In[5]:


# Create a BasicAuth credential based on user_name and password.  
auth_str = server_user_name + ':' + server_password
byte_str = auth_str.encode('ascii')
encoded_data = base64.b64encode(byte_str)
basic_auth = 'Basic ' + str(encoded_data, encoding='utf-8')

# Delete the credential variables for security purposes.
del server_user_name, server_password, auth_str, byte_str


# ---

# ## Net Flow per Tariff Group

# #### Input Parameters
# Enter the grid ID and year. 

# In[6]:


grid_id = input('Enter grid ID: ') # awefice
year = input('Enter year: ') # 2021

# Create a datetime object from the input string.
dt_start = datetime.strptime(year, '%Y')

# Set the desired time zone.
time_zone = pytz.timezone('America/Vancouver')

# Apply the time zone to the datetime object and convert the localized datetime to UTC.
dt_start = time_zone.localize(dt_start)
dt_start_UTC =  dt_start.astimezone(pytz.UTC)

# Set the datetime object to the end of the year and convert the localized time to UTC.
dt_end = dt_start.replace(month=12, day=31, hour=23, minute=59, second=59, microsecond=999999)
dt_end_UTC = dt_end.astimezone(pytz.UTC)

# Format the datetime object into the desired string format.
start_date = dt_start_UTC.strftime('%Y-%m-%dT%H:%M:%S.%fZ')
end_date = dt_end_UTC.strftime('%Y-%m-%dT%H:%M:%S.%fZ')


# In[7]:


# Get all the `grid_elements` of type `Meter`.
grid_element_type = 'Meter'
url_grid_explorer = 'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid_explorer'

params = {
        'limit': '30000',
        'offset': '0', 
        'ordering': '{"direction": "ASC", "query_name": "grid_id"}',
        'filter': f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
        'export': 'false',
        'grid_element_type': grid_element_type,
}

grid_elements = cr.get_rest_api(url_grid_explorer, subscription_key, basic_auth, format = 'json_format', params=params)
grid_elements


# In[8]:


# Unpack the data frame and display 'meter_id' and 'tariff_id`.
df_meters_tariff = cr.unpack_json(grid_elements, 'results')[['id', 'tariff_id']].rename(columns={'id': 'meter_id'}).sort_values(by='tariff_id').reset_index(drop=True)
df_meters_tariff.head()


# In[9]:


# Aggregate the net consumption for each meter.
df_ts = pd.DataFrame()
for meter_id, row in df_meters_tariff.iterrows():
        grid_element_id = df_meters_tariff['meter_id'][meter_id]
        url_grid_ts_consumer = f'https://api-connect.awesense.com/basic_data_retrieval/api/v1/grid/{grid_id}/element/{grid_element_id}/data'

        params = {
                'units': 'kWh',
                'group_by': 'all',
                'start': start_date,
                'end': end_date,
                'max_points': '2000',
                'timezone': 'America/Vancouver',
                'export': 'false'
        }

        ts = cr.get_rest_api(url_grid_ts_consumer, subscription_key, basic_auth,  format = 'json_format', params=params)
        ts['meter_id'] = grid_element_id
        df_ts = pd.concat([df_ts, ts])
df_ts.head()


# In[10]:


# Unpack the results and include the `meter_id` columns.
df_tariff = cr.unpack_json(df_ts, 'series', ['meter_id']).rename(columns={'amount': 'net_kWh'}).drop(columns=['period'])
df_tariff.head()


# In[11]:


# Merge the results with the data frame containing the `tariff_id`.
df_tariff = df_tariff.merge(df_meters_tariff, how='inner', on='meter_id')
df_tariff.head()


# In[12]:


# Group the results by `tariff_id` to calculate total net flow by tariff ID.
df_tariff = df_tariff.groupby(by='tariff_id').sum().reset_index()

# Display the results. 
df_tariff


# In[13]:


# Plot the results. 
fig = px.bar(df_tariff, x="net_kWh",  y="tariff_id", orientation="h", color="net_kWh",
             color_continuous_scale=px.colors.diverging.Temps, color_continuous_midpoint=0,
             title="<b>Net Flow by Tariff Group for the year {}<b>".format(year),
             labels={'tariff_id':'Tariff Group', 
                         'net_kWh': 'Net Flow (kWh)'})
fig.show()


# Negative net flow indicates that production from DERs (in this case, solar panels) exceeds current consumption. 

# ---
