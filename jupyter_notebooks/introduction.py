#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Introduce core views and functions available in Awesense's Energy Data Models (EDM).
# * Demonstrate various ways to fetch and work with data using SQL and Python.
# 
# For complete documentation on the available views and functions, please refer to the [edm_overview.ipynb](edm_overview.ipynb) notebook.

# ---

# ## Set up

# In[1]:


import getpass
import urllib.parse


# **Connection**

# Enter the full EDM server address to connect to (e.g. sandbox-edm.awesense.com), and the login credentials provided by Awesense.
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

# ## Views

# **Grid**

# The term `grid` represents a portion of a utility's grid, and the term `grid element` represents the physical elements that comprise the grid.
# 
# The view named `grid` lists available grids in the database, and `grid_id` is the primary key of the grid.

# In[3]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM grid;')


# **Grid Element**

# The `grid_element` view contains information about grid elements in a grid. Properties that are common to all grid elements, such as `phases`, are explicitly listed as columns of the view. And additional metadata that may differ between grid elements of different types (e.g. meter vs. transformers, etc.) are stored as JSONB in the `meta` column. 
# 
# The `grid_id` column from the `grid_element` view references the `grid_id` from the `grid` view. And the `grid_id` and `grid_element_id` columns are often used as input arguments to various functions, as demonstrated in the **Functions** section below and in the [tgi_tracing.ipynb](tgi_tracing.ipynb) notebook. 

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element\nWHERE grid_id = 'awefice'\nLIMIT 1;")


# Below are different grid element types available in the `awefice` grid. 
# 
# A CSV file detailing all grid element types in the `Sandbox` server and their respective properties that the Awesense Platform recognizes can be found [here](https://sandbox.awesense.com/docs/_static/gis_metadata.csv).

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT DISTINCT type as element_type\nFROM grid_element\nWHERE grid_id = 'awefice'\nORDER BY element_type;")


# A complete list of properties available in the `meta` column for a grid element can be returned using the built-in `json_each_text()` function. The following is an example of accessing each item in the `meta` column with the 'awefice' `grid_id` and the 'm_12' `grid_element_id`.

# In[6]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ge.grid_element_id, \n        meta_data.key, \n        meta_data.value\nFROM grid_element ge\nJOIN json_each_text(ge.meta::json) AS meta_data\n    ON true\nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'm_12';")


# Specific properties from the `meta` column can be fetched individually as well.

# In[7]:


get_ipython().run_cell_magic('sql', '', "\nSELECT meta ->> 'longitude' as longitude,\n        meta ->> 'latitude' as latitude\nFROM grid_element \nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'm_12';")


# **Grid Element Data Source**

# The `grid_element_data_source` view represents the linking between time-series data and physical elements on the grid. The primary key of this view is `grid_element_data_source_id`, and is used as an input argument of the `ts_data_source_select()` function to retrieve time series data, as demonstrated in the **Functions** section below and in the [time_series_access.ipynb](time_series_access.ipynb) notebook.

# In[8]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM grid_element_data_source\nLIMIT 5;')


# The `metrics` column is of type text[] and lists the units of measurements for time-series that can be retrieved for a given grid element. Please note that it is possible to have multiple metrics associated with the same `grid_element_data_source_id`. The following is an example of pulling out multiple grid element data sources available for the 'awefice' `grid_id` and the 'm_12' `grid_element_id`.

# In[9]:


get_ipython().run_cell_magic('sql', '', "\nSELECT geds.grid_element_data_source_id, \n        geds.grid_id, \n        geds.grid_element_id, \n        metric_key \nFROM grid_element_data_source geds\nJOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n    ON true\nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'm_12';    ")


# The `provider` column represents the origin of the stored data, and may be used to properly identify the grid_element_data_source for the time series of interest.

# In[10]:


get_ipython().run_cell_magic('sql', '', '\nSELECT distinct provider\nFROM grid_element_data_source;')


# ---

# ## Functions

# **grid_get_downstream()**

# The `grid_get_downstream()` function is one of tracing functions available in EDM, and is mentioned here to demonstrate a function with grid and grid element arguments. Its input arguments can be found by running the following query.
# 
# Please refer to the [tgi_tracing.ipynb](tgi_tracing.ipynb) notebook for examples of all tracing functions.
# 

# In[11]:


get_ipython().run_cell_magic('sql', '', "\nSELECT function_args\nFROM get_function_documentation('grid_get_downstream');")


# Below is an example with the 'awefice' `grid_id`, the 'm_10' `grid_element_id` and False to exclude the specified `grid_element_id` from the output list.

# In[12]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_get_downstream('awefice', 'm_10', False);")


# **ts_data_source_select()**

# The `ts_data_source_select()` returns time series data for the given `id`, `metric_key`, and `timerange` input arguments.
# 
# Please note that the `id` argument refers to the `grid_element_data_source_id` from the `grid_element_data_source` table discussed in the above. And the `metric_key` is one of the metrics listed in the `metrics` column in the same table. Below is an example of retrieving kWh time series for the 'm_12' grid_element (whose grid_element_data_source_id is '96ecaf9c-4c66-419d-a711-9d9027ff5f28') for April 1 ~ 30, 2022 time range.

# In[13]:


get_ipython().run_cell_magic('sql', '', "\nSELECT timestamp, value\nFROM ts_data_source_select('96ecaf9c-4c66-419d-a711-9d9027ff5f28', 'kWh', '[2022-04-01, 2022-04-30]'\n                          )\nLIMIT 5;")


# More generally, time series for multiple grid elements and metrics over the entire period can be retrieved by joining multiple tables as the following. Please note that the query output is ordered and limited for a demonstration purpose.

# In[14]:


get_ipython().run_cell_magic('sql', '', "\nSELECT geds.grid_element_id, \n        metric_key, \n        tdss.timestamp, \n        tdss.value\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_id = ge.grid_id\n    AND geds.grid_element_id = ge.grid_element_id\nJOIN UNNEST(geds.metrics::TEXT[]) metric_key\n    ON true\nJOIN ts_data_source_select(geds.grid_element_data_source_id, metric_key) tdss\n    ON true\nWHERE ge.grid_id = 'awefice'\n    AND ge.type = 'Meter'\nORDER by 3\nLIMIT 5;")


# ---

# ## SQL & Python

# **Data Retrieval**

# Below are a few ways to save SQL output into a local Python variable.

# Option 1

# In[15]:


get_ipython().run_cell_magic('sql', 'result <<', "\nSELECT grid_element_id, type\nFROM grid_element\nWHERE grid_id = 'awefice';")


# Option 2

# In[16]:


# Save the sql output as a variable in one line.
result = get_ipython().run_line_magic('sql', "SELECT grid_element_id, type FROM grid_element WHERE grid_id = 'awefice'")


# Option 3

# In[17]:


# Save the sql output as a variable in multiple lines.
result = get_ipython().run_line_magic('sql', "SELECT grid_element_id, type                 FROM grid_element                 WHERE grid_id = 'awefice'")


# Then the following code can be run to convert the above SQL output `result` into a Python dataframe to work with.

# In[18]:


df = result.DataFrame()

# Return the first three lines of the dataframe to take a peek.
df.head(3)


# **Variable Input**

# A python variable can be integrated into SQL queries by wrapping the variable with `'{}'`.

# In[19]:


grid_element_id = 'm_12'


# In[20]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element_data_source\nWHERE grid_element_id = '{grid_element_id}';")

