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

# In[ ]:


import getpass
import os
from dotenv import load_dotenv
import psycopg2
import pandas as pd
from shapely import wkb
import plotly.express as px

pd.set_option("display.max_columns", None)


# **Connection**
# 
# Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.
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
    from contextlib import suppress
    print('☁️ Running in Google Colab. Using Colab Secrets for SQLAPI connection')
    # Look for Colab Secrets, fall back to interactive prompts if missing
    with suppress(SecretNotFoundError): os.environ['EDM_HOST'] = userdata.get('EDM_HOST')
    with suppress(SecretNotFoundError): os.environ['EDM_USER'] = userdata.get('EDM_USER')
    with suppress(SecretNotFoundError): os.environ['EDM_PASSWORD'] = userdata.get('EDM_PASSWORD')
else:
    # If running locally, loads connection parameters from a `.env` file (in this directory or parent directories)
    load_dotenv()

# Prompt the user to manually enter any missing connection parameters not found in `.env` file / Google Colab Secrets
if 'EDM_HOST' not in os.environ: os.environ['EDM_HOST'] = input('EDM server address: ').strip()
if 'EDM_USER' not in os.environ: os.environ['EDM_USER'] = input('EDM username: ')

# psycopg2/libpq automatically read the PG* environment variables, so map the shared EDM_* names onto them.
# (The password is only mapped if provided via `.env`/Colab; otherwise libpq falls back to `~/.pgpass`.)
os.environ['PGHOST'] = os.environ['EDM_HOST']
os.environ['PGUSER'] = os.environ['EDM_USER']
os.environ.setdefault('PGDATABASE', 'edm')
if 'EDM_PASSWORD' in os.environ: os.environ['PGPASSWORD'] = os.environ['EDM_PASSWORD']

print('Verifying SQLAPI connection parameters/credentials with connection attempt')

try:
    # Test database connection
    conn = psycopg2.connect('')
    conn.close()
    print('✅ SQLAPI connection parameters/credentials verified')
except psycopg2.OperationalError:
    print('⚠️ SQLAPI credentials check failed (assume missing/incorrect password, but double-check `.env` / secrets!)')
    # Prompt user securely for password
    os.environ['PGPASSWORD'] = getpass.getpass('Enter EDM Password manually: ')

# Keeps `%sql` result tables rendering correctly on newer versions of prettytable
import prettytable
if 'DEFAULT' not in vars(prettytable): prettytable.DEFAULT = prettytable.TableStyle.DEFAULT

# Load the SQL extension and connect
get_ipython().run_line_magic('reload_ext', 'sql')
get_ipython().run_line_magic('sql', 'postgresql://')


# **Custom Functions**

# In[ ]:


def query_to_df(result):
    """
    Transform sql query result to dataframe, and
    expand out `meta` column into separate columns.
    """

    # Turn a query output into a Python dataframe.
    df = result.DataFrame()

    # Separate the `meta` column currently saved as JSONB into individual columns.
    # Replace NaN values to empty string for ease of spotting non-NaN values.
    df = pd.concat(
        [df.drop(["meta"], axis=1), df["meta"].apply(pd.Series).fillna("")], axis=1
    )

    # Transform the hex string form of geometry to coordinates.
    df["geometry"] = df["geometry"].apply(lambda x: wkb.loads(x, hex=True))

    return df


def plot_pie(df, interest_colname, title):
    """
    Compute counts of each value in `interest_colname` column,
    plot a pie chart of the breakdown, and
    return the counts dataframe.
    """

    # Create a dataframe of counts of each value.
    df_cts = (
        df[interest_colname]
        .value_counts()
        .to_frame(name="count")
        .rename_axis(interest_colname)
        .reset_index()
    )

    # Create a pie plot.
    fig = px.pie(df_cts, names=interest_colname, values="count", title=title)

    # Show the count values, percentages and labels for the pie plot.
    fig.update_traces(textposition="inside", textinfo="percent+label+value")
    fig.show()

    # Return the count dataframe sorted alphabetically by in the input column.
    return df_cts.sort_values(interest_colname)


def phase_table(df):
    """
    Return a table of phase breakdowns by grid element type.
    """

    # Create a dataframe of counts by both types and phases.
    df_counts = df[["type", "phases"]].value_counts().to_frame().reset_index()

    # Rename the first column as 'count'.
    df_counts = df_counts.rename(columns={0: "count"})

    # Pivot the dataframe to have phases as columns.
    df_phases = df_counts.pivot_table(index="type", columns="phases", fill_value=0)

    # Return the count dataframe sorted alphabetically by `type`.
    return df_phases.sort_values("type")


# ---

# ## Example

# ### Input Parameters

# In[ ]:


# PARAMETERS (Cell Tag: parameters)
# Do not split this cell structure. Papermill injects automated
# runtime overrides immediately below this block.
# If you don't want to be asked for input for these parameters, you can set them here.
grid_id = None  # Target grid identifier (e.g., 'awefice')
element_type = None  # Element type (e.g., 'Transformer')
grid_element_id = None  # Transformer for tracing (e.g., 'transformer_6')
trace_option = None  # Tracing option (e.g., 'Down')
meter_id = None  # Meter ID for tracing parent transformer (e.g., 'm_7')


# We'll take a look at the `awefice` grid. In the database, this property is referred to as a `grid_id`.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_id = grid_id or input("Enter grid ID: ").strip()  # awefice


# Check when this grid was last updated. 

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT last_updated\nFROM grid\nWHERE grid_id = :grid_id;\n')


# The below are different element types available in the `awefice` grid.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT DISTINCT type as element_type\nFROM grid_element\nWHERE grid_id = :grid_id\nORDER BY element_type;\n')


# Enter one of the element types from the above output to explore further in the `grid_element` view.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
element_type = (
    element_type or input("Enter the element type of interest: ").strip()
)  # Transformer


# The below are all available grid element ID for the specified grid `type`.

# In[ ]:


get_ipython().run_cell_magic('sql', '', '\nSELECT grid_element_id\nFROM grid_element\nWHERE grid_id = :grid_id\n    AND type = :element_type\nORDER BY grid_element_id;\n')


# Choose one of the grid element ID from the above output to conduct tracing on.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
grid_element_id = (
    grid_element_id or input("Enter the grid element id of interest: ").strip()
)  # transformer_6


# ### Trace

# Choose one of the tracing options: `Source`, `All Sources`, `Down`, `Connected`, and `Same Voltage`.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
trace_option = trace_option or input("Enter the tracing option: ").strip()  # Down


# Call an appropriate SQL query based on the specified tracing option.

# In[ ]:


if trace_option.lower() == 'source':

    # Set the third argument to `true` to retrieve the top feeder.
    result = get_ipython().run_line_magic('sql', "SELECT *              FROM grid_get_sources(:grid_id, :grid_element_id, 'true');")

elif trace_option.lower() == 'all sources':

    # Set the third argument to `false` to retrieve all feeders.
    result = get_ipython().run_line_magic('sql', "SELECT *              FROM grid_get_sources(:grid_id, :grid_element_id, 'false');")

elif trace_option.lower() == 'down':

    # Set the third argument to `false` to exclude itself from the output.
    result = get_ipython().run_line_magic('sql', "SELECT *              FROM grid_get_downstream(:grid_id, :grid_element_id, 'false');")

elif trace_option.lower() == 'connected':

    result = get_ipython().run_line_magic('sql', 'SELECT *              FROM grid_get_connected(:grid_id, :grid_element_id);')

elif trace_option.lower() == 'same voltage':

    result = get_ipython().run_line_magic('sql', 'SELECT *              FROM grid_get_same_voltage(:grid_id, :grid_element_id);')

else:
    print('Invalid input for tracing option. Please enter one of the following: Source, All Sources, Down, Connected.')


# In[ ]:


# Turn the sql output to a Python dataframe.
df = query_to_df(result)

# Return the first few rows.
df.head()


# The above dataframe can be processed further as needed. For example, TGI only returns a top feeder for the `Source` option whereas the notebook here returns all grid_elements. Applying the `is_producer==True` filter on the `df` will mirror the same functionality as in TGI.

# In[ ]:


# Filter the dataframe to rows with is_producer=True.
df[df["is_producer"] == True]


# For the demonstration of this notebook, we will continue using the full dataframe with unfiltered data.

# In[ ]:


# Create a pie plot for the breakdown of grid element types.
df_types = plot_pie(
    df, "type", "Breakdown of Grid Element Types for " + trace_option.title() + " Trace"
)


# In[ ]:


# Return a table of grid element type counts.
df_types


# In[ ]:


# Create a pie plot for the breakdown of phases.
df_phases = plot_pie(
    df, "phases", "Breakdown of Phases for " + trace_option.title() + " Trace"
)


# In[ ]:


# Return a table of phase counts.
df_phases


# In[ ]:


# Create a table of phase counts by grid element types.
phase_table(df)


# ### Combining Trace Functions

# Combining multiple trace functions provides the flexibility to find an answer to additional questions.
# 
# For example, a grid element's parent transformer can be identified by: 
# 1. Returning a list of transformers in the upstream using `grid_get_sources()`.
# 2. Entering the list of transformers from step 1 to `grid_get_same_voltage()` to get all grid elements with the same voltage as a given transformer.
# 3. Matching the input grid element to the returned grid elements from step 2 and filter to the respective transformer as the final output.

# In[ ]:


# Short-circuit check: If Papermill didn't inject values, prompt the user.
# To skip these prompts, set the parameter values directly in the parameters cell above (PAPERMILL PARAMETERS).
meter_id = meter_id or input("Enter the meter ID of interest: ").strip()  # m_7


# In[ ]:


get_ipython().run_cell_magic('sql', '', "\nSELECT ggs.grid_element_id AS transformer\nFROM grid_get_sources(:grid_id, :meter_id, true) ggs\nLEFT JOIN grid_get_same_voltage(:grid_id, ggs.grid_element_id) ggsv\n    ON true\nWHERE ggs.type = 'Transformer'\n    AND ggsv.grid_element_id = :meter_id;\n")


# ---
