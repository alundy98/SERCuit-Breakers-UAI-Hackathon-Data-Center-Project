#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# * Analyze the meters' master circuit breaker values in a grid and compare it to the meters' maximum hourly net consumer load. 
# 
# This use case is designed as follows:
#   * Define a grid of interest.
#   * Display all meters in the grid with their parent transformers and relevant attributes.
#   * Calculate the meters' master circuit breaker values.
#   * Use the meters' hourly net consumer load time-series to retrieve the maximum historical hourly net consumer loads. 
#   * Calculate the ratio of meters' maximum hourly net consumer loads to their respective master circuit breaker values.
#   * Display the results as a bar graph and on the grid's map.
# 
# The results of such analysis can be used to increase customer satisfaction as it might lower customers' capacity payments or can help the Grid Planning department to better understand the real grid capacity constraints.
# 
# For more details about master circuit breaker analysis and how it can be analyzed using Awesense's platform, please refer to the [UC13 - Master Circuit Breaker Value vs. Measured Values Analysis](https://github.com/Awesense/edm-app-examples/blob/master/use_cases/usecase_descriptions/UC13%20-%20Master%20Circuit%20Breaker%20Value%20vs.%20Measured%20Values%20Analysis.pdf) document.

# ---

# ## Setup

# In[ ]:


import getpass
import math
import pandas as pd
import urllib.parse
import plotly.express as px

pd.set_option("display.max_columns", None)


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[ ]:


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

# In[ ]:


def bar_plot(df, x_axis, y_axis, title, labels, average=False):
    """
    Plot bar graphs of meters' master circuit breaker values or the ratio of meters' maximum hourly consumer loads to
    to master circuit breaker values.
    """

    # Plot a bar graph of meters' master circuit breaker values.
    if average == False:

        # Plot a bar graph with parent transformers as facets.
        fig = px.bar(
            df,
            x=x_axis,
            y=y_axis,
            title=title,
            labels=labels,
            facet_col="top_feeder_transformer",
            facet_col_wrap=4,
            facet_row_spacing=0.15,
            facet_col_spacing=0.1,
            color="phases",
            category_orders={"phases": ["A", "B", "C", "ABC"]},
            color_discrete_sequence=px.colors.qualitative.Dark2,
            height=600,
            width=1000,
        )

        # Update the axes labels and ticks' positions.
        fig.update_xaxes(matches=None, showticklabels=True, visible=True)
        fig.update_xaxes(tickangle=270)
        fig.update_yaxes(showticklabels=True, visible=True)
        fig.update_traces(width=0.5)

        # Update the subplots and axes titles.
        fig.for_each_yaxis(lambda y: y.update(title=""))
        fig.add_annotation(
            x=-0.1,
            y=0.5,
            text="Master Circuit Breaker Value (kW)",
            textangle=-90,
            xref="paper",
            yref="paper",
            showarrow=False,
        )
        fig.for_each_xaxis(lambda x: x.update(title=""))
        fig.add_annotation(
            x=0.5, y=-0.2, text="Meter ID", xref="paper", yref="paper", showarrow=False
        )

    # Plot a bar graph of the ratio with discrete colors showing ratio>=1.
    else:

        # Plot the bar graph, set the colors, and the text information.
        fig = px.bar(
            df,
            x=x_axis,
            y=y_axis,
            title=title,
            labels=labels,
            color="ratio_bool",
            color_continuous_scale=px.colors.diverging.Picnic,
            hover_data={x_axis: True, y_axis: True, "ratio_bool": False},
        )

        # Calculate the average ratio.
        df_average = df[y_axis].mean()

        # Add a horizontal plot showing the average ratio.
        fig.add_hline(
            y=df_average,
            annotation_text="Average ratio",
            annotation_position="top right",
            line_color="Black",
            annotation_font_color="Black",
        )

        # Don't display the color scale.
        fig.update_coloraxes(showscale=False)

    # Oriente the ticks' angle.
    fig.update_xaxes(tickangle=270)

    fig.show()


def map_it(df, title):
    """
    Plot a map of meters, with their colors dictated by the field called 'percentage'.
    """

    # Plot a map, and set the map style, and marker size.
    fig = px.scatter_map(
        df,
        lat="latitude",
        lon="longitude",
        hover_name="meter_id",
        title=title,
        color="ratio",
        zoom=16,
        color_continuous_scale=px.colors.diverging.balance,
        color_continuous_midpoint=1,
        labels={"ratio": "Ratio"},
    )
    fig.update_layout(mapbox_style="open-street-map")
    fig.update_traces(marker={"size": 15})

    # Use custom (raster) tile server
    fig.update_layout(
        map_style="white-bg",
        map_layers=[
            {
                "below": "traces",
                "sourcetype": "raster",
                "source": [
                    "https://d.tile.awesense.com/{z}/{x}/{y}.png",
                    "https://e.tile.awesense.com/{z}/{x}/{y}.png",
                    "https://f.tile.awesense.com/{z}/{x}/{y}.png",
                ],
            }
        ],
        margin={"r": 0, "t": 60, "l": 40, "b": 0},
    )

    fig.show()


# ---

# ## Use Case - Master Circuit Breaker Value Analysis

# #### Input Parameters
# Input grid name. 

# In[ ]:


# User input for the grid.
grid_id = input("Enter grid ID: ")  # awefice


# ### Data
# #### Meters Information
# Fetch the meters in the grid and their relevant information. Adjust the limit clause as desired.

# In[ ]:


# number of meters for the limit clause
number_meters = 100


# In[ ]:


get_ipython().run_cell_magic('sql', 'result_meters <<', "\nWITH meters as (SELECT *\nFROM grid_element\nWHERE type='Meter'\nAND grid_id = '{grid_id}'\nORDER BY grid_element_id\nLIMIT {number_meters}\n)\n\nSELECT m.grid_element_id as meter_id,\n    m.phases,\n    m.meta ->> 'maximal_demand' as maximal_demand,\n    m.meta ->> 'parent_transformer_id' as parent_transformer_id,\n    ge1.meta ->> 'secondary_voltage' as secondary_voltage,\n    st_x(m.geometry) as longitude,\n    st_y(m.geometry) as latitude, ggs.grid_element_id as top_feeder_transformer\nFROM meters m\nINNER JOIN grid_element ge1\n    ON ge1.grid_element_id = m.meta ->> 'parent_transformer_id'\nJOIN grid_get_sources('{grid_id}', m.grid_element_id, 'true') ggs\n    ON true\nWHERE ggs.type = 'Transformer'\n    AND ggs.is_producer = true\nORDER BY top_feeder_transformer, LENGTH(m.phases), m.phases,\n        cast(substring(m.grid_element_id, 3, 2) as int) asc;\n")


# Calculate meters' Master Circuit Breaker values

# In[ ]:


# Convert the results to a data frame.
df_meters = result_meters.DataFrame()

# Change the variables' type.
df_meters = df_meters.astype(
    {"phases": "str", "maximal_demand": "int", "secondary_voltage": "int"}
)

# Calculate the master circuit breaker values based on the meters' phase.
df_meters.loc[df_meters["phases"].isin(["A", "B", "C"]), "master_circuit_kW"] = (
    (df_meters["secondary_voltage"] / math.sqrt(3))
    * df_meters["maximal_demand"]
    * 0.98
    / 1000
)

df_meters.loc[df_meters["phases"].isin(["ABC"]), "master_circuit_kW"] = (
    math.sqrt(3)
    * df_meters["secondary_voltage"]
    * df_meters["maximal_demand"]
    * 0.98
    / 1000
)

# Set up a multi-index data frame to display the transformers, their associated meters,
# and master circuit breaker values.
df_meters = df_meters.set_index(["top_feeder_transformer", "phases", "meter_id"])

# Display the results.
df_meters


# In[ ]:


# Plot the master circuit breaker value for each meter.
bar_plot(
    df_meters.reset_index(),
    "meter_id",
    "master_circuit_kW",
    title="Master Circuit Breaker Values for Meters in the {} grid by Top Feeder Transformer".format(
        grid_id
    ),
    labels={
        "meter_id": "Meter ID",
        "master_circuit_kW": "Master Circuit Breaker Value (kW)",
    },
)


# #### Meters' Hourly Time Series
# Fetch all meters in the grid and their hourly net consumer load time series. 

# In[ ]:


get_ipython().run_cell_magic('sql', 'result_system <<', '\nSELECT tdss.timestamp at time zone \'America/Vancouver\' as timestamp,\n        ge.grid_element_id as meter_id,\n        tdss.value as "kWh",\n        geds.type\nFROM grid_element ge\nJOIN grid_element_data_source geds\n    ON geds.grid_id = ge.grid_id\n    AND geds.grid_element_id = ge.grid_element_id\nJOIN ts_data_source_select(geds.grid_element_data_source_id, \'kWh\') tdss\n    ON TRUE\nWHERE geds.grid_id = \'{grid_id}\'\n    AND ge.type = \'Meter\'\n    AND geds.type = \'CONSUMER\'\nORDER BY tdss.timestamp;\n')


# Calculate the ratio of meters' hourly maximum net consumer load to their Master Circuit Breaker values

# In[ ]:


# Convert the SQL result to a Python dataframe.
df_system = result_system.DataFrame()

# Aggregate loads per meter and find the maximum hourly net consumer loads, and rename the kWh column.
df_system_agg = (
    df_system.groupby(["meter_id"])["kWh"]
    .max()
    .reset_index()
    .rename(columns={"kWh": "max_kWh"})
)


# In[ ]:


# Merge the df_meters data frame with the df_system_agg data frame.
df_meters_con = df_meters.reset_index().merge(df_system_agg, on="meter_id")

# Calculate the ratio of maximum hourly net consumer loads to the master circuit breaker values.
df_meters_con["ratio"] = df_meters_con["max_kWh"] / df_meters_con["master_circuit_kW"]

# Set the decimal places.
df_meters_con["ratio"] = df_meters_con["ratio"].round(2)

# Add a column with 1 for ratio>=1 and 0 for ratio<1.
df_meters_con.loc[df_meters_con["ratio"] >= 1, "ratio_bool"] = 1
df_meters_con.loc[df_meters_con["ratio"] < 1, "ratio_bool"] = 0

# Sort the data frame based on the ratio.
df_meters_con = df_meters_con.sort_values(by="ratio", ascending=False)


# In[ ]:


# Plot the ratio of the maximum loads to the master circuit breaker values for each meter.
bar_plot(
    df_meters_con,
    "meter_id",
    "ratio",
    "Ratio of Maximum Hourly Net Consumer Load to Master Circuit Breaker Value",
    {
        "meter_id": "Meter ID",
        "ratio": "Ratio <br> (max net consumer load/circuit breaker value)",
    },
    average=True,
)


# The plot above shows the ratio of maximum hourly net consumer loads to master circuit breaker values. A low ratio indicates oversized master circuit breaker values.  A ratio greater or equal to 1 is depicted by a different color indicating that the net consumer load was higher than the meter's master circuit breaker value at some point in time. 
# 
# Utility companies can use this analysis to redistribute resources from meters with a low ratio to other grid sections and identify meters with a ratio greater than 1. Overloading the meter above the designated master circuit breaker value can result in a meter shutdown or a substantial price increase for the customer.

# #### Map View

# In[ ]:


# Plot the meters and their ratio on a map.
map_it(
    df_meters_con,
    title="Map View of Meters' Ratio of Maximum Net Consumer Load to Master Circuit Breaker Value",
)


# The color of the dots in the map above describes the magnitude of the ratio of maximum hourly net consumer loads to master circuit breaker values. Darker blue dots represent meters with oversized master circuit breaker values, while red dots (if they exist) represent meters with a ratio >= 1. Utilities can use this information to adjust the master circuit breaker values, increase grid efficiency, and reduce customer costs.

# ---
