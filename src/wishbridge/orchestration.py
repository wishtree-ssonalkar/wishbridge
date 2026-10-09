"""ETL orchestration: which converted notebooks run in which order.

SSIS projects usually have a master package made only of Execute Package tasks joined by precedence
constraints ("load customers, then sales"). LakeBridge converts each package to a notebook but drops the
Execute Package tasks, so the master comes out empty and the order is lost. WishBridge reads the order from
the packages themselves and runs the notebooks as ONE Databricks job whose tasks depend on each other -
the Databricks equivalent of the master package. The job definition is also written to
output/jobs/<project>.json so it can be created as a scheduled Databricks job.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .staging import read_source, source_files

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


def job_tasks(cfg: ProjectConfig, notebooks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, list[str]]]:
    """Tasks for one Databricks job: [{key, file, path, depends_on}], plus orchestrator notebooks left out
    (file -> packages they ran)."""
    plan = ssis_plan(cfg) if cfg.source.key == "ssis" else {"depends": {}, "orchestrators": {}}
    lower = {k.lower(): k for k in plan["depends"]}
    by_package = {Path(nb["file"]).stem.lower(): nb for nb in notebooks}
    orchestrators = {nb["file"]: plan["orchestrators"][m] for m in plan["orchestrators"]
                     for nb in notebooks if Path(nb["file"]).stem.lower() == m.lower()}
    tasks = []
    for nb in notebooks:
        if nb["file"] in orchestrators:
            continue
        stem = Path(nb["file"]).stem
        waits = plan["depends"].get(lower.get(stem.lower(), ""), [])
        tasks.append({"key": task_key(nb["file"]), "file": nb["file"], "path": nb["path"],
                      "depends_on": [task_key(by_package[w.lower()]["file"]) for w in waits if w.lower() in by_package]})
    return tasks, orchestrators


def write_job_definition(cfg: ProjectConfig, tasks: list[dict[str, Any]]) -> Path:
    """A Jobs API definition that recreates the migrated schedule: `databricks jobs create --json @<file>`."""
    job = {
        "name": f"WishBridge - {cfg.name}",
        "tasks": [{"task_key": t["key"], "notebook_task": {"notebook_path": t["path"]},
                   **({"depends_on": [{"task_key": d} for d in t["depends_on"]]} if t["depends_on"] else {})}
                  for t in tasks],
        "max_concurrent_runs": 1,
        "tags": {"created_by": "wishbridge"},
    }
    out = cfg.out("jobs", f"{task_key(cfg.name)}.json")
    out.write_text(json.dumps(job, indent=2), encoding="utf-8")
    return out
