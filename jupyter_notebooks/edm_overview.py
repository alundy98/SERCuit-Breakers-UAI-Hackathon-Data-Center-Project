#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Show how to connect to Awesense's Energy Data Models (EDM).
# * Access various documentations on the available functions and views. 

# ## Set up

# In[1]:


import getpass
import urllib.parse


# **Connection** 

# Enter the full EDM server address to connect to (e.g. sandbox-edm.awesense.com), and the login credentials provided by Awesense. \
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


edm_address = input('EDM server address: ')

print('\nEDM login information')
edm_name = getpass.getpass(prompt='Username: ')
edm_password = getpass.getpass(prompt='Password: ')
edm_password = urllib.parse.quote(edm_password)

get_ipython().run_line_magic('load_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://$edm_name:$edm_password@$edm_address/edm')
get_ipython().run_line_magic('config', 'SqlMagic.displaycon = False')
get_ipython().run_line_magic('config', 'SqlMagic.feedback = False')


# ## Documentation

# Use the `get_function_documentation()` function to return the details of all available functions.

# In[3]:


get_ipython().run_cell_magic('sql', '', '\nSELECT function_name, function_args, description\nFROM get_function_documentation();')


# Use the `get_class_documentation()` function to return the information for all views, tables, and types in the schema.

# In[4]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM get_class_documentation();')


# The `class_name` and `function_name` arguments in the `get_class_documentation()` function can be used to do a wildcard matching. For example, the `class_name='grid'` input returns the results for all classes with the word 'grid' in them. 

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT class_name, column_name, description\nFROM get_class_documentation('grid');")

