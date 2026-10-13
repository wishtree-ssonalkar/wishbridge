"""Amazon Redshift.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import DbType, Source

SOURCES = [Source("redshift", "Redshift", "morph", "redshift")]
LABELS = {"redshift": "Amazon Redshift"}
HINTS = {"redshift": [r"\bDISTKEY\b", r"\bSORTKEY\b", r"\bDISTSTYLE\b", r"\bENCODE\s+\w+", r"\bUNLOAD\s*\("]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"redshift": "redshift"}
FEDERATION = DbType("redshift", "Amazon Redshift", 5439, "database")
FEDERATION_FOR = {"redshift": "redshift"}

DATABASE = "redshift"  # database type
INVENTORY_QUERY = """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, c.character_maximum_length AS max_length, c.numeric_precision AS precision,
       c.numeric_scale AS scale, c.is_nullable, i.tbl_rows AS row_count, i.size AS size_mb
FROM svv_columns c
LEFT JOIN svv_table_info i ON i."schema" = c.table_schema AND i."table" = c.table_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_internal')
ORDER BY 1, 2, 4;"""
HOW_TO_SAVE = 'Query editor v2: run, then Export > CSV.'
