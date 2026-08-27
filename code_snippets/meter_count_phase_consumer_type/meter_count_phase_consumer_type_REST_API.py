#!/usr/bin/env python
# coding: utf-8

# ## Overview

# This notebook is intended to:
# 
# * Display the number of meters per phase in a given grid.
# * Display the number of meters per consumer type in a given grid.
# * Display the number of meters per phase and consumer type combination in a given grid. 
# 
# This snippet notebook is designed as follows:
# 
# * Define the grid of interest.
# * Call the REST API endpoint to get all the meters and their attributes in the grid of interest. 
# * Aggregate the results by phase. Display and plot the outcome. 
# * Aggregate the results by consumer type. Display and plot the outcome. 
# * Aggregate the results by phase and consumer type. Display and plot the outcome. 
# 
# The insights derived from these results may be helpful in multiple use cases ranging from Data Quality Improvement to Grid Planning or Customer Analytics. 
# 
# It is assumed that the user has been given credentials for accessing the Awesense Sandbox. Otherwise, please contact us at [api@awesense.com](api@awesense.com).
# 
# You can find more information about how to use Awesense's REST API in the [access_and_basic_data_retrieval](https://github.com/Awesense/edm-app-examples/blob/master/intro_and_tutorials/rest_api/access_and_basic_data_retrieval.ipynb) notebook.

# ---

# ## Setup

# In[ ]:


import getpass
from dotenv import load_dotenv
import os
from contextlib import suppress
from io import StringIO
import pandas as pd
import plotly.express as px
import requests

pd.set_option("display.max_columns", None)


# ### Connection
# 
# Enter the login credentials provided by Awesense. If you do not have the credentials or have any trouble connecting, please contact api@awesense.com.
# <span style='color:red'> **Please do NOT store the credentials in the notebook, nor share them with anyone.** </span>

# In[ ]:


# Checks for Google Colab: If detected,
# it bypasses reading credentials from local files and securely draws from Colab Secrets.
try:
    from google.colab import userdata
    from google.colab.userdata import SecretNotFoundError

    IN_COLAB = True
except ImportError:
    IN_COLAB = False

if IN_COLAB:
    print("☁️ Running in Google Colab. Using Colab Secrets for REST API connection")
    # Look for Colab Secrets, fall back to interactive prompts if missing
    with suppress(SecretNotFoundError):
        os.environ["EDM_HOST"] = userdata.get("EDM_HOST")
    with suppress(SecretNotFoundError):
        os.environ["EDM_USER"] = userdata.get("EDM_USER")
    with suppress(SecretNotFoundError):
        os.environ["EDM_PASSWORD"] = userdata.get("EDM_PASSWORD")
else:
    # If running locally, loads connection parameters from a `.env` file (in this directory or parent directories)
    load_dotenv()

# Prompt the user to manually enter any missing connection parameters not found in `.env` file / Google Colab Secrets
if "EDM_HOST" not in os.environ:
    os.environ["EDM_HOST"] = input("EDM server address: ").strip()
if "EDM_USER" not in os.environ:
    os.environ["EDM_USER"] = input("EDM username: ")
if "EDM_PASSWORD" not in os.environ:
    os.environ["EDM_PASSWORD"] = getpass.getpass("EDM password: ")

# Ensure that the server address does not specify plaintext HTTP, then build the HTTPS server origin.
server_hostname = os.environ["EDM_HOST"]
assert (
    server_hostname.startswith("http://") == False
), "Server address uses https:// for secure communications, not http://"
if server_hostname.startswith("https://"):
    server_origin = server_hostname
else:
    server_origin = f"https://{server_hostname}"

# This auth tuple can be passed to `requests` functions as the `auth` argument.
# (An HTTP Basic Authentication header will be generated and used automatically)
auth = (os.environ["EDM_USER"], os.environ["EDM_PASSWORD"])
response = requests.get(f"{server_origin}/api/v1/grid", auth=auth, timeout=30)
response.raise_for_status()
print("✅ REST API connection parameters/credentials verified")


# ---

# ## Meter Count per Phase and Consumer Type

# #### Input Parameters
# Enter the grid ID of interest.

# In[ ]:


# PAPERMILL PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')


# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice


# #### Retrieve all Meter Information from the Endpoint

# In[ ]:


# Get all the `grid_elements` of type `Meter`.
grid_element_type = "Meter"

params = {
    "limit": "30000",
    "offset": "0",
    "ordering": '{"direction": "ASC", "query_name": "grid_id"}',
    "filter": f'{{"operator": "=", "column_name": "grid_id", "value": "{grid_id}"}}',
    "export": "false",
    "grid_element_type": grid_element_type,
}

response = requests.get(
    f"{server_origin}/api/v1/grid_explorer", auth=auth, params=params
)
grid_elements = pd.json_normalize(response.json())
grid_elements


# #### Meter Count by Phase

# In[ ]:


# Unpack the data frame, group the results by phases, count the number of elements in each phase and display the results.
df_meters_by_phases = (
    pd.json_normalize(grid_elements.to_dict("records"), record_path="results")
    .groupby(by="phases")
    .count()
    .reset_index()[["phases", "id"]]
    .rename(columns={"id": "number_of_meters"})
)
df_meters_by_phases


# In[ ]:


# Plot the number of meters per phase.
fig = px.pie(
    df_meters_by_phases,
    values="number_of_meters",
    names="phases",
    title="<b>Breakdown of Meters by Phase<b>",
    labels={"phase": "Meter Phase", "number_of_meters": "Meter Count"},
)

# Show meter phase, meter count and percentage inside the pie chart.
fig.update_traces(
    texttemplate="Meter Phase: %{label} <br> Count: %{value:} <br>(%{percent})"
)
fig.update_layout(legend_title_text="Phases")

fig.show()


# #### Meter Count by Consumer Type

# In[ ]:


# Unpack the data frame, group the results by consumer types, count the number of elements in each type and display the results.
df_meters_by_consumer_types = (
    pd.json_normalize(grid_elements.to_dict("records"), record_path="results")
    .groupby(by="type_of_consumer")
    .count()
    .reset_index()[["type_of_consumer", "id"]]
    .rename(columns={"id": "number_of_meters", "type_of_consumer": "consumer_type"})
)
df_meters_by_consumer_types


# In[ ]:


# Plot the number of meters per consumer type.
fig = px.pie(
    df_meters_by_consumer_types,
    values="number_of_meters",
    names="consumer_type",
    title="<b>Breakdown of Meters by Consumer Type<b>",
    color_discrete_sequence=px.colors.qualitative.G10,
    labels={"consumer_type": "Consumer Type", "number_of_meters": "Meter Count"},
)

# Show consumer type, meter count and percentage inside the pie chart.
fig.update_traces(
    texttemplate="Type: %{label} <br> Count: %{value:} <br>(%{percent})", rotation=90
)
fig.update_layout(legend_title_text="Consumer Types")

fig.show()


# #### Meter Count by both Phase and Consumer Type

# In[ ]:


# Unpack the data frame, group the results by phases and consumer type, count the number of elements in each category and display the results.
df_meters = (
    pd.json_normalize(grid_elements.to_dict("records"), record_path="results")
    .groupby(by=["phases", "type_of_consumer"])
    .count()
    .reset_index()[["phases", "type_of_consumer", "id"]]
    .rename(columns={"id": "number_of_meters", "type_of_consumer": "consumer_type"})
    .sort_values(by="phases", key=lambda x: x.str.len(), ascending=False)
)
df_meters


# In[ ]:


# Plot the breakdown of meters per phase and per consumer type.
fig = px.bar(
    df_meters,
    x="number_of_meters",
    y="consumer_type",
    color="phases",
    orientation="h",
    title="<b>Meters Count by Consumer Type and Phase<b>",
    labels={
        "consumer_type": "Consumer Type",
        "number_of_meters": "Number of Meters",
        "phases": "Meter Phase",
    },
)
fig.show()


# ---
