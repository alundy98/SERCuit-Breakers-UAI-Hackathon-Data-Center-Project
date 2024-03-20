# edm-app-examples &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
edm-app-examples is a collection of application examples of how to use [Awesense](https://www.awesense.com)'s Energy Data Model 
(EDM). 

## Folder Organization
edm-app-examples is organized as follows:
* [intro_and_tutorials](intro_and_tutorials) - a
collection of introduction and tutorial materials to get started and learn how to use EDM.
* [code_snippets](code_snippets) - code snippets demonstrating quick analytical insights.
* [use_cases](use_cases) - real implementations of use cases from various areas of the energy domain.
* [utils](utils) - general utility functions that are utillized from notebooks in other folders. 

Each folder contains an individual README.md file with more details specific to that folder. 


## Getting Started
The fastest way to get started with this repo is to make use of the Awesense Sandbox Environment. `Sandbox` is a collection of Awesense servers (for tiers 1, 2&3) where customers, partners and others can use the functionality of the Awesense platform, including APIs (SQL AND REST) and the True Grid Intelligence (TGI) web app, to explore and create applications and solutions for the grid ecosystem using realistic simulated data. Currently, most of the snippets and use cases are implemented using the Awesense SQL API, with only a handful using the REST API, but more to be added.

While highly portable, all the code in this repository can be run out of the box against one of the `Sandbox` servers, and, unless otherwise specified, the notebooks in this repo were run and their outputs saved using input parameters (for example, grid ID, transformer IDs) corresponding to the `Sandbox` tier 1 server dataset. With new releases, more notebooks will run out of the box against the richer datasets present only in the tier 2 & 3 servers (for example, the code snippet meter_ders_matrix). Using other servers might require entering different parameters and produce results different from those presented in these notebooks.

NOTE: While notebooks were saved to include chart outputs, GitHub does not render them. Once you check out the notebooks locally and open them in Jupyter, you may have to click the "Not Trusted" button on the upper right to tell Jupyter to trust the notebook and render the plots. Otherwise, you will need to re-run the notebooks yourself to regenerate the plots. Note also that for charts involving time series for which the queries retrieved data up to the "present" moment, re-running the notebook will always produce different results from the saved version, since the "present" will have advanced and more data will be retrieved.

If you do not already have account credentials for the `Sandbox` servers or would like to upgrade from tier 1 to tier 2 or 3, please contact api@awesense.com. 

If it is your first time working with EDM, [intro_and_tutorial](intro_and_tutorials) is a great place to start to ensure that your EDM credentials (whether for `Sandbox` or some other server) are working and to familiarize yourself with Awesense's EDM platform. <span style='color:red'> **Please do not store the credentials anywhere in the code, nor share them with anyone else.**</span>


### Install Dependencies
The notebooks in this repository have been mainly developed and tested using Python 3.9, although some of them can run on Python 3.7 as well. For the purpose of using this repository it is assumed that both Python and pip3 are already installed.
Please note that there are multiple python_requirements.txt files in this repository. The basic set of libraries required for all examples except for use cases and code snippets is at the main folder level. Any additional libraries specific to each use case or snippet are at the specific use_cases/name_of_use_case or code_snippets/name_of_code_snippet folder level.

In order to create a clean virtual environment for this repository, the following commands should be executed from a terminal opened in the directory this README is in:
```bash
python3 -m venv /your/desired/directory # While this only needs to be run once, repeated executions will not erase the existing libraries
source /your/desired/directory/bin/activate
pip3 install -r python_requirements.txt
python3 -m ipykernel install --user # Ensures that the Python3 kernel uses the new venv as expected
```

If restarting your command line terminal, only the `source` command needs to be re-run. If the `python_requirements.txt` file was updated, then the `pip3 install` and `python3 -m` commands must also be run again.

Please refer to the [use_cases' README](/use_cases/README.md) for instructions on installing the additional libraries specific to a given use case.


## Sandbox Dataset

Below is a summary of data types that are currently available in Awesense's Sandbox servers, as well as data types that are upcoming.

For more information on the data types used in Awesense's Energy Data Model and the EDM (SQL and REST) interfaces utilized by the notebooks in this repository, you can refer to the documentation available on all servers. To access the documentation, simply log in to Awesense's True Grid Intelligence (TGI) web app UI and click on the question mark symbol located on the top right corner.

### Present Data 
Below are the data currently in place. Tier 1 contains one grid, called `awefice`, which is composed of two primaries (also known as feeders or circuits). The southern primary represents North American grid topology, and the northern primary represents European grid topology. Tiers 2 & 3 contain an additional, larger grid called `North Central Zone`. This grid is located in Ohio and has forty six feeders, over fifteen thousand transformers, and over twenty thousand meters.  


For a detailed description of the grids' contents and access, please contact api@awesense.com.


**GIS Grid Elements**
* Line Segment
* Meter
* Transformer
* Switchable Element
  * Circuit Breaker
  * Disconnector
  * Fuse
  * Switch
* Enclosure
  * Cabinet
  * Substation
  * Pole
* Jumper
* Busbar
* EV Charger
* Photovoltaic
* Battery (currently only available on `awefice` grid)

**Common GIS Grid Element Attributes**
* Geo-coordinates
* Connectivity (2 terminals attributes)
* Type
* Phases
* Circuit
* Is Underground
* Is Operation

**Additional GIS Grid Element Specific Attributes**
* Meter
  * Type of Consumer (residential, business, industrial)
  * MCB (breaker) Value
  * Voltage Level
  * Parent Transformer
  * Maximal Demand and Maximal Demand Unit
  * Meter Number
  * Address
  * Is Critical
  * Tariff ID
  * Interval
  * Meter Type
  * Critical
  * Read Type
* Transformer
  * Load Loss & No Load Loss
  * kVA Rating
  * Voltage Level
  * Commission Date
  * Ownership
  * Primary Voltage
  * Maintenance Type
  * Installation Type
  * Secondary Voltage
* EV Charger
  * Make Model
  * Connector Type
  * Active Charging Power
  * Active Generation Power
  * Is DC Charger
  * Max Current & Max Voltage
  * Charging Level
  * Voltage Level
* Photovoltaic
  * Module
  * Panel Area
  * Panel Rated Power
  * Commission Date
  * AC & DC Size
  * DC to AC Ratio
  * Inverter
  * Racking
  * Tilt & Azimuth Angle
  * Voltage Level
  * Number of Panels
  * Panel Make and Model
  * Annual and 25 Years Degradations
  * Generation Capacity
* Line Segment
  * System
  * Phase to Phase Voltage
  * Make Model
  * Is Insulated
  * Wire Diameter
  * Materials
  * Rating A
  * Is Operation Status
  * Line Length
  * Voltage Level

**Time Series**
| Grid Element | Metrics | Frequency | Phase Granularity | History |
|---|---|---|---|---|
| SCADA | kWh | hourly | per-phase* | Jan 2021 - now |
| Meter | kWh | hourly | 3-phase aggregate** | Jan 2021 - now |
| Meter | V | hourly | 3-phase aggregate** | Jan 2021 - now |
| Solar | kWh | hourly | 3-phase aggregate** | Jan 2021 - now |
| EV Charger | kWh | minutely | per-phase| Jan 2021 - now |

\* Per-phase time series for SCADA elements are available only for the `North Central Zone` grid. The `awefice` grid has 3-phase aggregate SCADA data. 

\*\* 3-phase aggregate for three-phase grid elements, single phase for single-phase grid elements.


### Upcoming Data
Below are data that will become available in the future.

**GIS Grid Elements**
* Capacitor
* Line Conditioner
* Unmetered Load
* Service Delivery Point
* Manhole

While bringing new GIS Grid elements and attributes to the curated dataset, Awesense will also be populating additional attributes of existing GIS elements in the dataset. A CSV file detailing all grid element types and their respective properties (i.e. the EDM schema) that the Awesense Platform recognizes can be found in the documentation available from the TGI web app UI on all servers.  

**Time Series**
| Grid Element | Metrics | Frequency | Phase Granularity | 
|---|---|---|---|
| SCADA | V | hourly | per-phase| 
| Battery | kWh | hourly | per-phase| 
| Raptor* | kWh | hourly | per-phase| 
| Raptor Health* | IoT health | hourly | per-IoT device | 

\* Raptor is an Awesense IoT device that measures and records current flowing through a grid line.

Please stay tuned, as Awesense will be publishing schema updates and additional data on `Sandbox` soon.


## License
edm-app-examples is licensed under the [MIT license](LICENSE). 
