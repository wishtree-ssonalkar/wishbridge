"""Data mapping: which source table goes into which Databricks table, column by column.

After deploy the Databricks tables exist. For each source table WishBridge proposes the Databricks table with
the same name (in the schema the schema mapping points to), reads the columns on both sides and pairs them by
name. Columns that do not pair up are shown so the team can map them by hand; the mapping is kept in
project.yml (data.tables[].columns: target column -> source column) and the load step copies with it.
"""

from __future__ import annotations

from typing import Any

from .config import ProjectConfig
from .dbx import SqlError, Warehouse
from .reconcile import _columns


def _short(name: str) -> str:
    return name.split(".")[-1].strip("`").lower()


def source_tables(cfg: ProjectConfig, wh: Warehouse | None = None) -> list[str]:
    """`schema.table` names in the source: from the inventory if there is one, otherwise through the connection."""
    from .state import load_state

    if cfg.data_method == "local":  # read the database on this computer itself
        from . import localdb

        try:
            return localdb.list_tables(cfg)
        except Exception as e:  # noqa: BLE001 - ODBC raises its own types
            raise SqlError(f"Could not read the tables of {cfg.local_db.get('database')} on "
                           f"{cfg.local_db.get('server')}: {str(e).splitlines()[0][:250]}") from e
    inv = load_state(cfg).get("inventory")
    if inv and inv.get("tables"):
        from .inventory import table_list

        return table_list(inv)
    if not cfg.source_catalog:
        raise SqlError("Create the source database connection in Settings first.")
    from .source_db import list_tables, test_connection

    wh = wh or Warehouse(cfg)
    return [f"{s}.{t}" for s in test_connection(cfg.source_catalog, wh) for t in list_tables(cfg.source_catalog, s, wh)]


def target_tables(cfg: ProjectConfig, wh: Warehouse) -> list[str]:
    """`catalog.schema.table` names in the project's target schemas (the test schema and the schema mapping)."""
    schemas = [cfg.target_schema] + [v for v in cfg.schema_map.values() if v]
    out: list[str] = []
    for schema in dict.fromkeys(schemas):
        try:
            rows = wh.run(f"SHOW TABLES IN {schema}").rows
        except SqlError:
            continue  # schema not created yet
        out += [f"{schema}.{r[1]}" for r in rows if r and len(r) > 1 and r[1]]
    return out


def propose(cfg: ProjectConfig, sources: list[str], targets: list[str]) -> list[dict[str, Any]]:
    """Pair each source table with a Databricks table: the mapped name if it exists, else the same table name."""
    by_name: dict[str, list[str]] = {}
    for t in targets:
        by_name.setdefault(_short(t), []).append(t)
    lower = {t.lower(): t for t in targets}
    rows = []
    for s in sources:
        default = cfg.map_table(s)
        target = lower.get(default.lower())
        if not target:
            same = by_name.get(_short(s), [])
            target = same[0] if len(same) == 1 else ""
        rows.append({"copy": True, "source": s, "target": target or default, "exists": bool(target)})
    return rows


def compare_columns(source_cols: dict[str, str], target_cols: dict[str, str],
                    columns: dict[str, str] | None = None) -> dict[str, Any]:
    """Pair target columns with source columns: the saved mapping first, then the same name."""
    columns = {k.lower(): (v or "").lower() for k, v in (columns or {}).items()}
    pairs, missing = {}, []
    for tcol in target_cols:
        src = columns.get(tcol)
        if src is None:
            src = tcol if tcol in source_cols else ""
        if src and src in source_cols:
            pairs[tcol] = src
        else:
            missing.append(tcol)
    used = set(pairs.values())
    return {"pairs": pairs, "target_without_source": missing,
            "source_not_copied": [c for c in source_cols if c not in used]}


def check(cfg: ProjectConfig, wh: Warehouse | None = None) -> list[dict[str, Any]]:
    """Columns on both sides of every table in data.tables, and whether the copy can run."""
    wh = wh or Warehouse(cfg)
    out = []
    for t in cfg.tables:
        row: dict[str, Any] = {"source": t.source, "target": t.target, "load": t.load}
        try:
            tcols = _columns(wh, t.target)
        except SqlError as e:
            row.update(status="no target table", detail=f"{t.target} is not in Databricks - run deploy, or pick "
                                                        f"another table ({str(e).splitlines()[0][:150]})")
            out.append(row)
            continue
        if not t.load:
            row.update(status="compare only", target_columns=list(tcols))
            out.append(row)
            continue
        try:
            if cfg.data_method == "federation":
                scols = _columns(wh, f"{cfg.source_catalog}.{t.source}")
            elif cfg.data_method == "local":
                from . import localdb

                scols = localdb.columns(cfg, t.source)
            else:
                scols = {}
        except Exception as e:  # noqa: BLE001 - Databricks or the local database (ODBC)
            row.update(status="no source table", detail=f"{t.source} not found in the source "
                                                        f"({str(e).splitlines()[0][:150]})")
            out.append(row)
            continue
        cmp = compare_columns(scols, tcols, t.columns)
        row.update(source_columns=list(scols), target_columns=list(tcols), **cmp)
        if cfg.data_method == "files":  # exported files: columns are known only when copying
            row["status"] = "ready"
        elif not cmp["pairs"]:
            row["status"] = "no matching columns"
        elif cmp["target_without_source"] or cmp["source_not_copied"]:
            row["status"] = "check columns"
        else:
            row["status"] = "ready"
        out.append(row)
    return out


# Rough guide for the copy time through Lakehouse Federation; the real speed depends on the network and the rows.
ROWS_PER_MINUTE = 1_000_000


def preflight(cfg: ProjectConfig, wh: Warehouse | None = None) -> dict[str, Any]:
    """Before copying: the source is reachable, every mapped Databricks table exists, and how much there is to copy."""
    from .state import load_state, save_step

    wh = wh or Warehouse(cfg)
    out: dict[str, Any] = {"source_ok": False, "source_message": "", "tables": []}
    try:
        if cfg.data_method == "federation":
            from .source_db import test_connection

            schemas = test_connection(cfg.source_catalog, wh)
            out.update(source_ok=True, source_message=f"Databricks reached the source database ({len(schemas)} schema(s)).")
        elif cfg.data_method == "local":
            from . import localdb

            msg = localdb.test(cfg)
            localdb.ensure_volume(cfg, wh)
            out.update(source_ok=True, source_message=f"{msg} The tables are uploaded to {cfg.files_root} when you copy.")
        else:
            wh.run(f"LIST '{cfg.files_root}'")
            out.update(source_ok=True, source_message=f"Databricks can read the files in {cfg.files_root}.")
    except SqlError as e:
        out["source_message"] = str(e).splitlines()[0][:300]
    except Exception as e:  # noqa: BLE001 - the local database (ODBC) raises its own types
        out["source_message"] = f"Could not read the database on this computer: {str(e).splitlines()[0][:300]}"
    inventory = {f"{t['schema']}.{t['table']}".lower(): t for t in (load_state(cfg).get("inventory") or {}).get("tables", [])}
    for t in cfg.tables:
        if not t.load:
            continue
        row: dict[str, Any] = {"source": t.source, "target": t.target, "filter": t.where}
        try:
            _columns(wh, t.target)
            row["target_exists"] = True
        except SqlError:
            row["target_exists"] = False
        known = inventory.get(t.source.lower()) or {}
        rows = None if t.where else known.get("rows")
        if rows is None and out["source_ok"] and cfg.data_method == "federation":
            try:
                where = f" WHERE {t.where}" if t.where else ""
                rows = int(wh.run(f"SELECT COUNT(*) FROM {cfg.source_catalog}.{t.source}{where}").rows[0][0])
            except (SqlError, ValueError, IndexError, TypeError):
                rows = None
        row.update(rows=int(rows) if rows is not None else None, size_mb=known.get("size_mb"),
                   minutes=round(rows / ROWS_PER_MINUTE, 1) if rows else None)
        out["tables"].append(row)
    out["total_rows"] = sum(r["rows"] or 0 for r in out["tables"])
    out["minutes"] = round(out["total_rows"] / ROWS_PER_MINUTE, 1)
    out["missing_targets"] = [r["target"] for r in out["tables"] if not r["target_exists"]]
    out["ready"] = out["source_ok"] and not out["missing_targets"]
    save_step(cfg, "preflight", out)
    return out


def again_targets(state: dict[str, Any]) -> list[str]:
    """Databricks tables to copy again: they failed in the last copy, or differ from the source."""
    failed = [t["target"] for t in (state.get("load") or {}).get("tables", []) if t.get("status") == "failed"]
    rec = state.get("reconcile") or {}
    differ = [t["target"] for t in rec.get("tables", []) if t.get("status") not in ("match",)] if rec.get("mode") == "quick" else []
    return list(dict.fromkeys(failed + differ))
