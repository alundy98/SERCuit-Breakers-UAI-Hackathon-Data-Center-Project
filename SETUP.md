# Setup and Running Notebooks &middot; [![GitHub license](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

This guide covers everything needed to run the notebooks in this repository: installing dependencies, configuring credentials, and running notebooks from the command line. For an overview of the repository and the data available in the Awesense Sandbox, see the [main README](README.md).

## Install Dependencies
The notebooks in this repository have been tested using Python versions from 3.9 up to 3.13. For the purpose of using this repository it is assumed that both `python` and `pip3` are already installed. There is a `python_requirements.txt` file that must be installed to run any notebook locally.

### Linux/MacOS Installation
In order to create a clean virtual environment for this repository, the following commands should be executed from a terminal opened in the root directory of the repository:
```bash
python3 -m venv /your/desired/directory # While this only needs to be run once, repeated executions will not erase the existing libraries
source /your/desired/directory/bin/activate
pip3 install -r python_requirements.txt
```

### Windows Installation
Users in a Windows environment must download [Build Tools for Visual Studio](https://visualstudio.microsoft.com/downloads/#build-tools-for-visual-studio-2022) in order for `pip` to be able to build all the dependencies properly.
For users using PowerShell, the virtual environment commands differ slightly (aside from Windows using backslash).
```powershell
Set-ExecutionPolicy RemoteSigned -Scope Process
python -m venv \your\desired\directory # While this only needs to be run once, repeated executions will not erase the existing libraries
.\your\desired\directory\Scripts\activate
pip3 install -r python_requirements.txt
```

If restarting your command line terminal, only the command invoking `activate` needs to be re-run. If the `python_requirements.txt` file was updated, then the `pip3 install` command must also be run again.


## Authentication Setup Guide

This repository contains notebooks that connect to EDM through the **SQL API** (a PostgreSQL database) and/or the **REST API**. Both use the same TGI credentials, exposed under one shared set of names — `EDM_HOST`, `EDM_USER`, and `EDM_PASSWORD`. (The SQL notebooks map these onto the `PG*` environment variables that PostgreSQL's client library reads automatically, so you don't need to set `PG*` yourself.) To keep credentials secure, this project supports three authentication methods depending on your environment:
1. **Local Development:** a local `.env` configuration file combined with a secure system file (`~/.pgpass` for Linux/MacOS or `%APPDATA%\postgresql\pgpass.conf` for Windows) for SQL API and/or the `EDM_PASSWORD` environment variable for REST API.
2. **Google Colab Cloud Runtime:** Colab Secrets.
3. **Fallback method:** interactive terminal prompts. If you choose to use this method you can skip directly to [Running Notebooks with Papermill](#running-notebooks-with-papermill), or start exploring the [Sandbox Dataset](README.md#sandbox-dataset).

The credential keys are shared across both APIs:

| Purpose        | Variable       | Notes                                                                              |
| -------------- | -------------- | -----------------------------------------------------------------------------------|
| Server address | `EDM_HOST`     | e.g. `sandpit.awesense.com`                                                        |
| Username       | `EDM_USER`     | your TGI username                                                                  |
| Password       | `EDM_PASSWORD` | SQL API reads it from `~/.pgpass` or `pgpass.conf` locally; never put it in `.env` |

The SQL API's database name (`edm`) and port (`5432`) are handled by defaults, so you don't need to set them.

### Local Environment Setup

#### 1. Configure Environment Context (`.env` for both Linux/MacOS or Windows)
The `.env` file handles non-sensitive contextual parameters like host addresses and usernames.

1. Copy the file named `.env.example` in the root directory of this project and rename it `.env`:
```bash
cp .env.example .env
```

2. Open the new `.env` file in your text editor and edit the following keys (do not use spaces around the `=` signs):
```text
# Shared EDM credentials (SQL API + REST API)
EDM_HOST=your TGI server address
EDM_USER=your TGI username
```

**CRITICAL SECURITY NOTE:** Never add your password to the `.env` file, and ensure `.env` is listed inside your `.gitignore` file so it is never pushed to GitHub.

#### Restricting File Permissions (Linux/MacOS only)

To ensure other local users or unauthorized applications on your machine cannot read your environment configuration, tighten the file permissions via your terminal:

```bash
chmod 600 .env
```

*(This limits access strictly to Read/Write for your specific operating system user).*

#### 2. Configure Passwords (Linux/MacOS)

**SQL API — `~/.pgpass`:** PostgreSQL client tools (and our Python connection scripts) natively look for passwords inside a hidden, locked-down file in your user home directory.

1. Create or open the `.pgpass` file inside your system's home folder:
```bash
touch ~/.pgpass
```

2. Add your database connection parameters on a single line using the explicit `hostname:port:database:username:password` format:
```text
your-database-server-address:5432:edm:your_username:your_actual_password
```

3. **Mandatory Step:** PostgreSQL will completely ignore this file if its permissions are too open. You must restrict it:
```bash
chmod 0600 ~/.pgpass
```

#### 2. Configure Passwords (Windows)
**SQL API — `%APPDATA%\postgresql\pgpass.conf`:** PostgreSQL client tools (and our Python connection scripts) natively look for passwords inside a configuration file located in your user's `AppData` directory.

1. Ensure the `postgresql` directory exists in your user's `AppData` folder (either create it in Explorer, or use PowerShell, e.g.):

```PowerShell
New-Item -ItemType Directory -Force -Path "$env:APPDATA\postgresql"
```
2. Add your database connection parameters:
```
# Prompt for Awesense TGI SQLAPI server + credentials
$your_tgi_hostname = Read-Host "Enter your Awesense TGI server's hostname (do not include `https://`)"
$your_tgi_username = Read-Host "Enter your Awesense TGI username"
$your_tgi_password_secure_str = Read-Host -Prompt "Enter your Awesense TGI user's password" -AsSecureString
$your_tgi_password = [System.Net.NetworkCredential]::new("", $your_tgi_password_secure_str).Password

# Append the line to pgpass.conf

$pgpass_line = "${your_tgi_hostname}:5432:edm:${your_tgi_username}:${your_tgi_password}"
$pgpass_conf_path = "$env:APPDATA\postgresql\pgpass.conf"
Add-Content -Path $pgpass_conf_path -Value "$pgpass_line"

# Restrict permissions on pgpass.conf

icacls $pgpass_conf_path /inheritance:r /grant:r "$($env:USERNAME):(F)" | Out-Null

# Delete variables containing password data

$pgpass_line = $null; $your_tgi_password = $null; $your_tgi_password_secure_str.Dispose(); [GC]::Collect()
```

**REST API — `EDM_PASSWORD`:** the REST API has no `~/.pgpass` equivalent. When running interactively, the notebook prompts for the password securely. For non-interactive runs (e.g. Papermill), provide it via the exported `EDM_PASSWORD` environment variable — see [Running Notebooks with Papermill](#running-notebooks-with-papermill).

### Google Colab Cloud Setup

When running notebooks inside Google Colab, you should not upload an `.env` or `.pgpass` file, as cloud workspaces can be shared or exposed. Instead, use Colab's native, encrypted **Secrets** manager.

### How to set up Secrets in Colab:

1. Open your notebook in Google Colab.
2. On the left-hand vertical sidebar, click the **🔑 Key icon** (Secrets).
3. Click **Add new secret** and add these three secrets (shared by both the SQL API and REST API notebooks), with these exact names:

* `EDM_HOST` (your TGI server address)
* `EDM_USER` (your TGI username)
* `EDM_PASSWORD` (your TGI password)

4. **Crucial:** For each key you add, toggle the **Notebook access** switch to **ON**.


## Running Notebooks with Papermill

[Papermill](https://papermill.readthedocs.io) lets you execute notebooks programmatically and inject parameters at runtime avoiding the need to wait for user input.

Following the instructions provided above to activate your virtual environment, you can execute a notebook and preserve the resulting output in a new file:

```bash
papermill path/to/notebook.ipynb path/to/output_notebook.ipynb --cwd path/to -p parameter1 "value1" -p parameter2 "value2"
```
`--cwd` runs the notebook from its own folder so relative file references can be used; UC41 needs it to find `transformers_max_load.csv`.

For example, to run the notebook in UC01 using Papermill (please note that the path syntax for Windows is slightly different):
```bash
papermill use_cases/UC01_cp_ncp/cp_ncp.ipynb use_cases/UC01_cp_ncp/UC01_output_notebook.ipynb \
  --cwd use_cases/UC01_cp_ncp \
  -p grid_id "awefice" \
  -p start "2022-03-01 00:00:00" \
  -p end "2022-04-01 00:00:00" \
  -p tariff_id "res_basic" \
  -p feeder_id "transformer_6"
```
