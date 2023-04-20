#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Mirror existing TGI functionalities in a notebook format.
# * Demonstrate a use case: Transformer Asset Management.
# 
# For more details about transformer asset management, particularly as it relates to outages, and how it can be analyzed using Awesense's platform, please refer to the [UC24-01 - Analysis of Planned Outage for Assets Upgrades.pdf](../usecase_descriptions/UC24-01%20-%20Analysis%20of%20Planned%20Outage%20for%20Assets%20Upgrades.pdf) document.

# ## Set up

# In[1]:


import getpass
import pandas as pd
import numpy as np
import plotly.express as px
import folium
import urllib.parse

pd.set_option('display.max_columns', None)


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
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

# Delete the credential variables for security purpose.
del edm_name, edm_password


# ## Example

# The total count of meters downstream of any grid element can be found using the `grid_get_downstream()` function. The equivalent feature in TGI is the `Down` trace option.
# 
# We'll use a grid element ID `line_segment_5` in the `awefice` grid.

# In[3]:


grid_id = 'awefice'
grid_element_id = 'line_segment_5'


# Check when the grid was last updated.

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT last_updated\nFROM grid \nWHERE grid_id = '{grid_id}';")


# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(*) \nFROM grid_get_downstream('{grid_id}','{grid_element_id}') \nWHERE type = 'Meter';")


# *Note*: The `grid_id` for grids can be found in the `grid` view, and the `grid_element_id` for grid elements can be found in the `grid_element` view.

# **Use Case: Transformer Asset Management Outage**

# Let's take a look at the transformers downstream of the grid element ID `line_segment_5` in the `awefice` grid.

# In[6]:


# Get the transformer information.
result = get_ipython().run_line_magic('sql', "SELECT *,                 ST_Y(geometry) as geo_latitude,                 ST_X(geometry) as geo_longitude             FROM grid_get_downstream('{grid_id}','{grid_element_id}')             WHERE type LIKE 'Transformer';")

# Turn the query result into a dataframe to work easily in Python.
df = result.DataFrame()

# Pull out the information from `meta` column saved as JSONB.
df = pd.concat([df.drop(['meta'], axis=1),
                df['meta'].apply(pd.Series)], 
               axis=1)

# Return the first few rows. 
df.head()


# We can easily visualize the data, such as the breakdown of transformers' phases, their ages, etc.

# In[7]:


# Create a dataframe of counts by phases.
df_phases_cts = df[['grid_element_id','phases']].groupby('phases').count().reset_index()                 .rename(columns={'grid_element_id':'count'})

# Create a pie chart.
fig = px.pie(df_phases_cts, 
             values='count', names='phases',
             title='Division of Transformers Downstream of ' + grid_element_id)

fig.update_traces(textposition='inside', textinfo='percent+label+value')


# In[8]:


# Calculate age as the number of years passed since commission date to today.
df['age'] = (pd.to_datetime('now') - pd.to_datetime(df['commission_date'])) / np.timedelta64(1,'Y')

# Create a histogram of transformer age.
fig = px.histogram(df['age'], x='age',
                   title='Transformer Age Distribution')

fig.update_layout(xaxis_title_text='Age (years)',
                  yaxis_title_text='Count', 
                  bargap=0.2)


# From the histogram above, we can see that there are 2 transformers that are 40 years of age or greater. This can also be identified by filtering out the data directly as the below.

# In[9]:


# Filter the dataset for transformers with 40 years of age or greater.
df2 = df[df['age'] >= 40]

# Return the number of rows.
df2.shape[0]


# Let's see where these transformers are on the map.

# In[10]:


# Configure coordinates to display on the map.
map_latitude = df['geo_latitude'].mean()
map_longitude = df['geo_longitude'].mean()

# Create the map.
m = folium.Map(location=[map_latitude, map_longitude], zoom_start=12)

for _, row in df2.iterrows():
    folium.Marker(location=[row['geo_latitude'],row['geo_longitude']],
                  tooltip=round(row['age'],1)).add_to(m)

display(m)


# We can easily drill into the details of those transformers. For example, let's take a look at their KVA ratings.

# In[11]:


# Create a histogram of KVA ratings.
fig = px.histogram(df2['rating_kva'], x="rating_kva", nbins=10)

fig.update_layout(
    title_text='Transformer kVA distribution',
    xaxis_title_text='Rating [kVA]',
    yaxis_title_text='Count', 
    bargap=0.2,
)

fig.show()


# ---

# In[ ]:




