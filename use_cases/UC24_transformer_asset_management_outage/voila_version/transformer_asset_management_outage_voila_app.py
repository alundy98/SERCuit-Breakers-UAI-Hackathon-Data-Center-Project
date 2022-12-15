#!/usr/bin/env python
# coding: utf-8

# In[13]:


get_ipython().run_cell_magic('html', '', '\n<!-- Nice layouts (sizing, spacing, positioning) are easy to create via the widget layout attribute.\nBut to add colors, box shadow, and change font size, use plain CSS that is added to widgets with the add_class() method. -->\n\n<style>\n.header {\n    justify-content: space-between;\n    align-items: center;\n    position: absolute;\n    top: 0;\n    left: 0;\n    width: 100%;\n    padding: 15px;\n    border-bottom: 1px solid #e2e8f0;\n}\n.instruction {\n    margin-bottom: 10px;\n    font-size: 1rem;\n    line-height: 1.4;\n}\n.instruction_container {\n    background-color: #e0f2fe;\n    padding: 10px;\n    max-width: 1150px;\n    margin: 40px auto 20px;\n}\n.card {\n    margin: 20px auto;\n    box-shadow: 0 2px 6px rgba(0,0,0,0.15);\n    width: 100%;\n    max-width: 1150px;\n}\n.card-content {\n    min-height: 525px;\n}\n</style>')


# In[14]:


import ipywidgets as widgets
from ipywidgets import Box, Button, HBox, HTML, Layout, Output, Password, Text, VBox
from IPython.display import display
import psycopg2
import pandas.io.sql as psql
import pandas as pd
import numpy as np
import plotly.express as px
import geopandas as gpd
from shapely import wkb
import folium


# In[15]:


# General factory functions and variables used throughout the notebook

def get_label_html(label):
    return f'<p style="margin-bottom:-5px;">{label}</p>'

def get_text_input(value, placeholder='', width='175px'):
    # The layout attribute supports basic CSS layout properties, as well as Flexbox and Grid properties
    return Text(value=value, disabled=False, placeholder=placeholder, layout=Layout(width=width))

def get_password_input(value, placeholder='', width='175px'):
    return Password(value=value, disabled=False, placeholder=placeholder, layout=Layout(width=width))

def get_input_item(label, input):
    # VBox is a layout widget, it displays widgets vertically using Flexbox
    return VBox([HTML(value=get_label_html(label)), input])

def get_plot_title(title, age=''):
    age_title =  f' - Transformers older than {age} years'
    value = f'<h3>{title}{age_title if age else ""}</h3>'
    plot_title = HTML(value=value, layout=Layout(padding='0 15px 0 15px'))
    return plot_title

empty_box = Box(
    [HTML(
        value='<p>No data to display. Make sure you are connected to EDM, then click the "Load Transformers" button above.</p>'
    )],
    layout=Layout(align_items='center', justify_content='center', height='450px', padding='15px')
)


# In[16]:


# Page header (title and login area) display

# Text content is added with the HTML widget
# The HTML style attribute is another way of styling output with CSS
page_title = HTML(value='<h1 style="font-size:1.25rem; font-weight:500; color:#1e3a8a;">Transformer Asset Management</h1>')


# Create output areas for async content
# This is where the connection status will be displayed
edm_login_out = Output(layout=Layout(width='140px', height='25px', padding='0 0 0 10px'))
# This is where a potential connection error will be shown
edm_err_out = Output()

# Create the input fields, using the factory functions above
# We use the Password widget here, to keep login details hidden
edm_server_input = get_password_input('')
edm_server_item = get_input_item('EDM server address', edm_server_input)

edm_username_input = get_password_input('')
edm_username_item = get_input_item('Username', edm_username_input)

edm_password_input = get_password_input('')
edm_password_item = get_input_item('Password', edm_password_input)

edm_login_button = Button(
    button_style='primary', # Built-in, predefined button style
    description='Connect', # Button label
    disabled=False,
    tooltip='Click to Connect',
    icon='database',  # FontAwesome names without the `fa-` prefix
    layout=Layout(margin='0 0 0 10px')
)

# HBox is a layout widget, it displays widgets horizontally using Flexbox
login_box = HBox([
    edm_server_item,
    edm_username_item,
    edm_password_item,
    VBox([edm_login_out, edm_login_button])
])

# N.B. Running this code in the notebook will display the header as overlapping with the next cell, due to its absolute positioning.
header = HBox([page_title, login_box])
header.add_class('header') # Add CSS class name to widget, as defined in the style tag at the top
display(header, edm_err_out)


# In[17]:


# Connection info display

conn_instr = HTML(
    value='<div style="padding-top:50px;">Before you can load any data, you need to connect to EDM above to the right. Enter the EDM server address and the login credentials provided by Awesense. If you do not have the credentials, or have any trouble connecting, please contact api@awesense.com.</div>',
    layout=Layout(margin='50px auto 50px auto', max_width='1150px')
)
conn_instr.add_class('instruction')

display(conn_instr)


# In[18]:


# Grid element input display

load_instr = HTML(value='<p>Enter the grid ID and the grid element ID of an element to view age specific data of transformers downstream of that element.</p>')
load_instr.add_class('instruction')

# Create the input fields, using the factory functions above
grid_id_input = get_text_input('awefice')
grid_id_item = get_input_item('Grid ID', grid_id_input)

element_id_input = get_text_input('line_segment_5')
element_id_item = get_input_item('Grid Element ID', element_id_input)

load_button = Button(
    description='Load Transformers',
    disabled=True, # Will be enabled on EDM login
    button_style='primary',
    layout=Layout(margin='28.5px 0 0 10px')
)

load_container = VBox([
    load_instr,
    HBox([grid_id_item, element_id_item, load_button])
])
load_container.add_class('instruction_container')

display(load_container)


# In[19]:


# Transformer age distribution display

# Create an output area for async content
age_distr_wait_out = Output()
age_distr_wait_out.add_class('card-content')

with age_distr_wait_out: # Direct output to an Output widget (output area) using a context manager
    age_distr_wait_out.clear_output()
    display(empty_box) # Display the widget with the "empty message" while waiting for the data to be loaded

age_container = VBox([
    get_plot_title('Transformer Age Distribution'),
    age_distr_wait_out
])
age_container.add_class('card')

display(age_container)


# In[20]:


# Filter input display

filter_instr = HTML(value='<p>Filter location and rating by transformer minimum age.</p>')
filter_instr.add_class('instruction')

# Create the input field
age_input = get_text_input('0')
age_item = get_input_item('Minimum Age (years)', age_input)

filter_button = Button(
    description='Filter',
    disabled=True, # Will be enabled when transformers are loaded
    button_style='primary',
    layout=Layout(margin='28.5px 0 0 10px')
)

filter_container = VBox([
    filter_instr,
    HBox([age_item, filter_button])
])
filter_container.add_class('instruction_container')

display(filter_container)


# In[21]:


# Map display

# Create an output area for async content
map_wait_out = Output()
map_wait_out.add_class('card-content')

with map_wait_out: # Direct output to an Output widget (output area) using a context manager
    map_wait_out.clear_output()
    display(empty_box) # Display the widget with the "empty message" while waiting for the data to be loaded

map_container = VBox([
    get_plot_title('Transformer Location'),
    map_wait_out
])
map_container.add_class('card')

display(map_container)


# In[22]:


# Transformer rating display

# Create an output area for async content
rating_wait_out = Output()
rating_wait_out.add_class('card-content')

with rating_wait_out: # Direct output to an Output widget (output area) using a context manager
    rating_wait_out.clear_output()
    display(empty_box) # Display the widget with the "empty message" while waiting for the data to be loaded

# rating_title = get_title_box('Transformer Rating'),
rating_container = VBox([
    get_plot_title('Transformer Rating'),
    rating_wait_out
])
rating_container.add_class('card')

display(rating_container)


# In[23]:


# Declare and attach EDM connection functionality to "Connect" button

success_msg = HTML(value='<span style="color:#166534;">&check; Connected</span>')

def connect_to_edm(b): # Click handlers accept the button instance as an argument, here we don't use it
    global connection
    
    # Direct output to an Output widget (output area) using a context manager
    with edm_login_out:
        edm_login_out.clear_output()
        display(HTML(value='Connecting...'))
        
        try:
            # Use values entered by user in input fields above
            conn_data = "host={} dbname=edm user={} password='{}'".format(
                edm_server_input.value,
                edm_username_input.value,
                edm_password_input.value
            )
            connection = psycopg2.connect(conn_data)
            if connection.status == 1 or connection.status == 0:
                edm_login_out.clear_output()
                display(success_msg)
                load_button.disabled = False # Enable "Load Transformers" button now when EDM is connected
        except Exception as err:
            edm_login_out.clear_output()
            display(HTML(value='<span style="color:#dc2626;">&cross; NOT connected</span>'))
            
            # Widgets attributes can be updated after creation
            edm_err_out.layout.margin = '100px auto 50px auto'
            edm_err_out.layout.max_width = '1150px'
            # Output can also be appended to an output area directly, without using a context manager
            err_msg = str(err).split('\n')[0] # Err message is repeated
            edm_err_out.append_stderr(f'Connection error: {err_msg}')
        
        # Reset the login input fields for security purposes
        edm_server_input.value = ''
        edm_username_input.value = ''
        edm_password_input.value = ''

edm_login_button.on_click(connect_to_edm) # Add EDM connection function as button click handler


# Hook-up data loading to "Load Transformers" and "Filter" buttons 

def load_transformers():
    global df
    
    with age_distr_wait_out:
        
        df = psql.read_sql_query("""
        SELECT * FROM grid_get_downstream('{}','{}') WHERE type LIKE 'Transformer'
        """.format(grid_id_input.value, element_id_input.value), connection)

        df = pd.concat([df.drop(['meta'], axis=1), df['meta'].apply(pd.Series)], axis=1)
        df['geometry'] = df['geometry'].apply(lambda x: wkb.loads(x, hex=True))

        df['commission_date'] = pd.to_datetime(df['commission_date'], format='%Y-%m-%d')
        
        df['age'] = (pd.to_datetime('now') - pd.to_datetime(df['commission_date'])) / np.timedelta64(1, 'Y')
        
        fig = px.histogram(df['age'], x="age")

        fig.update_layout(
            xaxis_title_text='Age (years)',
            yaxis_title_text='Count',
            bargap=0.2,
        )
        
        age_distr_wait_out.clear_output() # Clear the current content (the "empty message") from the output area
        display(fig) # Update the output area with the plot
        
        filter_button.disabled = False # Enable "Filter" button when data has been loaded

def load_map():
    global df2
    
    with map_wait_out:
        global df2
        
        filt = (df['age'] >= int(age_input.value)) # The minimum age to filter by, entered by user
        df2 = df[filt]

        gdf = gpd.GeoDataFrame(df2, crs="EPSG:4326", geometry=df['geometry'])

        map_latitude = gdf['geometry'].y.mean()
        map_longitude = gdf['geometry'].x.mean()

        m = folium.Map(location=[map_latitude, map_longitude], zoom_start=15)
          
        for _, row in df2.iterrows():
            folium.Marker(
                location=[row['geometry'].y,row['geometry'].x],
                popup=row['grid_element_id'],
                tooltip=round(row['age'], 1)
            ).add_to(m)
        
        map_wait_out.clear_output() # Clear the current content (the "empty message") from the output area
        
        # A Box widget can have its content (children) added/updated after creation
        # Here we use it to update the title
        map_container.children = [get_plot_title('Transformer Location', age_input.value), map_wait_out]
        
        display(m) # Update the output area with the map

def load_rating():
    with rating_wait_out:
        
        fig2 = px.histogram(df2['rating_kva'], x="rating_kva", nbins=10)

        fig2.update_layout(
            xaxis_title_text='Rating (kVA)',
            yaxis_title_text='Count',
            bargap=0.2,
        )
        
        rating_wait_out.clear_output() # Clear the current content (the "empty message") from the output area
        
        # A Box widget can have its content (children) added/updated after creation
        # Here we use it to update the title
        rating_container.children = [get_plot_title('Transformer Rating', age_input.value), rating_wait_out]
        
        display(fig2) # Update the output area with the plot

        
def load_all(b): # Click handler accepts the button instance as an argument, here we don't use it
    load_transformers()
    load_map()
    load_rating()

load_button.on_click(load_all) # Attach click handler to load button


def filter_by_min_age(b): # Click handler accepts the button instance as an argument, here we don't use it
    load_map()
    load_rating()

filter_button.on_click(filter_by_min_age) # Attach click handler to filter button

