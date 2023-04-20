# edm-app-examples &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
edm-app-examples is a collection of application examples of how to use [Awesense](https://www.awesense.com)'s Energy Data Model 
(EDM). 

## Folder Organization
edm-app-examples is organized as follows:
* [intro_and_tutorials](intro_and_tutorials) - a
collection of introduction and tutorial materials to get started and learn how to use EDM.
* [code_snippets](code_snippets) - code snippets demonstrating quick analytical insights.
* [use_cases](use_cases) - real implementations of use cases from various areas of the energy domain.

Each folder contains an individual README.md file with more details specific to that folder. 


## Getting Started
While highly portable, all the code in this repository is set up to run out of the box against Awesense's `Sandbox` environment.

`Sandbox` is an Awesense server where its customers, partners and others can use the full functionality of the Awesense platform and TGI, to explore and create applications and solutions for the ecosystem using realistic simulated data. 

Notebooks were run and their outputs saved using input parameters (for example, grid ID, transformer IDs) corresponding to `Sandbox` data.  Using other servers might require entering different parameters and produce results different from those presented in these notebooks.

NOTE: While notebooks were saved to include chart outputs, GitHub does not render them. Once you check out the notebooks locally and open them in Jupyter, you may have to click the "Not Trusted" button on the upper right to tell Jupyter to trust the notebook and render the plots. Otherwise, you will need to re-run the notebooks yourself to regenerate the plots. Note also that for charts involving time series for which the queries retrieved data up to the "present" moment, re-running the notebook will always produce different results from the saved version, since the "present" will have advanced and more data will be retrieved.

If you do not already have account credentials for the `Sandbox` server, please contact api@awesense.com. 

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

If restarting your command line terminal, only the `source` command needs to be re-run.

Please refer to the [use_case's README](/use_cases/README.md) for instructions on installing the additional libraries specific to a given use case.


## Sandbox Dataset

Below is a summary of data types that are currently available in Awesense's Sandbox server, as well as data types that are upcoming.

Additional documentation on data types of Awesense's Energy Data Model and the EDM SQL interface used by the notebooks in this repo can be consulted on the `Sandbox` server once logging in [here](https://sandbox.awesense.com/docs/index.html).

### Present Data 
Below are data currently in place. There is one grid, called `awefice`, that is composed of two primaries (also known as feeders or circuits). The southern primary represents North American grid topology, and the northern primary represents European grid topology.

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
* Battery

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
  * Maximal Demand
  * Meter Number
  * Address
  * Is Critical
  * Tariff ID
* Transformer
  * Load Loss & No Load Loss
  * kVA Rating
  * Voltage Level
  * Commission Date
* EV Charger
  * Make Model
  * Connector Type
  * Active Charging Power
  * Active Generation Power
  * Is DC Charger
  * Max Current & Max Voltage
  * Charging Level
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
* Line Segment
  * System
  * Phase to Phase Voltage
  * Make Model
  * Is Insulated
  * Wire Diameter
  * Materials
  * Rating A
  * Is Operation Status

**Time Series**
| Grid Element | Metrics | Frequency | Phase Granularity | History |
|---|---|---|---|---|
| SCADA | kWh | hourly | 3-phase aggregate | Jan 2021 - now |
| Meter | kWh | hourly | 3-phase aggregate* | Jan 2021 - now |
| Meter | V | hourly | 3-phase aggregate* | Jan 2021 - now |
| Solar | kWh | hourly | 3-phase aggregate* | Jan 2021 - now |
| EV Charger | kWh | hourly | per-phase| Jan 2021 - now |

*3-phase aggregate for three-phase grid elements, single phase for single-phase grid elements.


### Upcoming Data
Below are data that will become available in the future.

**GIS Grid Elements**
* Capacitor
* Line Conditioner
* Unmetered Load
* Service Delivery Point
* Manhole

While bringing new GIS Grid elements and attributes to the curated dataset, Awesense will also be populating additional attributes of existing GIS elements in the dataset. A CSV file detailing all grid element types and their respective properties that the Awesense Platform recognizes can be found in the documentation available on the `Sandbox` server once logging in [here](https://sandbox.awesense.com/docs/_static/gis_metadata.csv).  

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