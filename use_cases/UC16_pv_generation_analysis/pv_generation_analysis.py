#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Quantify the historical portion of PV generation per circuit or feeder and how much of the consumption it accounts for. 
# 
# This use case is designed as follows:
# 
# * Choose the grid of interest.
# * View all transformers with downstream PV installations. 
# * Define the area of interest within the grid by choosing a given transformer.
# * Aggregate the historical hourly consumption loads from all meters and the historical hourly generation from PV installations downstream from the given transformer.
# * Plot aggregated hourly PV generation.
# * Plot percentage of hourly PV generation relative to consumption load. 
# * Plot percentage of monthly PV generation relative to consumption load and its all-time mean. 
# 
# Results of such analysis can provide valuable insights for decision-makers on PV penetration, connection permits, and grid upgrades or planning activities.
# 
# For more details about PV generation analysis and how it can be analyzed using Awesense's platform, please refer to the [UC16-01 - Registered PV Generation Analysis](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC16-01%20-%20Registered%20PV%20Generation%20Analysis.pdf) document.

# ## Setup 

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from IPython.display import Markdown as md


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


def calc_pv_production(df):
    """
    Calculate true consumption and percentage of PV generation relative to true_consumption. 
    The energy values for CONSUMER, PRODUCER, and TRUE_GENERATION (in capital letters) are imported from the DB 
    using the grid_element_data_source function. 
    Values calculated in this notebook are named using lower_case name. 
    """
    
    # Aggregate the hourly meter load.
    df_agg = df.groupby(['timestamp','type']).sum('kWh').reset_index()
    
    # Pivot the table.
    df_agg = df_agg.pivot(index='timestamp', columns='type', 
                          values='kWh').rename_axis(index='timestamp', columns=None)
    
    # Calculate true consumption.
    df_agg['true_consumption'] = df_agg['NET_FLOW'] + df_agg['TRUE_GENERATION']
    
    # Calculate the percentage of PV production relative to true consumption. 
    df_agg['percent_production'] = df_agg['TRUE_GENERATION']/df_agg['true_consumption']*100
    
    df_agg.reset_index(inplace=True)
    
    return df_agg


def plot_pv(df):
    """
    Plot the PV generation and its percentage with respect to consumption load. 
    """
    
    # Create three subplots with titles. 
    fig = make_subplots(rows=3, cols=1, vertical_spacing=0.15,
                        subplot_titles=('Hourly PV Generation',
                                        'Hourly Percentage of Consumption Load Supplied by PV Generation',
                                        'Hourly Distribution of Percentage of Consumption Load Supplied by PV Generation'))
    
    # Plot percentage of hourly PV generation.
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['TRUE_GENERATION'], 
                             showlegend = False), row=1, col=1)
    
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['percent_production'], 
                             showlegend=False), row=2, col=1)
    
    fig.add_trace(go.Box(x = df['timestamp'].dt.hour, y = df['percent_production'], 
                             showlegend=False), row=3, col=1)

    # Add a title, axis labels, axis ticks, and adjust the figure size.
    fig.update_layout(title = 'PV Generation Downstream of '+ grid_element_id, 
                      title_font_color='Black', title_font=dict(size=13))
    fig.update_xaxes(title_text = 'Timestamp', row=1, col=1)
    fig.update_xaxes(title_text = 'Timestamp', row=2, col=1)
    fig.update_xaxes(title_text = 'Hours of the Day', row=3, col=1)
    fig.update_yaxes(title_text = 'Hourly PV Generation (kWh)', row=1, col=1)
    fig.update_yaxes(title_text = 'Hourly Percentage (%)', row=2, col=1)
    fig.update_yaxes(title_text = 'Hourly Percentage (%)', row=3, col=1)
    fig.update_layout(xaxis3=dict(tick0=0, dtick=1), height=1200, width=800)
   
    fig.show()
    

def plot_monthly_bar(df):
    """
    Plot the percentage of monthly PV generation and its all-time average. 
    """

    # Drop unnecessary columns.  
    df_pv_month = df.drop(['percent_production'], axis=1)
    
    # Calculate the all-time PV generation percentage. 
    df_pv_average = df_pv_month['TRUE_GENERATION'].sum()/df_pv_month['true_consumption'].sum()*100
    
    # Group by months and aggregate.
    df_pv_month = df_pv_month.groupby(df_pv_month['timestamp'].dt.month).sum(numeric_only=True).reset_index()

    # Calculate the percentage of production relative to true consumption. 
    df_pv_month['percent_production'] = df_pv_month['TRUE_GENERATION']/df_pv_month['true_consumption']*100

    # Plot the percentage of monthly PV generation.
    fig = px.bar(df_pv_month, x='timestamp', y='percent_production')
    
    # Plot the all-time PV generation percentage. 
    fig.add_hline(y = df_pv_average, annotation_text='All-time PV generation percentage', 
                  annotation_position='top right', line_color='Black', annotation_font_color='Black')
    
    # Add a title, axis labels, axis ticks, and adjust the figure size.
    fig.update_layout(title = 'Percentage of Monthly PV Generation w.r.t Load Downstream of '+ grid_element_id,
                      title_font_color='Black', title_font=dict(size=13))           
    fig.update_layout(xaxis_title='Month of the Year', yaxis_title='PV Generation Percentage (%)',
                      font_color='Grey', font=dict(size=11), 
                      xaxis=dict(tick0=1, dtick=1), height=400, width=800)
    
    # Adjust the y-axis range and the bar's color. 
    fig.update_yaxes(range=[0, max(df_pv_month['percent_production']+10)])
    fig.update_traces(marker_color='green') 
    
    fig.show()


# ---

# ## Use Case - Registered PV Generation Analysis

# #### Input Parameters

# In[4]:


# User input for the grid.
grid_id = input('Enter grid ID: ') # awefice


# ### Data
# #### Transformers Information
# Fetch all the grid transformers upstream of PV installations. 

# In[5]:


get_ipython().run_cell_magic('sql', 'result <<', "\nSELECT ge.grid_element_id as transformer_id,\n    ggd.grid_element_id as pv_id, \n    ggd.type, \n    ggd.meta ->> 'generation_capacity' as generation_capacity\nFROM grid_element ge \nJOIN grid_get_downstream('{grid_id}', ge.grid_element_id, 'false') ggd \n    ON ggd.grid_id = ge.grid_id \nWHERE ggd.grid_id = '{grid_id}'\n    AND ge.type = 'Transformer' \n    AND ggd.type = 'Photovoltaic';\n")


# In[6]:


# Convert the results to a data frame. 
df_pv_info = result.DataFrame()

# Set up a multi-index data frame to display the transformers and their associated PV installations.  
df_pv_info = df_pv_info.set_index(['transformer_id', 'pv_id'])

# Display the results.
df_pv_info


# Choose a transformer from the list above.

# In[7]:


# User input for transformer.
grid_element_id = input('Enter transformer ID: ') # transformer_36


# In[8]:


# Get meter net flow time series. 
# If there's only downstream load, the "net flow" would refer to the downstream.
result = get_ipython().run_line_magic('sql', 'SELECT ge.grid_element_id as transformer_id,                          ggd.grid_element_id as grid_element_id,                          tdss_c.timestamp at time zone \'America/Vancouver\' as timestamp,                          tdss_c.value - COALESCE(tdss_p.value,0) as "kWh",                          \'NET_FLOW\' as type                 FROM grid_element ge                  JOIN grid_get_downstream(\'{grid_id}\', ge.grid_element_id, \'false\') ggd                     ON ggd.grid_id = ge.grid_id                  JOIN grid_element_data_source geds_c                      ON geds_c.grid_element_id = ggd.grid_element_id                      AND geds_c.type = \'CONSUMER\'                  JOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\') tdss_c                      ON true                  LEFT JOIN grid_element_data_source geds_p                      ON geds_p.grid_element_id = geds_c.grid_element_id                      AND geds_p.type = \'PRODUCER\'                  LEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\') tdss_p                      ON tdss_p.timestamp = tdss_c.timestamp                  WHERE ge.grid_element_id = \'{grid_element_id}\'                      AND ggd.type = \'Meter\';')

# Convert the results to dataframe.
df_meter = result.DataFrame()


# In[9]:


# Get PV time series.
result = get_ipython().run_line_magic('sql', 'SELECT ge.grid_element_id as transformer_id,                          ggd.grid_element_id as grid_element_id,                          tdss.timestamp at time zone \'America/Vancouver\' as timestamp,                          tdss.value as "kWh",                          geds.type                  FROM grid_element ge                  JOIN grid_get_downstream(\'{grid_id}\', ge.grid_element_id, \'false\') ggd                      ON ggd.grid_id = ge.grid_id                  JOIN grid_element_data_source geds                      ON geds.grid_id = ggd.grid_id                      AND geds.grid_element_id = ggd.grid_element_id                  JOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\') tdss                      ON true                  WHERE ggd.grid_id = \'{grid_id}\'                      AND ge.grid_element_id = \'{grid_element_id}\'                      AND ggd.type = \'Photovoltaic\'                  ORDER by tdss.timestamp;')

# Convert the results to dataframe.
df_pv = result.DataFrame()

# Inform the user if there's no PV time series data.
if df_pv.empty:
    raise SystemExit('There is no PV time series downstream of {}'.format(grid_element_id))


# In[10]:


# Put together both Meter and PV time series together.
df_ts = pd.concat([df_meter, df_pv])

# Display the results.
df_ts


# ### Visualization
# #### PV Generation Analysis

# Please note that the plots below are interactive visualizations. Users can zoom in by holding and dragging a box around a specific plot section.

# In[11]:


# Calculate true consumption and percentage of PV production.
df_pv_agg = calc_pv_production(df_ts)

# Plot the results. 
plot_pv(df_pv_agg)


# In[12]:


# Description of the plots above. 
winter_max = df_pv_agg.loc[df_pv_agg['timestamp'].dt.month<3, 'percent_production'].max()
md("The plots above show the hourly PV generation in kWh (top plot), \
   what percentage of the hourly consumption load is supplied by PV generation (middle plot), \
   and the distribution of this percentage with respect to the hour of the day (bottom plot). <br><br>\
   In the example above (downstream of {}), the PV installation generates up to {} kWh during the summer.\
   This corresponds to a maximum of up to {}% of the hourly consumption load being supplied by PV generation.\
   In the winter, only up to {}% of the hourly consumption load is supplied by PV generation.\
   Hourly distribution of PV generation is higher during the daytime, reaching a peak around 1-2 pm, \
   and is zero during the night when there is no sunlight. \
   However, even for the mid-day hours, there are still days when the percentage is very close to 0, \
   likely due to rainy/snowy days or possibly due to system malfunctions. <br><br>\
   For other transformers, these values may be different.".format(grid_element_id, 
                                                                  '%.2f' % df_pv_agg['TRUE_GENERATION'].max() 
                                                                  ,'%.2f' % df_pv_agg['percent_production'].max()
                                                                  ,'%.2f' % winter_max))


# In[13]:


df_pv_agg


# In[14]:


# Plot the percentage of monthly PV generations with respect to monthly loads and all-time PV generation percentage. 
plot_monthly_bar(df_pv_agg)


# Since there is no PV generation at night, but there is still consumption at night, it is interesting to aggregate the hourly energy amounts to monthly or longer and then re-compute the percentages of PV supply. The above plot shows what percentage of the monthly consumption load is supplied by PV generation. The percentage of consumption load supplied by PV generation is higher during the summer and lower during the winter. The horizontal line shows the all-time percentage of consumption load supplied by PV generation.

# ---
