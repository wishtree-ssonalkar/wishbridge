"""ETL projects (SSIS, Informatica, DataStage): the packages move the data, the warehouse database holds what
they load and call - tables, stored procedures, views and functions.

When the warehouse database's code was read (dbcode.py), it is assessed together with the packages: it is
converted as the database's own SQL (not as ETL), counted in the estimate, deployed with the rest, and the
packages' dependencies are checked against it - which tables they read and write and which procedures they
call, found or missing - so the team knows what else to ask the client for.
"""

from __future__ import annotations

import html
import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from .config import ETL_SOURCES, SOURCES, ProjectConfig
from .state import load_state

FOLDER = "_database_code"  # inside the converted code
DIALECT = {"sqlserver": "mssql", "synapse": "synapse"}  # database type -> WishBridge source for its SQL


def database_code_dir(cfg: ProjectConfig) -> Path:
    from .dbcode import folder

    return folder(cfg)


def applies(cfg: ProjectConfig) -> bool:
    """An ETL project whose warehouse database code was read, next to the packages (not instead of them)."""
    d = database_code_dir(cfg)
    return (cfg.source.key in ETL_SOURCES and d.is_dir() and any(d.rglob("*.sql"))
            and cfg.input_dir.resolve() != d.resolve())


def sub_config(cfg: ProjectConfig) -> ProjectConfig:
    """The same project seen as the warehouse database's SQL (its own working folder under output/)."""
    db = (load_state(cfg).get("inventory") or {}).get("database_type") or "sqlserver"
    src = SOURCES[DIALECT.get(db, "mssql")]
    return replace(cfg, source=src, transpiler=src.transpiler, target_technology="",
                   input_dir=database_code_dir(cfg), output_dir=cfg.output_dir / "database_code_run",
                   overrides_dir=(cfg.overrides_dir / FOLDER) if cfg.overrides_dir else None)


def convert_entries(cfg: ProjectConfig, final_dir: Path, use_ai: bool = False) -> list[dict[str, Any]]:
    """Convert the database code as SQL and return its files for the project's convert result."""
    if not applies(cfg):
        return []
    from .convert import run_convert

    result = run_convert(sub_config(cfg), use_ai)
    out = []
    for f in result["files"]:
        if f.get("converter") == "database":  # table scripts: the main project makes those
            continue
        rel = f"{FOLDER}/{f['file']}"
        final = ""
        if f.get("final") and Path(f["final"]).is_file():
            dest = final_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f["final"], dest)
            final = str(dest)
        out.append({**f, "file": rel, "final": final})
    return out


def analyze_extra(cfg: ProjectConfig) -> dict[str, Any] | None:
    """Analyze the database code too: (programs, hours) to add to the packages' estimate."""
    if not applies(cfg):
        return None
    from .analysis import run_analyze

    sub = run_analyze(sub_config(cfg))
    return {"programs": sub["programs"], "hours": sub["estimated_hours_baseline"], "report_file": sub.get("report_file", "")}


# --- What the packages use ------------------------------------------------------------------------------
_NAME = r"((?:\[[^\]]+\]|\"[^\"]+\"|[\w@#$]+)(?:\s*\.\s*(?:\[[^\]]+\]|\"[^\"]+\"|[\w@#$]+)){0,3})"
_PATTERNS = [
    ("calls", "procedure", re.compile(r"\bEXEC(?:UTE)?\s+(?:@\w+\s*=\s*)?" + _NAME, re.I)),
    ("writes", "table", re.compile(r"\b(?:INSERT\s+(?:INTO\s+)?|UPDATE\s+|DELETE\s+(?:FROM\s+)?|TRUNCATE\s+TABLE\s+|"
                                   r"MERGE\s+(?:INTO\s+)?)" + _NAME, re.I)),
    ("reads", "table", re.compile(r"\b(?:FROM|JOIN|USING)\s+" + _NAME, re.I)),
]
_SQL_ATTR = re.compile(r'SqlStatementSource="([^"]*)"', re.I)
_SQL_PROP = re.compile(r'<property[^>]*name="(SqlCommand|SqlCommandVariable)"[^>]*>([^<]*)</property>', re.I)
_ROWSET = re.compile(r'<property[^>]*name="OpenRowset"[^>]*>([^<]+)</property>', re.I)
_SKIP = re.compile(r"^(?:#|@|sys\.|information_schema\.|tempdb\.|master\.|msdb\.)|^(?:select|set|values|dual)$", re.I)


def _clean(name: str) -> str:
    parts = [p.strip().strip('[]"') for p in name.split(".") if p.strip()]
    return ".".join(parts[-2:]) if parts else ""


def package_usage(cfg: ProjectConfig) -> dict[str, dict[str, Any]]:
    """Objects the SSIS packages use: name -> {kind, how (reads/writes/calls), packages}."""
    from .staging import read_source

    used: dict[str, dict[str, Any]] = {}

    def add(name: str, kind: str, how: str, package: str) -> None:
        name = _clean(name)
        if not name or _SKIP.search(name) or name.lower().startswith(("(", "select")):
            return
        key = name.lower()
        e = used.setdefault(key, {"name": name, "kind": kind, "how": set(), "packages": set()})
        e["how"].add(how)
        e["packages"].add(package)
        if kind == "procedure":
            e["kind"] = "procedure"

    if not cfg.input_dir.is_dir():
        return {}
    for p in sorted(cfg.input_dir.rglob("*.dtsx")):
        text = read_source(p)
        sql = [html.unescape(m.group(1)) for m in _SQL_ATTR.finditer(text)]
        sql += [html.unescape(m.group(2)) for m in _SQL_PROP.finditer(text)]
        for stmt in sql:
            stmt = re.sub(r"--[^\n]*|/\*.*?\*/|'[^']*'", " ", stmt, flags=re.S)
            for how, kind, pat in _PATTERNS:
                for m in pat.finditer(stmt):
                    add(m.group(1), kind, how, p.stem)
        for m in _ROWSET.finditer(text):
            add(html.unescape(m.group(1)), "table", "reads or writes", p.stem)
    return used


def known_objects(cfg: ProjectConfig) -> tuple[set[str], set[str]] | None:
    """(tables, procedures/views/functions) known from the warehouse database or the code; None: nothing read yet."""
    state = load_state(cfg)
    inv, dc = state.get("inventory"), state.get("dbcode")
    if not inv and not dc:
        return None
    tables = {f"{t['schema']}.{t['table']}".lower() for t in (inv or {}).get("tables", [])}
    code = {o["name"].lower() for o in (dc or {}).get("objects", [])}
    views = {o["name"].lower() for o in (dc or {}).get("objects", []) if o["kind"] in ("Views", "Functions")}
    return tables | views, code


def dependencies(cfg: ProjectConfig) -> list[dict[str, Any]]:
    """Every table and procedure the packages use, with found / missing in what was read from the database."""
    if cfg.source.key != "ssis":
        return []
    used = package_usage(cfg)
    known = known_objects(cfg)

    def found(e: dict[str, Any]) -> bool | None:
        if known is None:
            return None
        pool = known[1] if e["kind"] == "procedure" else known[0] | known[1]
        n = e["name"].lower()
        return n in pool or ("." not in n and any(x.endswith("." + n) for x in pool))

    for e in used.values():
        if "reads or writes" in e["how"] and len(e["how"]) > 1:
            e["how"].discard("reads or writes")
    rows = [{"name": e["name"], "kind": e["kind"], "how": ", ".join(sorted(e["how"])),
             "packages": ", ".join(sorted(e["packages"])), "found": found(e)} for e in used.values()]
    return sorted(rows, key=lambda r: (r["found"] is not False, r["kind"] != "procedure", r["name"].lower()))
