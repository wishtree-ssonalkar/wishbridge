"""One file per warehouse: everything WishBridge knows about a source system is in sources/<warehouse>.py.

To change how WishBridge handles a warehouse, open its file. To add one (e.g. Microsoft Fabric), copy the
closest file, fill in what applies, and add it to WAREHOUSES below. The shared steps (convert, deploy,
report, ...) read everything through the tables built here.

What a warehouse file can define (all optional):

  SOURCES          project source systems it adds (project.yml `source:`), as Source(...)
  LABELS           how each of those sources is shown to the user
  HINTS            keywords that point at the dialect when a folder of code is opened (discover.py)
  ETL_TARGETS      ETL tools only: what the ETL converter generates (SPARKSQL / PYSPARK)
  DEFAULT_DB       the database type a source usually is / loads (for the table-list query)
  DATABASE         the database type this file describes, with:
    INVENTORY_QUERY  read-only query listing tables, columns, types, row counts, sizes (inventory.py)
    HOW_TO_SAVE      how the DBA saves that query's result as CSV
  TYPES            where a type name means something else here than in the shared type map (schema.py)
  TYPE_CODES       type codes instead of names (Teradata)
  FEDERATION       DbType for Lakehouse Federation (copying the data), None when it has no password connection
  FEDERATION_FOR   source key -> FEDERATION key (None: export files instead)
  FIXES            safe, mechanical rewrites of the converted code (rules.py)
  DETECTORS        checks on the converted code (rules.py)
  PREP             tidy-ups of the source before conversion (SQL Server)
  CODE_QUERY       reads procedures, views, functions and triggers from the database (SQL Server)
"""

from __future__ import annotations

from . import (bigquery, datastage, informatica, mysql, netezza, oracle, postgresql, redshift, snowflake, sqlserver,
               ssis, synapse, teradata)

# In the order they are offered in the app
WAREHOUSES = [sqlserver, synapse, snowflake, oracle, teradata, redshift, bigquery, netezza, datastage, informatica,
              ssis, postgresql, mysql]


def _merge(name: str) -> dict:
    out: dict = {}
    for w in WAREHOUSES:
        out.update(getattr(w, name, {}) or {})
    return out


SOURCES = {s.key: s for w in WAREHOUSES for s in getattr(w, "SOURCES", [])}
LABELS = _merge("LABELS")
HINTS = _merge("HINTS")
ETL_TARGETS = _merge("ETL_TARGETS")
DEFAULT_DB = _merge("DEFAULT_DB")
FEDERATION_FOR = _merge("FEDERATION_FOR")

# Database types (the table-list query, its type map) in the order the app lists them
_DATABASES = [sqlserver, synapse, oracle, snowflake, teradata, redshift, bigquery, netezza]
INVENTORY_QUERIES = {w.DATABASE: w.INVENTORY_QUERY for w in _DATABASES}
HOW_TO_SAVE = {w.DATABASE: w.HOW_TO_SAVE for w in _DATABASES}
TYPES = {w.DATABASE: w.TYPES for w in _DATABASES if getattr(w, "TYPES", None)}

# Lakehouse Federation connection types, in the order the app lists them
FEDERATION = {f.key: f for f in (getattr(w, "FEDERATION", None)
                                 for w in [sqlserver, synapse, oracle, snowflake, teradata, redshift, postgresql, mysql]) if f}

# Rewrites of converted code that belong to one warehouse; the ones for every source stay in rules.py
FIXES = [f for w in WAREHOUSES for f in getattr(w, "FIXES", [])]

# Checks on converted code that belong to one warehouse; the checks for every source stay in rules.py
DETECTORS = [d for w in WAREHOUSES for d in getattr(w, "DETECTORS", [])]
