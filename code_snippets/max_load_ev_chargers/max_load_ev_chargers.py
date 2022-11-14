#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Analyze the maximum possible cumulative load from existing EV chargers downstream of a specific transformer.
# 
# Context:
# 
# * Some transformers may have different numbers of EV Chargers downstream from them. Usually, these chargers may not all be in use at the same time, and when in use, they may not provide maximum power at the maximum rate. We would like to know the total power drawn by EV Chargers downstream of a transformer if all EV Chargers were in use at the same time and all at their maximum rate, i.e. the maximum possible cumulative load on a transformer due to EV Chargers alone. This metric can be used for various planning and reporting purposes.
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Define the transformer of interest.
# * Sum the active charging power of all the EV chargers downstream of this transformer.
# * Fetch all the EV chargers in the grid, their active charging power, parent transformers, and top feeders.
# * Aggregate the EV chargers by transformers and sum the active charging power.
# * Plot the results to show the sum of active charging power per transformer.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import plotly.express as px
from natsort import natsort_keygen


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

# ###  Sum of Active Charging Power 

# #### Input Parameters
# Enter the grid ID. 

# In[3]:


# User input for the grid.
grid_id = input('Enter grid ID: ') # awefice


# #### Analysis of Specific Transformer

# In[4]:


# User input for the transformer.
grid_element_id = input('Enter transformer ID: ') # transformer_2


# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT SUM(CAST(meta ->> 'active_charging_power' AS float)/1000.00) as sum_active_charging_power_kW\nFROM grid_get_downstream('{grid_id}', '{grid_element_id}', 'false')\nWHERE type = 'EVCharger';")


# #### Analysis of All Transformers with Downstream EV Chargers
# Fetch all the EV chargers in the grid, their active charging power, parent transformers, and top feeders. 

# In[6]:


get_ipython().run_cell_magic('sql', 'result_ev_chargers <<', "\nSELECT ge.grid_element_id as ev_charger, \n    CAST(ge.meta ->> 'active_charging_power' AS float)/1000.00 as sum_active_charging_power_kW,\n    ggs.grid_element_id as transformer_id,\n    ggs.is_producer as top_feeder\nFROM grid_element ge\nJOIN grid_get_sources('{grid_id}', ge.grid_element_id, 'true') ggs\n    ON true \nWHERE ge.type = 'EVCharger'\n    AND ggs.type = 'Transformer' \nORDER BY ggs.grid_element_id, ge.grid_element_id;")


# In[7]:


# Convert the results to a data frame. 
df_evchargers = result_ev_chargers.DataFrame()

df_evchargers = df_evchargers.sort_values(by=['transformer_id','ev_charger'], key=natsort_keygen())

# Set up a multi-index data frame.
df_evchargers = df_evchargers.set_index(['transformer_id', 'ev_charger'])

# Display the results.
df_evchargers


# In[8]:


# Aggregate the sum of the active charging power based on parent transformers and top feeders. 
df_evchargers_sum = df_evchargers.groupby('transformer_id').sum().rename(columns={'top_feeder': 'number_ev_chargers'}).replace(0,1)

# Sort the results based on the parent/feeder transformer_id
df_evchargers_sum.sort_index(key=natsort_keygen(), inplace=True)

#df_evchargers_sum.sort_index(key=lambda x: (x.to_series().str[12:].astype(int)), inplace=True)

# Display the results. 
df_evchargers_sum


# #### Visualization

# In[9]:


# Plot the results. 
fig = px.bar(df_evchargers_sum.reset_index(), x='transformer_id', y='sum_active_charging_power_kw', 
             color='number_ev_chargers', color_continuous_scale='Portland',
             title='Sum of Active Power per Transformer and the Number of EV Chargers Downstream from the Transformer',
             text='number_ev_chargers',
             labels={'transformer_id' : 'Transformer ID', 'active_charging_power_kw': 'Sum of Active Charging Power (kW)',
                                              'number_ev_chargers': 'Number of Chargers'})
# Rotate the x-axis ticks.
fig.update_xaxes(tickangle=-90)

# Update the text's parameters in the graph. 
fig.update_traces(textfont_size=12, textangle=0, textposition="outside", cliponaxis=False)

# Include a sentence explaining the value on top of the bars.
fig.add_annotation(dict(x=0.37, y=0.6, ax=0, ay=0,
                    xref = "paper", yref = "paper",
                    text= "The value above the bars indicates the number of <br> EV chargers downstream from this transformer"))
fig.show()


# ---
