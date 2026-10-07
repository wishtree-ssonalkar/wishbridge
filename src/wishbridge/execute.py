"""Step 4b - Execute: run the converted ETL notebooks on Databricks as a one-time job.

ETL tools (Informatica, DataStage, SSIS) convert to Databricks notebooks rather than SQL.
`wishbridge deploy` uploads them; this step runs each one (serverless compute) after the
data has been loaded, so `wishbridge reconcile` can compare their output with the legacy
ETL's output.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from .config import ProjectConfig, looks_like_prod
from .dbx import SqlError, Warehouse
from .state import load_state, save_step


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

    wh = wh or Warehouse(cfg)
    runs = []
    for nb in todo:
        entry: dict[str, Any] = {"file": nb["file"], "notebook": nb["path"]}
        try:
            waiter = wh.w.jobs.submit(
                run_name=f"wishbridge {cfg.name}: {nb['file']}",
                tasks=[SubmitTask(task_key="run", notebook_task=NotebookTask(notebook_path=nb["path"]))],
            )
            run = waiter.result(timeout=timedelta(minutes=timeout_min))
            state = run.state.result_state.value if run.state and run.state.result_state else "UNKNOWN"
            entry.update(status="succeeded" if state == "SUCCESS" else "failed", state=state, url=run.run_page_url)
            if state != "SUCCESS" and run.state and run.state.state_message:
                entry["error"] = run.state.state_message[:400]
        except Exception as e:  # failed runs raise from .result(); keep the message and the run link when present
            msg = str(e).strip().splitlines()[0][:400] if str(e).strip() else type(e).__name__
            entry.update(status="failed", error=msg)
        runs.append(entry)

    result = {
        "runs": runs,
        "summary": {"notebooks": len(runs), "succeeded": sum(r["status"] == "succeeded" for r in runs),
                    "failed": sum(r["status"] == "failed" for r in runs)},
    }
    save_step(cfg, "execute", result)
    return result
