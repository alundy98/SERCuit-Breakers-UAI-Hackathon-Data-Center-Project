#!/usr/bin/env python
# coding: utf-8

# ### Overview 

# This notebook is intended to:
# 
# * Analyze the difference between the contracted  capacity and the actual load on a grid section downstream from a feeder. 
# 
# This use case is designed as follows:
# 
# * Define a grid section by choosing a feeder of interest.
# * Display all meters in the grid with their parent transformers and relevant attributes.
# * Calculate the contracted capacity by summing the meters' Master Circuit Breaker (MCB) values.
# * Use the meters' hourly net consumer load time series to retrieve and aggregate the daily max of hourly-average power in this grid section.
# * Display a time series of the aggregated daily maximum net consumer load and the contracted capacity.
# 
# Utility companies can use the results of such analysis to determine if it is possible to connect more loads in this section of the grid, analyze the distribution of resources, and increase grid efficiency.
# 
# For more details about master circuit breaker analysis and how it can be analyzed using Awesense's platform, please refer to the [UC17-01 - Actual vs. Contracted Grid Capacity Analysis](UC17-01%20-%20Actual%20vs.%20Contracted%20Grid%20Capacity%20Analysis.pdf) document.

# ---

# ### Setup

# In[1]:


import getpass
import math
import pandas as pd
import urllib.parse
import plotly.express as px

pd.set_option('display.max_columns', None)


# #### Connection 

# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com. <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

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


# ### Use Case - Actual vs. Contracted Grid Capacity Analysis 

# #### Input Parameters 

# Input the grid name.

# In[3]:


# User input for the grid.
grid_id = input('Enter grid ID: ') # awefice


# Fetch and display all top feeders (transformers) in this grid.

# In[4]:


get_ipython().run_cell_magic('sql', 'result_feeders <<', "\nSELECT grid_element_id as top_feeder_transformer \nFROM grid_element\nWHERE grid_id = '{grid_id}'\n    AND type = 'Transformer'\n    AND is_producer = true;")


# In[5]:


# Convert the results to a data frame. 
df_feeders = result_feeders.DataFrame()

# Display the results.
df_feeders


# Input top feeder name.

# In[6]:


# User input for the feeder.
top_feeder_transformer = input('Enter feeder ID: ') # transformer_2


# ### Data 

# #### Meters' Information

# Fetch all meters in the grid and their relevant information.

# In[7]:


get_ipython().run_cell_magic('sql', 'result_meters <<', "\nSELECT ge_meter.grid_element_id as meter_id, \n    ge_meter.phases,\n    ge_meter.meta ->> 'maximal_demand' as maximal_demand,\n    ge_meter.meta ->> 'parent_transformer_id' as parent_transformer,\n    ge_trans.meta ->> 'secondary_voltage' as secondary_voltage\nFROM grid_element ge_trans\nJOIN grid_element ge_meter\n    ON ge_trans.grid_element_id = ge_meter.meta ->> 'parent_transformer_id' \nJOIN grid_get_downstream('{grid_id}', '{top_feeder_transformer}', 'false') ggd \n    ON ggd.grid_element_id = ge_trans.grid_element_id \nWHERE ge_meter.type = 'Meter'\nORDER BY LENGTH(ge_meter.phases), ge_meter.phases, \n        cast(substring(ge_meter.grid_element_id, 3, 2) as int) asc;   ")


# In[8]:


# Convert the results to a data frame. 
df_meters = result_meters.DataFrame()

# Change the variables' type.
df_meters = df_meters.astype({'phases': 'str', 'maximal_demand':'int', 'secondary_voltage': 'int'})

# Calculate the master circuit breaker values based on the meters' phase. 
df_meters.loc[df_meters['phases'].isin(['A','B','C']), 'master_circuit_kW']                 =((df_meters['secondary_voltage']/math.sqrt(3))*df_meters['maximal_demand']*0.98/1000)

df_meters.loc[df_meters['phases'].isin(['ABC']), 'master_circuit_kW']                 =(math.sqrt(3)*df_meters['secondary_voltage']*df_meters['maximal_demand']*0.98/1000)

# Set up a multi-index data frame to display the meter's phases, meter IDs, 
# information necessary to calculate MCB values, and the MCB values.
df_meters = df_meters.set_index(['phases', 'meter_id'])

# Display the results.
df_meters


# In[9]:


print('The sum of contracted capacity in this section of the grid is {} kW'      .format(round(df_meters['master_circuit_kW'].sum(),2)))


# #### Meters' Hourly Time Series 

# Fetch all meters downstream from the top feeder and their hourly net consumer load time series.

# In[10]:


get_ipython().run_cell_magic('sql', 'result_system <<', '\nSELECT tdss.timestamp at time zone \'America/Vancouver\' as timestamp,\n        ggd.grid_element_id as meter_id,\n        tdss.value as "kWh",\n        geds.type\nFROM grid_get_downstream(\'{grid_id}\', \'{top_feeder_transformer}\') AS ggd\nLEFT JOIN grid_element_data_source geds\n    ON geds.grid_id = ggd.grid_id\n    AND geds.grid_element_id = ggd.grid_element_id\nJOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\') tdss\n    ON TRUE\nWHERE ggd.type = \'Meter\'\n    AND geds.type = \'CONSUMER\'\nORDER BY tdss.timestamp, meter_id;')


# In[11]:


# Convert the SQL result to a Python dataframe.
df_system = result_system.DataFrame()

# Aggregate loads per meter and sum the hourly net consumer loads, and rename the kWh column.
df_system_agg = df_system.groupby(['timestamp'])['kWh'].sum().reset_index().rename(columns={'kWh':'sum_kWh'})

# Aggregate the sum of the hourly load by days and find the maximum load. 
df_system_agg_daily = df_system_agg.groupby(pd.Grouper(key='timestamp', axis=0, freq='D')).max().reset_index()


# In[12]:


# plot the aggregated load and the sum of the MCB values. 
fig = px.line(df_system_agg_daily, x="timestamp", y="sum_kWh", 
              title='Daily Max of Hourly-Average Power in the {} grid Downstream of Top Feeder {} '\
              .format(grid_id, top_feeder_transformer), 
              labels={"timestamp": "Timestamp (Days)", "sum_kWh": "Daily Max of Hourly-Average Power (kW)"})

# Add annotation to indicate the maximum point.
fig.add_annotation(x=df_system_agg_daily['timestamp'][df_system_agg_daily['sum_kWh'].idxmax()],
                   y=df_system_agg_daily['sum_kWh'].max(),
                   text='Max Point = {} kW occurred at {} <br> The ratio of Max Point to sum of contracted capacity is {}' \
                   .format(round(df_system_agg_daily['sum_kWh'].max(),2),
                           df_system_agg_daily['timestamp'][df_system_agg_daily['sum_kWh'].idxmax()].strftime('%Y-%m-%d'),
                           round((df_system_agg_daily['sum_kWh'].max()/df_meters['master_circuit_kW'].sum()),2)), 
                   arrowhead=2, arrowsize=1, arrowwidth=2, arrowcolor="#636363", ay=-60,
                   font=dict(family="sans serif", size=18, color="black"))

# Add a horizontal line showing the sum of contracted capacity.
fig.add_hline(y = df_meters['master_circuit_kW'].sum(), annotation_text='Sum of Contracted Capacity = {} kW'               .format(round(df_meters['master_circuit_kW'].sum(),2)), 
                      annotation_position='top right', line_color='Black', annotation_font_color='Black')
fig.show()


# The plot above shows the daily max of hourly-average power of all meters downstream from the feeder. The horizontal line shows the sum of the contracted capacity in the grid section, and the arrow points out the maximum point on the graph. The difference between the power time series and the horizontal line represents the average available capacity. Utility companies can use this to determine if more load can be added to this grid section or to reduce the contractual load and use the resources elsewhere. 

# ---
