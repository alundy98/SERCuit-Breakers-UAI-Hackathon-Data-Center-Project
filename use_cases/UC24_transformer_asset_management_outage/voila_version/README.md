# transformer_asset_management_outage - Adapted for Voilà

This notebook is intended to:
- Show how to use [Jupyter Widgets](https://ipywidgets.readthedocs.io/) and [Voilà](https://voila.readthedocs.io/) to turn a notebook into an interactive web app.
- Demonstrate a use case: Transformer Asset Management.

It is based on the [transformer_asset_management_outage](../transformer_asset_management_outage.ipynb) notebook. The difference is that this notebook is adapted for conversion into a standalone web app, where the user can interact with the visualizations without seeing or having to write any code. With Voilà, the notebook can also be deployed and thus easily shared across organizations.

## Running the Notebook as an App

This notebook can be run with `jupyter` as any other notebook. Please note that you need to run each cell of the notebook, in order of appearance, before you can attempt to connect to EDM. The reason is that the input fields and buttons are made interactive in the last cell, and the last cell references code in other cells.

If you want to view and use the app version of the notebook, you need to:

Install Voilà

```bash
pip3 install voila==0.3.6 # The latest version isn't compatible with the version of ipywidgets we're using
```

Should you have `jupyter_server>=1.15.0` installed, you will need to upgrade your version of `nbformat` to `5.2.0`:

```bash
pip3 uninstall nbformat
pip3 install nbformat==5.2.0
```

Run the notebook with Voilà

```bash
voila <path-to-notebook> # If terminal opened in this directory: voila transformer_asset_management_outage_voila_app.ipynb 
```

or serve a directory of notebooks

```bash
cd your-notebooks-directory/
voila
```

For further information on how to use Voilà, see [Using Voilà](https://voila.readthedocs.io/en/stable/using.html)
