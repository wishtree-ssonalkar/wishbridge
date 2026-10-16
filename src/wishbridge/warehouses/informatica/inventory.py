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
import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from . import sources as _sources
from .config import ProjectConfig
from .state import save_step

COLUMNS = ["schema_name", "table_name", "column_name", "ordinal", "data_type", "max_length", "precision", "scale",
           "is_nullable", "row_count", "size_mb"]

# Per warehouse (sources/<warehouse>.py): the database each source system is / loads, the read-only
# query listing its tables, and how the DBA saves the result.
DEFAULT_DB = _sources.DEFAULT_DB
HOW_TO_SAVE = _sources.HOW_TO_SAVE
QUERIES = _sources.INVENTORY_QUERIES


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


def import_file(cfg: ProjectConfig, path: str | Path, db: str | None = None, read_from: str = "") -> dict[str, Any]:
    """Keep the DBA's file in the project, record the inventory and write the Databricks table scripts."""
    path = Path(path)
    from .staging import read_source

    result = parse(read_source(path))
    dest = cfg.out("inventory", "inventory.csv")
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
    result.update(file=str(dest), imported_at=datetime.now().isoformat(timespec="seconds"), source_file=str(path),
                  database_type=db or (cfg.source_db or {}).get("type") or DEFAULT_DB.get(cfg.source.key, "sqlserver"),
                  read_from=read_from or "DBA's CSV")
    save_step(cfg, "inventory", result)
    from .schema import build_scripts

    build_scripts(cfg, result)
    from .dummy_data import build as build_dummy_data

    build_dummy_data(cfg)
    return result


# --- Read the catalog directly (SQL Server, Azure SQL, Synapse) ---------------------------------------------
LIVE_DATABASES = ("sqlserver", "synapse")
_DRIVERS = ("ODBC Driver 18 for SQL Server", "ODBC Driver 17 for SQL Server", "SQL Server")


def odbc_driver() -> str:
    try:
        import pyodbc
    except ImportError as e:
        raise RuntimeError("The SQL Server reader is not installed. Run the WishBridge installer again "
                           "(or: pip install pyodbc).") from e
    installed = set(pyodbc.drivers())
    for d in _DRIVERS:
        if d in installed:
            return d
    raise RuntimeError("No SQL Server ODBC driver on this computer. Install 'ODBC Driver 18 for SQL Server' from "
                       "Microsoft (it comes with SQL Server Management Studio), or use the DBA query instead.")


AZURE_SUFFIXES = (".database.windows.net", ".sql.azuresynapse.net", ".database.usgovcloudapi.net",
                  ".database.chinacloudapi.cn")


def is_azure_host(server: str) -> bool:
    """An Azure SQL Database / Azure Synapse server name (myserver.database.windows.net, ws.sql.azuresynapse.net)."""
    name = (server or "").strip().lower().removeprefix("tcp:").split(",")[0].split(":")[0]
    return name.endswith(AZURE_SUFFIXES)


def connection_string(server: str, database: str, windows_login: bool, user: str = "", password: str = "",
                      trust_certificate: bool = False, driver: str | None = None, auth: str | None = None) -> str:
    """auth: windows | sql | entra (Microsoft Entra ID, sign-in window, MFA works) | entra-password.
    Without auth: windows_login decides (windows or sql)."""
    driver = driver or odbc_driver()
    auth = auth or ("windows" if windows_login else "sql")
    parts = [f"DRIVER={{{driver}}}", f"SERVER={server}", f"DATABASE={database}", "ApplicationIntent=ReadOnly"]
    pwd = "PWD={" + password.replace("}", "}}") + "}"
    if auth == "windows":
        parts.append("Trusted_Connection=yes")
    elif auth == "entra":
        parts.append("Authentication=ActiveDirectoryInteractive")
        if user:
            parts.append(f"UID={user}")
    elif auth == "entra-password":
        parts += ["Authentication=ActiveDirectoryPassword", f"UID={user}", pwd]
    else:
        parts += [f"UID={user}", pwd]
    if driver != "SQL Server":  # the old built-in driver does not know these options
        parts += ["Encrypt=yes", f"TrustServerCertificate={'yes' if trust_certificate else 'no'}"]
    return ";".join(parts)


def read_live(cfg: ProjectConfig, server: str, database: str, windows_login: bool = True, user: str = "",
              password: str = "", trust_certificate: bool = False, db: str = "sqlserver",
              timeout_s: int = 30, with_code: bool = False, auth: str | None = None) -> dict[str, Any]:
    """Run the read-only inventory query on the database and import the result (the password is not kept).
    with_code: also save the procedures, views, functions and triggers as .sql files (dbcode.py).
    auth: windows | sql | entra | entra-password (connection_string); without it windows_login decides."""
    if db not in LIVE_DATABASES:
        raise ValueError(f"Reading directly works for SQL Server and Synapse; for {db} use the DBA query.")
    if not server.strip() or not database.strip():
        raise ValueError("Enter the server and the database")
    auth = auth or ("windows" if windows_login else "sql")
    if auth in ("sql", "entra-password") and not (user.strip() and password):
        raise ValueError("Enter the user and password, or use Windows login")
    import pyodbc

    cloud = is_azure_host(server)
    try:
        conn = pyodbc.connect(connection_string(server.strip(), database.strip(), auth == "windows", user.strip(),
                                                password, trust_certificate, auth=auth),
                              timeout=max(timeout_s, 60) if cloud else timeout_s, readonly=True)
    except pyodbc.Error as e:
        raw = str(e.args[-1] if e.args else e)
        # "[08001] [Microsoft][ODBC Driver 18 for SQL Server]SSL Provider: ... (-2146893019) (SQLDriverConnect); ..."
        msg = re.sub(r"[\s.]+$", "", re.sub(r"\[[^\]]*\]|\(-?\d+\)|\(SQL\w+\)", "", raw.split(";")[0])).strip() + "."
        low = raw.lower()
        ip = re.search(r"ip address '([\d.:a-f]+)'", low)
        if "not allowed to access the server" in low or ip:
            msg += (" The client's Azure firewall does not let this computer in"
                    + (f" (IP address {ip.group(1)})" if ip else "")
                    + ": ask them to add it in the Azure portal > SQL server > Networking, then read again.")
        elif "is not currently available" in low or "paused" in low:
            msg += " A serverless or paused database may still be starting: wait a minute and read again."
        elif "login failed" in low and auth.startswith("entra"):
            msg += " The Entra ID account has no access to this database: the client adds it as a database user."
        elif "certificate" in low and not cloud:
            msg += " Tick 'Trust the server certificate' if this is the client's own server."
        raise RuntimeError(f"Could not connect to {server} / {database}: {msg}") from e
    label = f"{server.strip()} / {database.strip()}"
    try:
        cur = conn.cursor()
        cur.execute(QUERIES[db])
        names = [c[0] for c in cur.description]
        rows = cur.fetchall()
        if with_code:
            from .dbcode import read as read_code

            read_code(cfg, conn, label)
    finally:
        conn.close()
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(names)
    w.writerows(["" if v is None else v for v in r] for r in rows)
    dest = cfg.out("inventory", "inventory.csv")
    dest.write_text(buf.getvalue(), encoding="utf-8")
    return import_file(cfg, dest, db, read_from=label)


def table_list(inv: dict[str, Any]) -> list[str]:
    """`schema.table` names for data.tables in project.yml."""
    return [f"{t['schema']}.{t['table']}" if t["schema"] else t["table"] for t in inv.get("tables", [])]
