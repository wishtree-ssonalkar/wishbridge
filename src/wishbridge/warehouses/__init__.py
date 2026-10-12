"""One complete, independent copy of WishBridge per warehouse: warehouses/<warehouse>/.

A change in one warehouse's folder affects only that warehouse's projects. The shared main screen and the
`wishbridge` command (src/wishbridge/ui/app.py, cli.py) open a project and hand over to its warehouse here.
"""

from __future__ import annotations

import importlib
from types import ModuleType

# project.yml `source:` -> the warehouse folder that handles it
WAREHOUSE_OF = {'mssql': 'sqlserver', 'synapse': 'synapse', 'snowflake': 'snowflake', 'oracle': 'oracle', 'teradata': 'teradata', 'redshift': 'redshift', 'bigquery': 'bigquery', 'netezza': 'netezza', 'datastage': 'datastage', 'informatica': 'informatica', 'informatica-cloud': 'informatica', 'ssis': 'ssis'}


def package_for(source_key: str) -> str:
    try:
        return WAREHOUSE_OF[source_key]
    except KeyError:
        raise ValueError(f"No WishBridge for source '{source_key}'. Choose one of: {', '.join(WAREHOUSE_OF)}") from None


def module(source_key: str, name: str) -> ModuleType:
    """A module of the warehouse that handles this source, e.g. module("ssis", "cli")."""
    return importlib.import_module(f"wishbridge.warehouses.{package_for(source_key)}.{name}")


def ui_for(source_key: str) -> ModuleType:
    """The warehouse's app (its ui/main.py: run(raw))."""
    return module(source_key, "ui.main")
