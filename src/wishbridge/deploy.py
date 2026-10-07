"""Step 3 - Deploy & validate: run converted DDL in a dev schema and EXPLAIN every query/DML.

DML is only planned (EXPLAIN), never executed, unless --execute-dml is given,
so validation does not change data. Re-running keeps objects that already exist;
--recreate rebuilds them with CREATE OR REPLACE.
"""

from __future__ import annotations

import re
from typing import Any

from .config import ProjectConfig, looks_like_prod
from .dbx import SqlError, Warehouse
from .sqltext import split_statements, statement_kind
from .state import load_state, save_step


_ALREADY_EXISTS = re.compile(r"\b(TABLE_OR_VIEW|ROUTINE|FUNCTION|SCHEMA)_ALREADY_EXISTS\b")
_CREATE = re.compile(
    r"^(\s*(?:--[^\n]*\n\s*)*)CREATE\s+(?!OR\s+REPLACE\b)(?!TEMP(?:ORARY)?\b)(TABLE|VIEW|PROCEDURE|FUNCTION)\b",
    re.IGNORECASE,
)


def _first_line(msg: str) -> str:
    return (msg or "").strip().splitlines()[0][:400] if msg else ""


def or_replace(stmt: str) -> str:
    """CREATE TABLE|VIEW|PROCEDURE|FUNCTION -> CREATE OR REPLACE ... (leading comments allowed)."""
    return _CREATE.sub(lambda m: f"{m.group(1)}CREATE OR REPLACE {m.group(2)}", stmt, count=1)


NOTEBOOK_HEADER = "# Databricks notebook source"


def with_target_schema(text: str, cfg: ProjectConfig) -> str:
    """Make the notebook's first cell select the project's target catalog and schema."""
    rest = text.lstrip().removeprefix(NOTEBOOK_HEADER).lstrip("\r\n")
    cell = (f"# Added by WishBridge: run in the project's target schema\n"
            f"spark.sql(\"USE CATALOG `{cfg.catalog}`\")\n"
            f"spark.sql(\"USE SCHEMA `{cfg.schema}`\")\n")
    return f"{NOTEBOOK_HEADER}\n{cell}\n# COMMAND ----------\n\n{rest}"


def workspace_folder(wh: Warehouse, cfg: ProjectConfig) -> str:
    user = wh.w.current_user.me().user_name
    return f"/Workspace/Users/{user}/wishbridge/{cfg.name}"


def _upload(wh: Warehouse, cfg: ProjectConfig, f: dict[str, Any]) -> dict[str, Any]:
    """Upload a converted notebook or helper module to the workspace (notebooks run with `wishbridge execute`)."""
    from databricks.sdk.service.workspace import ImportFormat

    text = open(f["final"], encoding="utf-8-sig").read()
    if f["kind"] == "notebook":
        text = with_target_schema(text, cfg)
    folder = workspace_folder(wh, cfg)
    name = f["file"].replace("\\", "/")
    path = f"{folder}/{name}"
    result: dict[str, Any] = {"n": 1, "kind": f["kind"], "action": "upload"}
    try:
        wh.w.workspace.mkdirs(path.rsplit("/", 1)[0])
        # AUTO: a file with the notebook header becomes a notebook (".py" dropped), anything else a workspace file
        wh.w.workspace.upload(path, text.encode("utf-8"), format=ImportFormat.AUTO, overwrite=True)
        result.update(ok=True, path=path.removesuffix(".py") if f["kind"] == "notebook" else path)
    except Exception as e:  # the SDK raises several error types; report the message
        result.update(ok=False, error=_first_line(str(e)))
    return {"file": f["file"], "statements": 1, "passed": int(result["ok"]), "ok": result["ok"], "results": [result]}


def run_deploy(cfg: ProjectConfig, execute_dml: bool = False, allow_prod: bool = False, recreate: bool = False,
               wh: Warehouse | None = None) -> dict[str, Any]:
    if looks_like_prod(cfg.target_schema) and not allow_prod:
        raise SqlError(f"Target {cfg.target_schema} looks like production. Deploy to a dev schema or pass --allow-prod.")
    convert = load_state(cfg).get("convert")
    if not convert:
        raise SqlError("Nothing to deploy - run `wishbridge convert` first.")

    wh = wh or Warehouse(cfg)
    wh.run(f"CREATE SCHEMA IF NOT EXISTS {cfg.target_schema}")

    files: list[dict[str, Any]] = []
    for f in convert["files"]:
        if f.get("kind", "sql") != "sql":
            files.append(_upload(wh, cfg, f))
            continue
        sql = open(f["final"], encoding="utf-8-sig").read()
        results = []
        for i, stmt in enumerate(split_statements(sql), 1):
            kind = statement_kind(stmt)
            action = "execute" if kind in ("ddl", "other") or (kind == "dml" and execute_dml) else "explain"
            try:
                if action == "explain":
                    res = wh.run(f"EXPLAIN {stmt}", cfg.catalog, cfg.schema)
                    plan = str(res.rows[0][0]) if res.rows and res.rows[0] else ""
                    if plan.startswith("Error occurred during query planning") or "AnalysisException" in plan:
                        detail = plan.removeprefix("Error occurred during query planning:")
                        raise SqlError(" ".join(detail.split()) or plan)
                else:
                    wh.run(or_replace(stmt) if recreate and kind == "ddl" else stmt, cfg.catalog, cfg.schema)
                results.append({"n": i, "kind": kind, "action": action, "ok": True})
            except SqlError as e:
                if action == "execute" and _ALREADY_EXISTS.search(str(e)):
                    results.append({"n": i, "kind": kind, "action": "exists", "ok": True,
                                    "note": "already existed - kept (use --recreate to rebuild)"})
                    continue
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
        "kept_existing": sum(1 for f in files for r in f["results"] if r["action"] == "exists"),
    }
    result = {"summary": summary, "files": files}
    save_step(cfg, "deploy", result)
    return result
