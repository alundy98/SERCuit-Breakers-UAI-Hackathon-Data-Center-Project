# jupyter_notebooks &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](https://github.com/Awesense/edm-app-examples/blob/master/LICENSE)
jupyter_notebooks is a collection of application examples of how to use [Awesense](https://www.awesense.com)'s Energy Data Model 
(EDM), specifically using Jupyter notebooks.  It currently contains examples of tracing connectivity in a circuit, transformer asset management, and time series meter and SCADA data retrieval.


## Format
**Overview** \
Each notebook begins with statements of its intended purpose. 

**Set up** \
This section includes importing required libraries, connecting to the EDM instance, and creating custom functions where applicable. 

**Example** \
This section provides an example of how to work with EDM.


## Applications
Listed below are applications in the [jupyter_notebooks](https://github.com/Awesense/edm-app-examples/tree/master/jupyter_notebooks) folder.
  * [transformer_asset_management_outage](https://github.com/Awesense/edm-app-examples/blob/master/jupyter_notebooks/transformer_asset_management_outage.ipynb) - 
    use case of transformer asset management.
  * [tgi_tracing](https://github.com/Awesense/edm-app-examples/blob/master/jupyter_notebooks/tgi_tracing.ipynb) - 
    programmatic approach to tracing connectivity in a circuit, a feature also available in TGI.
  * [time_series_access](https://github.com/Awesense/edm-app-examples/tree/master/jupyter_notebooks/time_series_access.ipynb) - 
    time series data retrieval for meters and SCADAs.

  

## Getting Started

### Install Dependencies
At the time of writing, it is assumed that Python 3.7 and pip3 are installed for the purposes of using these notebooks.
In order to create a clean virtual environment to isolate dependency installation, the following commands should be executed from a terminal opened in this directory:
```bash
python3 -m venv /your/desired/directory # While this only needs to be run once, repeated executions will not erase the existing libraries
source /your/desired/directory/bin/activate
pip3 install -r requirements.txt
python3 -m ipykernel install --user # Ensures that the Python3 kernel uses the new venv as expected
```

### Usage
The [edm_overview](https://github.com/Awesense/edm-app-examples/tree/master/jupyter_notebooks/edm_overview.ipynb) notebook
contains documentation on how to connect to Awesense's EDM and lists details of available functions and views. This is a 
great place to start to ensure that your credentials are working and to familiarize with the Awesense's EDM platform.

As the notebook is designed to run from top to bottom, one of the easiest way to execute all is to click Cell from 
the top menu ribbon, and click Run All. 
If there are any sections that require user inputs, such as credentials, the rest of the notebook will automatically be 
paused before continuing on after the inputs are entered.

Please do not store the credentials in notebooks, nor share them with anyone else.
