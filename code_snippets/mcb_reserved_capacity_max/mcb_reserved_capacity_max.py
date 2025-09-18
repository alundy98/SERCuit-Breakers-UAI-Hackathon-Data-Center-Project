#!/usr/bin/env python
# coding: utf-8

# ## Overview

# The process includes:
# * Retrieving all downstream grid elements via grid_get_downstream
# * Extracting maximal_demand values from metadata
# * Loading time series data for all traced elements and calculating the average voltage for each element for the past year (if available)
# * Converting amps to kW using the formula: 𝑃 = 𝐼 ⋅ 𝑉 ⋅ SQRT(3) ⋅ PF / 1000
# * Returning both per-element detail and transformer-level summary,  including % of maximum possible transformer load
# 
# Such a calculation supports transformer capacity analysis and planning across multiple assets.

# In[ ]:


| Column name                  | Description                                          |
| ---------------------------- | ---------------------------------------------------- |
| `transformer_id`             | Transformer identifier                               |
| `element_count`              | Number of downstream elements with valid demand data |
| `total_reserved_capacity_kw` | Sum of calculated reserved power (kW)                |
| `avg_voltage_used`           | Average voltage over last year per element           |
| `rating_kva`                 | Nameplate capacity of transformer (kVA)              |
| `rating_kw`                  | Converted rated capacity in kW (PF = 0.95)           |
| `reserved_capacity_pct`      | Load percentage of transformer                       |


# ---

# ## Set up

# In[1]:


import getpass
import urllib.parse
import pandas as pd
from IPython import get_ipython
from decimal import Decimal, InvalidOperation


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
del edm_name, edm_password


# ---

# ## Maximal reserved capacity for a Specific or Multiple Transformers

# In[3]:


# User input
print(
    "\n The calculation only works for awefice grid (maximal_demand values are specified here)."
)

ip = get_ipython()

grid_id = input("Enter grid ID (e.g. 'awefice'): ")
raw_ids = input(
    "Enter transformer grid_element_id (e.g. 'transformer_2' or 'transformer_2 transformer_6'): "
)
transformer_ids = raw_ids.strip().split()

if not grid_id or not transformer_ids:
    raise ValueError(" You must enter a grid_id and at least one transformer_id.")

# Check if grid exists
check_grid_query = f"SELECT COUNT(*) FROM grid WHERE grid_id = '{grid_id}';"
grid_check = ip.run_line_magic("sql", check_grid_query)
if grid_check.DataFrame().iloc[0, 0] == 0:
    raise ValueError(f" Grid ID '{grid_id}' does not exist.")

# Check if all transformer_ids exist
values_str = ", ".join(f"'{tid}'" for tid in transformer_ids)
check_transformers_query = f"""
SELECT grid_element_id
FROM grid_element
WHERE grid_id = '{grid_id}' AND grid_element_id IN ({values_str});
"""
transformer_check = ip.run_line_magic("sql", check_transformers_query)

result_list = [row[0] for row in transformer_check]

if set(result_list) == set(transformer_ids):
    print(f" All transformer_id(s) exist in the grid '{grid_id}'")
else:
    missing_ids = list(set(transformer_ids) - set(result_list))
    raise ValueError(
        f" The following transformer_id(s) do not exist in grid '{grid_id}': {', '.join(missing_ids)}"
    )

power_factor = 0.95


# In[4]:


# Output containers
all_elements = []
all_summaries = []

# SQL template
for transformer_id in transformer_ids:
    print(f" Processing: {transformer_id}")
    try:
        # Main SQL query: calculate reserved capacity
        sql_query = f"""
        WITH demand_elements AS (
            SELECT
                grid_element_id,
                CAST(meta ->> 'maximal_demand' AS double precision) AS amps
            FROM grid_get_downstream('{grid_id}', '{transformer_id}', false)
            WHERE meta ? 'maximal_demand'
              AND (meta ->> 'maximal_demand') ~ '^\\d+(\\.\\d+)?$'
        ),
        voltage_sources AS (
            SELECT
                grid_element_id,
                grid_element_data_source_id
            FROM grid_element_data_source
            WHERE metrics @> ARRAY['V']
              AND grid_element_id IN (SELECT grid_element_id FROM demand_elements)
        ),
        voltage_values AS (
            SELECT
                grid_element_id,
                AVG(v.value) AS avg_voltage
            FROM voltage_sources
            JOIN ts_data_source_select(grid_element_data_source_id, 'V',
                 tstzrange((NOW() - INTERVAL '1 year'), NOW(), '[]')) v
            ON TRUE
            GROUP BY grid_element_id
        ),
        combined AS (
            SELECT
                demand_elements.grid_element_id,
                demand_elements.amps,
                voltage_values.avg_voltage,
                demand_elements.amps * voltage_values.avg_voltage * 1.73205 * 0.95 / 1000 AS demand_kw
            FROM demand_elements
            JOIN voltage_values USING (grid_element_id)
        )
        SELECT
            grid_element_id,
            ROUND(amps::numeric, 2) AS amps,
            ROUND(avg_voltage::numeric, 1) AS avg_voltage_used,
            ROUND(demand_kw::numeric, 2) AS calculated_demand_kw
        FROM combined
        ORDER BY grid_element_id;
        """
        result = ip.run_cell_magic("sql", "", sql_query)
        df = result.DataFrame()

        df['transformer_id'] = transformer_id

        # Fetch transformer's rating_kva from metadata
        kva_query = f"""
        SELECT meta ->> 'rating_kva' AS kva_str
        FROM grid_element
        WHERE grid_element_id = '{transformer_id}';
        """
        kva_result = ip.run_cell_magic("sql", "", kva_query)
        kva_str = kva_result.DataFrame()["kva_str"].iloc[0]

        # Result processing
        if not df.empty:
            all_elements.append(df)

            count = len(df)
            avg_voltage = round(df['avg_voltage_used'].mean(), 1)

            # Convert reserved demand to float
            try:
                total_kw = float(df["calculated_demand_kw"].sum())
            except (TypeError, ValueError, InvalidOperation):
                total_kw = None

            # Convert rating_kva to float
            try:
                rating_kva = float(kva_str)
            except (TypeError, ValueError, InvalidOperation):
                rating_kva = None

            # Compute load %
            if rating_kva and total_kw and rating_kva > 0:
                rating_kw = rating_kva * power_factor
                load_pct = round((total_kw / rating_kw) * 100, 1)
            else:
                rating_kw = None
                load_pct = None

            # Build summary row
            df_summary = pd.DataFrame([{
                'transformer_id': transformer_id,
                'element_count': count,
                'total_reserved_capacity_kw': round(total_kw, 2) if total_kw else None,
                'avg_voltage_used': avg_voltage,
                'rating_kva': rating_kva,
                'rating_kw': round(rating_kw, 2) if rating_kw else None,
                'reserved_capacity_pct': load_pct
            }])
            all_summaries.append(df_summary)
        else:
            print(f"No valid demand data found for {transformer_id}")
    except Exception as e:
        print(f"Error for {transformer_id}: {e}")

# Final outputs
if all_elements:
    df_all_elements = pd.concat(all_elements, ignore_index=True)
    print("Per-element reserved capacity:")
    display(df_all_elements)

if all_summaries:
    df_summary_total = pd.concat(all_summaries, ignore_index=True)
    print("\n Transformer summary:")
    display(df_summary_total)
else:
    print("No transformer summary generated.")

