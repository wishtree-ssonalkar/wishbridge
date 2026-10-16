"""Step 4 - Load data: copy table data into the deployed Delta tables.

Methods:
  federation  INSERT ... SELECT from a Lakehouse Federation foreign catalog
  files       COPY INTO from files exported to a Unity Catalog volume

By default only a plan (SQL script) is written; pass --execute to run it.
"""

from __future__ import annotations

from typing import Any, Callable

from .config import ProjectConfig, TableMapping, looks_like_prod
from .dbx import SqlError, Warehouse
from .state import save_step


def _source_ref(cfg: ProjectConfig, t: TableMapping) -> str:
    if not cfg.source_catalog:
        raise SqlError("data.source_catalog is required for the federation method")
    return f"{cfg.source_catalog}.{t.source}"


def build_plan(cfg: ProjectConfig) -> list[tuple[TableMapping, list[str]]]:
    if not cfg.tables:
        raise SqlError("No tables listed under data.tables in project.yml")
    plan = []
    for t in cfg.tables:
        if not t.load:
            # Compare-only table (an ETL job's output): never copied; emptied in overwrite mode so a re-run starts clean
            plan.append((t, [f"TRUNCATE TABLE {t.target}"] if cfg.load_mode == "overwrite" else []))
            continue
        stmts = []
        filtered = cfg.data_method in ("federation", "local") and bool(t.where)
        if cfg.load_mode == "overwrite" and not filtered:  # a filtered copy adds rows; it never empties the table
            stmts.append(f"TRUNCATE TABLE {t.target}")
        if cfg.data_method == "federation":
            where = f" WHERE {t.where}" if t.where else ""
            if t.columns:  # mapped by hand in the app: target column <- source column
                cols = ", ".join(f"`{c}`" for c in t.columns)
                exprs = ", ".join(f"`{s}` AS `{c}`" for c, s in t.columns.items())
                stmts.append(f"INSERT INTO {t.target} ({cols}) SELECT {exprs} FROM {_source_ref(cfg, t)}{where}")
            else:
                stmts.append(f"INSERT INTO {t.target} BY NAME SELECT * FROM {_source_ref(cfg, t)}{where}")
        else:
            if not cfg.files_root:
                raise SqlError("data.files_root is required for the files method")
            table = t.source.split(".")[-1]
            local = cfg.data_method == "local"  # exported and uploaded by WishBridge just before the copy
            fmt = "PARQUET" if local else cfg.file_format
            opts = "FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'true') " if fmt == "CSV" else ""
            force = ", 'force' = 'true'" if local else ""
            stmts.append(
                f"COPY INTO {t.target} FROM '{cfg.files_root}/{table}/' "
                f"FILEFORMAT = {fmt} {opts}COPY_OPTIONS ('mergeSchema' = 'false'{force})"
            )
        plan.append((t, stmts))
    return plan


def run_load(cfg: ProjectConfig, execute: bool = False, allow_prod: bool = False, wh: Warehouse | None = None,
             only: list[str] | None = None,
             progress: Callable[[int, int, str], None] = lambda done, total, table: None) -> dict[str, Any]:
    """Copy the tables. only: copy just these Databricks tables again (the others keep their last result).
    progress(done, total, table) is called before each table."""
    plan = build_plan(cfg)
    if only:
        wanted = {x.lower() for x in only}
        plan = [(t, s) for t, s in plan if t.target.lower() in wanted and t.load]
    script = cfg.out("data", "load_plan.sql")
    script.write_text(
        f"-- WishBridge data load plan for project '{cfg.name}' ({cfg.data_method}, {cfg.load_mode})\n\n"
        + "\n\n".join(f"-- {t.source} -> {t.target}\n" + ";\n".join(s) + ";" for t, s in plan) + "\n",
        encoding="utf-8",
    )
    tables = [{"source": t.source, "target": t.target, "statements": s, "status": "planned", "compare_only": not t.load}
              for t, s in plan]

    if execute:
        bad = [t.target for t, _ in plan if looks_like_prod(t.target)]
        if bad and not allow_prod:
            raise SqlError(f"Targets look like production: {', '.join(bad)}. Pass --allow-prod to load them.")
        wh = wh or Warehouse(cfg)
        local = cfg.data_method == "local"
        by_target = {t.target: t for t, _ in plan}
        if local:
            from . import localdb

            localdb.ensure_volume(cfg, wh)
        for i, entry in enumerate(tables):
            progress(i, len(tables), entry["target"])
            if local and not entry["compare_only"]:
                # read the table on this computer, upload it, then copy it with every column cast to the table's type
                try:
                    t = by_target[entry["target"]]
                    n, cols = localdb.export_upload(cfg, wh, entry["source"], t.where)
                    entry["exported_rows"] = n
                    entry["statements"] = [localdb.copy_into_sql(cfg, wh, t, cols) if s.startswith("COPY INTO") else s
                                           for s in entry["statements"]]
                except Exception as e:  # noqa: BLE001 - ODBC, pandas and the SDK raise many types
                    entry.update(status="failed", error=f"Export from this computer failed: {str(e).splitlines()[0][:350]}")
                    continue
            try:
                rows = None
                for stmt in entry["statements"]:
                    res = wh.run(stmt)
                    if res.rows and res.columns:
                        rec = dict(zip(res.columns, res.rows[0]))
                        rows = rec.get("num_inserted_rows") or rec.get("num_affected_rows") or rows
                if entry["compare_only"]:
                    entry.update(status="reset" if entry["statements"] else "compare only")
                else:
                    entry.update(status="loaded", rows=int(rows) if rows is not None else None)
            except SqlError as e:
                entry.update(status="failed", error=str(e).splitlines()[0][:400])

    if only:  # keep the last result of the tables that were not copied again
        from .state import load_state

        again = {t["target"].lower() for t in tables}
        before = [t for t in (load_state(cfg).get("load") or {}).get("tables", []) if t["target"].lower() not in again]
        tables = before + tables
    result = {
        "method": cfg.data_method,
        "executed": execute,
        "plan_file": str(script),
        "tables": tables,
        "summary": {
            "tables": sum(not t["compare_only"] for t in tables),
            "loaded": sum(t["status"] == "loaded" for t in tables),
            "failed": sum(t["status"] == "failed" for t in tables),
            "compare_only": sum(t["compare_only"] for t in tables),
        },
    }
    save_step(cfg, "load", result)
    return result
