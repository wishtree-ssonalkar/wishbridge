"""Teradata.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import re

from .base import ERROR, I, M, TERADATA, WARNING, DbType, Detector, Source

SOURCES = [Source("teradata", "Teradata", "bladebridge", "teradata")]
LABELS = {"teradata": "Teradata"}
HINTS = {"teradata": [r"\bMULTISET\b", r"\bPRIMARY\s+INDEX\b", r"^\s*SEL\b", r"^\s*\.(?:LOGON|QUIT|IF)\b", r"\bCOLLECT\s+STAT", r"\bVOLATILE\s+TABLE\b"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"teradata": "teradata"}
FEDERATION = DbType("teradata", "Teradata", 1025, None)
FEDERATION_FOR = {"teradata": "teradata"}

DATABASE = "teradata"  # database type
INVENTORY_QUERY = """SELECT TRIM(c.DatabaseName) AS schema_name, TRIM(c.TableName) AS table_name, TRIM(c.ColumnName) AS column_name,
       c.ColumnId AS ordinal, c.ColumnType AS data_type, c.ColumnLength AS max_length,
       c.DecimalTotalDigits AS precision, c.DecimalFractionalDigits AS scale,
       CASE WHEN c.Nullable = 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable,
       NULL AS row_count,                  -- not in the dictionary; WishBridge counts rows on the migration visit
       z.size_mb
FROM DBC.ColumnsV c
JOIN DBC.TablesV t ON t.DatabaseName = c.DatabaseName AND t.TableName = c.TableName AND t.TableKind = 'T'
LEFT JOIN (SELECT DatabaseName, TableName, CAST(SUM(CurrentPerm) / 1048576.0 AS DECIMAL(18, 2)) AS size_mb
           FROM DBC.TableSizeV GROUP BY 1, 2) z ON z.DatabaseName = c.DatabaseName AND z.TableName = c.TableName
WHERE c.DatabaseName NOT IN ('DBC', 'SYSLIB', 'SYSUDTLIB', 'SYSSPATIAL', 'TD_SYSFNLIB', 'SystemFe', 'SYSBAR', 'TDStats')
ORDER BY 1, 2, 4;"""
HOW_TO_SAVE = 'Teradata Studio / SQL Assistant: run, then export the result as CSV.'

# Teradata stores type codes in DBC.ColumnsV.ColumnType (schema.py)
TYPE_CODES = {"CV": "STRING", "CF": "STRING", "CO": "STRING", "JN": "STRING", "XM": "STRING", "I": "INT", "I1": "TINYINT", "I2": "SMALLINT", "I8": "BIGINT", "F": "DOUBLE", "DA": "DATE", "TS": "TIMESTAMP", "SZ": "TIMESTAMP", "AT": "STRING", "TZ": "STRING", "BF": "BINARY", "BV": "BINARY", "BO": "BINARY", "PD": "STRING"}

# Checks on the converted code (rules.py)
DETECTORS: list[Detector] = [
    Detector("bteq-command", ERROR, TERADATA, re.compile(r"^[ \t]*\.(?:LOGON|LOGOFF|QUIT|IF|GOTO|LABEL|EXPORT|IMPORT|SET|RUN|OS)\b", I | M),
             "BTEQ command {m}: move control flow into a Databricks Job or notebook."),
    Detector("teradata-table-kind", WARNING, TERADATA, re.compile(r"\b(?:VOLATILE|MULTISET|GLOBAL\s+TEMPORARY)\s+TABLE\b", I),
             "{m}: use a regular Delta table or a TEMPORARY VIEW."),
    Detector("primary-index", WARNING, TERADATA, re.compile(r"\b(?:UNIQUE\s+)?PRIMARY\s+INDEX\b", I),
             "PRIMARY INDEX has no meaning on Delta: remove it; consider liquid clustering (CLUSTER BY)."),
    Detector("collect-stats", WARNING, TERADATA, re.compile(r"\bCOLLECT\s+STAT(?:ISTIC)?S?\b", I),
             "COLLECT STATISTICS: use ANALYZE TABLE ... COMPUTE STATISTICS, or rely on predictive optimisation."),
    Detector("sel-abbrev", ERROR, TERADATA, re.compile(r"^[ \t]*SEL\b", I | M),
             "Unconverted SEL abbreviation: use SELECT."),
]
