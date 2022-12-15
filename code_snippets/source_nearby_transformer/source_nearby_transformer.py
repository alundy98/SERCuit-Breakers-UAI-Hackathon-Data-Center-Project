#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the source transformer and the closest transformer of a specific meter. 
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Define the meter of interest.
# * Display the meter's source transformer.
# * Calculate and display the closest transformer to the meter.
# * Plot the distance between the meter and all the transformers in the grid. 
# 
# The gewspecial insights derived from these results may be helpful in multiple use cases ranging from Data Quality Improvement to Grid Planning.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
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

# ## Source Transformer and Closest Transformer

# #### Input Parameters
# Enter the grid ID of interest.

# In[3]:


grid_id = input('Enter grid ID: ') # awefice


# Display all the Meters in the Grid

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT grid_element_id\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Meter';")


# Enter the meter ID of interest.

# In[5]:


meter_id = input('Enter meter ID: ') # m_10


# Fetch the meter's source transformer.

# In[6]:


meter = get_ipython().run_line_magic('sql', "SELECT '{meter_id}' as meter_id,     ggs.grid_element_id AS source_transformer_id FROM grid_element ge JOIN grid_get_sources('{grid_id}', '{meter_id}', true) ggs     ON '{meter_id}' = ge.grid_element_id LEFT JOIN grid_get_same_voltage('{grid_id}', ggs.grid_element_id) ggsv     ON true WHERE ggs.type = 'Transformer'     AND ggsv.grid_element_id = '{meter_id}';")

meter


# Fetch the closest transformer to the meter and display the results.

# In[7]:


get_ipython().run_cell_magic('sql', '', "SELECT ge_m.grid_element_id as meter_id,\n    ge_t.grid_element_id as closest_transformer_id,\n    ge_t.geometry <-> ge_m.geometry ::geography as distance_m\nFROM grid_element ge_m\nJOIN grid_element ge_t\n    ON true\nWHERE ge_m.grid_id = '{grid_id}' \n    AND ge_m.grid_element_id = '{meter_id}'\n    AND ge_t.type = 'Transformer'\nORDER BY distance_m \nLIMIT 1;")


# Plot the distance between the meter of interest and all the transformers in the grid. 

# In[8]:


get_ipython().run_cell_magic('sql', 'transformer_distance <<', "SELECT ge_m.grid_element_id as meter_id,\n    ge_t.grid_element_id as transformer_id,\n    ge_t.geometry <-> ge_m.geometry ::geography as distance_m\nFROM grid_element ge_m\nJOIN grid_element ge_t\n    ON true\nWHERE ge_m.grid_id = '{grid_id}'\n    AND ge_m.grid_element_id = '{meter_id}'\n    AND ge_t.type = 'Transformer'\nORDER BY distance_m;")


# In[9]:


# Convert the results to data frames. 
df_meter = meter.DataFrame()
df_transformers = transformer_distance.DataFrame()


# Define a different colour for the closest transformer.
df_transformers['color'] = 'red'
df_transformers['color'].where(df_transformers['transformer_id']==df_meter.loc[0,'source_transformer_id'], 'blue', inplace=True)

# Plot the distance. 
fig = px.bar(df_transformers, x='distance_m', y='transformer_id', color='color', 
             color_discrete_sequence=df_transformers.color.unique(),
            labels={'transformer_id' : 'Transformer ID', 'distance_m' : 'Distance (m)'},
            title='<b>Distance of Transformers from Meter {} </b> \
            <br> The source transformer is depicted in red <br>'.format(meter_id))

# Do not show the legend.
fig.update_layout(showlegend=False)

# Plot the bars in descending order.
fig.update_layout(barmode='stack', yaxis={'categoryorder':'total ascending'})

fig.show()


# ---
