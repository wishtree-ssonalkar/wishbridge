"""Step 4 - Load data: copy table data into the deployed Delta tables.

Methods:
  federation  INSERT ... SELECT from a Lakehouse Federation foreign catalog
  files       COPY INTO from files exported to a Unity Catalog volume

By default only a plan (SQL script) is written; pass --execute to run it.
"""

from __future__ import annotations

from typing import Any

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
        stmts = []
        if cfg.load_mode == "overwrite":
            stmts.append(f"TRUNCATE TABLE {t.target}")
        if cfg.data_method == "federation":
            stmts.append(f"INSERT INTO {t.target} BY NAME SELECT * FROM {_source_ref(cfg, t)}")
        else:
            if not cfg.files_root:
                raise SqlError("data.files_root is required for the files method")
            table = t.source.split(".")[-1]
            opts = "FORMAT_OPTIONS ('header' = 'true', 'inferSchema' = 'true') " if cfg.file_format == "CSV" else ""
            stmts.append(
                f"COPY INTO {t.target} FROM '{cfg.files_root}/{table}/' "
                f"FILEFORMAT = {cfg.file_format} {opts}COPY_OPTIONS ('mergeSchema' = 'false')"
            )
        plan.append((t, stmts))
    return plan


def run_load(cfg: ProjectConfig, execute: bool = False, allow_prod: bool = False, wh: Warehouse | None = None) -> dict[str, Any]:
    plan = build_plan(cfg)
    script = cfg.out("data", "load_plan.sql")
    script.write_text(
        f"-- WishBridge data load plan for project '{cfg.name}' ({cfg.data_method}, {cfg.load_mode})\n\n"
        + "\n\n".join(f"-- {t.source} -> {t.target}\n" + ";\n".join(s) + ";" for t, s in plan) + "\n",
        encoding="utf-8",
    )
    tables = [{"source": t.source, "target": t.target, "statements": s, "status": "planned"} for t, s in plan]

    if execute:
        bad = [t.target for t, _ in plan if looks_like_prod(t.target)]
        if bad and not allow_prod:
            raise SqlError(f"Targets look like production: {', '.join(bad)}. Pass --allow-prod to load them.")
        wh = wh or Warehouse(cfg)
        for entry in tables:
            try:
                rows = None
                for stmt in entry["statements"]:
                    res = wh.run(stmt)
                    if res.rows and res.columns:
                        rec = dict(zip(res.columns, res.rows[0]))
                        rows = rec.get("num_inserted_rows") or rec.get("num_affected_rows") or rows
                entry.update(status="loaded", rows=int(rows) if rows is not None else None)
            except SqlError as e:
                entry.update(status="failed", error=str(e).splitlines()[0][:400])

    result = {
        "method": cfg.data_method,
        "executed": execute,
        "plan_file": str(script),
        "tables": tables,
        "summary": {
            "tables": len(tables),
            "loaded": sum(t["status"] == "loaded" for t in tables),
            "failed": sum(t["status"] == "failed" for t in tables),
        },
    }
    save_step(cfg, "load", result)
    return result
