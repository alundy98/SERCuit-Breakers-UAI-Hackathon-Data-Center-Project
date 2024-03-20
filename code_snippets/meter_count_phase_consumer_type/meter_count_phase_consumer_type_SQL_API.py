#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the number of meters per phase in a given grid.
# * Display the number of meters per consumer type in a given grid.
# * Display the number of meters per  phase and consumer type combination in a given grid. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid of interest.
# * Fetch the number of meters per phase and consumer type
# * Display the results.
# 
# The insights derived from these results may be helpful in multiple use cases ranging from Data Quality Improvement to Grid Planning or Customer Analytics

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
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

# ## Meter Count per Phase and per Consumer Type

# #### Input Parameters
# Enter the grid ID of interest.

# In[3]:


grid_id = input('Enter grid ID: ') # awefice


# #### Meter Count by Phase
# First, fetch the number of meters per phase using a query snippet without saving the results.

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases as phase\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Meter'\nGROUP  BY phases;\n")


# Second, save the query results to a dataframe for visualization purposes. 

# In[5]:


get_ipython().run_cell_magic('sql', 'meters <<', "\nSELECT COUNT(grid_element_id) as number_of_meters,\n    phases as phase\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Meter'\nGROUP  BY phases\nORDER BY LENGTH(phases) desc, phases;\n")


# In[6]:


# Convert the results to a data frame. 
df_meters = meters.DataFrame()

# Plot the number of meters per phase. 
fig = px.pie(df_meters, 
             values='number_of_meters', names='phase',
             title='<b>Breakdown of Meters by Phase<b>',
             labels={'phase':'Meter Phase', 
                         'number_of_meters': 'Meter Count'})

# Show meter phase, meter count and percent inside the pie chart.
fig.update_traces(textposition='inside', 
                  texttemplate = "Meter Phase: %{label} <br> Count: %{value:} <br>(%{percent})",)

fig.show()


# #### Meter Count by Consumer Type
# First, fetch the number of meters per consumer type using a query snippet without saving the results.

# In[7]:


get_ipython().run_cell_magic('sql', '', "SELECT COUNT(grid_element_id) as number_of_meters, \n    meta ->> 'type_of_consumer' as consumer_type \nFROM grid_element \nWHERE grid_id = '{grid_id}' \n    AND type = 'Meter' \nGROUP  BY (meta ->> 'type_of_consumer') \nORDER BY number_of_meters desc;\n")


# Second, save the query results to a dataframe for visualization purposes. 

# In[8]:


get_ipython().run_cell_magic('sql', 'meters <<', "SELECT COUNT(grid_element_id) as number_of_meters, \n    meta ->> 'type_of_consumer' as consumer_type \nFROM grid_element \nWHERE grid_id = '{grid_id}' \n    AND type = 'Meter' \nGROUP  BY (meta ->> 'type_of_consumer') \nORDER BY number_of_meters desc;\n")


# In[9]:


# Convert the results to a data frame. 
df_meters = meters.DataFrame()

# Plot the number of meters per consumer type. 
fig = px.pie(df_meters, 
             values='number_of_meters', names='consumer_type',
             title='<b>Breakdown of Meters by Consumer Type<b>',
             color_discrete_sequence=px.colors.qualitative.G10,
             labels={'consumer_type':'Consumer Type', 
                         'number_of_meters': 'Meter Count'})

# Show consumer type, meter count and percent inside the pie chart.
fig.update_traces(textposition='inside', 
                  texttemplate = "Type: %{label} <br> Count: %{value:} <br>(%{percent})",)
fig.show()


# #### Meter Count by both Phase and Consumer Type
# First, fetch the number of meters per phase and consumer type using a query snippet without saving the results.

# In[10]:


get_ipython().run_cell_magic('sql', '', "\nSELECT COUNT(grid_element_id) as number_of_meters, \n    phases, \n    meta ->> 'type_of_consumer' as type_consumer\nFROM grid_element\nWHERE grid_id = '{grid_id}' \n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer'), phases\nORDER BY LENGTH(phases) desc, phases, type_consumer;\n")


# Second, save the query results to a dataframe for visualization purposes.

# In[11]:


get_ipython().run_cell_magic('sql', 'meters <<', "\nSELECT COUNT(grid_element_id) as number_of_meters, \n    phases, \n    meta ->> 'type_of_consumer' as type_consumer\nFROM grid_element\nWHERE grid_id = '{grid_id}' \n    AND type = 'Meter'\nGROUP  BY (meta ->> 'type_of_consumer'), phases\nORDER BY LENGTH(phases) desc, phases, type_consumer;\n")


# In[12]:


# Convert the results to a data frame. 
df_meters = meters.DataFrame()

# Plot the breakdown of meters per phase and per consumer type. 
fig = px.bar(df_meters, x="type_consumer", y="number_of_meters", color="phases", 
             title="<b>Meters Count by Consumer Type and Phase<b>", 
             labels={'type_consumer':'Consumer Type', 
                         'number_of_meters': 'Number of Meters', 
                         'phases':'Meter Phase'})
fig.show()


# ---
