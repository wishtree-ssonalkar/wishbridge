"""Source database inventory without connecting to the client's database.

WishBridge writes a read-only query for the client's DBA to run once on the dev (or production) database.
It lists every table with its columns, data types, row count and size, and the DBA sends back the result
as one CSV file. WishBridge imports it, so the team knows the full data picture - which tables to copy, how
big they are, the target table definitions - before anyone connects to the client's systems again. On the
migration visit the row counts are the baseline the copied data is checked against.

CSV columns (header required, any case): schema_name, table_name, column_name, ordinal, data_type,
max_length, precision, scale, is_nullable, row_count, size_mb.
"""

from __future__ import annotations

import csv
import io
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .state import save_step

COLUMNS = ["schema_name", "table_name", "column_name", "ordinal", "data_type", "max_length", "precision", "scale",
           "is_nullable", "row_count", "size_mb"]

# Database that holds the warehouse, per source system (ETL tools load a database; ask which one if unsure).
DEFAULT_DB = {"mssql": "sqlserver", "synapse": "synapse", "oracle": "oracle", "snowflake": "snowflake",
              "teradata": "teradata", "redshift": "redshift", "bigquery": "bigquery", "netezza": "netezza",
              "ssis": "sqlserver", "informatica": "oracle", "informatica-cloud": "oracle", "datastage": "oracle"}

HOW_TO_SAVE = {
    "sqlserver": "SSMS: Query > Results To > Results to File, run, save as .csv - or right-click the result grid > "
                 "Save Results As (CSV). sqlcmd: sqlcmd -S <server> -d <db> -E -i this.sql -s \",\" -W -o inventory.csv",
    "synapse": "SSMS / Azure Data Studio: run, then save the result grid as CSV.",
    "oracle": "SQL Developer: run as script, right-click the result > Export > csv. "
              "SQL*Plus 12.2+: SET MARKUP CSV ON, SPOOL inventory.csv, run, SPOOL OFF.",
    "snowflake": "Snowsight: run, then Download results (CSV). SnowSQL: snowsql -f this.sql -o output_format=csv "
                 "-o header=true -o timing=false -o friendly=false > inventory.csv",
    "teradata": "Teradata Studio / SQL Assistant: run, then export the result as CSV.",
    "redshift": "Query editor v2: run, then Export > CSV.",
    "bigquery": "Console: run (replace region-us with your region), then Save results > CSV. "
                "bq: bq query --use_legacy_sql=false --format=csv --max_rows=1000000 < this.sql > inventory.csv",
    "netezza": "nzsql -d <db> -A -F ',' -f this.sql -o inventory.csv (add the header line if your client omits it).",
}

_SQLSERVER = """SELECT s.name AS schema_name, t.name AS table_name, c.name AS column_name, c.column_id AS ordinal,
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

QUERIES = {
    "sqlserver": _SQLSERVER,
    "synapse": _SQLSERVER.replace("p.index_id IN (0, 1)", "p.index_id IN (0, 1)  /* per distribution: approximate */"),
    "oracle": """SELECT c.owner AS schema_name, c.table_name, c.column_name, c.column_id AS ordinal, c.data_type,
       c.data_length AS max_length, c.data_precision AS precision, c.data_scale AS scale,
       CASE c.nullable WHEN 'Y' THEN 'YES' ELSE 'NO' END AS is_nullable,
       t.num_rows AS row_count,            -- from optimizer statistics (run DBMS_STATS first for exact counts)
       ROUND(t.blocks * 8192 / 1048576, 2) AS size_mb
FROM all_tab_columns c
JOIN all_tables t ON t.owner = c.owner AND t.table_name = c.table_name
WHERE c.owner NOT IN ('SYS', 'SYSTEM', 'XDB', 'MDSYS', 'CTXSYS', 'ORDSYS', 'OUTLN', 'DBSNMP', 'WMSYS', 'APEX_PUBLIC_USER',
                      'LBACSYS', 'OLAPSYS', 'GSMADMIN_INTERNAL', 'AUDSYS', 'DVSYS', 'OJVMSYS', 'APPQOSSYS')
ORDER BY c.owner, c.table_name, c.column_id;""",
    "snowflake": """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, c.character_maximum_length AS max_length, c.numeric_precision AS precision,
       c.numeric_scale AS scale, c.is_nullable, t.row_count, ROUND(t.bytes / 1048576, 2) AS size_mb
FROM information_schema.columns c
JOIN information_schema.tables t
  ON t.table_schema = c.table_schema AND t.table_name = c.table_name
WHERE t.table_type = 'BASE TABLE' AND c.table_schema <> 'INFORMATION_SCHEMA'
ORDER BY 1, 2, 4;""",
    "teradata": """SELECT TRIM(c.DatabaseName) AS schema_name, TRIM(c.TableName) AS table_name, TRIM(c.ColumnName) AS column_name,
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
ORDER BY 1, 2, 4;""",
    "redshift": """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, c.character_maximum_length AS max_length, c.numeric_precision AS precision,
       c.numeric_scale AS scale, c.is_nullable, i.tbl_rows AS row_count, i.size AS size_mb
FROM svv_columns c
LEFT JOIN svv_table_info i ON i."schema" = c.table_schema AND i."table" = c.table_name
WHERE c.table_schema NOT IN ('pg_catalog', 'information_schema', 'pg_internal')
ORDER BY 1, 2, 4;""",
    "bigquery": """SELECT c.table_schema AS schema_name, c.table_name, c.column_name, c.ordinal_position AS ordinal,
       c.data_type, NULL AS max_length, NULL AS precision, NULL AS scale, c.is_nullable,
       s.total_rows AS row_count, ROUND(s.total_logical_bytes / 1048576, 2) AS size_mb
FROM `region-us`.INFORMATION_SCHEMA.COLUMNS c          -- replace region-us with your dataset region
LEFT JOIN `region-us`.INFORMATION_SCHEMA.TABLE_STORAGE s
  ON s.table_schema = c.table_schema AND s.table_name = c.table_name
ORDER BY 1, 2, 4;""",
    "netezza": """SELECT c.SCHEMA AS schema_name, c.NAME AS table_name, c.ATTNAME AS column_name, c.ATTNUM AS ordinal,
       c.FORMAT_TYPE AS data_type, NULL AS max_length, NULL AS precision, NULL AS scale,
       CASE WHEN c.ATTNOTNULL THEN 'NO' ELSE 'YES' END AS is_nullable,
       t.RELTUPLES AS row_count, NULL AS size_mb
FROM _V_RELATION_COLUMN c
JOIN _V_TABLE t ON t.OBJID = c.OBJID
WHERE c.TYPE = 'TABLE'
ORDER BY 1, 2, 4;""",
}


def script(cfg: ProjectConfig, db: str | None = None) -> tuple[str, str]:
    """(file name, text) of the inventory query for the client's DBA."""
    db = db or (cfg.source_db or {}).get("type") or DEFAULT_DB.get(cfg.source.key, "sqlserver")
    if db not in QUERIES:
        raise ValueError(f"No inventory query for '{db}'. Choose one of: {', '.join(QUERIES)}")
    header = (f"-- WishBridge source inventory for project '{cfg.name}' ({db}).\n"
              "-- READ-ONLY: it lists tables, columns, data types, row counts and sizes from the database catalog.\n"
              "-- It reads no business data and changes nothing. Run it once on the dev database and send back\n"
              "-- the result as one CSV file with the column names in the first row.\n"
              f"-- How to save the result as CSV: {HOW_TO_SAVE[db]}\n\n")
    return f"wishbridge_inventory_{db}.sql", header + QUERIES[db] + "\n"


def _num(v: Any) -> float | None:
    try:
        return float(str(v).replace(",", "")) if str(v).strip() not in ("", "NULL", "None") else None
    except ValueError:
        return None


def parse(text: str) -> dict[str, Any]:
    """Tables with their columns, row count and size from the DBA's CSV."""
    text = text.lstrip("﻿")
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    rows = list(csv.DictReader(io.StringIO(text), dialect=dialect))
    if not rows:
        raise ValueError("The inventory file has no rows")
    keys = {k.strip().lower(): k for k in rows[0].keys() if k}
    missing = [c for c in ("schema_name", "table_name", "column_name", "data_type") if c not in keys]
    if missing:
        raise ValueError(f"The inventory file has no {', '.join(missing)} column(s); it needs the header row "
                         f"{', '.join(COLUMNS)}")

    def get(r: dict, col: str) -> str:
        k = keys.get(col)
        return (r.get(k) or "").strip() if k else ""

    tables: dict[tuple[str, str], dict[str, Any]] = {}
    for r in rows:
        key = (get(r, "schema_name"), get(r, "table_name"))
        if not key[1]:
            continue
        t = tables.setdefault(key, {"schema": key[0], "table": key[1], "rows": _num(get(r, "row_count")),
                                    "size_mb": _num(get(r, "size_mb")), "columns": []})
        t["columns"].append({"name": get(r, "column_name"), "type": get(r, "data_type"),
                             "length": _num(get(r, "max_length")), "precision": _num(get(r, "precision")),
                             "scale": _num(get(r, "scale")), "nullable": get(r, "is_nullable").upper() in ("YES", "Y", "1", "TRUE")})
    out = sorted(tables.values(), key=lambda t: (-(t["size_mb"] or 0), -(t["rows"] or 0), t["schema"], t["table"]))
    return {
        "tables": out,
        "summary": {"tables": len(out), "columns": sum(len(t["columns"]) for t in out),
                    "rows": int(sum(t["rows"] or 0 for t in out)),
                    "size_mb": round(sum(t["size_mb"] or 0 for t in out), 1),
                    "schemas": sorted({t["schema"] for t in out})},
    }


def import_file(cfg: ProjectConfig, path: str | Path) -> dict[str, Any]:
    """Keep the DBA's file in the project and record the inventory."""
    path = Path(path)
    from .staging import read_source

    result = parse(read_source(path))
    dest = cfg.out("inventory", "inventory.csv")
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    result.update(file=str(dest), imported_at=datetime.now().isoformat(timespec="seconds"), source_file=str(path))
    save_step(cfg, "inventory", result)
    return result


def table_list(inv: dict[str, Any]) -> list[str]:
    """`schema.table` names for data.tables in project.yml."""
    return [f"{t['schema']}.{t['table']}" if t["schema"] else t["table"] for t in inv.get("tables", [])]
