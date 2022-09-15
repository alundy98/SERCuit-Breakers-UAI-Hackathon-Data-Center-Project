#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Demonstrate how to programmatically use tracing functionalities available in TGI.
# 
# The five types of tracing this notebook covers are:
# 1. **Source**: trace upstream of a specified grid element. 
# 2. **All Sources**: trace all producers in the circuit of the specified grid element. Note that `Source` trace is a subset of `All Sources` trace.
# 3. **Down**: trace downstream of a specified grid element to all other grid elements.
# 4. **Connected**: trace all grid elements in the same circuit as the specified grid element. 
# 5. **Same Voltage**: trace all grid elements in the circuit with the same voltage as the specified grid element.
# 
# Please refer to the [main_concepts.ipynb](../2_main_concepts/main_concepts.ipynb) notebook for a high-level introduction to and simpler examples of the core views and functions available in Awesense's Energy Data Model (EDM).

# ---

# ## Set up

# In[1]:


import getpass
import pandas as pd
from shapely import wkb
import plotly.express as px
import urllib.parse

pd.set_option('display.max_columns', None)


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[2]:


**Connection**

Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
<span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>edm_address = getpass.getpass(prompt='EDM server address: ')

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


# **Custom Functions**

# In[3]:


def query_to_df(result):
    """
    Transform sql query result to dataframe, and
    expand out `meta` column into separate columns.
    """
    
    # Turn a query output into a Python dataframe.
    df = result.DataFrame()

    # Separate the `meta` column currently saved as JSONB into individual columns.
    # Replace NaN values to empty string for ease of spotting non-NaN values.
    df = pd.concat([df.drop(['meta'], axis=1),
                    df['meta'].apply(pd.Series).fillna('')], axis=1)
    
    # Transform the hex string form of geometry to coordinates.
    df['geometry'] = df['geometry'].apply(lambda x: wkb.loads(x, hex=True)) 
    
    return df


def plot_pie(df, interest_colname, title):
    """
    Compute counts of each value in `interest_colname` column,  
    plot a pie chart of the breakdown, and 
    return the counts dataframe.
    """
    
    # Create a dataframe of counts of each value.
    df_cts = df[interest_colname].value_counts().to_frame().reset_index()
    
    # Re-align the index and count columns from the above dataframe result.
    df_cts.rename(columns={'index':interest_colname, interest_colname: 'count'}, inplace=True)
    
    # Create a pie plot.
    fig = px.pie(df_cts, names=interest_colname, values='count',
                title=title)
    
    # Show the count values, percentages and labels for the pie plot.
    fig.update_traces(textposition='inside', textinfo='percent+label+value')
    fig.show()
    
    # Return the count dataframe sorted alphabetically by in the input column. 
    return df_cts.sort_values(interest_colname) 


def phase_table(df):
    """
    Return a table of phase breakdowns by grid element type.
    """

    # Create a dataframe of counts by both types and phases.
    df_counts = df[['type','phases']].value_counts().to_frame().reset_index()
    
    # Rename the first column as 'count'.
    df_counts.rename(columns={0:'count'}, inplace=True)
    
    # Pivot the dataframe to have phases as columns.
    df_phases = df_counts.pivot_table(index='type', columns='phases', fill_value=0)

    # Return the count dataframe sorted alphabetically by `type`.
    return df_phases.sort_values('type') 


# ---

# ## Example

# ### Input Parameters

# We'll take a look at the `awefice` grid. In the database, this property is referred to as a `grid_id`.

# In[4]:


grid_id = 'awefice'


# Check when this grid was last updated. 

# In[5]:


get_ipython().run_cell_magic('sql', '', "\nSELECT last_updated\nFROM grid\nWHERE grid_id = '{grid_id}';")


# The below are different element types available in the `awefice` grid.

# In[6]:


get_ipython().run_cell_magic('sql', '', "\nSELECT DISTINCT type as element_type\nFROM grid_element\nWHERE grid_id = '{grid_id}'\nORDER BY element_type;")


# Enter one of the element types from the above output to explore further in the `grid_element` view.

# In[7]:


element_type = input('Enter the element type of interest: ')


# The below are all available grid element ID for the specified grid `type`.

# In[8]:


get_ipython().run_cell_magic('sql', '', "\nSELECT grid_element_id\nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = '{element_type}'\nORDER BY grid_element_id;")


# Choose one of the grid element ID from the above output to conduct tracing on.

# In[9]:


grid_element_id = input('Enter the grid element id of interest: ')


# ### Trace

# Choose one of the tracing options: `Source`, `All Sources`, `Down`, `Connected`, and `Same Voltage`.

# In[10]:


trace_option = input('Enter the tracing option: ')


# Call an appropriate SQL query based on the specified tracing option.

# In[11]:


if trace_option.lower() == 'source':
    
    # Set the third argument to `true` to retrieve the top feeder.
    result = get_ipython().run_line_magic('sql', "SELECT *             FROM grid_get_sources('{grid_id}', '{grid_element_id}', 'true');   ")
    
elif trace_option.lower() == 'all sources':
    
    # Set the third argument to `false` to retrieve all feeders.
    result = get_ipython().run_line_magic('sql', "SELECT *             FROM grid_get_sources('{grid_id}', '{grid_element_id}', 'false');")
    
elif trace_option.lower() == 'down':
    
    # Set the third argument to `false` to exclude itself from the output.
    result = get_ipython().run_line_magic('sql', "SELECT *             FROM grid_get_downstream('{grid_id}', '{grid_element_id}', 'false');")
    
elif trace_option.lower() == 'connected':
    
    result = get_ipython().run_line_magic('sql', "SELECT *             FROM grid_get_connected('{grid_id}', '{grid_element_id}');")
    
elif trace_option.lower() == 'same voltage':
    
    result = get_ipython().run_line_magic('sql', "SELECT *             FROM grid_get_same_voltage('{grid_id}', '{grid_element_id}');")

else:
    print('Invalid input for tracing option. Please enter one of the following: Source, All Sources, Down, Connected.')


# In[12]:


# Turn the sql output to a Python dataframe.
df = query_to_df(result)

# Return the first few rows.
df.head()


# The above dataframe can be processed further as needed. For example, TGI only returns a top feeder for the `Source` option whereas the notebook here returns all grid_elements. Applying the `is_producer==True` filter on the `df` will mirror the same functionality as in TGI.

# In[13]:


# Filter the dataframe to rows with is_producer=True.
df[df['is_producer']==True]


# For the demonstration of this notebook, we will continue using the full dataframe with unfiltered, data.

# In[14]:


# Create a pie plot for the breakdown of grid element types.
df_types = plot_pie(df, 'type', 
                     'Breakdown of Grid Element Types for ' + trace_option.title() + ' Trace')


# In[15]:


# Return a table of grid element type counts.
df_types


# In[16]:


# Create a pie plot for the breakdown of phases.
df_phases = plot_pie(df, 'phases', 
                     'Breakdown of Phases for ' + trace_option.title() + ' Trace')


# In[17]:


# Return a table of phase counts.
df_phases


# In[18]:


# Create a table of phase counts by grid element types.
phase_table(df)


# ### Combining Trace Functions

# Combining multiple trace functions provides the flexibility to find an answer to additional questions.
# 
# For example, a grid element's parent transformer can be identified by: 
# 1. Returning a list of transformers in the upstream using `grid_get_sources()`.
# 2. Entering the list of transformers from step 1 to `grid_get_same_voltage()` to get all grid elements with the same voltage as a given transformer.
# 3. Matching the input grid element to the returned grid elements from step 2 and filter to the respective transformer as the final output.

# In[19]:


grid_element_id = input('Enter the grid element id of interest: ')


# In[20]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ggs.grid_element_id AS transformer\nFROM grid_get_sources('{grid_id}', '{grid_element_id}', true) ggs\nLEFT JOIN grid_get_same_voltage('{grid_id}', ggs.grid_element_id) ggsv\n    ON true\nWHERE ggs.type = 'Transformer'\n    AND ggsv.grid_element_id = '{grid_element_id}';")


# ---
