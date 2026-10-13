"""SQL Server and Azure SQL: the most common warehouse WishBridge migrates.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import re

from .base import ERROR, INFO, TERADATA, TSQL, WARNING, DbType, Detector, Source, I, M

SOURCES = [Source("mssql", "MS SQL Server", "morph", "mssql")]
LABELS = {"mssql": "SQL Server data warehouse"}
HINTS = {"mssql": [r"\[dbo\]\.", r"^\s*GO\s*$", r"\bNVARCHAR\b", r"@@ROWCOUNT|@@IDENTITY|@@ERROR", r"\bIDENTITY\s*\(\s*\d", r"\bGETDATE\s*\(", r"\bSET\s+NOCOUNT\b", r"\bNOLOCK\b"]}  # keywords that point at this dialect (discover.py)
DEFAULT_DB = {"mssql": "sqlserver"}
FEDERATION = DbType("sqlserver", "Microsoft SQL Server / Azure SQL", 1433, "database")
FEDERATION_FOR = {"mssql": "sqlserver"}

DATABASE = "sqlserver"  # database type
INVENTORY_QUERY = """SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, c.column_id AS ordinal,
       ty.name AS data_type, c.max_length, c.precision, c.scale,
       CASE WHEN c.is_nullable = 1 THEN 'YES' ELSE 'NO' END AS is_nullable,
       (SELECT SUM(p.rows) FROM sys.partitions p WHERE p.object_id = t.object_id AND p.index_id IN (0, 1)) AS row_count,
       (SELECT CAST(SUM(a.total_pages) * 8 / 1024.0 AS DECIMAL(18, 2)) FROM sys.partitions p
          JOIN sys.allocation_units a ON a.container_id = p.partition_id WHERE p.object_id = t.object_id) AS size_mb
FROM sys.tables t
JOIN sys.schemas s ON s.schema_id = t.schema_id
JOIN sys.columns c ON c.object_id = t.object_id
JOIN sys.types ty ON ty.user_type_id = c.user_type_id
WHERE t.is_ms_shipped = 0
ORDER BY s.name, t.name, c.column_id;"""
HOW_TO_SAVE = 'SSMS: Query > Results To > Results to File, run, save as .csv - or right-click the result grid > Save Results As (CSV). sqlcmd: sqlcmd -S <server> -d <db> -E -i this.sql -s "," -W -o inventory.csv'
TYPES = {"timestamp": "BINARY"}  # where a type name means something else here than in the shared map

# Safe tidy-ups of the source before conversion (staging.py): generated (SSDT / SSMS) scripts use patterns the
# converter fails on. (key, severity, pattern, replacement, message)
PREP: list[tuple[str, str, re.Pattern, str, str]] = [
    ("with-check", "info", re.compile(r"\bWITH\s+(?:NO)?CHECK\s+(ADD\s+CONSTRAINT)\b", I), r"\1",
     "Removed WITH CHECK / WITH NOCHECK from ADD CONSTRAINT (it only controls validation of existing rows)"),
    ("check-constraint", "info",
     re.compile(r"^[ \t]*ALTER\s+TABLE\s+[^\n;]*?\s(?:NO)?CHECK\s+CONSTRAINT\s+[^\n;]*;?[ \t]*\r?\n?", I | M), "",
     "Removed ALTER TABLE ... CHECK / NOCHECK CONSTRAINT (enabling or disabling constraints has no Databricks equivalent)"),
    ("temporal-columns", "warning", re.compile(r"\s+GENERATED\s+ALWAYS\s+AS\s+ROW\s+(?:START|END)(?:\s+HIDDEN)?", I), "",
     "Removed system-versioning (temporal table) clauses before conversion: Delta keeps history itself - review the "
     "period columns, drop the history table and query history with VERSION AS OF / TIMESTAMP AS OF"),
    ("temporal-period", "warning", re.compile(r",?\s*PERIOD\s+FOR\s+SYSTEM_TIME\s*\([^)]*\)", I), "", ""),
    ("temporal-versioning", "warning",
     re.compile(r"\s*WITH\s*\(\s*SYSTEM_VERSIONING\s*=\s*ON(?:\s*\([^()]*\))?\s*\)", I), "", ""),
]

# Procedures, views, functions and triggers stored in the database (dbcode.py)
CODE_QUERY = """SELECT s.name AS schema_name, o.name AS object_name, RTRIM(o.type) AS type,
       m.definition, CAST(ISNULL(OBJECTPROPERTY(o.object_id, 'IsEncrypted'), 0) AS int) AS encrypted
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('P', 'V', 'FN', 'IF', 'TF', 'TR')
ORDER BY s.name, o.type, o.name"""

# Safe, mechanical rewrites of the converted code (rules.py): (rule, dialects, pattern, replacement, note)
FIXES: list[tuple[str, frozenset | None, re.Pattern, str, str]] = [
    ("bracket-identifier", TSQL, re.compile(r"\[([A-Za-z_#@][^\]\n]*)\]"), r"`\1`",
     "Converted [bracketed] identifier to `backticks`"),
    ("nolock-hint", TSQL, re.compile(r"\s*\bWITH\s*\(\s*NOLOCK\s*\)|\s*\(\s*NOLOCK\s*\)", I), "",
     "Removed NOLOCK hint (Delta uses snapshot isolation)"),
    ("getdate", TSQL, re.compile(r"\b(?:GETDATE|SYSDATETIME)\s*\(\s*\)", I),
     "CURRENT_TIMESTAMP()", "Normalised current-time function to CURRENT_TIMESTAMP()"),
    ("getutcdate", TSQL, re.compile(r"\b(?:GETUTCDATE|SYSUTCDATETIME)\s*\(\s*\)", I),
     "to_utc_timestamp(CURRENT_TIMESTAMP(), current_timezone())", "Converted UTC current-time function"),
    ("set-nocount", TSQL, re.compile(r"^[ \t]*SET\s+NOCOUNT\s+(?:ON|OFF)\s*;?[ \t]*$", I | M), "",
     "Removed SET NOCOUNT (no row-count messages in Databricks)"),
    ("go-separator", TSQL, re.compile(r"^[ \t]*GO[ \t]*$", I | M), "",
     "Removed GO batch separator"),
    ("count-big", TSQL, re.compile(r"\bCOUNT_BIG\s*\(", I), "COUNT(",
     "Replaced COUNT_BIG with COUNT (Databricks COUNT already returns BIGINT)"),
]

# Checks on the converted code (rules.py). Some also apply to Teradata (TOP) and SSIS (BIT columns).
DETECTORS: list[Detector] = [
    Detector("system-variable", ERROR, TSQL, re.compile(r"@@\w+(?:\s*\(\s*\))?"),
             "T-SQL system variable {m} is not supported. For row counts, read num_affected_rows from the statement result."),
    Detector("local-variable", WARNING, TSQL, re.compile(r"(?<![@\w])@[A-Za-z_]\w*"),
             "Local variable {m} is not valid Databricks SQL. Use DECLARE VARIABLE / procedure parameters."),
    Detector("temp-table", ERROR, TSQL, re.compile(r"(?<![\w#`])##?[A-Za-z_]\w*"),
             "Temp table {m}: use a TEMPORARY VIEW or a scratch Delta table."),
    Detector("temp-table-name", WARNING, TSQL, re.compile(r"`##?[A-Za-z_]\w*`"),
             "Temp table converted as {m}: confirm temporary tables are enabled on your warehouse, or use a TEMPORARY VIEW."),
    Detector("top-clause", ERROR, TSQL | TERADATA, re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s*\(?\s*\d+", I),
             "Unconverted TOP clause: use LIMIT."),
    Detector("output-clause", ERROR, TSQL, re.compile(r"\bOUTPUT\s+(?:INSERTED|DELETED)\.", I),
             "OUTPUT INSERTED/DELETED is not supported: query the table or use Change Data Feed."),
    Detector("raiserror", WARNING, TSQL, re.compile(r"\bRAISERROR\b|\bTHROW\s+\d", I),
             "Error raising: use SIGNAL SQLSTATE or raise_error()."),
    Detector("identity-insert", WARNING, TSQL, re.compile(r"\bIDENTITY_INSERT\b", I),
             "IDENTITY_INSERT is not needed: GENERATED BY DEFAULT AS IDENTITY accepts explicit values."),
    Detector("full-text-search", ERROR, TSQL,
             re.compile(r"\bCREATE\s+FULLTEXT\b|\b(?:CONTAINS|FREETEXT)\s*\(|\b(?:CONTAINSTABLE|FREETEXTTABLE)\b", I),
             "SQL Server full-text search ({m}): Databricks has no full-text index, and its contains() is a plain "
             "substring test, so results silently differ. Rewrite with ILIKE / rlike, or use Databricks Vector Search."),
    Detector("temporal-table", ERROR, TSQL,
             re.compile(r"\bPERIOD\s+FOR\s+SYSTEM_TIME\b|\bSYSTEM_VERSIONING\b|\bGENERATED\s+ALWAYS\s+AS\s+ROW\s+(?:START|END)\b|"
                        r"\bFOR\s+SYSTEM_TIME\s+(?:AS\s+OF|BETWEEN|FROM|ALL)\b", I),
             "System-versioned (temporal) table: Delta keeps history itself - drop the period columns and history "
             "table, and query history with VERSION AS OF / TIMESTAMP AS OF (or Change Data Feed)."),
    Detector("system-catalog", WARNING, TSQL, re.compile(r"\bsys\.\w+|\bOBJECT_ID\s*\(|\bINFORMATION_SCHEMA\.\w+", I),
             "SQL Server system catalog ({m}): use information_schema / SHOW / DESCRIBE, or IF [NOT] EXISTS on the "
             "CREATE / DROP statement."),
    Detector("convert-leftover", WARNING, TSQL, re.compile(r"\b(?:TRY_)?CONVERT\s*\(\s*\w+", I),
             "Unconverted CONVERT(): use CAST / date_format."),
    Detector("bit-flag-compare", WARNING, TSQL | {"ssis"},
             re.compile(r"\b(?:[A-Za-z_]\w*\.)?`?(?:Is|Has|Can|Should|Allow)[A-Z_]\w*`?\s*(?:=|<>|!=)\s*[01]\b"),
             "{m}: SQL Server BIT columns arrive in Databricks as BOOLEAN, which cannot be compared with 1/0. "
             "If this column is a BIT, compare with true / false (or just use the column)."),
]
