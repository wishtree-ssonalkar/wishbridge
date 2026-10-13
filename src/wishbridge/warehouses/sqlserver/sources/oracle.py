"""Oracle used as a data warehouse (often Exadata, PL/SQL loads).

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import re

from .base import ERROR, I, M, ORACLE, WARNING, DbType, Detector, Source

SOURCES = [Source("oracle", "Oracle", "morph", "oracle")]
LABELS = {"oracle": "Oracle data warehouse"}
HINTS = {"oracle": [r"\bVARCHAR2\b", r"\bNUMBER\s*\(", r"\bNVL\s*\(", r"\bCREATE\s+OR\s+REPLACE\s+PACKAGE\b", r"\bSYSDATE\b", r"\bDBMS_\w+\.", r"\bROWNUM\b", r"^\s*/\s*$"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"oracle": "oracle"}
FEDERATION = DbType("oracle", "Oracle", 1521, "service_name", catalog_label="Service name")
FEDERATION_FOR = {"oracle": "oracle"}

DATABASE = "oracle"  # database type
INVENTORY_QUERY = """SELECT c.owner AS schema_name, c.table_name, c.column_name, c.column_id AS ordinal, c.data_type,
       c.data_length AS max_length, c.data_precision AS precision, c.data_scale AS scale,
       CASE c.nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable,
       t.num_rows AS row_count,            -- from optimizer statistics (run DBMS_STATS first for exact counts)
       ROUND(t.blocks * 8192 / 1048576, 2) AS size_mb
FROM all_tab_columns c
JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
WHERE c.owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS', 'OUTLN', 'DBSNMP', 'WMSYS', 'APEX_PUBLIC_USER',
                      'LBACSYS', 'OLAPSYS', 'GSMADMIN_INTERNAL', 'AUDSYS', 'DVSYS', 'OJVMSYS', 'APPQOSSYS')
ORDER BY c.owner, c.table_name, c.column_id;"""
HOW_TO_SAVE = 'SQL Developer: run as script, right-click the result > Export > csv. SQL*Plus 12.2+: SET MARKUP CSV ON, SPOOL inventory.csv, run, SPOOL OFF.'
TYPES = {"date": "TIMESTAMP", "float": "DOUBLE"}  # where a type name means something else here than in the shared map

# Checks on the converted code (rules.py)
DETECTORS: list[Detector] = [
    Detector("rownum", ERROR, ORACLE, re.compile(r"\bROWNUM\b", I),
             "ROWNUM is not supported: use LIMIT n, or ROW_NUMBER() OVER (...)."),
    Detector("connect-by", ERROR, ORACLE, re.compile(r"\bCONNECT\s+BY\b|\bSTART\s+WITH\b(?!\s*\d)", I),
             "Hierarchical query (START WITH / CONNECT BY): rewrite as a recursive CTE (WITH RECURSIVE)."),
    Detector("outer-join-plus", ERROR, ORACLE, re.compile(r"\(\s*\+\s*\)"),
             "Oracle (+) outer join: rewrite as LEFT/RIGHT OUTER JOIN ... ON."),
    Detector("sequence", ERROR, ORACLE, re.compile(r"\bCREATE\s+SEQUENCE\b|\.\s*(?:NEXTVAL|CURRVAL)\b", I),
             "Sequences are not supported: use an IDENTITY column (GENERATED ALWAYS AS IDENTITY)."),
    Detector("plsql-package", ERROR, ORACLE, re.compile(r"\b(?:DBMS|UTL)_\w+\.\w+", I),
             "Oracle package call {m}: remove it or replace with Databricks equivalents (e.g. SELECT for output)."),
    Detector("plsql-cursor-attr", ERROR, ORACLE, re.compile(r"\bSQL%(?:ROWCOUNT|FOUND|NOTFOUND|ISOPEN)\b", I),
             "{m} is not supported: read num_affected_rows from the statement result."),
    Detector("oracle-date-column", WARNING, ORACLE,
             re.compile(r"^\s*`?\w+`?\s+DATE\b(?!\s*\()(?=[^\n]*(?:,|NOT\s+NULL|DEFAULT|$))", I | M),
             "Oracle DATE also stores the time of day; a Databricks DATE column drops it. Use TIMESTAMP unless the "
             "column really holds dates only."),
    Detector("oracle-date-mask", WARNING, ORACLE,
             # function names in any case; mask tokens case-sensitive (Oracle masks are upper-case, Java's are not)
             re.compile(r"\b(?i:TO_CHAR|TO_DATE|TO_TIMESTAMP|DATE_FORMAT)\s*\([^;]*?'[^']*(?:YYYY|HH24|\bMI\b|\bRR\b|\bMON\b)[^']*'"),
             "Oracle date format mask in {m}...: Databricks uses Java patterns (yyyy-MM-dd HH:mm:ss).", raw=True),
]
