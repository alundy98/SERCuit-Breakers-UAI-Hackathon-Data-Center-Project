#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the number of transformers in a given grid.
# * Display the number of transformers in a given grid older than twenty years.
# * Display the breakdown of transformers older than twenty years by feeders.
# 
# This snippet is designed as follows:
# 
# * Define the grid of interest.
# * Display the number of transformers in the grid.
# * Display the number of transformers older than twenty years in the grid and aggregate them by feeders.
# * Plot the results.
# 
# Feeders are modelled as having a top transformer that is marked as a producer of energy. This denotes a substation where power shifts from generation and transmission networks to a distribution network.
# 
# The insights derived from these results may be helpful in multiple analytics use cases for areas like Asset Management, Grid Planning or Grid Maintenance.

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

# ## Transformer Age

# #### Input Parameters
# Enter the grid ID of interest.

# In[3]:


grid_id = input('Enter grid ID: ') # awefice


# #### Transformer Count per Grid
# Fetch the number of transformers in the entire grid.

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_transformers\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Transformer';\n")


# Fetch the number of transformers that are older than 20 years.

# In[5]:


get_ipython().run_cell_magic('sql', '', "SELECT COUNT(grid_element_id) as number_of_transformers\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Transformer'\n    AND (meta ->> 'commission_date')::timestamp < (SELECT current_date  - interval '20 year');\n")


# Fetch the transformers older than 20 years, their commission date, and their top feeders. Top feeders are obtained by getting the transformers' main power source (the producer on the path obtained by tracing to the highest_source of power) using the `grid_get_sources` function.
# 
# First, use a single query snippet without saving the results.

# In[6]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ge.grid_element_id as transformer,\n    ge.meta ->> 'commission_date' as commission_date,\n    ggd.grid_element_id as feeder_top_element\nFROM grid_element ge\nLEFT JOIN grid_get_sources('{grid_id}', ge.grid_element_id , 'true') ggd\nON true\nWHERE ge.type = 'Transformer'\n    AND ggd.is_producer = 'True'\n    AND (ge.meta ->> 'commission_date')::timestamp < (SELECT current_date - interval '20 year');\n")


# Second, aggregate the number of transformers per feeder. Save the query results to a dataframe for visualization purposes.

# In[7]:


get_ipython().run_cell_magic('sql', 'feeders <<', "\nSELECT ggd.grid_element_id as feeder_top_element, \n    COUNT(ge.grid_element_id) as number_of_transformers   \nFROM grid_element ge\nLEFT JOIN grid_get_sources('{grid_id}', ge.grid_element_id , 'true') ggd\nON true\nWHERE ge.type = 'Transformer'\n    AND ggd.is_producer = 'True'\n    AND (ge.meta ->> 'commission_date')::timestamp < (SELECT current_date - interval '20 year')\nGROUP BY ggd.grid_element_id;\n")


# In[8]:


# Convert the results to a data frame and display them. 
df_feeder = feeders.DataFrame()
df_feeder


# In[9]:


# Plot the results using a bar chart. 
fig = px.bar(df_feeder.reset_index(), x="feeder_top_element", y="number_of_transformers",  
             title='<b> Number of Transformers Older than 20 Years by Feeder<b>',
             labels={"feeder_top_element": "Top-feeder Elements", "number_of_transformers": "Number of Transformers"})
fig.show()


# ---
