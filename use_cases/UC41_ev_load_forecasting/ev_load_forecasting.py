#!/usr/bin/env python
# coding: utf-8

# ## Abstract

# The upcoming increase in electric vehicles (EVs) will have a significant impact on electricity usage. To prepare for the electrification of the transportation sector, utilities will need to understand the nature of this impact. In particular, it will be beneficial to forecast the load increase due to EV chargers and identify grid areas requiring additional capacity to accommodate this increase.
# 
# The following notebook uses a simple model to predict the increase in *residentially-owned* EVs based on current EV penetration values and the average number of vehicles per household. This increase is translated into a forecast of additional load from EVs based on an average Level 2 Charger. Finally, the increased load is compared to the feeder's available capacity to predict if and when the total load from EV chargers will cause feeder capacity to be exceeded, thus overwhelming this grid section even in the absence of other load growth.
# 
# Utilities can use this analysis to quickly evaluate areas within their grid where the current total capacity will not suffice to support future EV charging. Please refer to the use case description for more detailed information about the notebook's design.
# 
# Further enhancements to the notebooks could be made to accommodate more sophisticated forecasting techniques (e.g., agent-based simulation) and to take into account other types of load growth, such as from commercial fleet electrification.
# 
# For more details about EV load forcasting and how it can be analyzed using Awesense's platform, please refer to the [UC41-01 - EV Load Growth Forecasting](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC41-01%20-%20EV%20Load%20Growth%20Forecasting.pdf) document.

# ## Notebook Overview

# This notebook is intended to:
# 
# * Forecast the growth of residential EVs in segments of the grid and the ensuing impact on grid loads.
# 
# This notebook is designed as follows:
# 
# * Setup
#     * Establish a connection to EDM using SQL API and define functions to be used by the rest of the notebook.
# 
# 
# * Section 1: EV Growth and Load Forecasting For the Entire Grid
# 
#     * Display the breakdown of meters by consumer type for the entire grid.
#     * Forecast the growth in the number of residential EVs (based on the current number of EV Chargers behind residential meters and various growth assumptions).
#     * Forecast the load resulting from the increased residential EV counts.
# 
# 
# * Section 2: EV Growth and Load Forecasting For a Given Feeder.
# 
#     * Aggregate the *existing load* from all meters in the given feeder (on a 365 day / 24h basis).
#     * Calculate the given feeder's *available capacity* (on a 365 day / 24h basis) by subtracting the existing aggregate load from the feeders' *total capacity* (a static property).
#     * Forecast the *growing load* from residential EV chargers (per methodology in Section 1) and visualize it in comparison to available capacity with the following scenarios:
#         * (Worst case) All chargers charge simultaneously during peak time.
#         * (Intermediate) Only 50% of the chargers charge during peak time, the rest during off-peak.
#         * (Most optimistic) All chargers charge simultaneously during off-peak hours (1 am to 7 am).
# 
# 
# * Section 3: Forecasting the Years When Feeders Will Exceed Capacity.
# 
#     * Fetch all feeders in the grid and determine their *available* capacities (per methodology in Section 2).
#     * Forecast each feeder's *growing load* from residential EV chargers (per methodology in Section 2).
#     * Compare the two and compute the year when the increased load due to residential EV chargers will exceed the available capacity.
#    
#    This analysis can be done using different growth rate scenarios (ranging from 20% per year to 50% per year) and with different percentages of chargers charging simultaneously.
# 
# * Assumptions used in this notebook:
#     * The average number of vehicles in American households is 1.88. 
#     * A typical Level 1 charger power output is 1.35 kW.
#     * A typical Level 2 charger power output is 7.2 kW. 
#     * Every EV is charged using a separate EV charger, even when multiple EVs are in the same household.
# 

# ---

# ## Setup

# In[1]:


# Import libraries
import getpass
import urllib.parse
import plotly.express as px
import pandas as pd
import numpy as np
import plotly.graph_objects as go
import math


# In[2]:


# Define global variables:
# Average number of vehicles in an American household.
average_vehicles_per_household = 1.88 

#Average power output of level 1 and 2 chargers. The average power for the Level 2 charger is based on multi-phase meters.
level_1_power_kW = 1.35 #kW
level_2_power_kW = 7.2 # kW


# ### Connection
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[3]:


# Connect to server
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


# ### Custom Functions

# In[4]:


def plot_available_capacity(df: pd.DataFrame, title: str) -> None:
    """"
    The function plots the distribution of available capacity over the hours of the day. It is typically used for one feeder at a time.

    Parameters:
    - df: A DataFrame containing the available capacity data with the following columns:
        - 'timestamp' (datetime): The timestamp of the available capacity data.
        - 'available_capacity_kW' (float): The available capacity in kilowatts.
    - title: The title of the plot.

    Returns:
    - None
    """  

     # Create a box plot using Plotly Express
    fig = px.box(x=df['timestamp'].dt.hour, y=df['available_capacity_kW'], color_discrete_sequence=['#1f77b4'])
    
    # Customize the layout of the plot
    fig.update_layout(
        title=title,
        xaxis_title='Hour of the Day',
        yaxis_title='Hourly Available Capacity (kW)'
    )
    fig.update_layout(xaxis=dict(dtick=1))  # Set x-axis tick frequency to 1
    
    # Show the plot
    fig.show()


def plot_calc_number_of_ev_chargers(df: pd.DataFrame, charger_power_kW: float, feeder_name: str) -> None:
    """
    The function visualizes the number of EV chargers that could run simultaneously without going over the hourly available capacity. 
    It calculates the number of EV chargers based on the available capacity and the given EV power rating, then plots the data using the Plotly library.

    Parameters:
    - df: A DataFrame containing the available capacity data with the following columns:
        - 'timestamp' (datetime): The timestamp of the available capacity data.
        - 'available_capacity_kW' (float): The available capacity in kilowatts.
    - charger_power_kW: Average power output of a charger.
    - feeder_name: ID of the feeder.

    Return:
    - None
    """
    
    # Calculate the number of EV chargers based on an average power output 
    df['EVs'] = (df['available_capacity_kW'] / float(charger_power_kW)).apply(np.floor)
    
    # Create the figure using Plotly.
    fig = go.Figure()
                                         
    # Plot the number of EV chargers.
    fig.add_trace(go.Scatter(x=df['timestamp'], y=df['EVs'], line=dict(color="#1f77b4"), showlegend=False))
    
    # Customize the title, axis labels, axis ticks, and figure size.
    fig.update_layout(title='Number of Additional EV Chargers that Could Be Installed in Feeder ' + feeder_name)
    fig.update_yaxes(title_text='Number of EV Chargers')
    fig.update_xaxes(title_text='Timestamp')
    fig.update_layout(yaxis=dict(dtick=10))
    fig.update_yaxes(dtick=10)
    
    # Show the figure.
    fig.show()

    
def calculate_ev_increase(ev_chargers_count: float, meters_count: float, average_vehicles: float) -> pd.DataFrame:
    """
    The function calculates the linear and exponential increase in EVs based on the current number of chargers and various growth rates. 
    It generates growth projections over a specified time range and stores the results in a DataFrame. 
    Calculations are performed for a range of potential yearly growth rates going from 20% to 50% in 1% increments. 

    Parameters:
    - ev_chargers_count: The current number of EV chargers.
    - meters_count: The total number of meters. 
    - average_vehicles: The average number of vehicles in a household. 

    Return:
    - df (data frame): A DataFrame containing the growth projections for EVs over time. It includes columns for the year, growth rate, linear EV growth, and exponential EV growth.
    """

     # Define start and end year for growth calculations
    start_year = '2024-01-01'
    end_year = '2051-01-01'
    
    # Create DataFrame to store growth projections
    df_growth = pd.DataFrame(columns = ["year"])
    df_growth['year'] = pd.DataFrame(pd.date_range(start=start_year, 
                                                   end=end_year, freq='y').strftime('%Y'), columns=['year'])
    
    # Generate growth rates from 20% annually to 50% in 1% increments. 
    growth = np.arange(0.2, 0.5, 0.01).round(2)

    # Initialize an empty DataFrame to store results
    df = pd.DataFrame()
    
    # Iterate through each growth rate
    for growth_rate in growth:
        # Assign growth rate to the df_growth DataFrame
        df_growth['growth_rate'] = growth_rate
        
        # Calculate linear growth of EVs
        df_growth['linear_ev_growth'] = range(0, df_growth.shape[0])
        df_growth['linear_ev_growth'] = (1 + growth_rate) * df_growth['linear_ev_growth'] + ev_chargers_count
        
        # Calculate the exponential growth of EVs
        # If there are no current EV Chargers, assign one EV Charger to calculate the exponential growth.  
        if ev_chargers_count == 0:
            ev_chargers_count = 1
        df_growth['exponential_ev_growth'] = range(0, df_growth.shape[0])
        df_growth['exponential_ev_growth'] = ((1 + growth_rate)**df_growth['exponential_ev_growth']) * ev_chargers_count
        
        # Round the linear and exponential growth values
        df_growth.linear_ev_growth = df_growth.linear_ev_growth.round(0)
        df_growth.exponential_ev_growth = df_growth.exponential_ev_growth.round(0)
        
        # Apply condition to restrict exponential growth values based on the number of meters and average number of vehicles per household
        df_growth['exponential_ev_growth'].where(df_growth['exponential_ev_growth'] < meters_count * average_vehicles, 
                                                 other = math.floor(meters_count * average_vehicles), inplace=True) 
        
        # Concatenate the growth projections to the result DataFrame
        df = pd.concat([df, df_growth])
        df.reset_index()
    
    # Return DataFrame with growth projections
    return df


def plot_ev_increase(df: pd.DataFrame, title: str) -> None:
    """
    The function visualizes the forecasted growth of EVs using both linear and exponential growth models across various growth rates.

    Parameters:
   - df: A DataFrame containing the forecasted EV increase data with the following columns:
        - year (str): The year of the predicted data.
        - linear_ev_growth (float): The linear prediction for the number of EVs.
        - exponential_ev_growth (float): The exponential prediction for the number of EVs. 
        - growth_rate (float): The growth rate used to predict the increase in EVs. 
    - title: The title of the plot.

    Returns:
    - None
    """

    # Create a line plot using Plotly Express
    fig = px.line(df, x='year', y=['linear_ev_growth', 'exponential_ev_growth'], 
                  animation_frame='growth_rate', range_y=[0, 1.1 * df['exponential_ev_growth'].max()],
                  color_discrete_sequence=['#1f77b4', 'purple'])

    # Customize the layout of the plot
    fig.update_layout(
        title=title,
        xaxis_title='Year',
        yaxis_title='Number of EVs'
    )
    fig.update_xaxes(tickangle=45)  # Rotate x-axis tick labels for better readability

    # Show the plot
    fig.show()


def plot_ev_load(df: pd.DataFrame, available_capacity_kW: float, title: str, plot_available_capacity: bool = False) -> None:
    """
    The function visualizes the forecast load from EV growth.
    
    - df: A DataFrame containing the forecasted load data with the following columns:
        - year (str): The years of the forecast.
        - linear_load_kW (float): The forecasted load using a linear growth model in kilowatts.
        - exponential_load_kW (float): The forecasted load using an exponential growth model in kilowatts.
        - growth_rate (float): The growth rate used to predict the increase in EVs. 
    - available_capacity_kW: The available capacity in kilowatts.
    - title: The title of the plot.
    - plot_available_capacity: Whether to include the available capacity line. The default is False.

    Returns:
    - fig: The Plotly figure object.
    """
    # Create the line plot.
    fig = px.line(df, x='year', y=['linear_load_kW', 'exponential_load_kW'], 
                  animation_frame='growth_rate', color_discrete_sequence=['#1f77b4', 'purple'], 
                  range_y=[0, 1.1 * max(df['exponential_load_kW'].max(), available_capacity_kW+10)])
    
    if plot_available_capacity:
        
        # Add the available capacity line.
        fig.add_hline(y=available_capacity_kW, 
                      annotation_text='Available Capacity (kW) = {}'.format(available_capacity_kW), 
                      annotation_position='bottom right', line_color='black')
            
        # Position the legend in the upper left corner.
        fig.update_layout(showlegend=True)
    
    # Update plot layout.
    fig.update_layout(title=title, xaxis_title='Year', yaxis_title='Power (kW)')
    fig.update_xaxes(tickangle=45) # Rotate x-axis tick labels for better readability

    # Show the plot
    fig.show()


def plot_consumption_per_day(df: pd.DataFrame, feeder_name: str) -> None:
    """
    The function visualizes the maximum hourly consumption per day for a specific feeder.
    
    Parameters:
    df: A DataFrame containing the load data with the following columns:
        - 'timestamp' (datetime): The timestamp of the net consumption data.
        - 'net_consumption_kWh' (float): The net consumption data in kilowatt-hours. 
    feeder_name: ID of the feeder.
    
    Returns:
    None
    """
    # Group the DataFrame by date and get the row with the maximum hourly consumption for each day
    df_load_day = df.loc[df.groupby(df['timestamp'].dt.date)['net_consumption_kWh'].idxmax()]
    
    # Create a line plot using plotly.express with 'timestamp' as the x-axis and 'net_consumption_kWh' as the y-axis
    fig = px.line(df_load_day, x='timestamp', y='net_consumption_kWh', color_discrete_sequence=['#1f77b4'])
    
    # Update the layout of the figure
    
    fig.update_layout(
        title='Maximum Hourly Consumption per Day in Feeder {}'.format(feeder_name),
        xaxis_title='Year',
        yaxis_title='Energy (kWh)'
    )
    
    # Display the plot
    fig.show()


def calculate_year_capacity_exceeded(df: pd.DataFrame, yearly_growth_rate: float, level_1_power: float, level_2_power: float, percent_of_chargers: float, calc_load_col_name: str, average_vehicles: float) -> pd.DataFrame:
    """
    Calculate the additional aggregate load for each feeder and determine the year when this load will 
    exceed the total capacity of the feeder.
    
    Parameters:
    df: A DataFrame containing the feeder data with the following columns:
        - grid_element_id (str): ID of the feeder.
        - number_of_evchargers (float): The current number of EV Chargers in the feeder.
        - number_of_meters (float): The number of meters in the feeder. 
        - total_capacity_kW (float): The total capacity of the feeder top transformer in kilowatts. 
        - max_kWh (float): The maximum hourly consumption in the feeder.
    yearly_growth_rate: Growth rate of EV chargers' adoption. 
    level_1_power: The proportion of Level 1 chargers multiplied by the average level 1 power. 
    level_2_power: The proportion of Level 2 chargers multiplied by the average level 2 power. 
    percent_of_chargers: The percent of EV chargers assumed to be charging simultaneously.
    calc_load_col_name: Name to be used for the calculated additional load column in the output DataFrame.
    average_vehicles: The average number of vehicles in an American household.  
    
    Returns:
    pd.DataFrame: DataFrame with the forecasted year for each feeder-top grid element when the load exceeds the total capacity.
    """
    
    # Create an empty DataFrame to store the forecasted years
    df_forecast = pd.DataFrame(columns=['grid_element_id'])
    
    # Iterate over each row in the input DataFrame
    for index, row in df.iterrows():
        # Extract necessary information from the row
        grid_element_id = row['grid_element_id']
        current_ev_chargers_count = row['number_of_evchargers']
        
        # Calculate the increase in EVs 
        df_feeder_ev = calculate_ev_increase(current_ev_chargers_count, row['number_of_meters'], average_vehicles)
        
        # Calculate the load from additional level 1 chargers and level 2 chargers
        df_feeder_ev['linear_load'] = (df_feeder_ev['linear_ev_growth'] - row['number_of_evchargers']) * (level_1_power + level_2_power) * (percent_of_chargers / 100)
        df_feeder_ev['exponential_load'] = (df_feeder_ev['exponential_ev_growth'] - row['number_of_evchargers']) * (level_1_power + level_2_power) * (percent_of_chargers / 100)
        df_feeder_ev['period'] = 'hourly'
        
        # Create a new DataFrame to store the forecasted year
        df_forecast_year = pd.DataFrame()
        
        # Determine the year when the load exceeds the total available capacity. 
        # Total available capacity is calculated by subtracting the maximum load (consumption/1 hour) from the total capacity. 
        df_forecast_year.at[0, 'grid_element_id'] = grid_element_id
        df_forecast_year.at[0, calc_load_col_name] = df_feeder_ev.loc[(df_feeder_ev['exponential_load'] >= (row['total_capacity_kW'] - row['max_kWh']/1)) 
                                                               & (df_feeder_ev['growth_rate'] == yearly_growth_rate)]['year'].min()
        
        # Append the forecasted year to the final DataFrame
        df_forecast = pd.concat([df_forecast, df_forecast_year])
        
    # Return the DataFrame with the forecasted years
    return df_forecast


# ---

# ## Section 1: EV Growth and Load Forecasting For the *Entire Grid*
# ### Display Meter Count by Consumer Type

# #### Input Parameters
# Enter the grid ID of interest.

# In[5]:


grid_id = input('Enter grid ID: ') # North Central Zone


# #### Meter Count by Consumer Type

# In[6]:


# Fetch the number of meters per consumer type.
meters_query = """
    SELECT COUNT(grid_element_id) as number_of_meters, 
        meta->> 'type_of_consumer' as consumer_type 
    FROM grid_element 
    WHERE grid_id = '{}' 
        AND type = 'Meter' 
    GROUP  BY (meta->> 'type_of_consumer') 
    ORDER BY number_of_meters desc;
    """
formatted_query = meters_query.format(grid_id)
meters = get_ipython().run_line_magic('sql', '$formatted_query')

# Convert the results to a data frame and plot. 
df_meters = meters.DataFrame()
df_meters['number_of_meters'] = df_meters['number_of_meters'].astype(float)


# In[7]:


# Plot the number of meters per consumer type. 
fig = px.pie(df_meters, 
             values='number_of_meters', names='consumer_type',
             title='Breakdown of Meters by Consumer Type',
             color_discrete_sequence=px.colors.qualitative.G10,
             labels={'consumer_type':'Consumer Type', 
                         'number_of_meters': 'Meter Count'})

# Show the consumer type, meter count and percentage inside the pie chart.
fig.update_traces(textposition='inside', 
                  texttemplate = "Type: %{label} <br> Count: %{value:} <br>(%{percent})",)
fig.show()


# ### Residential EV *Count* - Growth Forecasting For the Entire Grid

# In[8]:


# Get the number of residential EV Chargers currently existing on the grid. 
meters_query = """
    SELECT COUNT(ge.grid_element_id)::float as number_of_evs
    FROM grid_element ge
    JOIN grid_get_sources('{}', ge.grid_element_id, 'true') ggs
    ON true
    WHERE ge.grid_id = '{}' 
        AND ge.type = 'EVCharger'
        AND ggs.meta->> 'type_of_consumer' IN ('residential')
        AND ggs.type = 'Meter';
    """
formatted_query = meters_query.format(grid_id, grid_id)
current_ev_chargers_count_entire_grid = get_ipython().run_line_magic('sql', '$formatted_query')
current_ev_chargers_count_entire_grid = current_ev_chargers_count_entire_grid[0][0]
print('The current number of residential EV Chargers is {}'.format(current_ev_chargers_count_entire_grid))


# <mark style="background-color: #FFFF00">  
# Assuming potential yearly growth rates of EVs going from 20% to 50% in 1% increments, calculate the linear and compounded increase in EVs in the entire grid. </mark>
# 

# In[9]:


# Calculate the forecasted total number of EVs in the entire grid.
df_grid = calculate_ev_increase(current_ev_chargers_count_entire_grid, df_meters.loc[df_meters['consumer_type']=='residential', 'number_of_meters'][0], average_vehicles_per_household)


# In[10]:


plot_ev_increase(df_grid, title = 'Forecasting the Total Number of EVs in the {} grid'.format(grid_id))


# ### Residential EV *Load* - Growth Forecasting for the Entire Grid

# In[11]:


# Calculate the average daily load from additional EVs. All EVs are assumed to be charged using level 2 chargers. 
df_grid['linear_load_kW'] = (df_grid['linear_ev_growth'] - current_ev_chargers_count_entire_grid) * level_2_power_kW
df_grid['exponential_load_kW'] = (df_grid['exponential_ev_growth'] - current_ev_chargers_count_entire_grid) * level_2_power_kW
df_grid['period'] = 'daily'


# In[12]:


plot_ev_load(df_grid, available_capacity_kW=0, title = 'Forecasting Increase of Average Daily Power from Additional EVs for the Entire Grid')


# ---

# ## Section 2:  EV Growth and Load Forecasting For a *Given Feeder*
# ### Display All the Feeders and Calculate their Total Capacities

# In[13]:


# Get all the feeders in the grid and the number of meters and EV Chargers in each feeder. 
feeders_query = """ 
    WITH feeders_table_1 AS (
    SELECT ge.grid_element_id, 
        COUNT(ggd.grid_element_id) as number_of_meters,
        CAST(ge.meta->> 'rating_kva' AS float) as rating_kva 
    FROM grid_element ge
    JOIN grid_get_downstream('{}', ge.grid_element_id, 'false') ggd
    ON true
    WHERE ge.is_producer and ge.type = 'Transformer' 
        AND ge.grid_id = '{}'
        AND ggd.type = 'Meter'
        AND ggd.meta->> 'type_of_consumer' = 'residential'
    GROUP BY ge.grid_element_id, ge.meta
    ),
    feeders_table_2 AS (
    SELECT ge.grid_element_id, 
        COALESCE(COUNT(ggd.grid_element_id), 0) as number_of_evchargers
    FROM grid_element ge
    JOIN grid_get_downstream('{}', ge.grid_element_id, 'false') ggd
    ON true
    WHERE ge.is_producer and ge.type = 'Transformer' 
        AND ge.grid_id = '{}'
        AND ggd.type = 'EVCharger'
    GROUP BY ge.grid_element_id
    )
    SELECT t1.grid_element_id, t1.number_of_meters, t1.rating_kva, t2.number_of_evchargers
    FROM feeders_table_1 t1
    LEFT JOIN feeders_table_2 t2
        ON t2.grid_element_id = t1.grid_element_id
    ORDER BY t1.grid_element_id;
    """
formatted_query = feeders_query.format(grid_id, grid_id, grid_id, grid_id)
feeders = get_ipython().run_line_magic('sql', '$formatted_query')

# Convert the results to a data frame and display it. 
df_feeders = feeders.DataFrame().fillna(0)
df_feeders = df_feeders[['grid_element_id', 'number_of_meters', 'number_of_evchargers', 'rating_kva']]
df_feeders


# In[14]:


# Calculate the total capacity per feeder from the feeder-top transformers' rating_kva and display the results (top few).
df_feeders['total_capacity_kW'] = df_feeders['rating_kva'].astype(int) * 0.98
df_feeders.head()


# In[15]:


# Sort the feeders by the number of meters and display the results. 
df_feeders_sorted = df_feeders.sort_values(by='number_of_meters')
fig = px.scatter(df_feeders_sorted,
                x = 'grid_element_id', 
                y = 'number_of_meters',
                size = 'number_of_meters',
                color ='total_capacity_kW',
                title = 'Number of Meters in Each Feeder',
                labels=dict(number_of_meters="Number of Meters", grid_element_id='Grid Element ID')
                )
fig.update_xaxes(ticklabelstep=2)
fig.show()


# ### Select Feeder

# In[16]:


# Choose a `grid_element_id` of a feeder-top element. 
grid_element_id = input('Enter a feeder name: ') 
# For example: 
#'10407_hvmv' is a large feeder with 810 residential meters
#'17128_hvmv' is a medium feeder with 112 residential meters
#'39660_hvmv' is a small feeder with 64 residential meters


# ### Aggregate the Existing Consumption for the Given Feeder

# In[17]:


# Fetch the consumption time series for all meters in the feeder (i.e. downstream of the feeder-top element), 
# aggregate them to obtain the feeder's existing aggregate net consumption and display the results (top view). 
# This query retrieves both consumption and production time series (usually from PVs) for each meter 
# and subtracts production from consumption to get the feeder net consumption. 
net_consumption_kWh = """
    SELECT ge.grid_element_id as feeder_id,
            tdss_c.timestamp at time zone 'AST' as timestamp,
            SUM(tdss_c.value - COALESCE(tdss_p.value, 0)) as "net_consumption_kWh"
    FROM grid_element ge
    JOIN grid_get_downstream('{}', ge.grid_element_id, 'false') ggd
        ON ggd.grid_id = ge.grid_id
    JOIN grid_element_data_source geds_c
        ON geds_c.grid_element_id = ggd.grid_element_id
        AND geds_c.type = 'CONSUMER'
    JOIN ts_data_source_select(geds_c.grid_element_data_source_id, 'kWh') tdss_c
        ON true
    LEFT JOIN grid_element_data_source geds_p
        ON geds_p.grid_element_id = geds_c.grid_element_id
        AND geds_p.type = 'PRODUCER'
    LEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, 'kWh') tdss_p
        ON tdss_p.timestamp = tdss_c.timestamp
    WHERE ge.grid_element_id = '{}'
        AND ggd.type = 'Meter'
    GROUP BY ge.grid_element_id, tdss_c.timestamp
    ORDER by 2;
    """
formatted_query = net_consumption_kWh.format(grid_id, grid_element_id)
feeder_net_consumption = get_ipython().run_line_magic('sql', '$formatted_query')

# Convert the results to a data frame and display the top few.  
df_feeder_net_consumption = feeder_net_consumption.DataFrame()
df_feeder_net_consumption.head()


# ### Display *Maximum* Existing Hourly Consumption Per Day for the Given Feeder

# In[18]:


# Plot the maximum hourly consumption per day.
plot_consumption_per_day(df_feeder_net_consumption, grid_element_id)


# ### Calculate the Available Capacity for the Given Feeder

# In[19]:


# Calculate hourly available capacity for the given feeder and display it (a few timestamps only).

# Map each feeder capacity based on the feeder id. 
feeders_map = df_feeders.set_index('grid_element_id').to_dict()['total_capacity_kW']

# Map the total capacity for the specific feeder. 
df_feeder_net_consumption['total_capacity_kW'] = df_feeder_net_consumption['feeder_id'].map(feeders_map)

# Calculate the hourly available capacity by subtracting the total hourly load (obtained as total hourly consumption divided by 1 hour) from the feeder's total capacity. 
df_feeder_net_consumption['available_capacity_kW'] = df_feeder_net_consumption['total_capacity_kW'] - df_feeder_net_consumption['net_consumption_kWh']/1

df_feeder_net_consumption.head()


# ### Display the Distribution of Available Capacity for the Given Feeder with Respect to the Hour of the Day.

# In[20]:


# Plot the distribution of available capacity for the given feeder with respect to the hour of the day.
plot_available_capacity(df_feeder_net_consumption, title='Hourly Available Capacity Distribution w.r.t Hours of the Day')


# ### How many additional Level 2 EV Chargers can operate simultaneously every day during the hour with minimum available capacity?
# Note: In reality, it is not expected that all EVs would be charging simultaneously during peak hours; however, ensuring that *never* happens is not straightforward either. The grid may need to be prepared for worst-case load scenarios. Later in this notebook, we explore less stringent scenarios as well.

# In[21]:


# Compute the minimum hourly available capacity per day. 
df_feeder_load_day = df_feeder_net_consumption.loc[df_feeder_net_consumption.groupby
                                                 (df_feeder_net_consumption['timestamp'].dt.date)['available_capacity_kW'].idxmin()]

# Compute and plot how many additional EV chargers can simultaneously charge within that capacity.
plot_calc_number_of_ev_chargers(df_feeder_load_day, level_2_power_kW, grid_element_id)


# ### Growth of EV Count and Load *Over Time* for the Given Feeder 
# The question we will try to answer next is how soon the growth in EV adoption will reach levels problematic for the grid infrastructure's capacity.

# In[22]:


# Display information about the feeder. 
df_feeder = (df_feeders[df_feeders['grid_element_id']==grid_element_id]).copy().reset_index()
df_feeder.drop(columns=['index'], inplace=True)
current_ev_chargers_count_in_feeder = df_feeder['number_of_evchargers'][0]
df_feeder


# In[23]:


# Calculate the forecasted total number of EVs in the feeder. 
df_feeder_ev = calculate_ev_increase(df_feeder.loc[0, 'number_of_evchargers'], df_feeder.loc[0, 'number_of_meters'], average_vehicles_per_household)
df_feeder_ev


# In[24]:


plot_ev_increase(df_feeder_ev, title = 'Forecasting the Total Number of EVs in Feeder {} Over Time'.format(grid_element_id))


# <mark style="background-color: #FFFF00">  
# Assume 30% Level 1 chargers and 70% Level 2. </mark>

# In[25]:


# Set the proportion of level 1 and level 2 chargers. 
level_1_dist = 0.3
level_2_dist = 1 - level_1_dist

# Calculate the proportional power of each charger. level_1_power and level_2_power are global variables declared at the top of the notebook. 
level_1_proportional_power = level_1_power_kW * level_1_dist
level_2_proportional_power = level_2_power_kW * level_2_dist


# In[26]:


# Calculate the additional hourly load of the combination of Level 1 and Level 2 chargers, all charging simultaneously. 
df_feeder_ev['linear_load_kW'] = (df_feeder_ev['linear_ev_growth'] - current_ev_chargers_count_in_feeder) * (level_1_proportional_power + level_2_proportional_power)
df_feeder_ev['exponential_load_kW'] = (df_feeder_ev['exponential_ev_growth'] - current_ev_chargers_count_in_feeder) * (level_1_proportional_power + level_2_proportional_power)
df_feeder_ev['period'] = 'hourly'
df_feeder_ev


# #### Scenario 1
# Display the forecasted hourly load if *all* additional EVs are charging simultaneously at their respective charger's max rate.
# Compare it to the historic minimum hourly available capacity to denote the "worst case scenario" where this simultaneous charging happens during *peak time* every day.

# In[27]:


# Scenario 1
min_available_capacity_kW = df_feeder_net_consumption['available_capacity_kW'].min().round(2)
plot_ev_load(df_feeder_ev, min_available_capacity_kW,
             title = 'Forecasting Increase in Average Hourly Power from All Additional EVs in Feeder {} <br><sup> Compared to historic minimum hourly available capacity</sup>'
             .format(grid_element_id), 
             plot_available_capacity = True)

min_growth_rate = min(df_feeder_ev['growth_rate'])
max_growth_rate = max(df_feeder_ev['growth_rate'])

overflow_year_min_growth_rate = df_feeder_ev[(df_feeder_ev['growth_rate']==min_growth_rate) 
                                             & (df_feeder_ev['exponential_load_kW']>=min_available_capacity_kW)]['year'].min()
overflow_year_min_growth_rate = str(overflow_year_min_growth_rate).replace('nan','Not by 2050')

overflow_year_max_growth_rate = df_feeder_ev[(df_feeder_ev['growth_rate']==max_growth_rate) 
                                             & (df_feeder_ev['exponential_load_kW']>=min_available_capacity_kW)]['year'].min()
overflow_year_max_growth_rate = str(overflow_year_max_growth_rate).replace('nan','Not by 2050')

print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(min_growth_rate, overflow_year_min_growth_rate))
print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(max_growth_rate, overflow_year_max_growth_rate))


# #### Scenario 2
# This scenario assumes charging behaviour can be manipulated so that some EVs will not charge during peak time.
# Display the forecasted hourly load if *50%* EVs are charging simultaneously at their respective charger's max rate.
# Compared to the historic minimum hourly available capacity to denote the scenario where this simultaneous charging happens during *peak time* every day.

# In[28]:


# Scenario 2
df_feeder_ev_50 = df_feeder_ev.copy()
df_feeder_ev_50['linear_load_kW'] = df_feeder_ev_50['linear_load_kW']/2
df_feeder_ev_50['exponential_load_kW'] = df_feeder_ev_50['exponential_load_kW']/2

min_available_capacity = df_feeder_net_consumption['available_capacity_kW'].min().round(2)
plot_ev_load(df_feeder_ev_50, min_available_capacity,
             title = 'Forecasting Increase in Average Hourly Power from 50% of Additional EVs in Feeder {} \
             <br><sup> Compared to historic minimum hourly available capacity</sup>'
             .format(grid_element_id), 
             plot_available_capacity = True)

min_growth_rate = min(df_feeder_ev_50['growth_rate'])
max_growth_rate = max(df_feeder_ev_50['growth_rate'])

overflow_year_min_growth_rate = df_feeder_ev_50[(df_feeder_ev_50['growth_rate']==min_growth_rate) 
                                                & (df_feeder_ev_50['exponential_load_kW']>=min_available_capacity)]['year'].min()
overflow_year_min_growth_rate = str(overflow_year_min_growth_rate).replace('nan','Not by 2050')

overflow_year_max_growth_rate = df_feeder_ev_50[(df_feeder_ev_50['growth_rate']==max_growth_rate) 
                                                & (df_feeder_ev_50['exponential_load_kW']>=min_available_capacity)]['year'].min()
overflow_year_max_growth_rate = str(overflow_year_max_growth_rate).replace('nan','Not by 2050')

print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(min_growth_rate, overflow_year_min_growth_rate))
print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(max_growth_rate, overflow_year_max_growth_rate))


# #### Scenario 3
# Display the forecasted hourly load if *all* EVs are charging simultaneously at their respective charger's max rate.
# Compare it to historic *average off-peak* hourly available capacity to denote a scenario where all EV charging is shifted to off-peak.

# In[29]:


# Scenario 3
off_peak_available_capacity_kW = df_feeder_net_consumption[(df_feeder_net_consumption['timestamp'].dt.hour>1) 
                                             & (df_feeder_net_consumption['timestamp'].dt.hour<7)]['available_capacity_kW'].mean().round(2)

plot_ev_load(df_feeder_ev, off_peak_available_capacity_kW,
             title = 'Forecasting Increase in Average Hourly Power All from Additional EVs in Feeder {} \
             <br><sup> Compared to historic average hourly available capacity during off-peak hours</sup>'.format(grid_element_id),
             plot_available_capacity = True)

min_growth_rate = min(df_feeder_ev['growth_rate'])
max_growth_rate = max(df_feeder_ev['growth_rate'])

overflow_year_min_growth_rate = df_feeder_ev[(df_feeder_ev['growth_rate']==min_growth_rate) 
                                             & (df_feeder_ev['exponential_load_kW']>=off_peak_available_capacity_kW)]['year'].min()
overflow_year_min_growth_rate = str(overflow_year_min_growth_rate).replace('nan','Not by 2050')

overflow_year_max_growth_rate = df_feeder_ev[(df_feeder_ev['growth_rate']==max_growth_rate) 
                                             & (df_feeder_ev['exponential_load_kW']>=off_peak_available_capacity_kW)]['year'].min()
overflow_year_max_growth_rate = str(overflow_year_max_growth_rate).replace('nan','Not by 2050')

print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(min_growth_rate, overflow_year_min_growth_rate))
print('At a growth rate of {}, the year when capacity is overwhelmed is: {}'.format(max_growth_rate, overflow_year_max_growth_rate))


# ---

# ## Section 3:  Forecasting When Capacity Will Be Exceeded Across All Feeders

# ### Compute Per-Feeder Maximum Hourly Load and Pair with Per-Feeder Total Capacity

# In[30]:


# Get feeders hourly maximum load. It takes a few hours to run this query. The results were saved to a file called `feeders_max_load.csv`, which is saved in the repo. 
# Users can skip this cell and use the attached file instead.

# df_feeders_max_load = pd.DataFrame()
# for _ in df_feeders['grid_element_id']:
#     grid_id = 'North Central Zone'
#     grid_element_id=_
#     print(grid_element_id)

#     feeders_max_load_query = """
#     WITH total_consumption AS (SELECT ge.grid_element_id as grid_element_id,
#             SUM(tdss_c.value - COALESCE(tdss_p.value, 0)) as net_consumption_kWh
#     FROM grid_element ge
#     JOIN grid_get_downstream('{}', ge.grid_element_id, 'false') ggd
#         ON ggd.grid_id = ge.grid_id
#     JOIN grid_element_data_source geds_c
#         ON geds_c.grid_element_id = ggd.grid_element_id
#         AND geds_c.type = 'CONSUMER'
#     JOIN ts_data_source_select(geds_c.grid_element_data_source_id, 'kWh') tdss_c
#         ON true
#     LEFT JOIN grid_element_data_source geds_p
#         ON geds_p.grid_element_id = geds_c.grid_element_id
#         AND geds_p.type = 'PRODUCER'
#     LEFT JOIN ts_data_source_select(geds_p.grid_element_data_source_id, 'kWh') tdss_p
#         ON tdss_p.timestamp = tdss_c.timestamp
#     WHERE ge.grid_element_id = '{}'
#         AND ggd.type = 'Meter'
#     GROUP BY ge.grid_element_id, tdss_c.timestamp) SELECT DISTINCT grid_element_id, MAX(net_consumption_kWh) as "max_kWh" from total_consumption GROUP BY grid_element_id;
#     """
#     # Call the query and save the results to a data frame. 
#     formatted_query = feeders_max_load_query.format(grid_id, grid_element_id)
#     feeders_max_load = %sql $formatted_query
#     df_feeders_max_load = pd.concat([df_feeders_max_load, feeders_max_load.DataFrame()])


# In[31]:


# Comment out this line if you run the query above.
df_feeders_max_load = pd.read_csv('feeders_max_load.csv')

# Combine with a per-feeder total capacity (previously computed at the beginning of Section 2).
df_feeders_enhanced = pd.merge(df_feeders, df_feeders_max_load, on='grid_element_id')
df_feeders_enhanced.head()


# ### Input Growth and Charging Patterns Assumptions and Calculate and Display Years the Feeders Will Reach Capacity

# In[32]:


# Calculate the year when capacity is reached given a certain growth rate (between 5% and 33%) and a percentage of chargers charging simultaneously during peak hour.
current_growth_rate = 25.0
percent_chargers_peak_hour = 50.0
year_growth_chargers = 'year_g' + str(current_growth_rate) + '%_s' + str(percent_chargers_peak_hour) + '%'

# Call the function with a data frame, current growth rate and percentage of EV chargers charging simultaneously. 
df_feeders_limit_years = calculate_year_capacity_exceeded(df_feeders_enhanced, current_growth_rate/100, level_1_proportional_power, level_2_proportional_power, percent_chargers_peak_hour, year_growth_chargers, average_vehicles_per_household)
df_feeders_enhanced = pd.merge(df_feeders_enhanced, df_feeders_limit_years, on='grid_element_id')
df_feeders_enhanced.fillna('not by 2050', inplace=True)

# Display a few of the results. 
df_feeders_enhanced.head()


# In[33]:


# Plot a histogram of how many feeders will reach capacity each year given assumptions.
df_feeders_enhanced[year_growth_chargers].replace({'not by 2050':'Nan'}, inplace = True)
df_feeders_enhanced[year_growth_chargers] = df_feeders_enhanced[year_growth_chargers].astype(float).astype('Int64') # convert to int in a way that handles NAs
df_feeders_enhanced.sort_values(by=[year_growth_chargers], inplace=True)
fig = px.histogram(df_feeders_enhanced.dropna(), x=year_growth_chargers, nbins = 20, 
                   color_discrete_sequence=['#1f77b4'])
fig.update_layout(title="Distribution of Years when Additional Loads will Exceed Feeders' Available Capacity \
                  <br><sup> {}% growth rate and {}% of EV chargers charging simultaneously"
                  .format(current_growth_rate, percent_chargers_peak_hour), 
                   xaxis_title='Year')
    
fig.show()


# ---
