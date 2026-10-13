"""Google BigQuery.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

from .base import Source

SOURCES = [Source("bigquery", "BigQuery", "morph", "bigquery")]
LABELS = {"bigquery": "Google BigQuery"}
HINTS = {"bigquery": [r"\bINT64\b", r"\bSTRUCT\s*<", r"\bARRAY\s*<", r"`[\w-]+\.[\w-]+\.[\w-]+`", r"\bSAFE_CAST\s*\(", r"\bTIMESTAMP_(?:DIFF|SUB|ADD|TRUNC)\s*\(", r"\bSELECT\s+\*\s+EXCEPT\s*\(", r"\bPARTITION\s+BY\s+DATE\s*\(", r"\bDATE_(?:DIFF|SUB|ADD)\s*\([^)]*\bINTERVAL\b"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"bigquery": "bigquery"}
FEDERATION = None  # Lakehouse Federation has no password connection for it: export files instead
FEDERATION_FOR = {"bigquery": None}

DATABASE = "bigquery"  # database type
INVENTORY_QUERY = """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, NULL AS max_length, NULL AS precision, NULL AS scale, c.is_nullable,
       s.total_rows AS row_count, ROUND(s.total_logical_bytes / 1048576, 2) AS size_mb
FROM `region-us`.INFORMATION_SCHEMA.COLUMNS c          -- replace region-us with your dataset region
LEFT JOIN `region-us`.INFORMATION_SCHEMA.TABLE_STORAGE s
  ON s.table_schema = c.table_schema AND s.table_name = c.table_name
ORDER BY 1, 2, 4;"""
HOW_TO_SAVE = 'Console: run (replace region-us with your region), then Save results > CSV. bq: bq query --use_legacy_sql=false --format=csv --max_rows=1000000 < this.sql > inventory.csv'
TYPES = {"datetime": "TIMESTAMP_NTZ", "numeric": "DECIMAL(38,9)", "bignumeric": "DECIMAL(38,9)", "struct": "VARIANT", "record": "VARIANT"}  # where a type name means something else here than in the shared map
