# edm-app-examples &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
edm-app-examples is a collection of application examples of how to use [Awesense](https://www.awesense.com)'s Energy Data Model 
(EDM). 

## Folder Organization
edm-app-examples is organized as the following:
* [intro_and_tutorials](intro_and_tutorials) - a
collection of introduction and tutorial materials to get started and learn how to use EDM.
* [code_snippets](code_snippets) - code snippets demonstrating quick analytical insights.
* [use_cases](use_cases) - real implementations of use cases from various areas of the energy domain.


## Sandbox
`Sandbox` is an Awesense server where its customers, partners and others can use the full functionality of the Awesense platform and TGI, to explore and create applications and solutions for the ecosystem using realistic simulated data. 

Awesense users can test the code in this repository using the `Sandbox` server. Notebooks were run using input parameters (for example, transformer's name) corresponding to Sandbox data.  Using other servers might require different parameters and produce results different from those presented in these notebooks.

Below are data types that are currently available in this server and those that are upcoming.

### Present Data 
Below are data currently in place. The grid is composed of two primaries (also known as feeders or circuits). The southern primary represents North American grid topology, and the northern primary represents European grid topology.

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
| SCADA | kWh | hourly | 3-phase | Jan 2021 - now |
| Meter | kWh | hourly | 3-phase* | Jan 2021 - now |
| Meter | V | hourly | 3-phase* | Jan 2021 - now |

*3-phase for three phase meters, single phase for single phase meters


### Upcoming Data
Below are data that will become available in the future.

**GIS Grid Elements**
* Capacitor
* Line Conditioner
* Unmetered Load
* Service Delivery Point
* Manhole

While bringing new GIS Grid elements and attributes to the curated dataset, Awesense will also be populating additional attributes of existing GIS elements in the dataset. A CSV file detailing all grid element types and their respective properties that the Awesense Platform recognizes can be found [here](https://sandbox.awesense.com/docs/_static/gis_metadata.csv). Please note that some custom fields stored in the `meta` data may not be included in this CSV file. 

**Time Series**
| Grid Element | Metrics | Frequency | Phase Granularity | 
|---|---|---|---|
| SCADA | V | hourly | per-phase| 
| Solar | kWh | hourly | per-phase| 
| Battery | kWh | hourly | per-phase| 
| EV Charger | kWh | hourly | per-phase| 
| Raptor* | kWh | hourly | per-phase| 
| Raptor Health* | IoT health | hourly | per-IoT device | 

\* Raptor is an Awesense IoT device that measures and records current flowing through a grid line.

Please stay tuned as Awesense continues publishing additional `Sandbox` data catalogs soon.


## Getting Started
Each folder contains an individual README.md file with more details specific to that folder. If it is your first time working with EDM, [intro_and_tutorial](intro_and_tutorials) is a great place to start to ensure that your EDM credentials are working and to familiarize with Awesense's EDM platform. Please do not store the credentials anywhere in the code, nor share them with anyone else.


### Install Dependencies
At the time of writing, it is assumed that Python 3.7 and pip3 are installed for the purposes of using this repository. Please note that there are multiple python_requirements.txt files in this repository. The basic set of libraries required for all examples except for use cases is at the main folder level. Any additional libraries specific to each use case are at the specific use_cases/name_of_use_cases folder level.

In order to create a clean virtual environment for this repository, the following commands should be executed from a terminal opened in this directory:
```bash
python3 -m venv /your/desired/directory # While this only needs to be run once, repeated executions will not erase the existing libraries
source /your/desired/directory/bin/activate
pip3 install -r python_requirements.txt
python3 -m ipykernel install --user # Ensures that the Python3 kernel uses the new venv as expected
```

Please refer to the [use_case's README](/use_cases/README.md) for instruction on installing the additional libraries specific to a given use case.

## License
edm-app-examples is licensed under the [MIT license](LICENSE). 