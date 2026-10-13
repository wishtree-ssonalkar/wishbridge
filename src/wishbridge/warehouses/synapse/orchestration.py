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

# Reading the package order from SSIS packages is in sources/ssis.py.
from .sources.ssis import DTS, _package_name, ssis_plan, task_key  # noqa: E402,F401


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
