"""Snowflake.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import re

from .base import ERROR, I, SNOWFLAKE, WARNING, DbType, Detector, Source

SOURCES = [Source("snowflake", "Snowflake", "morph", "snowflake")]
LABELS = {"snowflake": "Snowflake"}
HINTS = {"snowflake": [r"\bVARIANT\b", r"\bIFF\s*\(", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TASK|STREAM|STAGE)\b", r"\bFLATTEN\s*\(", r"\bTIMESTAMP_NTZ\b", r"@\w+/"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"snowflake": "snowflake"}
FEDERATION = DbType("snowflake", "Snowflake", 443, "database", extra=(("sfWarehouse", "Snowflake warehouse"),))
FEDERATION_FOR = {"snowflake": "snowflake"}

DATABASE = "snowflake"  # database type
INVENTORY_QUERY = """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, c.character_maximum_length AS max_length, c.numeric_precision AS precision,
       c.numeric_scale AS scale, c.is_nullable, t.row_count, ROUND(t.bytes / 1048576, 2) AS size_mb
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type = 'BASE TABLE' AND c.table_schema <> 'INFORMATION_SCHEMA'
ORDER BY 1, 2, 4;"""
HOW_TO_SAVE = 'Snowsight: run, then Download results (CSV). SnowSQL: snowsql -f this.sql -o output_format=csv -o header=true -o timing=false -o friendly=false > inventory.csv'
TYPES = {"datetime": "TIMESTAMP_NTZ", "timestamp": "TIMESTAMP_NTZ"}  # where a type name means something else here than in the shared map

# Safe, mechanical rewrites of the converted code (rules.py): (rule, dialects, pattern, replacement, note)
FIXES: list[tuple[str, frozenset | None, re.Pattern, str, str]] = [
    ("snowflake-max-varchar", SNOWFLAKE, re.compile(r"\bVARCHAR\s*\(\s*16777216\s*\)", I), "STRING",
     "Replaced VARCHAR(16777216) (Snowflake's default max length) with STRING"),
]

# Checks on the converted code (rules.py)
DETECTORS: list[Detector] = [
    Detector("snowflake-stage", ERROR, SNOWFLAKE, re.compile(r"(?<![\w@'])@~?[A-Za-z_][\w./]*"),
             "Snowflake stage {m}: load from a Unity Catalog volume path (/Volumes/...) instead."),
    Detector("snowflake-flatten", ERROR, SNOWFLAKE, re.compile(r"\bFLATTEN\s*\(", I),
             "FLATTEN(): use explode() / variant_explode() with LATERAL VIEW or a lateral join."),
    Detector("snowflake-tz-timestamp", WARNING, SNOWFLAKE, re.compile(r"\bTIMESTAMP_(?:LTZ|TZ)\b", I),
             "{m}: Databricks TIMESTAMP is session-time-zone based; confirm time-zone behaviour."),
]
