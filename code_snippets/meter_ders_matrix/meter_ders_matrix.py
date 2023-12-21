#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to provide some snippets of code summarizing the distribution of DERs of various types across meters of various types, including the combinations in which DERs appear together behind meters.

# ## Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import numpy as np


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

# Delete the credential variables for security purposes.

del edm_name, edm_password


# ## DER combinations behind meters

# **Input Parameters**
# 
# Enter the grid ID of interest.
# 
# The example below uses the North Central Zone grid, which is only available on Awesense tiers 2 & 3 environments.  

# In[3]:


grid_id = input('Enter grid ID: ') # North Central Zone


# **Retrieve all meter-DER pairs where the DER is downstream of (behind) the meter.**

# In[4]:


get_ipython().run_cell_magic('sql', 'meter_ders << SELECT ggs.grid_element_id as meter_id,', "    ggs.meta ->> 'type_of_consumer' as meter_type,\n    ge.type as der_type,\n    ge.grid_element_id as der_id\nFROM grid_element ge\nJOIN grid_get_sources('{grid_id}', ge.grid_element_id, True) ggs ON True\nWHERE ge.grid_id = '{grid_id}' AND ggs.type = 'Meter'\n    AND (ge.type='EVCharger' OR ge.type='Photovoltaic' OR ge.type='Battery')\nORDER BY meter_type, meter_id;\n")


# In[5]:


# Convert the results to a data frame. 
df_meter_ders = meter_ders.DataFrame()
df_meter_ders


# **Find the DER combinations**

# In[6]:


# Pivotal the table by DER type
df_matrix = df_meter_ders.pivot(index=['meter_type', 'meter_id'], columns='der_type', values='der_id')
df_matrix


# In[7]:


# Label the DER combinations
df_matrix['DERs Present'] = (df_matrix['Battery'].isnull().map({True: '_', False: 'B'}) if np.any(df_matrix.columns == 'Battery') else '_')\
    + (df_matrix['EVCharger'].isnull().map({True: '_', False: 'E'}) if np.any(df_matrix.columns == 'EVCharger') else '_') \
    + (df_matrix['Photovoltaic'].isnull().map({True: '_', False: 'P'}) if np.any(df_matrix.columns == 'Photovoltaic') else '_')
df_matrix


# In[8]:


# Pivot again to list and count the meters for each DER combination, grouped by meter type.
df_matrix.rename_axis(None, axis=1).reset_index().pivot_table(index=['meter_type'], columns='DERs Present', values='meter_id', aggfunc=lambda x: str(len(x)) + ': ' + ', '.join(x.astype(str)))

