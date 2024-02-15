#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the yearly net flow per tariff group. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid and year of interest.
# * Sum the net flow per tariff group.
# * Present and plot the results. 
# 
# This snippet presents two methods of querying, aggregating and calculating these results. The first method uses a single SQL query to fetch the data, aggregate, and calculate the results. The second method uses a SQL query to fetch the data and Python to aggregate it and calculate the results.

# ---

# ## Setup

# In[1]:


import getpass
import urllib.parse
import plotly.express as px
import pandas as pd
import numpy as np
import datetime
import pytz


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

# ## Net Flow per Tariff Group

# #### Input Parameters
# Enter the grid ID and year. 

# In[3]:


grid_id = input('Enter grid ID: ') # awefice
year = input('Enter year: ') # 2021

# Get the start and end dates for the year and localize them.
year_start = datetime.datetime.min.replace(year = int(year))
year_start = pytz.timezone('America/Vancouver').localize(year_start)
year_end = datetime.datetime.max.replace(year = int(year))
year_end = pytz.timezone('America/Vancouver').localize(year_end)

# Convert it to string. 
timerange_tz = f'[ {year_start} , {year_end} ]'


# #### Sum of net flow by tariff group - Method 1 - Using only SQL to fetch, aggregate and calculate results

# In[4]:


get_ipython().run_cell_magic('sql', 'net_tariff <<', '\nSELECT ge.meta ->> \'tariff_id\' as tariff_id, \n    ROUND(CAST(SUM(tdss_c.value - COALESCE(tdss_p.value,0)) AS NUMERIC),2) as "net_kWh"\nFROM grid_element ge \nJOIN grid_element_data_source geds_c \n    ON geds_c.grid_element_id = ge.grid_element_id \n    AND geds_c.type = \'CONSUMER\' \nJOIN ts_data_source_select(geds_c.grid_element_data_source_id, \'kWh\', \'{timerange_tz}\') tdss_c \n    ON true \nLEFT JOIN grid_element_data_source geds_p \n    ON geds_p.grid_element_id = geds_c.grid_element_id \n    AND geds_p.type = \'PRODUCER\' \nLEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, \'kWh\', \'{timerange_tz}\') tdss_p \n    ON tdss_p.timestamp = tdss_c.timestamp \nWHERE ge.grid_id = \'{grid_id}\'\n    AND ge.type = \'Meter\'\nGROUP BY ge.meta ->> \'tariff_id\'\nORDER BY ge.meta ->> \'tariff_id\';\n')


# In[5]:


# Convert the results to a data frame.
df_tariff = net_tariff.DataFrame()
df_tariff['net_kWh'] = df_tariff['net_kWh'].astype(float)
df_tariff


# In[6]:


fig = px.bar(df_tariff, x="net_kWh",  y="tariff_id", orientation="h", color="net_kWh",
             color_continuous_scale=px.colors.diverging.Temps, color_continuous_midpoint=0,
             title="<b>Net Flow by Tariff Group for the year {}<b>".format(year),
             labels={'tariff_id':'Tariff Group', 
                         'net_kWh': 'Net Flow (kWh)'})
fig.show()


# Negative net flow indicates that production from DERs (in this case, solar panels) is greater than the current consumption. 

# ---

# #### Sum of net flow by tariff group - Method 2 - Using a SQL query to fetch the data and Python to aggregate and calculate the results  

# In[7]:


get_ipython().run_cell_magic('sql', 'net_flow <<', '\nSELECT ge.meta ->> \'tariff_id\' as tariff_id, \n    tdss.value as "kWh",\n    geds.type\nFROM grid_element ge \nJOIN grid_element_data_source geds \n    ON geds.grid_element_id = ge.grid_element_id  \nJOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\', \'{timerange_tz}\') tdss \n    ON true \nWHERE ge.grid_id = \'{grid_id}\'\n    AND ge.type = \'Meter\';\n')


# In[8]:


# Convert the results to a data frame.
df_flow = net_flow.DataFrame()


# In[9]:


# Aggregate the consumer load.
df_consumer = df_flow.loc[df_flow['type']=='CONSUMER'].groupby('tariff_id').sum(numeric_only=True).rename(columns={'kWh': 'consumer_kWh'})

# Aggregate the producer load.
df_producer = df_flow.loc[df_flow['type']=='PRODUCER'].groupby('tariff_id').sum(numeric_only=True).rename(columns={'kWh': 'producer_kWh'})

# Concat the aggregated consumer and producer loads. 
df_net=pd.concat([df_consumer, df_producer], axis=1).fillna(0).reset_index()

# Calculate the net load. 
df_net['net_kWh']=(df_net['consumer_kWh']-df_net['producer_kWh']).round(2)

# Display the results. 
df_net.sort_values(by=['tariff_id'])


# ---
