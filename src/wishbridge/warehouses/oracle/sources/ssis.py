"""SSIS (SQL Server Integration Services): packages that load a SQL Server warehouse.

Everything WishBridge knows about this warehouse is in this file; the shared steps (convert, deploy,
report...) read it through wishbridge.sources. See sources/__init__.py for what each name means.
"""

from __future__ import annotations

import html
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .base import Source

if TYPE_CHECKING:
    from ..config import ProjectConfig

SOURCES = [Source("ssis", "SSIS", "bladebridge", "ssis")]
LABELS = {"ssis": "SSIS (ETL)"}
ETL_TARGETS = {"ssis": "SPARKSQL"}  # what the ETL converter generates
DEFAULT_DB = {"ssis": "sqlserver"}  # the database the packages load

# --- Package order: which package runs which, and in what order (orchestration.py) ---------------------------
DTS = "{www.microsoft.com/SqlServer/Dts}"


def task_key(name: str) -> str:
    return re.sub(r"[^\w-]", "_", Path(name).stem)[:100]


def _package_name(task: ET.Element, connections: dict[str, str]) -> str | None:
    """The package an Execute Package task runs: project reference, or a file connection (package deployment)."""
    for el in task.iter():
        if el.tag.endswith("PackageName") and (el.text or "").strip():
            return Path(el.text.strip().replace("\\", "/")).stem
    for el in task.iter():
        if el.tag.endswith("Connection"):
            conn = (el.text or "").strip()
            if conn in connections:
                return Path(connections[conn].replace("\\", "/")).stem
    return None


def ssis_plan(cfg: ProjectConfig) -> dict[str, Any]:
    """{'depends': {package: [packages it waits for]}, 'orchestrators': {master: [packages it runs]}}"""
    depends: dict[str, set[str]] = {}
    orchestrators: dict[str, list[str]] = {}
    from ..staging import read_source, source_files

    for rel in source_files(cfg):
        if rel.suffix.lower() != ".dtsx":
            continue
        try:
            root = ET.fromstring(read_source(cfg.input_dir / rel).encode("utf-8"))
        except ET.ParseError:
            continue
        connections = {}
        for cm in root.iter(f"{DTS}ConnectionManager"):
            if cm.attrib.get(f"{DTS}ObjectName"):
                inner = next((x for x in cm.iter(f"{DTS}ConnectionManager") if x is not cm), None)
                cs = (inner.attrib.get(f"{DTS}ConnectionString") if inner is not None else "") or ""
                for key in (cm.attrib.get(f"{DTS}DTSID", ""), cm.attrib.get(f"{DTS}refId", ""), cm.attrib[f"{DTS}ObjectName"]):
                    if key:
                        connections[key] = cs
        execs = [e for e in root.iter(f"{DTS}Executable") if e is not root]
        runs: dict[str, str] = {}  # refId of an Execute Package task -> package it runs
        leaf_types = set()
        for e in execs:
            etype = e.attrib.get(f"{DTS}ExecutableType", "")
            if "ExecutePackageTask" in etype:
                name = _package_name(e, connections)
                if name:
                    runs[e.attrib.get(f"{DTS}refId", "")] = name
            elif not any(c in etype for c in ("Sequence", "ForLoop", "ForEachLoop", "STOCK:SEQUENCE")):
                leaf_types.add(etype)
        if not runs:
            continue
        master = rel.stem
        children = list(dict.fromkeys(runs.values()))
        if not leaf_types:
            orchestrators[master] = children

        def packages_under(ref: str) -> set[str]:
            return {p for r, p in runs.items() if r == ref or r.startswith(ref + "\\")}

        for pc in root.iter(f"{DTS}PrecedenceConstraint"):
            before, after = packages_under(pc.attrib.get(f"{DTS}From", "")), packages_under(pc.attrib.get(f"{DTS}To", ""))
            for a in after:
                depends.setdefault(a, set()).update(before - {a})
        for c in children:
            depends.setdefault(c, set())
    return {"depends": {k: sorted(v) for k, v in depends.items()}, "orchestrators": orchestrators}


# --- What the packages use: tables read and written, procedures called (companion.py) ------------------------
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
    from ..staging import read_source

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
