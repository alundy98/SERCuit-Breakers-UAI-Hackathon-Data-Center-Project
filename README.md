# edm-app-examples &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
edm-app-examples is a collection of application examples of how to use [Awesense](https://www.awesense.com)'s Energy Data Model
(EDM).

The fastest way to get started with this repo is to make use of the Awesense Sandbox Environment. `Sandbox` is a collection of Awesense servers where customers, partners and others can use the functionality of the Awesense platform, including APIs (SQL AND REST) and the True Grid Intelligence (TGI) web app, to explore and create applications and solutions for the grid ecosystem using realistic simulated data. Currently, most of the snippets and use cases are implemented using the Awesense SQL API, with only a handful using the REST API, but more to be added.

While highly portable, all the code in this repository can be run out of the box against one of the `Sandbox` servers, and, unless otherwise specified, the notebooks in this repo were run and their outputs saved using input parameters (for example, grid ID, transformer IDs) corresponding to the `awefice` grid. With new releases, more notebooks will run out of the box against the richer `SAF` and `GSO` grid datasets (for example, the code snippet meter_ders_matrix). Using other servers might require entering different parameters and produce results different from those presented in these notebooks.

**Running notebooks** [SETUP.md](SETUP.md) covers installing dependencies, configuring credentials, and running notebooks from the command line.

If you do not already have account credentials for the `Sandbox` servers, please contact api@awesense.com.

## Sandbox Dataset

Below is a summary of data types that are currently available in Awesense's Sandbox servers, as well as data types that are upcoming.

For more information on the data types used in Awesense's Energy Data Model and the EDM (SQL and REST) interfaces utilized by the notebooks in this repository, you can refer to the documentation available on all servers. To access the documentation, simply log in to Awesense's True Grid Intelligence (TGI) web app UI and click on the question mark symbol located on the top right corner.

### Present Data

The `awefice` grid is composed of two primaries (also known as feeders or circuits). The southern primary represents North American grid topology, and the northern primary represents European grid topology. There are also two additional sets of grids in Santa Fe and Greensboro derived from the National Laboratory of the Rockies [SMART-DS](https://www.nlr.gov/grid/smart-ds.html), whose grid ids are prefixed with `SAF_` and `GSO_`, e.g. `SAF_1`. They comprise 220 grids with 220k+ line segments, 106k+ meters, 27k+ transformers, 6.8k+ switches, 2.9k+ PV, and 1.1k+ EV chargers.

**GIS Grid Elements**
* Line Segment
* Meter
* Transformer
* Capacitor (`SAF`, `GSO` only)
* Switchable Element
  * Circuit Breaker
  * Disconnector (`awefice` only)
  * Fuse
  * Switch
* Enclosure (`awefice` only)
  * Cabinet
  * Substation
  * Pole
* Jumper (`awefice` only)
* Busbar (`awefice` only)
* EV Charger
* Photovoltaic
* Battery (`awefice` only)

**Common GIS Grid Element Attributes**
* Geo-coordinates (latitude/longitude)
* Connectivity (terminal attributes)
* Type
* Phases
* Circuit association

**Additional GIS Grid Element Specific Attributes**

The `awefice` grid carries richer business and lifecycle metadata — billing/metering fields on meters and maintenance details on transformers. It represents HV/MV transformers as feeder heads while the `SAF`, `GSO` grids represent them using producer circuit breakers.

* Meter
  * Consumer Type
  * Voltage Level
  * Zone Levels
  * `awefice` only
    * Parent Transformer
    * Maximal Demand
    * Address
    * Is Critical
    * Tariff ID
    * Interval
    * Meter Type
    * Read Type
* Transformer
  * kVA Rating
  * Voltage Level
  * Primary & Secondary Voltage
  * `awefice` only
    * Load & No Load Loss
    * Commission Date
    * Ownership
    * Maintenance Type
    * Installation Type
* Capacitor (`SAF`, `GSO` only)
  * kVAR Rating
  * Voltage Level
* EV Charger
  * Make Model
  * Connector Type
  * Active Charging & Generation Power
  * Is DC Charger
  * Max Current & Max Voltage
  * Charging & Voltage Level
  * Meter ID
  * Parent Transformer
  * Ownership
* Photovoltaic
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
  * Panel Make & Model
  * Annual & 25 Years Degradations
  * Generation Capacity
  * Meter ID
  * Parent Transformer
  * Ownership
* Line Segment
  * System
  * Phase to Phase Voltage
  * Make Model
  * Is Insulated
  * Wire Diameter
  * Materials
  * Rating A
  * Operation Status
  * Line Length
  * Voltage Level

**Time Series**

All time-series metrics listed below are programmatically linked to their corresponding GIS elements via grid_element_data_source (feature of the Awesense EDM data model), so any retrieved time series can be joined back to that element's grid topology and attributes — enabling integrated spatial and temporal analysis (e.g. tracing downstream from a substation, then pulling meter-level load or voltage for that exact set of elements).

| Grid Element | Metrics | Frequency | Phase Granularity | History |
|---|---|---|---|---|
| SCADA | kWh, V | 5 minutes | per-phase* | Jan 2024 - present‡ |
| Meter | kWh, V | hourly | 3-phase aggregate** | Jan 2024 - present‡ |
| Solar | kWh | hourly | 3-phase aggregate** | Jan 2024 - present‡ |
| EV Charger | kWh | minutely | 3-phase aggregate** | Jan 2024 - present‡ |
| Raptor† | A, PF, lagging | minutely | per-phase | Jan 2024 - present‡ |
| Raptor Health† | battery, scavenge, temperature, cell strength | minutely | per-phase | Jan 2024 - present‡ |

\* Per-phase time series for SCADA elements are available only for the `SAF`, `GSO` grids. The `awefice` grid has 3-phase aggregate SCADA data.

\*\* 3-phase aggregate for three-phase grid elements, single phase for single-phase grid elements.

† Raptor is an Awesense IoT device that measures and records current flowing through a grid line.

‡ The `awefice` grid has time series going back to Jan 2021, but has no raptor time series.


### Upcoming Data
Below are data that will become available in the future.

**GIS Grid Elements**
* Line Conditioner
* Unmetered Load
* Service Delivery Point
* Manhole

While bringing new GIS Grid elements and attributes to the curated dataset, Awesense will also be populating additional attributes of existing GIS elements in the dataset. A CSV file detailing all grid element types and their respective properties (i.e. the EDM schema) that the Awesense Platform recognizes can be found in the documentation available from the TGI web app UI on all servers.


## Folder Organization
edm-app-examples is organized as follows:
* [intro_and_tutorials](intro_and_tutorials) - a collection of introduction and tutorial materials to get started and learn how to use EDM.
  * [sql_api](intro_and_tutorials/sql_api) - how to access EDM through the SQL API. Its subfolders are numbered in the recommended order to familiarize yourself with the platform:
    * [1_access_and_documentation](intro_and_tutorials/sql_api/1_access_and_documentation) - shows how to connect to EDM, and access various documentation on the available views and functions.
    * [2_main_concepts](intro_and_tutorials/sql_api/2_main_concepts) - introduces EDM's core views and functions in more detail, and demonstrates various ways to fetch and work with data using SQL and Python.
    * [3_grid_tracing](intro_and_tutorials/sql_api/3_grid_tracing) - demonstrates how to programmatically use tracing functionalities available in TGI (True Grid Intelligence).
    * [4_time_series](intro_and_tutorials/sql_api/4_time_series) - demonstrates how to access time series data for meters and SCADAs from EDM.
  * [rest_api](intro_and_tutorials/rest_api) - how to access EDM through the REST API. This folder keeps [its own README](intro_and_tutorials/rest_api/README.md).
* [code_snippets](code_snippets) - code snippets demonstrating quick analytical insights. Each snippet folder contains one or more notebooks.
* [use_cases](use_cases) - real implementations of use cases from various areas of the energy domain. Each use case folder contains the materials for that use case, and a link to a detailed description of it in PDF format is available in the Overview section of its notebook. In addition, [usecase_descriptions](use_cases/usecase_descriptions) contains PDF descriptions of current and future use cases and a table summarizing use case implementations. Each use case description provides a quick start guide on implementing the respective use case using the Awesense Energy Transition Platform.
* [utils](utils) - general utility functions that are utilized from notebooks in other folders.

Please stay tuned as Awesense continues publishing additional snippets, use case descriptions and implementations.


## Running the Notebooks

If it is your first time working with EDM, [intro_and_tutorials](intro_and_tutorials) is a great place to start to ensure that your EDM credentials (whether for `Sandbox` or some other server) are working and to familiarize yourself with Awesense's EDM platform. <span style='color:red'> **Please do not store the credentials anywhere in the code, nor share them with anyone else.**</span>

To run the notebooks on your own machine, follow [SETUP.md](SETUP.md). To run them in the cloud instead, use the link below to open the introductory SQL API notebook in Google Colab. You can also open any notebook from the repository by following these steps within [Google Colab](https://colab.research.google.com/):
* Navigate to "File" and select "Open notebook".
* Select the "GitHub" tab and search for "Awesense".
* Enable "Include private repos" and complete the authentication process in the resulting pop-up window.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Awesense/edm-app-examples/blob/master/intro_and_tutorials/sql_api/1_access_and_documentation/access_and_documentation.ipynb)

NOTE: While notebooks were saved to include chart outputs, GitHub does not render them. Once you check out the notebooks locally and open them in Jupyter, you may have to click the "Not Trusted" button on the upper right to tell Jupyter to trust the notebook and render the plots. Otherwise, you will need to re-run the notebooks yourself to regenerate the plots. Note also that for charts involving time series for which the queries retrieved data up to the "present" moment, re-running the notebook will always produce different results from the saved version, since the "present" will have advanced and more data will be retrieved.


## License
edm-app-examples is licensed under the [MIT license](LICENSE).
