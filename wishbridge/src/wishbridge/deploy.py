"""Step 3 - Deploy & validate: run converted DDL in a dev schema and EXPLAIN every query/DML.

DML is only planned (EXPLAIN), never executed, unless --execute-dml is given,
so validation does not change data.
"""

from __future__ import annotations

from typing import Any

from .config import ProjectConfig, looks_like_prod
from .dbx import SqlError, Warehouse
from .sqltext import split_statements, statement_kind
from .state import load_state, save_step


def _first_line(msg: str) -> str:
    return (msg or "").strip().splitlines()[0][:400] if msg else ""


def run_deploy(cfg: ProjectConfig, execute_dml: bool = False, allow_prod: bool = False, wh: Warehouse | None = None) -> dict[str, Any]:
    if looks_like_prod(cfg.target_schema) and not allow_prod:
        raise SqlError(f"Target {cfg.target_schema} looks like production. Deploy to a dev schema or pass --allow-prod.")
    convert = load_state(cfg).get("convert")
    if not convert:
        raise SqlError("Nothing to deploy - run `wishbridge convert` first.")

    wh = wh or Warehouse(cfg)
    wh.run(f"CREATE SCHEMA IF NOT EXISTS {cfg.target_schema}")

    files: list[dict[str, Any]] = []
    for f in convert["files"]:
        sql = open(f["final"], encoding="utf-8").read()
        results = []
        for i, stmt in enumerate(split_statements(sql), 1):
            kind = statement_kind(stmt)
            action = "execute" if kind in ("ddl", "other") or (kind == "dml" and execute_dml) else "explain"
            try:
                if action == "explain":
                    res = wh.run(f"EXPLAIN {stmt}", cfg.catalog, cfg.schema)
                    plan = str(res.rows[0][0]) if res.rows and res.rows[0] else ""
                    if plan.startswith("Error occurred during query planning") or "AnalysisException" in plan:
                        raise SqlError(plan.split("\n", 2)[1] if "\n" in plan else plan)
                else:
                    wh.run(stmt, cfg.catalog, cfg.schema)
                results.append({"n": i, "kind": kind, "action": action, "ok": True})
            except SqlError as e:
                results.append({"n": i, "kind": kind, "action": action, "ok": False, "error": _first_line(str(e)),
                                "statement": stmt[:300]})
        passed = sum(r["ok"] for r in results)
        files.append({"file": f["file"], "statements": len(results), "passed": passed,
                      "ok": passed == len(results), "results": results})

    summary = {
        "target": cfg.target_schema,
        "warehouse_id": wh.warehouse_id,
        "files": len(files),
        "files_ok": sum(f["ok"] for f in files),
        "statements": sum(f["statements"] for f in files),
        "statements_ok": sum(f["passed"] for f in files),
    }
    result = {"summary": summary, "files": files}
    save_step(cfg, "deploy", result)
    return result
