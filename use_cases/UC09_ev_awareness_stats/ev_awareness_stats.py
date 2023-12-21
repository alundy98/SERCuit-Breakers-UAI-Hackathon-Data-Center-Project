#!/usr/bin/env python
# coding: utf-8

# # Overview

# This notebook is intended to showcase some analytics that can be run on EV Charger time series data, with a focus on sumary statistics. It is not meant to be comprehensive, many further analyses could be envisioned.
# 
# The notebook is structured as follows:
# - A Setup section for establishing a connection and performing basic data checks
# - A Data Retrieval section for actual retrieval of the time series for EV Chargers as well as Meters
# - Three Analysis sections:
#     1. Comparing total load of EV Chargers with that of Meters over the entire period that data is available for
#     2. Summarizing EV Charger load over the period by day and by month
#     3. Visualizing temporal patterns in EV Charger load, e.g. versus day of the week, hour of the day or month of the year
# 
# These kinds of analyses can help utilities better understand the nature of EV Charger load, which in turn can lead to better planning and management of EV charging infrastructure or EV Charger rate design.
# 
# For more details about EV Charger usage analysis and how it can be done using Awesense's platform, please refer to the [UC09-01 - EV Charging and Use of Reserved Capacity Analysis.pdf](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC09-01%20-%20EV%20Charging%20and%20Use%20of%20Reserved%20Capacity%20Analysis.pdf) document.

# # Setup

# In[1]:


import getpass
import urllib.parse
import pandas as pd
import plotly.express as px
import numpy as np


# ## Conection

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


# ## Data checks

# #### Checking the awefice grid is present

# In[3]:


get_ipython().run_cell_magic('sql', '', '\nSELECT * FROM grid;\n')


# #### Looking up the EV Chargers in this grid.

# In[4]:


get_ipython().run_cell_magic('sql', '', "\nSELECT *\nFROM grid_element_data_source geds, grid_element ge\nWHERE ge.grid_element_id = geds.grid_element_id\nAND ge.grid_id = 'awefice'\nAND ge.type = 'EVCharger';\n")


# # Data Retrieval

# In[5]:


# Defining Data Retrieval Function.

def get_hourly_kwh(grid_id, grid_element_type, new_col_name_for_id):
    """
    Retrieves the consumption (kWh) time series for a given grid_element_type from a given grid, aggregated per hour,
        converted from UTC to Vancouver timezone, and returns the final output as a dataframe.
        
    Note that to avoid infinitesimally small load which impacts the capacity analysis a lot, 
        the values are rounded to 2 decimal places.
    """
    
    result =  get_ipython().run_line_magic('sql', 'SELECT geds.grid_element_id,                          date_trunc(\'hour\', timestamp) at time zone \'America/Vancouver\' as date_hour,                          round(sum(value)::numeric, 2) AS "kWh",                          geds.type                      FROM grid_element ge                      JOIN grid_element_data_source geds                          ON ge.grid_id = geds.grid_id                              AND ge.grid_element_id = geds.grid_element_id                      JOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\', null) tdss                          ON true                      WHERE ge.grid_id = \'{grid_id}\'                          AND ge.type = \'{grid_element_type}\'                      GROUP BY geds.grid_element_id, date_hour, geds.type;')

    df = result.DataFrame()
    df['kWh'] = df['kWh'].astype(float)
    df.rename(columns={'grid_element_id': new_col_name_for_id}, inplace=True)
    return df


# ## Retrieving and summarizing EV Charger time series

# In[6]:


# Get time series consumption data for EVChargers, aggregated to hourly granularity.
df_evch = get_hourly_kwh('awefice', 'EVCharger', 'evch_id')


# In[7]:


df_evch.head()


# In[8]:


# Summary stats for EV Chargers.
df_evch.describe()


# ## Retrieving and summarizing the Meter time series

# In[9]:


# Get time series consumption data for Meters, aggregated to hourly granularity.
df_meter = get_hourly_kwh('awefice', 'Meter', 'meter_id')


# In[10]:


df_meter.head()


# In[11]:


# Summary stats for Meters.
df_meter.describe()


# ## Further data munging (reshaping, joining Meters and EV Chargers)

# In[12]:


# Pivot the Meter data to separate out Producer (flow in) and Consumer (flow out) components.
df_meter_split = df_meter.pivot(index=['meter_id', 'date_hour'], columns='type', values='kWh').rename_axis(None, axis=1).reset_index()
df_meter_split.head()


# In[13]:


# Summary row counts
df_meter_split.groupby(['meter_id']).count()


# In[14]:


# Replace Nan values with 0.
df_meter_split.replace(np.nan, 0, inplace=True)
df_meter_split.head()


# In[15]:


# Compute the net of flow in and out for each meter.
df_meter_split['NET'] = df_meter_split['CONSUMER'] - df_meter_split['PRODUCER']
df_meter_split.head()


# In[16]:


# Summary stats of Meter data.
df_meter_split.describe()


# #### Retrieve the EV Charger to Meter correspondence from the DB:

# In[17]:


get_ipython().run_cell_magic('sql', 'evchs_meter <<', "\nSELECT ge1.grid_element_id as evch_id,\n    ge2.grid_element_id as meter_id\nFROM grid_element ge1\nJOIN grid_get_sources('awefice', ge1.grid_element_id, True) ge2 ON True\nWHERE ge1.type='EVCharger'\n    AND ge2.type = 'Meter';\n")


# In[18]:


# Convert to data frame for later use.
evchs_meter = evchs_meter.DataFrame()
evchs_meter


# In[19]:


# Add-in the information about the corresponding Meter in the EV Charger time series data frame.
df_evch_m = pd.merge(df_evch, evchs_meter, on='evch_id', how='inner')
df_evch_m.head()


# # Analysis: Meter-EVCharger Correspondence

# ## Summing up data over all time

# In[20]:


# Print out the time ranges of the data for both Meters and EV Chargers for informational purposes
print(df_meter['date_hour'].min())
print(df_meter['date_hour'].max())
print(df_evch['date_hour'].min())
print(df_evch['date_hour'].max())


# In[21]:


# Compute the sum of EV Charger consumption from all the EV Chargers corresponding to each Meter.
meter_evch_kwh_portion = df_evch_m.groupby('meter_id').sum('kWh').reset_index()
meter_evch_kwh_portion


# In[22]:


# Sum up the Meter time series (flow in, flow out and net) for each Meter.
meter_kwh_total_net = df_meter_split.groupby('meter_id').sum(numeric_only=True).reset_index()
meter_kwh_total_net.sort_values(by='NET', ascending=False)


# In[23]:


# Add in the information about respective EV Charger consumption for each Meter
meter_combined_data = pd.merge(meter_evch_kwh_portion, meter_kwh_total_net, on='meter_id', how='outer')
meter_combined_data = meter_combined_data.rename(
    columns={'kWh': 'EV Charger Consumption'
             , 'PRODUCER': 'Meter Flow In'
             , 'CONSUMER': 'Meter Flow Out'
             , 'NET': 'Meter Net'})
meter_combined_data


# ## Plotting

# In[24]:


# Plot per-Meter EVCharger consumption alongside Meter's Net flow, sorted by the latter descending. 
fig = px.bar(meter_combined_data.sort_values(by='Meter Net', ascending=False)
       , x='meter_id', y=['Meter Net', 'EV Charger Consumption']
       , labels={'value':'kWh', 'meter_id':'Meter'}
       , barmode='group'
       , color_discrete_map={'Meter Net': 'gray', 'EV Charger Consumption': 'red'}
       , title='<b>All-time</b> EV Charger Consumption compared to Meter Net<br>Meters sorted descending by their <i>Net</i>'
      )
fig.update_xaxes(tickangle=45)
fig.show()


# In[25]:


# Plot per-Meter EVCharger consumption alongside Meter's Net flow, sorted by the former descending. 
fig = px.bar(meter_combined_data.sort_values(by='EV Charger Consumption', ascending=False)
       , x='meter_id', y=['Meter Net', 'EV Charger Consumption']
       , labels={'value':'kWh', 'meter_id':'Meter'}
       , barmode='group'
       , color_discrete_map={'Meter Net': 'gray', 'EV Charger Consumption': 'red'}
       , title='<b>All-time</b> EV Charger Consumption compared to Meter Net<br>Meters sorted descending by their <i>downstream EV Charger Consumption</i>'
      )
fig.update_xaxes(tickangle=45)
fig.show()


# In[26]:


# Plot per-Meter EVCharger consumption alongside Meter's Flow In&Out, sorted by the former descending. 
fig = px.bar(meter_combined_data.sort_values(by='EV Charger Consumption', ascending=False)
       , x='meter_id', y=['Meter Flow Out', 'Meter Flow In', 'EV Charger Consumption']
       , labels={'value':'kWh', 'meter_id':'Meter'}
       , barmode='group'
       , color_discrete_map={'Meter Flow Out': 'blue','Meter Flow In': 'lightblue', 'EV Charger Consumption': 'red'}
       , title='<b>All-time</b> EV Charger Consumption compared to Meter Flow In/Out<br>Meters sorted descending by their <i>downstream EV Charger Consumption</i>'
      )
fig.update_xaxes(tickangle=45)
fig.show()


# # Analysis: EV Charger Consumption Over All Time

# ## Helper functions

# In[27]:


def sum_loads(df, groupby_list, sum_list):
    """
    Returns a dataframe that is grouped by `groupby_list` columns and summed `sum_list` columns.
    Note that the `groupby_list` and `sum_list` may be a single string.
    """
    
    df_final = df.groupby(groupby_list
                     ).sum(sum_list, min_count=1).reset_index()  # min_count=1 keeps null as null in summation.
    
    return df_final


# In[28]:


def add_date_range(df, time_col, desc_str):
    """
    Returns a string with a date range added to `desc_str` string, to be used in a chart's title.
    """

    # Pull out min and max dates to use in the title.
    min_date = min(df[time_col].dt.date).strftime('%b %d, %Y')
    max_date = max(df[time_col].dt.date).strftime('%b %d, %Y')

    # Prepare the title.
    title = desc_str + " (" + min_date + " ~ " + max_date + ")"
    
    return title


# ## Daily EV Charger consumption over entire history

# In[29]:


# Create a copy to prepare the data with.
df = df_evch.copy()

# Pull out a date component out of timestamp to aggregate over.
df['date'] = df['date_hour'].dt.date

# Sum all EV Chargers loads for each date.
df_daily = sum_loads(df, 'date', 'kWh')

# Prepare a title with a date range.
title = add_date_range(df, time_col='date_hour', 
                       desc_str='<b>Daily</b> Sum of All EV Charger Load')

# Plot.
fig = px.line(df_daily, 
        x = 'date', y = 'kWh',
        title = title,
        labels = {'date':'Date'},
        width = 1000, height = 500,
        range_y = [0, max(df_daily['kWh'])*1.05]
      )
fig.update_xaxes(tickangle=45)
fig.show()


# ## Monthly EV Charger consumption over entire history

# In[30]:


# Create a copy to prepare the data with.
df = df_evch.copy()

# Pull out a date component out of timestamp to aggregate over.
df['year_month'] = df['date_hour'].dt.strftime('%Y-%m')

# Sum EV Chargers for all parkades for each month.
df_monthly = sum_loads(df, 'year_month', 'kWh')

# Order the data by correct year month notion and not by alphabetically.
df_monthly = df_monthly.sort_values('year_month').reset_index()

# Prepare a title with a date range.
title = add_date_range(df, time_col = 'date_hour', 
                       desc_str = '<b>Monthly</b> Sum of All EV Charger Load')

# Plot.
fig = px.bar(df_monthly, 
        x = 'year_month', y = 'kWh',
        title = title,
        labels = {'year_month':'Date'},
        width = 1000, height = 500,
        range_y = [0, max(df_monthly['kWh'])*1.05]
      )
fig.update_xaxes(tickangle=45)
fig.show()


# # Analysis: EV Charger consumption patterns w.r.t. various units of time

# ## Data processing and generic helper functions

# In[31]:


# Helper function
def enrich_data_for_plotting(df_evch):
    """
    Adds columns to the given dataframe for various units of time (e.g. hour-of-day, day-of-week, month)
    by computing them from the date_hour column of the given dataframe.
    """
    
    # Create a copy of the raw dataframe to work with.
    df_all = df_evch.copy()

    # Pull out year-week combinations to separate out each day based on the week.
    df_all.loc[:,'year_week'] = df_all['date_hour'].dt.strftime('%Y-%W')

    # Assign word version of days of the week.
    df_all.loc[:,'day'] = df_all['date_hour'].dt.day_name()    

    # Assign numeric days of the week to maintain its ordinality.
    df_all.loc[:,'day_n'] = df_all['date_hour'].dt.weekday
    
    # Pull out the hours.
    df_all.loc[:,'hour'] = df_all['date_hour'].dt.hour

    # Pull out the months.
    df_all.loc[:,'month'] = df_all['date_hour'].dt.month_name()

    # Assign numeric month of year to maintain its ordinality.
    df_all.loc[:,'month_n'] = df_all['date_hour'].dt.month

    return df_all


# In[32]:


# Enrich EV Charger data with hour-of-day/day-of-week/month information.
df_evch_enh = enrich_data_for_plotting(df_evch)
df_evch_enh.head()


# In[33]:


# Helper function
def prep_data_for_overlaid_line_chart(df_evch_enh_agg, column, line_length):
    """
    Process data for overlaid line chart using the given column of the given dataframe 
    and breaking the data into segments of line_length length (e.g. 7 for day-of-week chart, 
    24 for hour-of-day chart).
    The easiest way to obtain a chart of overlaid lines is to insert rows with Nones into 
    the dataframe at the places where a new segment is supposed to start.
    """
    df = df_evch_enh_agg.copy()

    # Order properly to then be able to remove first&last partial weeks.
    df.sort_values(['year_week', 'evch_id', column], ascending = True, inplace = True, ignore_index=True)

    # find portion of first week (if it doesn't start on Monday)
    first_0 = next(idx for idx, val in enumerate(df[column], 1) if val == 0) - 1

    # find portion of last week (if it doesn't end on Sunday)
    last_6 = len(df) - next(idx for idx, val in enumerate(df.loc[::-1, column], 1) if val == 6)

    # remove first and last partial weeks
    df = df.iloc[first_0 : (last_6+1),].reset_index(drop=True)

    # Re-sort by evch_id first
    df.sort_values(['evch_id', 'year_week', column], ascending = True, inplace = True, ignore_index=True)

    # Get the distinct evch_ids.
    evch_ids = df['evch_id'].unique()
    evch_ids_and_counts = df.groupby('evch_id')['kWh'].count().reset_index().sort_values('evch_id', ascending=True)

    # Insert Nones with proper evch_id for plotting purposes (to break into segments)
    idx = 0
    for i in range(len(evch_ids_and_counts)):
        evch_id_val, count = evch_ids_and_counts.iloc[i,:]
        n_weeks = count // line_length
        break_rows = pd.DataFrame({"hour": np.NaN, "kWh": None, "evch_id": evch_id_val, "year_week": None}
                                  , index=[idx + line_length * (i + 1) - 0.5 for i in range(n_weeks)])
        #df = df.append(break_rows, ignore_index=False)
        df = pd.concat([df, break_rows], ignore_index=False)
        idx = idx + count
    # Get the Nones to be in the right spots
    df = df.sort_index().reset_index(drop=True)

    return df


# ## Day-of-Week Analysis

# In[34]:


# Helper function
def plot_evch_daily_load_vs_dow(df_all, evch_id, chart_type):
    """
    Makes a plot of EV Charger Load vs. Day of Week.
    Can plot one EV Charger or many (if evch_id='all'), in either multi-line form or boxplot form 
    as specified by chart_type.
    """

    # Prepare a title with an appropriate date range.
    df_for_title = (df_all if evch_id == 'all' else df_all[df_all['evch_id'] == evch_id])
    title = add_date_range(df_for_title,
                           time_col = 'date_hour', 
                           desc_str = 'EV Charger <b>Daily</b> Sum of Load vs. <b>Day-of-week</b>' + '<br>' + ('<b>All</b> EV Chargers' if evch_id == 'all' else 'EV Charger: <b>' + evch_id + '</b>')
                          )

    # Sum EV Load data to days.
    df_agg = sum_loads(df_all, ['evch_id', 'year_week', 'day', 'day_n', 'month', 'month_n'], 'kWh')

    # Filter out for a single evch id if needed.
    if evch_id == 'all':
        df = df_agg
    else:
        df = df_agg[df_agg['evch_id'] == evch_id].copy()
    
    # Plot appropriate chart based on the chart type specified.
    if chart_type=='box':
        # Order by numeric days of the week to order the plot properly.
        df.sort_values('day_n', ascending = True, inplace = True)
        # Plot.
        fig = px.box(df, x='day', y='kWh',
                         title=title,
                         labels={'day':'Day of Week'},
                         width=1000, height=500)
        fig.update_traces(marker_color = 'red')

    elif chart_type=='line':
        # Split into weekly chunks each with 7 daily values
        df = prep_data_for_overlaid_line_chart(df, 'day_n', 7)
        
        # Plot.
        fig = px.line(df, x='day', y='kWh', 
                      color = 'evch_id',
                      title=title,
                      labels={'day':'Day of Week', 'evch_id':'EV Charger ID'}, 
                      width=1000, height=500)
        # Set the multiple lines to be more transparent
        fig.update_traces(opacity=0.2)

    fig.show()


# In[37]:


# Plotting day-of-week boxplots and line charts, first for all EV Chargers combined and then one EV Charger per plot.
plot_evch_daily_load_vs_dow(df_evch_enh, evch_id='all', chart_type='line')
plot_evch_daily_load_vs_dow(df_evch_enh, evch_id='all', chart_type='box')
# Below loop is currently commented out because Jupyter has issues rendering that many plots.
# for evch_id in df_evch['evch_id'].unique():
#     plot_evch_daily_load_vs_dow(df_evch_enh, evch_id=evch_id, chart_type='line')
#     plot_evch_daily_load_vs_dow(df_evch_enh, evch_id=evch_id, chart_type='box')


# ## Hour-of-Day Analysis

# In[38]:


def plot_evch_hourly_load_vs_hod(df_all, evch_id, chart_type):
    """
    Makes a plot of EV Charger Load vs. Hour-of-Day.
    Can plot one EV Charger or many, in either multi-line form or boxplot form
    """

    # Prepare a title with an appropriate date range.
    df_for_title = (df_all if evch_id == 'all' else df_all[df_all['evch_id'] == evch_id])
    title = add_date_range(df_for_title,
                           time_col = 'date_hour', 
                           desc_str = 'EV Charger <b>Hourly</b> Load vs. <b>Hour-of-Day</b>' + '<br>' + ('<b>All</b> EV Chargers' if evch_id == 'all' else 'EV Charger: <b>' + evch_id + '</b>')
                          )

    # Sum EV Load data to hours.
    df_agg = sum_loads(df_all, ['evch_id', 'year_week', 'hour'], 'kWh')

    # Filter out for a single evch id if needed.
    if evch_id == 'all':
        df = df_agg
    else:
        df = df_agg[df_agg['evch_id'] == evch_id].copy()
    
    # Plot appropriate chart based on the chart type specified.
    if chart_type=='box':
        # Order by numeric days of the week to order the plot properly.
        df.sort_values('hour', ascending = True, inplace = True)
        
        fig = px.box(df, x='hour', y='kWh',
                         title=title,
                         labels={'hour':'Hour of Day'},
                         width=1000, height=500)
        fig.update_traces(marker_color = 'red')

    elif chart_type=='line':
        # Split into daily chunks each with 24 hourly values
        df = prep_data_for_overlaid_line_chart(df, 'hour', 24)
        
        # Plot.
        fig = px.line(df, x='hour', y='kWh', 
                      color = 'evch_id',
                      title=title,
                      labels={'hour':'Hour of Day', 'evch_id':'EV Charger ID'}, 
                      width=1000, height=500)
        # Set the multiple lines to be more transparent
        fig.update_traces(opacity=0.2)

    fig.show()


# In[40]:


# Plotting hour-of-day boxplots and line charts, first for all EV Chargers combined and then one EV Charger per plot.
plot_evch_hourly_load_vs_hod(df_evch_enh, evch_id='all', chart_type='line')
plot_evch_hourly_load_vs_hod(df_evch_enh, evch_id='all', chart_type='box')
# Below loop is currently commented out because Jupyter has issues rendering that many plots.
# for evch_id in df_evch['evch_id'].unique():
#     plot_evch_hourly_load_vs_hod(df_evch_enh, evch_id=evch_id, chart_type='line')
#     plot_evch_hourly_load_vs_hod(df_evch_enh, evch_id=evch_id, chart_type='box')


# ## Month-of-Year Analysis

# In[41]:


def plot_evch_daily_load_vs_moy(df_all, evch_id):
    """
    Makes a plot of Daily EV Charger Load vs. Month of Year.
    Can plot one EV Charger or many, but in boxplot form only
    """

    # Prepare a title with an appropriate date range.
    df_for_title = (df_all if evch_id == 'all' else df_all[df_all['evch_id'] == evch_id])
    title = add_date_range(df_for_title,
                           time_col = 'date_hour', 
                           desc_str = 'EV Charger <b>Daily</b> Sum of Load vs. <b>Month-of-Year</b>' + '<br>' + ('<b>All</b> EV Chargers' if evch_id == 'all' else 'EV Charger: <b>' + evch_id + '</b>')
                          )

    # Sum EV Load data to days.
    df_agg = sum_loads(df_all, ['evch_id', 'year_week', 'month', 'month_n'], 'kWh')

    # Filter out for a single evch id if needed.
    if evch_id == 'all':
        df = df_agg
    else:
        df = df_agg[df_agg['evch_id'] == evch_id].copy()

    # Order by numeric days of the week to order the plot properly.
    df.sort_values('month_n', ascending = True, inplace = True)
    
    # Plot.
    fig = px.box(df, x='month', y='kWh',
                     title=title,
                     labels={'month':'Month'},
                     width=1000, height=500)
    fig.update_traces(marker_color = 'red')

    fig.show(config={'staticPlot': True})


# In[42]:


# plot_evch_daily_load_vs_moy(df_evch_enh, evch_id='all')
for evch_id in df_evch['evch_id'].unique():
    plot_evch_daily_load_vs_moy(df_evch_enh, evch_id=evch_id)

