"""Made-up sample rows for every table - built from the column definitions only, never from the client's data.

For each table (from the database inventory, or from the CREATE TABLE statements in the code) WishBridge
writes a few rows whose values fit each column's type, length, precision and NULL rule, with realistic
values where the column name says what it holds (email, name, phone, date, amount, status...). Key
columns (Id, CustomerId, ...) get 1..N, so related tables join on the same values. The last row is NULL in
every column that allows NULL, to test NULL handling.

Output, in output/dummy_data/: one CSV per table and dummy_data.sql (INSERT statements for the Databricks
tables), so converted code can be tried on Databricks before any real data is copied.
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .schema import _quote, _quote_target, databricks_type
from .state import load_state, save_step

ROWS = 5
FOLDER = "dummy_data"

_FIRST = ["Asha", "Rahul", "Maria", "John", "Wei"]
_LAST = ["Patil", "Sharma", "Garcia", "Smith", "Chen"]
_CITY = ["Pune", "Mumbai", "London", "New York", "Singapore"]
_COUNTRY = ["India", "India", "United Kingdom", "United States", "Singapore"]
_STATE = ["Maharashtra", "Karnataka", "England", "New York", "Central"]
_STATUS = ["Active", "Inactive", "Pending", "Closed", "Active"]
_COMPANY = ["Acme Ltd", "Globex Corp", "Initech", "Umbrella Inc", "Stark Industries"]
_PRODUCT = ["Laptop", "Monitor", "Keyboard", "Mouse", "Headset"]
_CATEGORY = ["Hardware", "Software", "Services", "Accessories", "Support"]


def _length(c: dict[str, Any], db: str) -> int | None:
    """Maximum characters of a text column (None: no limit known)."""
    m = re.search(r"\(\s*(max|\d+)", str(c.get("type", "")), re.IGNORECASE)
    if m:
        return None if m.group(1).lower() == "max" else int(m.group(1))
    try:
        n = int(float(c.get("length")))
    except (TypeError, ValueError):
        return None
    if n <= 0:
        return None  # -1 = MAX
    if db in ("sqlserver", "synapse") and str(c.get("type", "")).lower().startswith("n"):
        n //= 2  # SQL Server reports bytes; nchar/nvarchar use 2 per character
    return n


def _is_key(name: str) -> bool:
    n = name.lower()
    return n in ("id", "key", "pk") or bool(re.search(r"(_?id|_?key|_?no|_?num|_?code)$", n)) and not n.endswith("guid")


def _text(name: str, i: int, length: int | None, table: str) -> str:
    n = name.lower()
    picks = [
        (r"e_?mail", f"user{i + 1}@example.com"),
        (r"first_?name|given", _FIRST[i]),
        (r"last_?name|surname|family", _LAST[i]),
        (r"phone|mobile|tel|fax", f"+91 98{i:01d}{(i * 7919) % 10000000:07d}"),
        (r"company|vendor|supplier|client_?name|org", _COMPANY[i]),
        (r"product|item", _PRODUCT[i]),
        (r"category|type|group|class", _CATEGORY[i]),
        (r"^(full_?|customer_?|employee_?|emp_?|contact_?|user_?|manager_?|person_?|owner_?|staff_?|display_?)?name$",
         f"{_FIRST[i]} {_LAST[i]}"),
        (r"name$", f"{re.sub(r'_?name$', '', name, flags=re.IGNORECASE)} {chr(65 + i)}"),  # ProjectName -> Project A
        (r"city|town", _CITY[i]),
        (r"country", _COUNTRY[i]),
        (r"status", _STATUS[i]),
        (r"state|province|region", _STATE[i]),
        (r"zip|postal|pin_?code", f"{411001 + i * 7}"),
        (r"address|street|addr", f"{12 + i} Sample Street"),
        (r"gender|sex", ["F", "M", "F", "M", "F"][i]),
        (r"currency", ["INR", "USD", "GBP", "EUR", "SGD"][i]),
        (r"url|link|website", f"https://example.com/{i + 1}"),
        (r"code|ref|number|no$|num$", f"{re.sub(r'[^A-Z]', '', table.upper())[:3] or 'ABC'}{i + 1:04d}"),
        (r"desc|comment|note|remark|text|message|detail", f"Sample {name} {i + 1}"),
        (r"user|login|created_?by|modified_?by|updated_?by", f"user{i + 1}"),
    ]
    value = next((v for pat, v in picks if re.search(pat, n)), f"{name} {i + 1}")
    return value[:length] if length else value


def _date_shift(name: str) -> int:
    """Days added for the column's role, so end dates come after start dates and updates after creation."""
    n = name.lower()
    if re.search(r"end|to_?date|expir|close|finish|until|due|valid_?to", n):
        return 90
    if re.search(r"updat|modif|chang", n):
        return 3
    if re.search(r"birth|dob", n):
        return -365 * 30
    return 0


def _value(c: dict[str, Any], dtype: str, i: int, db: str, table: str) -> Any:
    name, raw = c["name"], str(c.get("type", "")).lower()
    base = re.sub(r"\(.*$", "", dtype)
    if base == "BOOLEAN":
        return i % 2 == 0
    if base in ("TINYINT", "SMALLINT", "INT", "BIGINT"):
        if _is_key(name):
            return i + 1
        return [3, 12, 7, 25, 1][i] if base == "TINYINT" else [10, 250, 42, 120, 5][i]  # fits every integer type
    if base == "DECIMAL":
        m = re.match(r"DECIMAL\((\d+),(\d+)\)", dtype)
        p, s = (int(m.group(1)), int(m.group(2))) if m else (18, 2)
        whole = max(p - s, 0)
        top = 10 ** min(whole, 6) - 1 if whole else 0
        if _is_key(name) and s == 0:
            return i + 1
        value = round(min([1250.5, 99.99, 18450.75, 7.25, 560.0][i], top + 0.99) if top else [0.5, 0.25, 0.75, 0.1, 0.9][i], s)
        return f"{value:.{s}f}" if s else str(int(value))
    if base in ("FLOAT", "DOUBLE"):
        return [12.5, 3.75, 980.125, 0.5, 47.25][i]
    if base == "DATE":
        return (date(2024, 1, 15) + timedelta(days=37 * i + _date_shift(name))).isoformat()
    if base.startswith("TIMESTAMP"):
        return (datetime(2024, 1, 15, 9, 30) + timedelta(days=37 * i + _date_shift(name), hours=3 * i)).isoformat(sep=" ")
    if base == "BINARY":
        return f"0x{(i + 1):02X}A1B2C3"
    if base == "VARIANT":
        return '{"sample": %d}' % (i + 1)
    # text
    if "uniqueidentifier" in raw or "uuid" in raw or name.lower().endswith("guid"):
        return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{table}.{name}.{i}"))
    if raw.startswith("time"):
        return f"{9 + i:02d}:30:00"
    if "xml" in raw:
        return f"<row id=\"{i + 1}\"/>"
    if re.search(r"^id$|_id$|Id$|ID$", name, re.IGNORECASE if name.lower() == "id" else 0):  # Id, CustomerId, cust_id
        return str(i + 1)
    return _text(name, i, _length(c, db), table)


def _sql_literal(v: Any, dtype: str) -> str:
    if v is None:
        return "NULL"
    base = re.sub(r"\(.*$", "", dtype)
    if base == "BOOLEAN":
        return "TRUE" if v else "FALSE"
    if base in ("TINYINT", "SMALLINT", "INT", "BIGINT", "DECIMAL", "FLOAT", "DOUBLE"):
        return str(v)
    if base == "DATE":
        return f"DATE'{v}'"
    if base.startswith("TIMESTAMP"):
        return f"TIMESTAMP'{v}'"
    if base == "BINARY":
        return f"X'{str(v)[2:]}'"
    text = "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"
    return f"PARSE_JSON({text})" if base == "VARIANT" else text


def tables(cfg: ProjectConfig) -> tuple[str, list[dict[str, Any]], str]:
    """(database type, tables with columns, where they came from): the inventory, else the code's CREATE TABLEs."""
    from .schema import database_type

    inv = load_state(cfg).get("inventory")
    if inv and inv.get("tables"):
        return database_type(cfg, inv), inv["tables"], "the database's table list"
    from .inventory import DEFAULT_DB
    from .overview import parse_tables
    from .staging import read_source, source_files

    found: dict[str, dict[str, Any]] = {}
    for rel in source_files(cfg):
        if rel.suffix.lower() not in (".sql", ".ddl"):
            continue
        for t in parse_tables(read_source(cfg.input_dir / rel)):
            parts = [p for p in t["name"].split(".") if p]
            schema, table = (parts[-2] if len(parts) >= 2 else "dbo"), parts[-1]
            if t["columns"] and table.lower() not in ("#", "") and not table.startswith(("#", "@")):
                found[f"{schema}.{table}".lower()] = {"schema": schema, "table": table, "columns": t["columns"]}
    db = (cfg.source_db or {}).get("type") or DEFAULT_DB.get(cfg.source.key, "sqlserver")
    return db, list(found.values()), "the CREATE TABLE statements in the code"


def build(cfg: ProjectConfig, rows: int = ROWS) -> dict[str, Any] | None:
    """Write output/dummy_data/ (CSV per table + dummy_data.sql). None when no table definitions are known."""
    db, tbls, origin = tables(cfg)
    if not tbls:
        return None
    rows = max(1, min(rows, len(_FIRST)))
    folder = cfg.output_dir / FOLDER
    if folder.exists():
        for old in folder.glob("*"):
            if old.is_file():
                old.unlink()
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().isoformat(timespec="seconds")
    sql_parts = [f"-- Dummy data made by WishBridge ({stamp}) from {origin}.\n"
                 "-- MADE-UP VALUES, NOT CLIENT DATA: built from column types and names only.\n"
                 f"-- {rows} rows per table; the last row is NULL wherever the column allows NULL.\n"
                 "-- Run it after the tables exist in Databricks (target names follow the schema mapping).\n"]
    summary = []
    for t in sorted(tbls, key=lambda t: (t["schema"], t["table"])):
        source = f"{t['schema']}.{t['table']}" if t["schema"] else t["table"]
        cols = [c for c in t["columns"] if c.get("name")]
        types = [databricks_type(db, c["type"], c.get("precision"), c.get("scale"))[0] for c in cols]
        data = []
        for i in range(rows):
            last = rows > 1 and i == rows - 1
            data.append([None if last and c.get("nullable", True) and not _is_key(c["name"])
                         else _value(c, dt, i, db, t["table"]) for c, dt in zip(cols, types)])
        buf = io.StringIO()
        w = csv.writer(buf, lineterminator="\n")
        w.writerow([c["name"] for c in cols])
        w.writerows([["" if v is None else v for v in r] for r in data])
        fname = re.sub(r'[\\/:*?"<>|]', "_", source) + ".csv"
        (folder / fname).write_text(buf.getvalue(), encoding="utf-8")
        values = ",\n".join("  (" + ", ".join(_sql_literal(v, dt) for v, dt in zip(r, types)) + ")" for r in data)
        sql_parts.append(f"-- {source}\nINSERT INTO {_quote_target(cfg.map_table(source))} "
                         f"({', '.join(_quote(c['name']) for c in cols)})\nVALUES\n{values};\n")
        summary.append({"table": source, "file": fname, "columns": len(cols)})
    (folder / "dummy_data.sql").write_text("\n".join(sql_parts), encoding="utf-8")
    result = {"rows": rows, "tables": summary, "origin": origin, "folder": str(folder), "written_at": stamp}
    save_step(cfg, "dummy_data", result)
    return result
