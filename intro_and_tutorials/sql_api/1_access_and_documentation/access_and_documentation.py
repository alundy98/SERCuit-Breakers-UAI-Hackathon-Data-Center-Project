#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Show how to connect to Awesense's Energy Data Model (EDM) using SQL API.
# * Access various documentations on the available functions and views. 
# 
# Please refer to the [main_concepts.ipynb](../2_main_concepts/main_concepts.ipynb) notebook for more details about the core views such as `grid`, `grid_element`, and `grid_element_data_source`.

# ---

# ## Set up

# In[1]:


import getpass
import urllib.parse


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


# ---

# ## Documentation

# Use the `get_class_documentation()` function to return the information for all views, tables, and types in the schema.

# In[3]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM get_class_documentation();\n')


# Please note that the `ts_data_source_double` class is the type that the `ts_data_source_select()` function mentioned below returns. 
# 
# Use the `get_function_documentation()` function to return the details of all available functions.

# In[4]:


get_ipython().run_cell_magic('sql', '', '\nSELECT function_name, function_args, description\nFROM get_function_documentation();\n')


# For both `get_class_documentation()` and `get_function_documentation()` functions, their input arguments can be used to do a wildcard matching. For example, the input argument of 'grid' returns the results that contain the word 'grid' in them.

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT class_name, column_name, description\nFROM get_class_documentation('grid');\n")


# In[6]:


get_ipython().run_cell_magic('sql', '', "\nSELECT function_name, function_args, description\nFROM get_function_documentation('grid');\n")

