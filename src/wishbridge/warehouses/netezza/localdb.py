"""A database on this computer - the "local" way to copy the data.

For a demo, or a database Databricks cannot reach: WishBridge reads the mapped tables read-only, saves them as Parquet,
uploads them to a Unity Catalog volume and copies them into the Databricks tables (each column cast to the table's
type). The comparison counts and sums the source here, in the database itself. At a client whose database Databricks
can reach, Lakehouse Federation reads it directly instead.
A password (SQL login) is never saved: the app passes it to the copy in the environment variable below.
"""

from __future__ import annotations

import io
import os
import warnings
from typing import Any

from .config import ProjectConfig, TableMapping

PASSWORD_ENV = "WISHBRIDGE_LOCAL_PASSWORD"


def connect(cfg: ProjectConfig, password: str | None = None):
    import pyodbc

    from .inventory import connection_string

    db = cfg.local_db
    pw = password if password is not None else os.environ.get(PASSWORD_ENV, "")
    return pyodbc.connect(connection_string(db.get("server", ""), db.get("database", ""),
                                            bool(db.get("windows_login", True)), db.get("user", ""), pw,
                                            bool(db.get("trust", True))), readonly=True, timeout=30)


def _ref(source: str) -> str:
    return ".".join("[" + p.replace("]", "]]") + "]" for p in source.split("."))


def test(cfg: ProjectConfig) -> str:
    conn = connect(cfg)
    try:
        name = conn.cursor().execute("SELECT DB_NAME()").fetchone()[0]
    finally:
        conn.close()
    return f"WishBridge reached {name} on {cfg.local_db.get('server')} (this computer)."


def volume_of(files_root: str) -> str:
    """/Volumes/<catalog>/<schema>/<volume>/... -> catalog.schema.volume"""
    parts = [p for p in files_root.split("/") if p]
    if len(parts) < 4 or parts[0] != "Volumes":
        raise ValueError(f"The upload folder must be in a Unity Catalog volume: /Volumes/<catalog>/<schema>/<volume>, "
                         f"not {files_root}")
    return ".".join(parts[1:4])


def ensure_volume(cfg: ProjectConfig, wh) -> None:
    wh.run(f"CREATE VOLUME IF NOT EXISTS {volume_of(cfg.files_root)}")


def export_upload(cfg: ProjectConfig, wh, source: str, where: str = "") -> tuple[int, list[str]]:
    """Read one table (read-only), save it as Parquet and upload it to <files_root>/<table>/<table>.parquet."""
    import pandas as pd

    conn = connect(cfg)
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # pandas prefers SQLAlchemy; a plain DBAPI connection is fine here
            df = pd.read_sql(f"SELECT * FROM {_ref(source)}" + (f" WHERE {where}" if where else ""), conn,
                             coerce_float=False)  # keep DECIMAL exact (money), not float
    finally:
        conn.close()
    buf = io.BytesIO()
    # microseconds: Databricks does not read nanosecond Parquet timestamps
    df.to_parquet(buf, index=False, coerce_timestamps="us", allow_truncated_timestamps=True)
    buf.seek(0)
    table = source.split(".")[-1]
    folder = f"{cfg.files_root.rstrip('/')}/{table}"
    wh.w.files.create_directory(folder)
    wh.w.files.upload(f"{folder}/{table}.parquet", buf, overwrite=True)
    return len(df), [str(c) for c in df.columns]


def copy_into_sql(cfg: ProjectConfig, wh, t: TableMapping, exported: list[str]) -> str:
    """COPY INTO that casts every column to the Databricks table's own type (Parquet types differ from SQL Server's)."""
    from .reconcile import _columns

    have = {c.lower(): c for c in exported}
    source_for = {k.lower(): v for k, v in (t.columns or {}).items()}
    select = []
    for name, dtype in _columns(wh, t.target).items():
        src = source_for.get(name, name)
        if src.lower() in have:
            select.append(f"CAST(`{have[src.lower()]}` AS {dtype}) AS `{name}`")
    table = t.source.split(".")[-1]
    return (f"COPY INTO {t.target} FROM (SELECT {', '.join(select)} FROM '{cfg.files_root.rstrip('/')}/{table}/') "
            "FILEFORMAT = PARQUET COPY_OPTIONS ('force' = 'true')")


def compare(cfg: ProjectConfig, wh, t: TableMapping) -> dict[str, Any]:
    """Row count and the sum of every numeric column: counted in the source database here, and in Databricks."""
    from .reconcile import _NUMERIC, _columns, _eq, _profile_sql

    target_cols = _columns(wh, t.target)
    conn = connect(cfg)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT TOP 0 * FROM {_ref(t.source)}")
        source_cols = {d[0].lower(): d[0] for d in cur.description}
        source_for = {k.lower(): v.lower() for k, v in (t.columns or {}).items()}
        numeric = [c for c, dtype in target_cols.items() if dtype.startswith(_NUMERIC)
                   and source_for.get(c, c) in source_cols]
        sums = "".join(f", SUM(CAST([{source_cols[source_for.get(c, c)]}] AS DECIMAL(38, 6)))" for c in numeric)
        where = f" WHERE {t.where}" if t.where else ""
        s = list(cur.execute(f"SELECT COUNT_BIG(*){sums} FROM {_ref(t.source)}{where}").fetchone())
    finally:
        conn.close()
    d = wh.run(_profile_sql(t.target, numeric, [])).rows[0]
    checks = [{"check": "row_count", "source": s[0], "target": d[0], "match": _eq(s[0], d[0])}]
    checks += [{"check": f"sum({c})", "source": s[i + 1], "target": d[i + 1], "match": _eq(s[i + 1], d[i + 1])}
               for i, c in enumerate(numeric)]
    return {"checks": checks, "status": "match" if all(c["match"] for c in checks) else "mismatch",
            "note": f"source counted in {cfg.local_db.get('database')} on this computer"}
