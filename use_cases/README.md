# use_cases &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](../LICENSE)
use_cases contains various examples that can be addressed with [Awesense](https://www.awesense.com)'s Energy Data Model (EDM). 

# Folder Organization
Each use case folder contains materials related to the respective use cases. A detailed description of each use case in a PDF format is also available in the folders. In addition, a folder called `usecases_descriptions` contains PDF descriptions of current and future use cases and a table summarizing use cases implementations. Each use case description provides a quick start guide on implementing the respective use case using the Awesense Energy Transition Platform.

Please stay tuned as Awesense continues publishing additional use case descriptions and implementations.

## Getting Started
### Install Dependencies
As noted in the general [README](../README.md), the additional libraries specific to the use case are included in the respective python_requirements.txt found in each folder if applicable. For example, the requirement file for the transformer asset management use case is in the [UC24_transformer_asset_management_outage](UC24_transformer_asset_management_outage) folder. 

The same following commands can be executed from a terminal opened in the main directory to install these requirements as well:

```bash
python3 -m venv /your/desired/directory # Same directory where the general venv was installed
source /your/desired/directory/bin/activate
pip3 install -r use_cases/name_of_use_case/python_requirements.txt # Point to python_requirements.txt within the specific use case's sub folder
python3 -m ipykernel install --user # Ensures that the Python3 kernel uses the new venv as expected
```