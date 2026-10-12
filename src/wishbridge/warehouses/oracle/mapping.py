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
            scols = _columns(wh, f"{cfg.source_catalog}.{t.source}") if cfg.data_method == "federation" else {}
        except SqlError as e:
            row.update(status="no source table", detail=f"{t.source} not found through the connection "
                                                        f"({str(e).splitlines()[0][:150]})")
            out.append(row)
            continue
        cmp = compare_columns(scols, tcols, t.columns)
        row.update(source_columns=list(scols), target_columns=list(tcols), **cmp)
        if cfg.data_method != "federation":
            row["status"] = "ready"
        elif not cmp["pairs"]:
            row["status"] = "no matching columns"
        elif cmp["target_without_source"] or cmp["source_not_copied"]:
            row["status"] = "check columns"
        else:
            row["status"] = "ready"
        out.append(row)
    return out
