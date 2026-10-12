"""Step 4b - Execute: run the converted ETL notebooks on Databricks as a one-time job.

ETL tools (Informatica, DataStage, SSIS) convert to Databricks notebooks rather than SQL.
`wishbridge deploy` uploads them; this step runs each one (serverless compute) after the
data has been loaded, so `wishbridge reconcile` can compare their output with the legacy
ETL's output.
"""

from __future__ import annotations

import re
from datetime import timedelta
from typing import Any

from .config import ProjectConfig, looks_like_prod
from .dbx import SqlError, Warehouse
from .state import load_state, save_step


def _run_id_from(message: str) -> int | None:
    m = re.search(r"runs?/(\d+)|run_id[=: ]+(\d+)", message)
    return int(m.group(1) or m.group(2)) if m else None


def _task_error(wh: Warehouse, task: Any) -> str:
    """The notebook's own error (e.g. the SQL error), not just 'Workload failed'."""
    try:
        out = wh.w.jobs.get_run_output(task.run_id)
        if out.error:
            return out.error.strip().splitlines()[0][:400]
    except Exception:  # output not available (e.g. cluster never started) - fall back to the state message
        pass
    msg = task.state.state_message if task.state and task.state.state_message else "failed"
    return msg[:400]


def notebooks_to_run(cfg: ProjectConfig) -> list[dict[str, Any]]:
    deploy = load_state(cfg).get("deploy") or {}
    out = []
    for f in deploy.get("files", []):
        for r in f["results"]:
            if r.get("kind") == "notebook" and r.get("action") == "upload" and r.get("ok"):
                out.append({"file": f["file"], "path": r["path"]})
    return out


def run_execute(cfg: ProjectConfig, allow_prod: bool = False, timeout_min: int = 60,
                wh: Warehouse | None = None) -> dict[str, Any]:
    if looks_like_prod(cfg.target_schema) and not allow_prod:
        raise SqlError(f"Target {cfg.target_schema} looks like production. Pass --allow-prod to run notebooks there.")
    todo = notebooks_to_run(cfg)
    if not todo:
        raise SqlError("No uploaded notebooks - run `wishbridge convert` and `wishbridge deploy` first "
                       "(notebooks come from ETL sources such as Informatica).")
    from databricks.sdk.service.jobs import NotebookTask, SubmitTask

    from databricks.sdk.service.jobs import TaskDependency

    from .orchestration import job_tasks, write_job_definition

    wh = wh or Warehouse(cfg)
    tasks, orchestrators = job_tasks(cfg, todo)
    job_file = write_job_definition(cfg, tasks)
    # One job for all notebooks; task dependencies keep the legacy order (e.g. an SSIS master package).
    runs: list[dict[str, Any]] = [
        {"file": f, "status": "skipped", "note": "orchestration only - replaced by the job's task order: " + " -> ".join(p)}
        for f, p in orchestrators.items()]
    entries = {t["key"]: {"file": t["file"], "notebook": t["path"], "task": t["key"], "depends_on": t["depends_on"]}
               for t in tasks}
    run = None
    run_id = None
    try:
        waiter = wh.w.jobs.submit(
            run_name=f"wishbridge {cfg.name}",
            tasks=[SubmitTask(task_key=t["key"], notebook_task=NotebookTask(notebook_path=t["path"]),
                              depends_on=[TaskDependency(task_key=d) for d in t["depends_on"]] or None)
                   for t in tasks],
        )
        run_id = getattr(waiter, "run_id", None)
        run = waiter.result(timeout=timedelta(minutes=timeout_min))
    except Exception as e:  # a failed task makes .result() raise; read the run for per-task results below
        error = str(e).strip().splitlines()[0][:400] if str(e).strip() else type(e).__name__
        run_id = run_id or _run_id_from(error)
        if run_id:
            run = wh.w.jobs.get_run(run_id)
        else:
            for entry in entries.values():
                entry.update(status="failed", error=error)
    if run is not None:
        for t in run.tasks or []:
            entry = entries.get(t.task_key)
            if entry is None:
                continue
            state = t.state.result_state.value if t.state and t.state.result_state else "UNKNOWN"
            status = {"SUCCESS": "succeeded", "UPSTREAM_FAILED": "skipped", "UPSTREAM_CANCELED": "skipped",
                      "EXCLUDED": "skipped"}.get(state, "failed")
            entry.update(status=status, state=state, url=getattr(t, "run_page_url", None) or run.run_page_url)
            if status == "skipped":
                entry["note"] = "not run: a task it depends on failed"
            elif status == "failed":
                entry["error"] = _task_error(wh, t)
        for entry in entries.values():
            entry.setdefault("status", "failed")
            entry.setdefault("url", run.run_page_url)
    runs += list(entries.values())

    result = {
        "runs": runs,
        "job_definition": str(job_file),
        "summary": {"notebooks": len(entries), "succeeded": sum(r["status"] == "succeeded" for r in runs),
                    "failed": sum(r["status"] == "failed" for r in runs),
                    "skipped": sum(r["status"] == "skipped" for r in runs)},
    }
    save_step(cfg, "execute", result)
    return result
