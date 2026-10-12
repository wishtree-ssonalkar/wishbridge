"""Run pipeline steps in a background process, so the app can be used (and left) while they run.

The app starts `python -m wishbridge pipeline ...`; the process writes its progress to
output/logs/run_status.json after every step. The Run step of the app reads that file to show live
progress, also after the user moved to another step and came back.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

from .config import ProjectConfig

STEP_LABELS = {
    "describe": "Describe the code (fit check and code overview)",
    "analyze": "Analyze the code",
    "convert": "Convert",
    "deploy": "Deploy to the test schema",
    "load": "Copy the data",
    "reconcile": "Reconcile the data",
    "report": "Report",
    "package": "Save everything in one zip",
}
ASSESSMENT_STEPS = ["describe", "analyze", "convert", "report", "package"]


def status_file(cfg: ProjectConfig) -> Path:
    return cfg.output_dir / "logs" / "run_status.json"


def _step(cfg: ProjectConfig, name: str, opts: dict[str, Any]) -> tuple[str, Any]:
    """Run one step; returns (summary, result)."""
    if name == "describe":
        from .fit import run_fit
        from .overview import build_overview

        f = run_fit(cfg)
        build_overview(cfg)
        return f"{len(f['objects'])} objects · {f['verdict']}", f
    if name == "analyze":
        from .analysis import run_analyze

        a = run_analyze(cfg)
        return f"{len(a['programs'])} files, estimate {a['estimated_hours_baseline']} h", a
    if name == "convert":
        from .convert import run_convert

        c = run_convert(cfg, bool(opts.get("ai")))
        s = c["summary"]
        return f"{s['ready']} ready, {s['review']} review, {s['needs_fix']} need fixes", c
    if name == "deploy":
        from .deploy import run_deploy

        d = run_deploy(cfg, recreate=bool(opts.get("recreate")))
        return f"{d['summary']['statements_ok']}/{d['summary']['statements']} statements OK", d
    if name == "load":
        from .data import run_load

        execute = bool(opts.get("execute"))
        ld = run_load(cfg, execute=execute)
        s = ld["summary"]
        return (f"{s['loaded']}/{s['tables']} tables loaded" if execute else f"plan for {s['tables']} tables written"), ld
    if name == "reconcile":
        from .reconcile import run_reconcile

        r = run_reconcile(cfg)
        return f"{r['summary']['matched']}/{r['summary']['tables']} tables match", r
    if name == "report":
        from .report import build_report

        return "report.html written", str(build_report(cfg))
    if name == "package":
        from .package import build_package

        p = build_package(cfg)
        return p.name, str(p)
    raise ValueError(f"Unknown step {name}")


def run_pipeline(cfg: ProjectConfig, steps: list[str], opts: dict[str, Any], keep_going: bool,
                 progress: Callable[[dict], None] = lambda s: None) -> dict:
    """Run the steps in order. keep_going: note a failed step and continue (assessment) instead of stopping."""
    status = {"started": datetime.now().isoformat(timespec="seconds"), "pid": os.getpid(), "state": "running",
              "keep_going": keep_going,
              "steps": [{"name": s, "label": STEP_LABELS[s], "state": "pending", "summary": "", "error": ""} for s in steps]}
    out = status_file(cfg)

    def save() -> None:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(status, indent=2), encoding="utf-8")
        progress(status)

    save()
    stopped = False
    for st in status["steps"]:
        if stopped:
            st["state"] = "skipped"
            continue
        st["state"] = "running"
        save()
        try:
            st["summary"], result = _step(cfg, st["name"], opts)
            st["state"] = "done"
            if st["name"] == "package":
                status["package"] = result
        except Exception as e:  # noqa: BLE001 - every failure is reported in the status, never lost
            st["state"], st["error"] = "failed", (str(e).strip().splitlines() or [type(e).__name__])[0][:500]
            stopped = not keep_going
        save()
    status["state"] = "done"
    status["finished"] = datetime.now().isoformat(timespec="seconds")
    save()
    return status


def read_status(cfg: ProjectConfig) -> dict | None:
    try:
        data = json.loads(status_file(cfg).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    starting = data.get("pid") is None and data.get("started", "") >=         (datetime.now().replace(microsecond=0) - timedelta(seconds=60)).isoformat()
    if data.get("state") == "running" and not starting and not _alive(data.get("pid")):
        data["state"] = "interrupted"  # the process ended without finishing (closed, crashed)
    return data


def is_running(cfg: ProjectConfig) -> bool:
    s = read_status(cfg)
    return bool(s and s.get("state") == "running")


def _alive(pid: Any) -> bool:
    if not isinstance(pid, int):
        return False
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def start(cfg: ProjectConfig, steps: list[str], opts: dict[str, Any], keep_going: bool) -> None:
    """Start the pipeline in a background process."""
    cmd = [sys.executable, "-m", "wishbridge", "pipeline", "-c", str(cfg.path), "--steps", ",".join(steps),
           "--options", json.dumps(opts)] + (["--keep-going"] if keep_going else [])
    # Mark it running at once (before the process starts), so the page shows progress straight away.
    status_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    status_file(cfg).write_text(json.dumps({
        "state": "running", "pid": None, "started": datetime.now().isoformat(timespec="seconds"), "keep_going": keep_going,
        "steps": [{"name": s, "label": STEP_LABELS[s], "state": "pending", "summary": "", "error": ""} for s in steps]}),
        encoding="utf-8")
    subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
