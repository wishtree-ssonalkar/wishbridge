"""Databricks table scripts from the source database's catalog (when the client has no table scripts).

The table list comes from the inventory (see inventory.py): either the DBA ran the read-only query and sent
back the CSV, or WishBridge read the catalog directly (`read_live`). Only the catalog is read - table and
column names, data types, row counts - never business data.

For every table that the client's code does not already create, WishBridge writes a
`CREATE TABLE IF NOT EXISTS` statement with the matching Databricks types, one file per source schema, in
output/database_schema/. Convert adds these files to the converted code (so they are in the package and
ready to review) and deploy runs them before everything else, so the tables exist before data is loaded.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .state import load_state, save_step

FOLDER = "_database_schema"  # name of the generated files inside the converted code

_STRING = "STRING"
# Type names shared by most databases (lower case, without length/precision).
_COMMON = {
    "bit": "BOOLEAN", "bool": "BOOLEAN", "boolean": "BOOLEAN",
    "tinyint": "SMALLINT",  # SQL Server tinyint is 0-255; Databricks TINYINT stops at 127
    "byteint": "TINYINT", "smallint": "SMALLINT", "int2": "SMALLINT",
    "int": "INT", "integer": "INT", "int4": "INT", "mediumint": "INT",
    "bigint": "BIGINT", "int8": "BIGINT", "int64": "BIGINT",
    "real": "FLOAT", "float4": "FLOAT", "binary_float": "FLOAT",
    "float": "DOUBLE", "float8": "DOUBLE", "double": "DOUBLE", "double precision": "DOUBLE", "float64": "DOUBLE",
    "binary_double": "DOUBLE",
    "money": "DECIMAL(19,4)", "smallmoney": "DECIMAL(10,4)",
    "date": "DATE",
    "datetime": "TIMESTAMP", "datetime2": "TIMESTAMP", "smalldatetime": "TIMESTAMP", "datetimeoffset": "TIMESTAMP",
    "timestamp": "TIMESTAMP", "timestamptz": "TIMESTAMP", "timestamp with time zone": "TIMESTAMP",
    "timestamp without time zone": "TIMESTAMP", "timestamp with local time zone": "TIMESTAMP",
    "timestamp_ntz": "TIMESTAMP_NTZ", "timestamp_ltz": "TIMESTAMP", "timestamp_tz": "TIMESTAMP",
    "time": _STRING, "time without time zone": _STRING, "time with time zone": _STRING, "timetz": _STRING,
    "char": _STRING, "nchar": _STRING, "varchar": _STRING, "nvarchar": _STRING, "varchar2": _STRING,
    "nvarchar2": _STRING, "character": _STRING, "character varying": _STRING, "national character varying": _STRING,
    "bpchar": _STRING, "text": _STRING, "ntext": _STRING, "string": _STRING, "clob": _STRING, "nclob": _STRING,
    "long": _STRING, "tinytext": _STRING, "mediumtext": _STRING, "longtext": _STRING, "xml": _STRING,
    "xmltype": _STRING, "json": _STRING, "jsonb": _STRING, "uniqueidentifier": _STRING, "uuid": _STRING,
    "sysname": _STRING, "sql_variant": _STRING, "hierarchyid": _STRING, "geography": _STRING, "geometry": _STRING,
    "rowid": _STRING, "urowid": _STRING, "interval": _STRING, "super": _STRING, "enum": _STRING, "set": _STRING,
    "variant": "VARIANT", "object": "VARIANT", "array": "VARIANT",
    "binary": "BINARY", "varbinary": "BINARY", "image": "BINARY", "raw": "BINARY", "long raw": "BINARY",
    "blob": "BINARY", "bytea": "BINARY", "bytes": "BINARY", "rowversion": "BINARY", "varbyte": "BINARY",
    "byte": "BINARY", "tinyblob": "BINARY", "mediumblob": "BINARY", "longblob": "BINARY",
}
_DECIMAL = {"decimal", "numeric", "number", "dec", "bignumeric", "bigdecimal"}

# Per database, where a name means something else than in _COMMON.
_SPECIAL = {
    "sqlserver": {"timestamp": "BINARY"},  # SQL Server timestamp = rowversion
    "synapse": {"timestamp": "BINARY"},
    "oracle": {"date": "TIMESTAMP", "float": "DOUBLE"},  # Oracle DATE keeps the time of day
    "bigquery": {"datetime": "TIMESTAMP_NTZ", "numeric": "DECIMAL(38,9)", "bignumeric": "DECIMAL(38,9)",
                 "struct": "VARIANT", "record": "VARIANT"},
    "snowflake": {"datetime": "TIMESTAMP_NTZ", "timestamp": "TIMESTAMP_NTZ"},
}
# Teradata stores type codes in DBC.ColumnsV.ColumnType.
_TERADATA = {"CV": _STRING, "CF": _STRING, "CO": _STRING, "JN": _STRING, "XM": _STRING, "I": "INT", "I1": "TINYINT",
             "I2": "SMALLINT", "I8": "BIGINT", "F": "DOUBLE", "DA": "DATE", "TS": "TIMESTAMP", "SZ": "TIMESTAMP",
             "AT": _STRING, "TZ": _STRING, "BF": "BINARY", "BV": "BINARY", "BO": "BINARY", "PD": _STRING}


def _int(v: Any) -> int | None:
    try:
        return int(float(v)) if v not in (None, "") else None
    except (TypeError, ValueError):
        return None


def _decimal(precision: Any, scale: Any) -> str:
    p, s = _int(precision), _int(scale)
    if not p:
        return "DECIMAL(38,10)"  # precision not declared (e.g. Oracle NUMBER): wide enough for most values
    p = min(p, 38)
    s = max(0, min(s or 0, p))
    return f"DECIMAL({p},{s})"


def databricks_type(db: str, data_type: str, precision: Any = None, scale: Any = None) -> tuple[str, bool]:
    """Databricks type for a source column type; the flag is False when the type is unknown (STRING used)."""
    raw = (data_type or "").strip()
    args = re.search(r"\(\s*(\d+)\s*(?:,\s*(\d+)\s*)?\)", raw)
    if args and precision in (None, ""):
        precision, scale = args.group(1), args.group(2)
    if db == "teradata" and raw.upper() in _TERADATA:
        return _TERADATA[raw.upper()], True
    if db == "teradata" and raw.upper() in ("D", "N"):
        return _decimal(precision, scale), True
    name = re.sub(r"\s*\(.*$", "", raw).strip().lower()
    name = re.sub(r"\s+", " ", re.sub(r"\(\d+\)", "", name))
    if name.startswith("timestamp") and "time zone" in raw.lower():
        name = "timestamp with time zone" if "without" not in raw.lower() else "timestamp without time zone"
    elif name.startswith("timestamp("):
        name = "timestamp"
    special = _SPECIAL.get(db, {})
    if name in special:
        return special[name], True
    if name in _DECIMAL:
        return _decimal(precision, scale), True
    if name in _COMMON:
        if name == "float" and db in ("sqlserver", "synapse") and _int(precision) and _int(precision) <= 24:
            return "FLOAT", True
        return _COMMON[name], True
    if name.startswith("interval"):
        return _STRING, True
    return _STRING, False


def _quote(name: str) -> str:
    return "`" + name.replace("`", "``") + "`"


def _quote_target(target: str) -> str:
    return ".".join(_quote(p) for p in target.split("."))


def defined_in_code(cfg: ProjectConfig) -> tuple[set[str], set[str]]:
    """Tables the client's code creates, lower case: (schema.table names, names written without a schema)."""
    from .overview import parse_tables
    from .staging import read_source, source_files

    qualified: set[str] = set()
    bare: set[str] = set()
    for rel in source_files(cfg):
        if rel.suffix.lower() not in (".sql", ".ddl"):
            continue
        for t in parse_tables(read_source(cfg.input_dir / rel)):
            parts = [p for p in t["name"].lower().split(".") if p]
            if len(parts) >= 2:
                qualified.add(".".join(parts[-2:]))
            elif parts:
                bare.add(parts[0])
    return qualified, bare


def _already_defined(schema: str, table: str, code: tuple[set[str], set[str]]) -> bool:
    qualified, bare = code
    return f"{schema}.{table}".lower() in qualified or table.lower() in bare


def create_table_sql(cfg: ProjectConfig, db: str, t: dict[str, Any]) -> tuple[str, list[str]]:
    """CREATE TABLE IF NOT EXISTS for one inventory table; also returns the columns whose type was unknown."""
    source = f"{t['schema']}.{t['table']}" if t["schema"] else t["table"]
    cols = list(t["columns"])
    width = max((len(c["name"]) for c in cols), default=0) + 2
    lines, unknown = [], []
    for i, c in enumerate(cols):
        dtype, known = databricks_type(db, c["type"], c.get("precision"), c.get("scale"))
        if not known:
            unknown.append(f"{c['name']} ({c['type']})")
        comma = "," if i < len(cols) - 1 else ""
        note = "" if known else f"  -- review: source type {c['type']}"
        null = "" if c.get("nullable", True) else " NOT NULL"
        lines.append(f"  {_quote(c['name']).ljust(width)} {dtype}{null}{comma}{note}")
    rows = f"{int(t['rows']):,} rows" if t.get("rows") is not None else "row count unknown"
    return (f"-- {source} ({rows})\nCREATE TABLE IF NOT EXISTS {_quote_target(cfg.map_table(source))} (\n"
            + "\n".join(lines) + "\n);"), unknown


def database_type(cfg: ProjectConfig, inv: dict[str, Any]) -> str:
    from .inventory import DEFAULT_DB

    return inv.get("database_type") or (cfg.source_db or {}).get("type") or DEFAULT_DB.get(cfg.source.key, "sqlserver")


def output_folder(cfg: ProjectConfig) -> Path:
    return cfg.output_dir / "database_schema"


def build_scripts(cfg: ProjectConfig, inv: dict[str, Any] | None = None) -> dict[str, Any]:
    """Write output/database_schema/<schema>.sql for the tables the client's code does not create."""
    inv = inv or load_state(cfg).get("inventory")
    if not inv or not inv.get("tables"):
        raise ValueError("No database tables yet: read the schema from the database or import the DBA's CSV first.")
    db = database_type(cfg, inv)
    code = defined_in_code(cfg)
    folder = output_folder(cfg)
    if folder.exists():
        for old in folder.glob("*.sql"):
            old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    per_schema: dict[str, list[str]] = {}
    skipped, unknown = [], {}
    for t in inv["tables"]:
        if _already_defined(t["schema"], t["table"], code):
            skipped.append(f"{t['schema']}.{t['table']}")
            continue
        sql, bad = create_table_sql(cfg, db, t)
        per_schema.setdefault(t["schema"] or "default", []).append(sql)
        if bad:
            unknown[f"{t['schema']}.{t['table']}"] = bad
    stamp = datetime.now().isoformat(timespec="seconds")
    files = []
    for schema, stmts in sorted(per_schema.items()):
        path = folder / f"{re.sub(r'[^A-Za-z0-9_.-]', '_', schema)}.sql"
        path.write_text(
            f"-- Databricks tables for source schema '{schema}', made by WishBridge from the database catalog ({stamp}).\n"
            "-- Target names follow the schema mapping in Settings. Tables the client's scripts already create are\n"
            "-- not repeated here. Lines marked 'review' had a source type WishBridge does not know (STRING used).\n\n"
            + "\n\n".join(stmts) + "\n", encoding="utf-8")
        files.append({"file": f"{FOLDER}/{path.name}", "path": str(path), "tables": len(stmts)})
    result = {"database_type": db, "tables": sum(f["tables"] for f in files), "skipped": skipped,
              "unknown_types": unknown, "files": files, "written_at": stamp}
    save_step(cfg, "schema", result)
    return result


def scripts(cfg: ProjectConfig) -> list[dict[str, Any]]:
    """The generated files that still exist (for convert and deploy)."""
    state = load_state(cfg).get("schema") or {}
    return [f for f in state.get("files", []) if Path(f["path"]).is_file()]
