#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is designed to:
# * Provide SQL code snippets for break down lines into overhead and underground using Awesense's Energy Data Model (EDM).
# * Providing information for decision-making on the current ratio of overhead and underground lines in a specific area of the grid. This information can assist in grid modernization and planning activities.
# 
# * In this code snippet, it is possible to determine the OH/UG ratio for one or more transformers (Downstream of a transformer(s)). The input parameters are `grid_id` and `transformer_id`.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse

import pandas as pd
from IPython import get_ipython


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[ ]:


edm_address = getpass.getpass(prompt="EDM server address: ")

print("\nEDM login information")
edm_name = getpass.getpass(prompt="Username: ")
edm_password = getpass.getpass(prompt="Password: ")
edm_password = urllib.parse.quote(edm_password)

get_ipython().run_line_magic('load_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://$edm_name:$edm_password@$edm_address/edm')
get_ipython().run_line_magic('config', 'SqlMagic.displaycon = False')
get_ipython().run_line_magic('config', 'SqlMagic.feedback = False')

# Delete the credential variables for security purpose.
del edm_name, edm_password


# ---

# ## Examples 

# ### Distribution of overhead and underground lines for Specific or Multiple Transformers
# Find the overhead and underground lines in a grid downstream of specific or multiple transformers and determine their lengths.

# #### Input Parameters
# 
# Enter the grid ID and specific transformer ID or multiple transformer IDs of interest. 

# In[ ]:


ip = get_ipython()

grid_id = input("Enter grid ID: ")  # 'awefice' or 'North Central Zone'
raw_ids = input(
    "Enter transformer ID(s) separated by space: "
)  # 'transformer_2' or '29610_hvmv 24966_hvmv'
transformer_ids = raw_ids.strip().split()

if not grid_id or not transformer_ids:
    raise ValueError("You must enter a grid_id and at least one transformer_id.")

# Check if grid exists
check_grid_query = f"SELECT COUNT(*) FROM grid WHERE grid_id = '{grid_id}';"
grid_check = ip.run_line_magic("sql", check_grid_query)
if grid_check.DataFrame().iloc[0, 0] == 0:
    raise ValueError(f" Grid ID '{grid_id}' does not exist.")

# Check if transformer_ids exist in the grid
values_str = ", ".join(f"'{tid}'" for tid in transformer_ids)
check_transformers_query = f"""
SELECT grid_element_id
FROM grid_element
WHERE grid_id = '{grid_id}' AND grid_element_id IN ({values_str});
"""
transformer_check = ip.run_line_magic("sql", check_transformers_query)

# Safer extraction: works even if result is empty
result_list = [row[0] for row in transformer_check]

if set(result_list) == set(transformer_ids):
    print(f" All transformer_id(s) exist in the grid '{grid_id}'")
else:
    missing_ids = list(set(transformer_ids) - set(result_list))
    raise ValueError(
        f" These transformer_id(s) do not exist in grid '{grid_id}': {', '.join(missing_ids)}"
    )


# #### SQL Queries
# Fetch lines downstream of specific or multiple transformers.

# In[12]:


from IPython import get_ipython

# Quietly load the %sql extension if not already loaded
ip = get_ipython()
if "sql" not in ip.extension_manager.loaded:
    ip.run_line_magic("load_ext", "sql")

# Prepare VALUES list directly from transformer_ids
values_list = ", ".join(f"('{tid}')" for tid in transformer_ids)

# SQL query
sql_query = f"""
WITH ids(transformer_id) AS (
    VALUES {values_list}
),
downstream AS (
    SELECT
        i.transformer_id,
        d.is_underground,
        ST_Length(ST_Transform(d.geometry, 3857)) AS length_m
    FROM ids i
    CROSS JOIN LATERAL grid_get_downstream('{grid_id}', i.transformer_id, false) d
    WHERE d.type ILIKE '%linesegment%' AND d.geometry IS NOT NULL
)
SELECT
    transformer_id,
    CASE WHEN is_underground THEN 'Underground' ELSE 'Overhead' END AS line_type,
    COUNT(*) AS segment_count,
    ROUND(SUM(length_m)) AS total_length_meters
FROM downstream
GROUP BY transformer_id, is_underground
ORDER BY transformer_id, line_type;
"""

# Execute the query and store the result in a DataFrame
res = ip.run_line_magic("sql", sql_query)
final_df = res.DataFrame()
final_df


# ---

# In[ ]:




