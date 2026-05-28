# use_cases &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](../LICENSE)
use_cases contains various examples that can be addressed with [Awesense](https://www.awesense.com)'s Energy Data Model (EDM).

Use the link below to access the introductory notebook for getting started with the SQL API in Google Colab. You can also open any notebook from the repository by following these steps within [Google Colab](https://colab.research.google.com/):
* Navigate to "File" and select "Open notebook".
* Select the "GitHub" tab and search for "Awesense".
* Enable "Include private repos" and complete the authentication process in the resulting pop-up window.

[![Open in Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/Awesense/edm-app-examples/blob/master/intro_and_tutorials/sql_api/1_access_and_documentation/access_and_documentation.ipynb)

# Folder Organization
Each use case folder contains materials related to the respective use cases. A link to a detailed description of the use case in a PDF format is available in the Overview section of each use cases' file. In addition, a folder called `usecases_descriptions` contains PDF descriptions of current and future use cases and a table summarizing use cases implementations. Each use case description provides a quick start guide on implementing the respective use case using the Awesense Energy Transition Platform.

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