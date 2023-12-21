#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Introduce core views and functions available in Awesense's Energy Data Model (EDM).
# * Demonstrate various ways to fetch and work with data using SQL and Python.
# 
# For complete documentation on the available views and functions, please refer to the [access_and_documentation.ipynb](../1_access_and_documentation/access_and_documentation.ipynb) notebook.

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


edm_address = getpass.getpass(prompt="EDM server address: ")

print("\nEDM login information")
edm_name = getpass.getpass(prompt="Username: ")
edm_password = getpass.getpass(prompt="Password: ")
edm_password = urllib.parse.quote(edm_password)

get_ipython().run_line_magic('load_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://$edm_name:$edm_password@$edm_address/edm')
get_ipython().run_line_magic('config', 'SqlMagic.displaycon = False')
get_ipython().run_line_magic('config', 'SqlMagic.feedback = False')

# Delete the credential variables for security purposes.
del edm_name, edm_password


# ---

# ## Views

# **Grid**

# The term `grid` represents a portion of a utility's grid, and the term `grid element` represents the physical elements that comprise the grid.
# 
# The view named `grid` lists available grids in the database, and `grid_id` is the primary key of the grid.

# In[3]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM grid;\n')


# **Grid Element**

# The `grid_element` view contains information about grid elements in a grid. Properties that are common to all grid elements, such as `phases`, are explicitly listed as columns of the view. And additional metadata that may differ between grid elements of different types (e.g. meter vs. transformers, etc.) are stored as JSONB in the `meta` column. 
# 
# The `grid_id` column from the `grid_element` view references the `grid_id` from the `grid` view. And the `grid_id` and `grid_element_id` columns are often used as input arguments to various functions, as demonstrated in the **Functions** section below and in the [grid_tracing.ipynb](../3_grid_tracing/grid_tracing.ipynb) notebook. 

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element\nWHERE grid_id = 'awefice'\nLIMIT 1;\n")


# Below are different grid element types available in the `awefice` grid. 
# 
# A CSV file detailing all grid element types in the `Sandbox` server and their respective properties that the Awesense Platform recognizes can be found [here](https://sandbox.awesense.com/docs/_static/gis_metadata.csv).

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT DISTINCT type as element_type\nFROM grid_element\nWHERE grid_id = 'awefice'\nORDER BY element_type;\n")


# A complete list of properties available in the `meta` column for a grid element can be returned using the built-in `json_each_text()` function. The following is an example of accessing each item in the `meta` column with the 'awefice' `grid_id` and the 'transormer_2' `grid_element_id`.

# In[6]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ge.grid_element_id, \n        meta_data.key, \n        meta_data.value\nFROM grid_element ge\nJOIN json_each_text(ge.meta::json) AS meta_data\n    ON true\nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'transformer_2';\n")


# Specific properties from the `meta` column can be fetched individually as well.

# In[7]:


get_ipython().run_cell_magic('sql', '', "\nSELECT meta ->> 'rating_kva' as rating_kva,\n       meta ->> 'commission_date' as commission_date\nFROM grid_element \nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'transformer_2';\n")


# The `geometry` field can be easily converted to `latitude` and `longitude` values using postGIS functions.
# 
# For grid elements that are single points, such as `transformers`:

# In[8]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ST_Y(geometry) as latitude,\n        ST_X(geometry) as longitude      \nFROM grid_element \nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'transformer_2';\n")


# For grid elements such as `line_segment` with starting and ending points, `geometry` can be used to display the `latitude` and `longitude` of the central point of the line as well as the lines' start and end coordinates:

# In[9]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ST_Y(ST_Centroid(geometry)) as center_point_latitude,\n    ST_X(ST_Centroid(geometry)) as center_point_longitude,\n    ST_Y(ST_StartPoint(geometry)) as start_point_latitude, \n    ST_X(ST_StartPoint(geometry)) as start_point_longitude,\n    ST_Y(ST_EndPoint(geometry)) as end_point_latitude, \n    ST_X(ST_EndPoint(geometry)) as end_point_longitude   \nFROM grid_element\nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'line_segment_81';\n")


# **Grid Element Data Source**

# The `grid_element_data_source` view represents the linking between time-series data and physical elements on the grid. The primary key of this view is `grid_element_data_source_id`, and is used as an input argument of the `ts_data_source_select()` function to retrieve time series data, as demonstrated in the **Functions** section below and in the [time_series.ipynb](../4_time_series/time_series.ipynb) notebook.

# In[10]:


get_ipython().run_cell_magic('sql', '', '\nSELECT *\nFROM grid_element_data_source\nLIMIT 5;\n')


# The `type` field refers to the class of stored data, and there may be multiple `type` values for a given grid element. To retrieve the exact time series data of interest, filter the `grid_element_data_source` by the `type` as below.

# In[11]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element_data_source\nWHERE type = 'CONSUMER'\nLIMIT 5;\n")


# The `metrics` column is of type text[] and lists the units of measurement for time-series that can be retrieved for a given grid element. Please note that it is possible to have multiple metrics associated with the same `grid_element_data_source_id`. The following is an example of pulling out multiple grid element data sources available for the 'awefice' `grid_id` and the 'm_12' `grid_element_id`.

# In[12]:


get_ipython().run_cell_magic('sql', '', "\nSELECT geds.grid_element_data_source_id, \n        geds.grid_id, \n        geds.grid_element_id, \n        metric_key \nFROM grid_element_data_source geds\nJOIN UNNEST(geds.metrics::TEXT[]) AS metric_key\n    ON true\nWHERE grid_id = 'awefice'\n    AND grid_element_id = 'm_12';    \n")


# The `provider` column represents the origin of the stored data, and may be used to properly identify the grid_element_data_source for the time series of interest.

# In[13]:


get_ipython().run_cell_magic('sql', '', '\nSELECT distinct provider\nFROM grid_element_data_source;\n')


# ---

# ## Functions

# **grid_get_downstream()**

# The `grid_get_downstream()` function is one of the tracing functions available in EDM, and is mentioned here to demonstrate a function with grid and grid element arguments. Its input arguments can be found by running the following query.
# 
# Please refer to the [grid_tracing.ipynb](../3_grid_tracing/grid_tracing.ipynb) notebook for examples of all tracing functions.
# 

# In[14]:


get_ipython().run_cell_magic('sql', '', "\nSELECT function_args\nFROM get_function_documentation('grid_get_downstream');\n")


# Below is an example with the 'awefice' `grid_id`, the 'm_10' `grid_element_id` and False to exclude the specified `grid_element_id` from the output list.

# In[15]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_get_downstream('awefice', 'm_10', False);\n")


# **ts_data_source_select()**

# The `ts_data_source_select()` returns time series data for the given `id`, `metric_key`, and `timerange` input arguments.
# 
# Please note that the `id` argument refers to the `grid_element_data_source_id` from the `grid_element_data_source` table discussed in the above. And the `metric_key` is one of the metrics listed in the `metrics` column in the same table. Below is an example of retrieving V time series for the 'm_12' grid_element (whose grid_element_data_source_id is '96ecaf9c-4c66-419d-a711-9d9027ff5f28') for April 1 ~ 30, 2022 time range.

# In[16]:


get_ipython().run_cell_magic('sql', '', "\nSELECT timestamp, value\nFROM ts_data_source_select('96ecaf9c-4c66-419d-a711-9d9027ff5f28', 'V', '[2022-04-01, 2022-04-30]'\n                          )\nLIMIT 5;\n")


# More generally, time series for multiple grid elements and metrics over the entire period can be retrieved by joining multiple tables as the following. Please note that the query output is ordered and limited for demonstration purposes.

# In[17]:


get_ipython().run_cell_magic('sql', '', "\nSELECT geds.grid_element_id, \n        geds.type,\n        metric_key, \n        tdss.timestamp, \n        tdss.value\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_id = ge.grid_id\n    AND geds.grid_element_id = ge.grid_element_id\nJOIN UNNEST(geds.metrics::TEXT[]) metric_key\n    ON true\nJOIN ts_data_source_select(geds.grid_element_data_source_id, metric_key) tdss\n    ON true\nWHERE ge.grid_id = 'awefice'\n    AND ge.type = 'Meter'\n    AND geds.type = 'CONSUMER' \nORDER by 3\nLIMIT 5;\n")


# ---

# ## SQL & Python

# **Data Retrieval**

# Below are a few ways to save SQL output into a local Python variable.

# Option 1

# In[18]:


get_ipython().run_cell_magic('sql', 'result <<', "\nSELECT grid_element_id, type\nFROM grid_element\nWHERE grid_id = 'awefice';\n")


# Option 2

# In[19]:


# Save the sql output as a variable in one line.
result = get_ipython().run_line_magic('sql', "SELECT grid_element_id, type FROM grid_element WHERE grid_id = 'awefice'")


# Option 3

# In[20]:


# Save the sql output as a variable in multiple lines.
result = get_ipython().run_line_magic('sql', "SELECT grid_element_id, type                  FROM grid_element                  WHERE grid_id = 'awefice'")


# Option 4

# In[21]:


# Use the %sql magic command with a variable holding the query.
# Define your query.
query = """
SELECT grid_element_id, type
FROM grid_element
WHERE grid_id = 'awefice';
"""

# Execute the query using %sql magic command.
result = get_ipython().run_line_magic('sql', '$query')


# Then the following code can be run to convert the above SQL output `result` into a Python dataframe to work with.

# In[22]:


df = result.DataFrame()

# Return the first three lines of the dataframe to take a peek.
df.head(3)


# **Variable Input**

# A python variable can be integrated into SQL queries by wrapping the variable with `'{}'`.

# In[23]:


grid_element_id = "m_12"


# In[24]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element_data_source\nWHERE grid_element_id = '{grid_element_id}';\n")


# In[25]:


# Use string formatting to integrate variables if the query is stored as a variable.
query = """
SELECT *
FROM grid_element_data_source
WHERE grid_element_id = '{}';
"""

formatted_query = query.format(grid_element_id)
get_ipython().run_line_magic('sql', '$formatted_query')


# In[26]:


# Use string formatting to integrate variables if the query is stored as a variable.
# Notice that when including properties from the meta column using this SQL option, there should be no space between `meta` and `->>`.
query = """
SELECT grid_element_id,
    meta->> 'meter_number' as meter_number,
    meta->> 'voltage_level' as voltage_level
FROM grid_element 
WHERE grid_id = 'awefice'
    AND grid_element_id = '{}';
"""

formatted_query = query.format(grid_element_id)
get_ipython().run_line_magic('sql', '$formatted_query')

