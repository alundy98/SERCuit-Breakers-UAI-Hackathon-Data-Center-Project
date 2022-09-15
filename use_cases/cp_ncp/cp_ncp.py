#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * analyze Coincident Peak (CP) and Non-Coincident Peak (NCP) throughout the distribution area and various customer segments using Awesense's Energy Data Model (EDM). In particular, this notebook will cover examples of this analysis on:
#     * entire grid
#     * feeder level topology
#     * tariff group
# 
# Insights originating from this type of analysis are essential for regulatory reporting, and allow utilities to understand consumer behaviour and improve rate design to increase the satisfaction and retention of consumers. 
# 
# For a comprehensive list of topological, hierarchical, geographical and customer segments that can be used for the CP & NCP analysis using the Awesense Platform, please refer to the [UC01-01 - Coincident Peak and Non-Coincident Peak Analysis.pdf](UC01-01%20-%20Coincident%20Peak%20and%20Non-Coincident%20Peak%20Analysis.pdf) document.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import plotly.express as px


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


# **Custom Functions**

# In[3]:


def plot_line(df, df_agg, title, value):
    """
    Compute and plot a line chart for hourly aggregated load `df_agg`.
    """

    # Plot a line chart.
    fig = px.line(df_agg, x=df_agg.index, y='value',
                  title=title, labels={'value': value,'timestamp': 'Timestamp'})
    
    # Format the chart - black line and manually fix y-axis to be from 0 to 105% of max y value.
    fig.update_traces(line_color='black')
    fig.update_yaxes(range=[0, df_agg['value'].max()*1.05])
    
    fig.show()
    
    return None    


def plot_line_bar(df, df_agg, df_cp, title):
    """
    Plot a stacked bar chart for meter load at the maximum system load timestamp `df_cp` 
        using the meter-level raw data `df` for full time range indices, 
        and add a line chart of the hourly aggregated load `df_agg`.
    """
    
    # Left join `df` with `df_cp` to fill in timestamps outside of the max system load with Nulls.
    #  This is needed to overlay two charts in one figure properly by sharing the same x-axis range.
    df_full = df.merge(df_cp, how='left', on=['timestamp', 'grid_element_id']
                      ).rename(columns={'value_y':'value_cp'})
    
    # Order the resulting dataframe to display stacked bar properly.
    df_full.sort_values('value_cp', ascending=False, inplace=True)

    # Plot a stacked bar chart first.
    fig = px.bar(df_full, x='timestamp', y='value_cp', barmode='stack',
                 title=title, color='grid_element_id', text_auto=True,
                 labels={'value_cp':'Load (kW)', 'timestamp':'Timestamp'})
    
    # Make the stacked bar chart text labels to show horizontally only.
    fig.update_traces(textangle=0)

    # Overlay a line chart using the `add_scatter()` for the hourly system aggregated loads.
    fig.add_scatter(x=df_agg.index, y=df_agg['value'], 
                    line_color='black')

    fig.show()
    
    return None
    
    
def plot_pie(df_cp, title):
    """
    Plot a pie chart of load breakdown at Coincident Peak (CP) `df_cp`.
    """
    
    # Plot a pie chart.
    fig = px.pie(df_cp, values='value', names='grid_element_id', title=title)
    
    # Make the chart text to be inside the pie. 
    #  Otherwise, the chart looks messy when there are many breakdowns.
    fig.update_traces(textposition='inside', textinfo='percent+label')
    fig.show()
    
    return None


def map_it(df, meters, title):
    """
    Plot a map of meters, with their colors and marker sizes dictated by the field called "value".
    `meters` input is a SQL result set that will be convereted to a dataframe.
    """
    
    # Convert the SQL result set to dataframe, and cast the longitude and latitude to floats.
    df_geo = meters.DataFrame().astype({'longitude':float, 'latitude':float})
    
    # Add the geo-location information to the main dataframe. 
    df_geo = df.merge(df_geo, on='grid_element_id')
    
    # Plot a map.
    fig = px.scatter_mapbox(df_geo, lat='latitude', lon='longitude', hover_name='grid_element_id',
                            title=title, color='value', size='value', zoom=15,
                            labels={'value':'Load (kW)'})
    fig.update_layout(mapbox_style="open-street-map")
    
    # Configure margin to show the title properly.
    fig.update_layout(margin={"r":0,"t":60,"l":40,"b":0})
    
    fig.show()
    
    return None      


def compute_ncp(df):
    """
    Return a dataframe of Non-Coincident Peak and the corresponding timestamp for each grid element.
    """
    
    # Calculate the max meter load regardless regardless of time.
    df_ncp = df.groupby('grid_element_id').max('value').reset_index()

    # Find timestamps of NCP values by merging `df` and `df_ncp`.
    df_ncp = df.merge(df_ncp, on=['grid_element_id', 'value'])

    # If NCP values occur multiple times, then take the first occurrence.
    df_ncp = df_ncp.groupby('grid_element_id').first().reset_index()
    
    return df_ncp


def plot_scatter(df_ncp, title):
    """
    Plot a scatter chart of meter loads, with their colors and marker sizes dictated by the field called "value".
    """

    # Plot a scatter chart.
    fig = px.scatter(df_ncp, x='timestamp', y='value', color='value', size='value',
                     title=title, hover_name='grid_element_id',
                     labels={'value':'Load (kW)', 'timestamp':'Timestamp'})
    
    fig.show()
    
    return None


# ---

# #### Input Parameters

# In[4]:


grid_id = input('Enter grid ID: ') # awefice
start = input('Enter start date (inclusive): ') # 2022-03-01 00:00:00-07
end = input('Enter end date (inclusive): ') # 2022-03-02 00:00:00-07

# Create a timerange using start and end date to use as an input argument.
timerange_tz = '[' + start + ', ' + end + ']'

# Drop timezone offset to use in title.
timerange = start[:10] + ' ~ ' + end[:10]


# ## System

# #### Data: Hourly Load Time Series 

# In[5]:


get_ipython().run_cell_magic('sql', 'result_system <<', "\nSELECT tdss.timestamp at time zone 'America/Vancouver' as timestamp,\n        ge.grid_element_id,\n        tdss.value\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_id = ge.grid_id\n    AND geds.grid_element_id = ge.grid_element_id\nJOIN ts_data_source_select(geds.grid_element_data_source_id, 'kWh', '{timerange_tz}') tdss\n    ON TRUE\nWHERE geds.grid_id = '{grid_id}'\n    AND ge.type = 'Meter'\nORDER BY tdss.timestamp;")


# #### Visualization

# In[6]:


# Convert the SQL result to Python dataframe.
df_system = result_system.DataFrame()

# Aggregate loads for each hour.
df_system_agg = df_system.groupby('timestamp').sum('value')

# Plot the system time series.
plot_line(df_system, df_system_agg, title='Hourly System Load Time Series', value='Hourly System Load (kW)')


# In[7]:


# Find the max load and the corresponding timestamp.
system_max_load = df_system_agg['value'].max()
system_max_time = df_system_agg['value'].idxmax()

print('System peak load of {} kW occurring at {}.'.format(system_max_load, system_max_time))


# ---
# ## Entire Grid

# ### Coincident Peak (CP)

# In[8]:


# Filter the raw data `df_system` to the given system max load timestamp for Coincident Peak (CP).
df_grid_cp = df_system.loc[df_system['timestamp']==system_max_time].copy()

# Plot the system load and grid load breakdown.
plot_line_bar(df_system, df_system_agg, df_grid_cp,
              title='Hourly System Load & Grid Load Breakdown at {}'.format(system_max_time))


# In[9]:


# Plot the breakdown of the grid load at system peak.
plot_pie(df_grid_cp, 
         'Breakdown of Grid Load at System Peak {}'.format(system_max_time))


# In[10]:


# Find the max load and the corresponding meter.
grid_max_load = df_grid_cp['value'].max()
grid_max_user = df_grid_cp.set_index('grid_element_id')['value'].idxmax()

print('Meter {} with the largest contribution to CP at {} kW.'.format(grid_max_user, grid_max_load))


# #### Map View

# In[11]:


# Prepare a string of meters to use in SQL.
meters = "','".join(df_grid_cp['grid_element_id'].unique())


# #### Data: Geo-locations

# In[12]:


get_ipython().run_cell_magic('sql', 'grid_meters <<', "\nSELECT grid_element_id,\n        meta ->> 'longitude' as longitude, \n        meta ->> 'latitude' as latitude \nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND grid_element_id IN ('{meters}')")


# In[13]:


# Plot the meters on a map.
map_it(df_grid_cp, grid_meters, title='Map View of Meter Load at System Peak {}'.format(system_max_time))


# ### Non-Coincident Peak

# In[14]:


# Compute NCP for each meter.
df_grid_ncp = compute_ncp(df_system)

# Plot NCP meter loads.
plot_scatter(df_grid_ncp, title='Max Load for Each Meter Between {}'.format(timerange))


# In[15]:


# Table view of the NCP meter data.
df_grid_ncp.sort_values('timestamp').set_index('timestamp')


# ---
# ## Feeder Level Topology

# In[16]:


feeder_id = input('Enter top feeder transformer ID: ') # transformer_6


# #### Data: Downstream Meters & Geo-locations

# In[17]:


get_ipython().run_cell_magic('sql', 'feeder_meters <<', "SELECT ggd.grid_element_id,\n        ge.meta ->> 'longitude' as longitude, \n        ge.meta ->> 'latitude' as latitude \nFROM grid_get_downstream('{grid_id}', '{feeder_id}') ggd\nJOIN grid_element ge\n    ON ge.grid_id = ggd.grid_id\n    AND ge.grid_element_id = ggd.grid_element_id;")


# In[18]:


# Convert the sql resultset from above to list.
feeder_meters_ls = feeder_meters.DataFrame()['grid_element_id'].to_list()

# Filter the system load time series data to applicable meters only.
df_feeder = df_system.loc[df_system['grid_element_id'].isin(feeder_meters_ls),:]


# ### Coincident Peak (CP)

# In[19]:


# Filter the raw data `df_feeder` to the given feeder max load timestamp for Coincident Peak (CP).
df_feeder_cp = df_feeder.loc[df_feeder['timestamp']==system_max_time].copy()

# Plot the system load and feeder load breakdown.
plot_line_bar(df_feeder, df_system_agg, df_feeder_cp,
              title='Hourly System Load & {} Load Breakdown at {}'.format(feeder_id, system_max_time))


# In[20]:


# Plot the breakdown of the feeder load at the system peak.
plot_pie(df_feeder_cp, 
         title='Breakdown of {} Load at System Peak {}'.format(feeder_id, system_max_time))


# In[21]:


# Find the max load and the corresponding meter.
feeder_max_load = df_feeder_cp['value'].max()
feeder_max_user = df_feeder_cp.set_index('grid_element_id')['value'].idxmax()

print('Meter {} with the largest contribution to CP at {} kW.'.format(feeder_max_user, feeder_max_load))


# #### Map View

# In[22]:


# Plot the meters on map.
map_it(df_feeder_cp, feeder_meters, title='Map View of {} Downstream Meter Load at {}'.format(
    feeder_id, system_max_time))


# ### Non-Coincident Peak (NCP)

# In[23]:


# Compute NCP for each meter.
df_feeder_ncp = compute_ncp(df_feeder)

# Plot NCP meter loads.
plot_scatter(df_feeder_ncp, title='Max Meter Load Downstream of {} Between {}'.format(feeder_id, timerange))


# In[24]:


# Table view of the NCP meter data.
df_feeder_ncp.sort_values('timestamp').set_index('timestamp')


# ---
# ## Customer Tariff Groups

# In[25]:


tariff_id = input('Enter Tariff ID: ') # res_basic


# #### Data: Tariff Group Meters & Geo-locations

# In[26]:


get_ipython().run_cell_magic('sql', 'tariff_meters <<', "\nSELECT grid_element_id,\n        meta ->> 'longitude' as longitude, \n        meta ->> 'latitude' as latitude \nFROM grid_element \nWHERE grid_id = '{grid_id}'\n    AND type = 'Meter'\n    AND meta ->> 'tariff_id' = '{tariff_id}';")


# In[27]:


# Convert the sql resultset from above to list.
tariff_meters_ls = tariff_meters.DataFrame()['grid_element_id'].to_list()

# Filter the system load time series data to applicable meters only.
df_tariff = df_system.loc[df_system['grid_element_id'].isin(tariff_meters_ls), :]


# ### Coincident Peak

# In[28]:


# Filter the raw data `df_tariff` to the given tariff max load timestamp for Coincident Peak (CP).
df_tariff_cp = df_tariff.loc[df_tariff['timestamp']==system_max_time].copy()

# Plot the system load and tariff group load breakdown.
plot_line_bar(df_tariff, df_system_agg, df_tariff_cp,
              title='Hourly System Load & {} Load Breakdown at {}'.format(tariff_id, system_max_time))


# In[29]:


# Plot the breakdown of the tariff group load.
plot_pie(df_tariff_cp, 
         title='Breakdown of {} Load at {}'.format(feeder_id, system_max_time))


# In[30]:


# Find the max load and the corresponding meter.
tariff_max_load = df_tariff_cp['value'].max()
tariff_max_user = df_tariff_cp.set_index('grid_element_id')['value'].idxmax()

print('Meter {} with the largest contribution to CP at {} kW.'.format(tariff_max_user, tariff_max_load))


# #### Map View

# In[31]:


# Plot the meters on map.
map_it(df_tariff_cp, tariff_meters, 
       title='Map View of {} Meter Load at {}'.format(tariff_id, system_max_time))


# ### Non-Coincident Peak

# In[32]:


# Compute NCP for each meter.
df_tariff_ncp = compute_ncp(df_tariff)

# Plot NCP meter loads.
plot_scatter(df_tariff_ncp, title='Max Meter Load for Tariff {} Between {}'.format(tariff_id, timerange))


# In[33]:


# Table view of the NCP meter data.
df_tariff_ncp.sort_values('timestamp').set_index('timestamp')


# ---
