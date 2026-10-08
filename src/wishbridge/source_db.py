"""Connect the legacy source database to Databricks (Lakehouse Federation), so `load` and `reconcile`
can read its tables.

WishBridge creates, in the project's Databricks workspace:
  1. a secret holding the database password (Databricks secret scope `wishbridge`) - the password is never
     written to project.yml or kept on this computer
  2. a connection (server, port, user, password via secret())
  3. a foreign catalog that exposes the source database as `<catalog>.<schema>.<table>`
and points `data.source_catalog` at that catalog.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import ProjectConfig
from .dbx import SqlError, Warehouse

SECRET_SCOPE = "wishbridge"


@dataclass(frozen=True)
class DbType:
    key: str            # Databricks connection TYPE
    label: str
    port: int
    catalog_option: str | None  # option naming the database in CREATE FOREIGN CATALOG (None: not needed)
    catalog_label: str = "Database"
    extra: tuple[tuple[str, str], ...] = field(default_factory=tuple)  # (option, label) also required


DB_TYPES: dict[str, DbType] = {t.key: t for t in [
    DbType("sqlserver", "Microsoft SQL Server / Azure SQL", 1433, "database"),
    DbType("sqldw", "Azure Synapse (dedicated SQL pool)", 1433, "database"),
    DbType("oracle", "Oracle", 1521, "service_name", "Service name"),
    DbType("snowflake", "Snowflake", 443, "database", extra=(("sfWarehouse", "Snowflake warehouse"),)),
    DbType("teradata", "Teradata", 1025, None),
    DbType("redshift", "Amazon Redshift", 5439, "database"),
    DbType("postgresql", "PostgreSQL", 5432, "database"),
    DbType("mysql", "MySQL", 3306, None),
]}

# Default connection type for each WishBridge source system (None: not supported by Lakehouse Federation
# with a password - use exported files instead).
DEFAULT_FOR_SOURCE = {"mssql": "sqlserver", "synapse": "sqldw", "oracle": "oracle", "snowflake": "snowflake",
                      "teradata": "teradata", "redshift": "redshift", "bigquery": None, "netezza": None}

NOT_SUPPORTED_HINT = ("Lakehouse Federation can't connect to this source with a user and password. Export the tables "
                      "to Parquet/CSV, upload them to a Unity Catalog volume and use the 'files' data method.")

_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,120}$")


def _lit(v: Any) -> str:
    return "'" + str(v).replace("\\", "\\\\").replace("'", "\\'") + "'"


def _ident(name: str) -> str:
    if not _IDENT.match(name):
        raise SqlError(f"'{name}' is not a valid name (letters, digits and _ only)")
    return f"`{name}`"


def object_names(cfg: ProjectConfig) -> dict[str, str]:
    """Names WishBridge uses for this project's connection, catalog and secret."""
    pid = re.sub(r"[^a-z0-9_]", "_", cfg.name.lower()).strip("_") or "project"
    return {"connection": f"wb_{pid}_conn", "catalog": f"wb_{pid}_source", "scope": SECRET_SCOPE,
            "key": f"{pid}-source-password"}


def connection_sql(db: DbType, name: str, host: str, port: int, user: str, scope: str, key: str,
                   extra: dict[str, str] | None = None) -> str:
    opts = [f"host {_lit(host)}", f"port {_lit(port)}", f"user {_lit(user)}",
            f"password secret({_lit(scope)}, {_lit(key)})"]
    for opt, label in db.extra:
        value = (extra or {}).get(opt, "")
        if not value:
            raise SqlError(f"{label} is required for {db.label}")
        opts.append(f"{opt} {_lit(value)}")
    return f"CREATE CONNECTION {_ident(name)} TYPE {db.key}\nOPTIONS (\n  " + ",\n  ".join(opts) + "\n)"


def catalog_sql(db: DbType, catalog: str, connection: str, database: str) -> str:
    sql = f"CREATE FOREIGN CATALOG {_ident(catalog)} USING CONNECTION {_ident(connection)}"
    if db.catalog_option:
        if not database:
            raise SqlError(f"{db.catalog_label} is required for {db.label}")
        sql += f" OPTIONS ({db.catalog_option} {_lit(database)})"
    return sql


def _store_password(wh: Warehouse, scope: str, key: str, password: str) -> None:
    if not password:
        raise SqlError("Enter the database password")
    existing = {s.name for s in wh.w.secrets.list_scopes()}
    if scope not in existing:
        wh.w.secrets.create_scope(scope=scope)
    wh.w.secrets.put_secret(scope=scope, key=key, string_value=password)


def create_connection(cfg: ProjectConfig, settings: dict[str, Any], password: str,
                      wh: Warehouse | None = None) -> dict[str, str]:
    """(Re)create the secret, connection and foreign catalog for this project. Returns the names used."""
    db = DB_TYPES.get(str(settings.get("type", "")))
    if not db:
        raise SqlError(f"Choose a database type: {', '.join(DB_TYPES)}")
    host, user = str(settings.get("host", "")).strip(), str(settings.get("user", "")).strip()
    if not host or not user:
        raise SqlError("Server (host) and user are required")
    port = int(settings.get("port") or db.port)
    names = object_names(cfg)
    wh = wh or Warehouse(cfg)
    conn_sql = connection_sql(db, names["connection"], host, port, user, names["scope"], names["key"],
                              settings.get("options") or {})
    cat_sql = catalog_sql(db, names["catalog"], names["connection"], str(settings.get("database", "")).strip())
    try:
        _store_password(wh, names["scope"], names["key"], password)
    except SqlError:
        raise
    except Exception as e:  # the SDK raises several error types; keep the message
        raise SqlError(f"Could not store the password in Databricks secrets: {str(e).splitlines()[0]}") from e
    # Replace any earlier version of this project's objects (names are WishBridge's own, wb_<project>_...)
    wh.run(f"DROP CATALOG IF EXISTS {_ident(names['catalog'])} CASCADE")
    wh.run(f"DROP CONNECTION IF EXISTS {_ident(names['connection'])}")
    wh.run(conn_sql)
    wh.run(cat_sql)
    return names


def test_connection(catalog: str, wh: Warehouse, timeout_s: int = 180) -> list[str]:
    """Ask Databricks to read the source's schema list - this is the first real round trip to the database."""
    try:
        rows = wh.run(f"SHOW SCHEMAS IN {_ident(catalog)}", timeout_s=timeout_s).rows
    except SqlError as e:
        if str(e).startswith("Timed out"):
            raise SqlError(f"No answer from the database within {timeout_s // 60} minutes - Databricks probably "
                           "cannot reach it over the network (firewall, VPN or private link needed).") from e
        if re.search(r"FAILED_JDBC|CANNOT_ESTABLISH_CONNECTION|Failed to connect", str(e)):
            raise SqlError("Databricks could not connect to the database. Check the server, port, database and "
                           "login, and that the database accepts connections from Databricks (allow-list, VPN or "
                           f"private link). Details: {str(e).splitlines()[0]}") from e
        raise
    return sorted(r[0] for r in rows if r and r[0] and r[0].lower() not in ("information_schema",))


def list_tables(catalog: str, schema: str, wh: Warehouse) -> list[str]:
    rows = wh.run(f"SHOW TABLES IN {_ident(catalog)}.`{schema.replace('`', '')}`").rows
    # SHOW TABLES returns (namespace, tableName, isTemporary)
    return sorted(r[1] for r in rows if r and len(r) > 1 and r[1])


def remove_connection(cfg: ProjectConfig, wh: Warehouse | None = None) -> None:
    names = object_names(cfg)
    wh = wh or Warehouse(cfg)
    wh.run(f"DROP CATALOG IF EXISTS {_ident(names['catalog'])} CASCADE")
    wh.run(f"DROP CONNECTION IF EXISTS {_ident(names['connection'])}")
    try:
        wh.w.secrets.delete_secret(scope=names["scope"], key=names["key"])
    except Exception:  # already gone
        pass
