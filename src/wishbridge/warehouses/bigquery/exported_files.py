"""Data copied from files the client exported into a Unity Catalog volume (data.method: files).

One folder per table under data.files_root (<root>/<table>/). The files' own column types rarely match the
Databricks table exactly (an INT column often arrives as BIGINT, a DATE as a timestamp), so the copy and the
comparison both cast every column to the table's type - the same as the copy from a database on this computer.
"""

from __future__ import annotations

from .config import ProjectConfig, TableMapping
from .dbx import SqlError


def folder(cfg: ProjectConfig, t: TableMapping) -> str:
    return f"{cfg.files_root.rstrip('/')}/{t.source.split('.')[-1]}/"


def _format(cfg: ProjectConfig) -> tuple[str, str, str]:
    """(COPY INTO format, its FORMAT_OPTIONS, read_files options)."""
    fmt = (cfg.file_format or "PARQUET").upper()
    if fmt == "CSV":
        return fmt, "FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'true') ", ", header => true, inferSchema => true"
    return fmt, "", ""


def read_files(cfg: ProjectConfig, t: TableMapping) -> str:
    fmt, _, opts = _format(cfg)
    return f"read_files('{folder(cfg, t)}', format => '{fmt.lower()}'{opts})"


def columns(cfg: ProjectConfig, wh, t: TableMapping) -> list[str]:
    """The columns in the exported files of one table (a clear message when there are none)."""
    try:
        rows = wh.run(f"DESCRIBE QUERY SELECT * FROM {read_files(cfg, t)}").rows
    except SqlError as e:
        if "PATH_NOT_FOUND" in str(e) or "does not exist" in str(e).lower():
            raise SqlError(f"No exported files in {folder(cfg, t)} - the table was not exported (or it is empty and "
                           "the export wrote no file).") from e
        raise
    return [r[0] for r in rows if r[0] and not str(r[0]).startswith("#") and r[0] != "_rescued_data"]


def copy_sql(cfg: ProjectConfig, wh, t: TableMapping) -> str:
    """COPY INTO with every column cast to the table's type. With Replace the files are read again (force),
    since the table was emptied first; with Add, files copied before are skipped (no duplicates)."""
    from .localdb import copy_into_sql

    fmt, fmt_opts, _ = _format(cfg)
    return copy_into_sql(cfg, wh, t, columns(cfg, wh, t), fmt=fmt, fmt_opts=fmt_opts,
                         force=cfg.load_mode == "overwrite" and not t.where)


def source_query(cfg: ProjectConfig, wh, t: TableMapping) -> str:
    """The exported files as the table would read them (columns cast to its types) - for the comparison."""
    from .reconcile import _columns

    have = {c.lower(): c for c in columns(cfg, wh, t)}
    source_for = {k.lower(): v for k, v in (t.columns or {}).items()}
    select = [f"CAST(`{have[source_for.get(name, name).lower()]}` AS {dtype}) AS `{name}`"
              for name, dtype in _columns(wh, t.target).items() if source_for.get(name, name).lower() in have]
    where = f" WHERE {t.where}" if t.where else ""
    return f"(SELECT {', '.join(select)} FROM {read_files(cfg, t)}{where})"
