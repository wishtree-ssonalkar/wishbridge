"""One file per warehouse for the parts of the app that differ: change SSIS screens in ssis.py, and no
other warehouse is affected.

What a file defines (default.py has every name; the others change only what differs):

  ASSESS_TOGETHER     the database's code is assessed next to the code files (ETL), not instead of them
  READ_CODE_DEFAULT   "Also read the code" is ticked for folder projects
  database_text()     title and explanation of the Code step's database section
  open_database_section(inventory)   open that section when the page opens
  code_extras(cfg, raw)              extra parts at the end of the Code step
  report_extras(cfg, state)          extra parts in the Report
"""

from __future__ import annotations

from types import ModuleType

from . import default, etl, ssis

_BY_SOURCE = {"ssis": ssis, "informatica": etl, "informatica-cloud": etl, "datastage": etl}


def warehouse_ui(cfg) -> ModuleType:
    """The screens for this project's source system (default.py when nothing differs)."""
    return _BY_SOURCE.get(cfg.source.key, default)
