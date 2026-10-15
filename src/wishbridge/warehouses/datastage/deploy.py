"""Step 3 - Deploy & validate: run converted DDL in a dev schema and EXPLAIN every query/DML.

DML is only planned (EXPLAIN), never executed, unless --execute-dml is given,
so validation does not change data. Re-running keeps objects that already exist;
--recreate rebuilds them with CREATE OR REPLACE.
"""

from __future__ import annotations

import re
from pathlib import Path
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


def source_schemas(cfg: ProjectConfig) -> set[str]:
    """The schemas of the client's warehouse: from the tables and the code read from its database."""
    inv = load_state(cfg).get("inventory") or {}
    names = {t["schema"] for t in inv.get("tables", []) if t.get("schema")}
    code = cfg.path.parent / "database_code"
    if code.is_dir():
        names |= {p.name for p in code.iterdir() if p.is_dir()}
    return names


def deploy_schema_map(cfg: ProjectConfig) -> dict[str, str]:
    """Where each source schema goes in Databricks: the mapping in Settings, else the project's target schema - so
    `dw.FactDailySales` is created and used as `<catalog>.<schema>.FactDailySales`, whatever catalog is chosen now."""
    explicit = {k.lower() for k in cfg.schema_map}
    mapping = {s: cfg.target_schema for s in sorted(source_schemas(cfg)) if s.lower() not in explicit}
    mapping.update(cfg.schema_map)
    return mapping


def workspace_folder(wh: Warehouse, cfg: ProjectConfig) -> str:
    user = wh.w.current_user.me().user_name
    return f"/Workspace/Users/{user}/wishbridge/{cfg.name}"


def deployable_file(cfg: ProjectConfig, f: dict[str, Any]) -> Path | None:
    """The file to deploy: the hand fix (overrides/) when there is one - so fixes reach Databricks without
    converting again - else the converted file."""
    if cfg.overrides_dir:
        fixed = cfg.overrides_dir / f["file"]
        if fixed.is_file():
            return fixed
    final = Path(f["final"]) if f.get("final") else None
    return final if final is not None and final.is_file() else None


def _upload(wh: Warehouse, cfg: ProjectConfig, f: dict[str, Any], schema_map: dict[str, str] | None = None
            ) -> dict[str, Any]:
    """Upload a converted notebook or helper module to the workspace (notebooks run with `wishbridge execute`)."""
    from databricks.sdk.service.workspace import ImportFormat

    src = deployable_file(cfg, f)
    if src is None:
        return {"file": f["file"], "statements": 1, "passed": 0, "ok": False, "results": [
            {"n": 1, "kind": f["kind"], "action": "skip", "ok": False, "error": "Not converted - nothing to upload"}]}
    text = src.read_text(encoding="utf-8-sig")
    if f["kind"] == "notebook":
        from .rules import map_schemas_in_notebook

        text = with_target_schema(map_schemas_in_notebook(text, schema_map or {}), cfg)
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
    from dataclasses import replace

    from .fit import excluded_files
    from .rules import map_schemas

    # The table scripts are rebuilt for today's target (the catalog may have changed since the assessment), and the
    # warehouse's own schemas (dw, stg, ...) are mapped to the target in every statement.
    smap = deploy_schema_map(cfg)
    fresh: dict[str, Path] = {}
    if (load_state(cfg).get("inventory") or {}).get("tables"):
        from .schema import build_scripts

        try:
            fresh = {s["file"]: Path(s["path"]) for s in build_scripts(replace(cfg, schema_map=smap))["files"]}
        except ValueError:
            fresh = {}

    left_out = excluded_files(cfg, load_state(cfg).get("fit"))

    files: list[dict[str, Any]] = []
    for f in convert["files"]:
        if f["file"] in left_out:
            files.append({"file": f["file"], "statements": 0, "passed": 0, "ok": True, "results": [
                {"n": 1, "kind": "file", "action": "left-out", "ok": True,
                 "note": f"Not deployed - fit check: {left_out[f['file']]} (scope: recommended)"}]})
            continue
        if f.get("kind", "sql") != "sql":
            files.append(_upload(wh, cfg, f, smap))
            continue
        src = deployable_file(cfg, f)
        if src is None:
            files.append({"file": f["file"], "statements": 1, "passed": 0, "ok": False, "results": [
                {"n": 1, "kind": "file", "action": "skip", "ok": False,
                 "error": "Not converted - nothing to deploy (see the convert results)"}]})
            continue
        hand_fixed = bool(cfg.overrides_dir) and (cfg.overrides_dir / f["file"]).is_file()
        if f["file"] in fresh and not hand_fixed:
            src = fresh[f["file"]]
        sql = map_schemas(src.read_text(encoding="utf-8-sig"), smap)
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
        "left_out": len(left_out),
    }
    result = {"summary": summary, "files": files}
    save_step(cfg, "deploy", result)
    return result
