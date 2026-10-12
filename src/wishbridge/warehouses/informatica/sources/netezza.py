"""IBM Netezza.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import re

from .base import ERROR, I, NETEZZA, WARNING, Detector, Source

SOURCES = [Source("netezza", "Netezza", "bladebridge", "netezza")]
LABELS = {"netezza": "IBM Netezza"}
HINTS = {"netezza": [r"\bDISTRIBUTE\s+ON\b", r"\bORGANIZE\s+ON\b", r"\bGROOM\s+TABLE\b", r"\bGENERATE\s+STATISTICS\b"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"netezza": "netezza"}
FEDERATION = None  # Lakehouse Federation has no password connection for it: export files instead
FEDERATION_FOR = {"netezza": None}

DATABASE = "netezza"  # database type
INVENTORY_QUERY = """SELECT c.SCHEMA AS schema_name, c.NAME AS table_name, c.ATTNAME AS column_name, c.ATTNUM AS ordinal,
       c.FORMAT_TYPE AS data_type, NULL AS max_length, NULL AS precision, NULL AS scale,
       CASE WHEN c.ATTNOTNULL THEN 'NO' ELSE 'YES' END AS is_nullable,
       t.RELTUPLES AS row_count, NULL AS size_mb
FROM _V_RELATION_COLUMN c
JOIN _V_TABLE t ON t.OBJID = c.OBJID
WHERE c.TYPE = 'TABLE'
ORDER BY 1, 2, 4;"""
HOW_TO_SAVE = "nzsql -d <db> -A -F ',' -f this.sql -o inventory.csv (add the header line if your client omits it)."

# Checks on the converted code (rules.py)
DETECTORS: list[Detector] = [
    Detector("netezza-groom", ERROR, NETEZZA, re.compile(r"\bGROOM\s+TABLE\b", I),
             "GROOM TABLE: run OPTIMIZE on the Delta table instead (or rely on predictive optimisation)."),
    Detector("netezza-stats", WARNING, NETEZZA, re.compile(r"\bGENERATE\s+(?:EXPRESS\s+)?STATISTICS\b", I),
             "GENERATE STATISTICS: use ANALYZE TABLE ... COMPUTE STATISTICS."),
    Detector("netezza-age", ERROR, NETEZZA, re.compile(r"\bAGE\s*\(", I),
             "AGE() does not exist in Databricks: use datediff() or months_between()."),
]
