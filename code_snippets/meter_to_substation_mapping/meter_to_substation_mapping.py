#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook displays the mapping of meters to their associated high-voltage transformers and substations based on GIS connectivity. Users can input a specific meter for mapping or view the entire grid.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import plotly.express as px

pd.set_option('display.max_rows', None)


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

# ###  Meter to Substation Mapping

# #### Input Parameters
# Enter the grid ID. 

# In[3]:


# User input for the grid.
grid_id = input('Enter grid ID: ') # e.g. North Central Zone


# Get the high-voltage transformer and substation upstream of a specific meter in the grid:

# In[4]:


# User input for the meter.
meter_id = input('Enter meter ID: ') # e.g. 6D4Y23


# In[5]:


# Get the high-voltage transformers and substations upstream of a specific meter in the grid. 
substation_query = """
    SELECT grid_element_id as hv_transformer, 
        meta->> 'enclosure_id' as substation_id
    FROM grid_get_sources('{}', '{}', 'true') 
    WHERE grid_id = '{}'
        AND type = 'Transformer'
        AND meta->> 'voltage_level' = 'HV/MV' ; 
    """
formatted_query = substation_query.format(grid_id, meter_id, grid_id)
substation = get_ipython().run_line_magic('sql', '$formatted_query')

# Convert the results to a data frame and display it. 
df_substation = substation.DataFrame()
df_substation['meter_id'] = meter_id

df_substation


# Get the high-voltage transformer and substations associated with every meter in the grid:

# In[6]:


substations_query = """
    SELECT ge.grid_element_id as hv_transformer_id,
        ggd.grid_element_id as meter_id, 
        ge.meta->> 'enclosure_id' as substation_id
    FROM grid_element ge
    JOIN grid_get_downstream('{}', ge.grid_element_id) ggd
        ON True
    WHERE ge.grid_id = '{}'
        AND ge.type = 'Transformer'
        AND ggd.type = 'Meter'
        AND ge.meta->> 'voltage_level' = 'HV/MV'; 
    """
formatted_query = substations_query.format(grid_id, grid_id)
substations = get_ipython().run_line_magic('sql', '$formatted_query')

# Convert the results to a data frame and display it. 
df_substations = substations.DataFrame().set_index(['substation_id', 'hv_transformer_id', 'meter_id'])

df_substations.sort_values(by=['substation_id', 'hv_transformer_id'])


# In[7]:


# Count the number of meters per transformer. 
meters_per_transformers = df_substations.reset_index().groupby(by=['substation_id', 'hv_transformer_id']).count().rename(
    columns={'meter_id': 'number_of_meters_per_transformer'})
meters_per_transformers


# In[8]:


# Count the number of transformers and meters per substation.
substations = meters_per_transformers.reset_index().groupby(by=['substation_id']).count().reset_index().rename(
    columns={'hv_transformer_id': 'number_of_transformers_per_substation'})
substations['number_of_meters_per_substation'] = meters_per_transformers.reset_index().groupby(by=['substation_id']).sum().reset_index()['number_of_meters_per_transformer']
substations.drop(columns=['number_of_meters_per_transformer'],inplace=True)
substations


# ---
