#!/usr/bin/env python
# coding: utf-8

# ## Overview

# The notebook is intended to:
# * Simulate and quantify the number of PV systems that can be installed on a low-voltage (LV) grid (i.e. downstream from a ML/VL transformer) before reaching a state of reversed power flow.
# 
# The use case is designed as follows:
#   * Choose the grid of interest. 
#   * Define the area of interest within the grid by choosing a ML/LV transformer.
#   * Aggregate all loads downstream from the specific transformer.
#   * Quantify and simulate the additional amount of PV generation allowed in this grid section based on temporal load analysis.
# 
# Results of such analysis can provide valuable insights for decision-making on PV connection permits and grid upgrades or planning activities.  
# 
# For more details about reversed power flow and how it can be analyzed using Awesense's platform, please refer to the [UC05-01 - Reversed Power Flow and PV Capacity Analysis](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC05-01%20-%20Reversed%20Power%20Flow%20and%20PV%20Capacity%20Analysis.pdf)
# 
# document.

# ## Setup 

# In[1]:


import getpass
import pandas as pd
import numpy as np
import urllib.parse
import plotly.graph_objects as go
from plotly.subplots import make_subplots

pd.set_option('display.max_columns', None)


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


# ##### Custom Functions

# In[3]:


def load_plot(df_tr, pv = 0, pv_generation_plot = False):
    """
    Plot load time series and PV generation capacity if applicable.    
    """
    
    # Create subplots. 
    fig = make_subplots()

    # Add scatter trace of transformer load. 
    fig.add_trace(go.Scatter(x = df_tr['timestamp'], 
                             y = df_tr['total_kw'], name = ''))
    
    # Add a figure title.
    fig.update_layout(title = ' Load Downstream of ' + grid_element_id)
    
    # Add PV generation load. 
    if pv_generation_plot:
        
        # Add a trace of maximum generation capacity. 
        fig.add_hline(y = pv, annotation_text = 'PV generation', 
                      annotation_position = 'bottom right', line_color = 'red')
            
        # Add figure title.
        fig.update_layout(title = ' Load and PV Generation Capacity Downstream of ' + grid_element_id)
        
        # Position the legend in the upper left corner.
        fig.update_layout(legend = dict(yanchor = 'top', 
                                        y = 1.00, xanchor = 'left', x = 0.05))
    
    # Set the range of y-axis.
    fig.update_layout(yaxis = dict(range = [0, df_tr['total_kw'].apply(np.ceil).max()+1]))
    
    # Set x-axis title.
    fig.update_xaxes(title_text = 'Timestamp')

    # Set y-axes titles.
    fig.update_yaxes(title_text = 'Load (kW)')
    
    fig.show()
    
    return None


def capacity_plot_add(df, pv, filter_name, start_hour, end_hour, 
                      start_month, end_month, pvs_dict = {}):
    """
    Plot the load time series, the generation capacity, and the number of PV systems based on a filter.
    
    Return the dictionary with the filter name and maximum number of PVs.
    """
    
    # Create a filter based on daytime and month inputs. 
    if filter_name != 'Daytime Winter':
        
        # Non-winter filters.
        df_filtered = df.where(((df['hour'] >= start_hour) & (df['hour'] <= end_hour)) 
                               & ((df['month'] >= start_month) & (df['month'] <= end_month)))
    
    else:
        
        # Winter filter.
        df_filtered = df.where(((df['hour'] >= start_hour) & (df['hour'] <= end_hour)) 
                               & ((df['month'] >= start_month) | (df['month'] <= end_month)))            
         
    # Calculate the maximum number of PV installations. 
    maxpv = (df_filtered['total_kw']/pv).apply(np.floor).min()
    
    # Update the dictionary with the filter name. 
    pvs_dict_new = pvs_dict.copy()
    pvs_dict_new.update({filter_name: maxpv})
    
    # Create two subplots with titles. 
    fig = make_subplots(rows = 1, cols = 2, column_widths = [0.7, 0.3], 
                        subplot_titles = (' Load and PV Generation Capacity Downstream of '
                                          + grid_element_id + '<br> Filtered: ' + filter_name, 
                                          'Number of PV Installations <br> '))
    
    # Add trace of load.
    fig.add_trace(go.Scatter(x = df_filtered['timestamp'], y = df_filtered['total_kw'], 
                             name = ' Load'),row = 1, col = 1)

    # Add trace of maximum generation capacity. 
    fig.add_hline(y = maxpv*pv, annotation_text = ('PV generation'), 
                  annotation_position = 'bottom right', line_color = 'red', row = 1, col = 1)
    
    # Add a bar plot showing the number of PV installations.  
    fig.add_trace(go.Bar(x = list(pvs_dict_new.keys()), y = list(pvs_dict_new.values()), 
                         showlegend = False, marker_color = '#2ca02c'), row = 1, col = 2)
    
    # Set the range of the axes.
    fig.update_layout(xaxis1 = dict(range = [df['timestamp'].min(), df['timestamp'].max()]))
    fig.update_layout(yaxis1 = dict(range = [0, df['total_kw'].apply(np.ceil).max()+1]))
    fig.update_layout(yaxis2 = dict(range = [0, max(list(pvs_dict_new.values()))+1]))
    
    # Edit axis labels.
    fig['layout']['xaxis']['title'] = 'Timestamp'
    fig['layout']['xaxis2']['title'] = ''
    fig['layout']['yaxis']['title'] = 'Load (kW)'
    fig['layout']['yaxis2']['title'] = 'PV Installations'
    fig['layout']['xaxis2']['tickangle'] = 270
    
    # Position the legend in the upper left corner.
    fig.update_layout(legend = dict( yanchor = "top", 
                                    y = 1.0, xanchor = "left", x = 0.05))

    fig.show()
    
    # Print a message with the number of PV installations. 
    print('Number of PV installations - {}: {}'
          .format(list(pvs_dict_new)[-1], maxpv)) 
    
    return pvs_dict_new


# ---

# ## Use Case - Reversed Power Flow

# #### Input Parameters
# Input grid name to find all the MV/LV transformers in this grid. 

# In[4]:


# User input for the grid.
grid_id = input('Enter grid ID: ') # awefice


# ### Data
# #### Transformers Information
# Fetch MV/LV transformers and display the relevant information.

# In[5]:


result = get_ipython().run_line_magic('sql', "SELECT grid_element_id,                          meta,                          phases                 FROM grid_element                 WHERE grid_id = '{grid_id}'                     AND type = 'Transformer';")

# Turn the query result into a dataframe to work easily in Python.
df_transformer = result.DataFrame()

# Pull out the information from `meta` column saved as JSONB.
df_transformer = pd.concat([df_transformer.drop(['meta'], axis=1),
                            df_transformer['meta'].apply(pd.Series)], axis=1)

# Choose the relevant columns to display only MV/LV transformers. 
df_transformer = df_transformer[['grid_element_id', 'ownership', 'rating_kva', 'phases', 
                                 'voltage_level', 'commission_date', 'primary_voltage', 
                                 'secondary_voltage']].loc[(df_transformer['voltage_level'] == 'MV/LV')]

# display the results.
df_transformer


# Choose a transformer from the above list.

# In[6]:


# User input for transformer.
grid_element_id = input('Enter transformer ID: ') # transformer_92


# #### Meter Information
# Fetch and display all the meters downstream from the transformer, the consumer type, and the voltage level.

# In[7]:


# Find all the meters downstream of the transformer.
result = get_ipython().run_line_magic('sql', "SELECT ggd.grid_element_id,                          ggd.type,                          ggd.meta                  FROM grid_get_downstream('{grid_id}', '{grid_element_id}', 'false') ggd                  WHERE ggd.type = 'Meter';")

# Turn the query result into a dataframe to work easily in Python.
df_meter = result.DataFrame()

# Pull out the information from `meta` column saved as JSONB.
df_meter = pd.concat([df_meter.drop(['meta'], axis=1),
                      df_meter['meta'].apply(pd.Series)],
                         axis=1)

# Choose the relevant columns to display. 
df_meter = df_meter[['grid_element_id', 'type', 'type_of_consumer', 'parent_transformer_id', 'voltage_level']]

# Display the results.
df_meter


# #### Load
# Fetch and display the aggregate consumption load downstream of the transformer.

# In[8]:


# Aggregate the load of all the meters downstream from the specific transformer. 
result = get_ipython().run_line_magic('sql', "SELECT tdss.timestamp at time zone 'America/Vancouver' as timestamp,                          SUM(tdss.value) as total_kW                  FROM grid_get_downstream('{grid_id}', '{grid_element_id}', 'false') ggd                  JOIN grid_element_data_source geds                      ON geds.grid_id = ggd.grid_id                      AND geds.grid_element_id = ggd.grid_element_id                  JOIN ts_data_source_select(geds.grid_element_data_source_id, 'kWh') tdss                      ON true                  WHERE ggd.grid_id = '{grid_id}'                      AND ggd.type = 'Meter'                      AND geds.type = 'CONSUMER'                  GROUP BY tdss.timestamp                  ORDER by 1;")

# Convert the results to a dataframe.
df_transformer_load = result.DataFrame()

# Extract the month and hour into separate columns. 
df_transformer_load['month'] = df_transformer_load['timestamp'].dt.month
df_transformer_load['hour'] = df_transformer_load['timestamp'].dt.hour

# Display the results.
df_transformer_load


# ### Visualization
# #### Load
# Visualize the load over the time period.

# In[9]:


# Display the transformer load.
load_plot(df_transformer_load)


# #### Load and PV Generation Capacity
# 
# Let's assume we want to install a PV system with a generation capacity of 1.9 kW (DC) in this grid section with a maximum generation capacity of 1.87 kW (AC). How many similar systems can we install in this section of the grid?
# 
# Initially, let's compare the load with the generation capacity of one PV system.

# In[10]:


# Add PV maximum generation capacity and display.
pv_value = 1.87
load_plot(df_transformer_load, pv_value, pv_generation_plot = True)


# #### Temporal Analysis
# ##### Summer
# To prevent reversed power flow, the power generated from PV installations cannot exceed the load. Since there is no solar power generation at night, let's evaluate the number of PV systems that can be installed using daytime load. Further, load varies significantly between summer and winter. So, let's consider the load during the daytime in the summer.
# 
# The left plot below shows the filtered load during daytime in the summer, and the right bar plot shows the number of PV installations based on the filtered data.

# In[11]:


# Assign daytime hours and summer months (e.g., daytime is from 9 am to 5 pm, and summer is from June to October). 
start_hour = 9
end_hour = 17
start_month = 6 
end_month = 10

# Display the maximum number of PVs over the time series using the daytime-summer filter. 
pvs_filter_summer = capacity_plot_add(df_transformer_load, pv_value, 'Daytime Summer', 
                                      start_hour, end_hour, start_month, end_month)


# ##### Winter
# Load increases in the winter. Will it be possible to have more PVs during the daytime in the winter?
# 
# 
# The left plot below shows the filtered load during daytime in the winter, and the right bar plot shows the number of PV installations based on the filtered data.

# In[12]:


# Assign daytime hours and winter months (e.g., daytime is from 9 am to 3 pm, and winter is from November to March).
start_hour = 9
end_hour = 15
start_month = 11
end_month = 3

# Display the maximum number of PVs over the time series using the daytime-winter filter. 
pvs_filter_winter = capacity_plot_add(df_transformer_load, pv_value, 'Daytime Winter',
                                      start_hour, end_hour, start_month, end_month, pvs_filter_summer)


# To conclude, this use case demonstrates that by looking at the grid segment more closely and analyzing it using the tools available on Awesense's platform, it is possible to increase the number of PV installations during certain times of the year without causing reversed power flow.

# ---
