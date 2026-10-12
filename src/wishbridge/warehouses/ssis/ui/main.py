"""SSIS: everything a project does once it is open - the step bar and the steps.

The shared main screen (wishbridge/ui/app.py) opens the project and hands over to run(). Changing anything
in this folder changes SSIS projects only.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.state import load_state
from wishbridge.warehouses.ssis.ui import helpers as h

ss = st.session_state


def run(raw: dict) -> None:
    cfg = load_config(h.project_file(ss.project))
    from wishbridge.warehouses.ssis import runner

    LABELS = {"settings": "Settings", "code": "Code", "run": "Run", "data": "Map the data", "results": "Report",
              "fix": "Fix code", "send": "Share the zip"}
    from_package = (Path(ss.project) / "opened_from_package.json").exists()
    if cfg.phase == "assessment":
        # At the client: everything ends up in the zip, so the last step is sending it. Fixing happens later,
        # in the project opened from that zip.
        ORDER = ["settings", "code", "run", "results"] + (["fix"] if from_package else []) + ["send"]
    else:
        # The report right after the run gives the overview; it is updated again after the data copy.
        ORDER = ["settings", "code", "run", "results", "data", "fix"]
    state_now = load_state(cfg)
    running = runner.is_running(cfg)
    has_code = bool(h.input_files(ss.project, raw.get("input", "input")))
    has_results = any(k in state_now for k in ("analyze", "convert", "fit", "deploy", "reconcile"))
    packages = sorted((cfg.output_dir / "review_package").glob("*.zip"), key=lambda q: q.stat().st_mtime)
    done = {
        "settings": bool(ss.get(f"settings_done:{ss.project}")) or has_results,  # confirmed with Next (or used before)
        "code": has_code,
        "run": has_results and not running,
        "data": bool((state_now.get("load") or {}).get("executed")) and not running,
        "results": has_results and not running,
        "fix": False,
        "send": bool(ss.get(f"sent:{ss.project}")),
    }


    def reachable(key: str) -> bool:
        if running:
            return key in ("run", "data")  # while a run (or the data copy) is going, stay there to follow it
        if key == "results":
            return all(done[k] for k in ("settings", "code", "run"))  # copying the data is not required to look
        if key == "fix":
            return "convert" in state_now and all(done[k] for k in ("settings", "code"))
        if key == "send":
            return bool(packages) and all(done[k] for k in ("settings", "code", "run"))
        return all(done[k] for k in ORDER[:ORDER.index(key)])


    if ss.get("step") not in ORDER or ss.get("step_project") != ss.project:
        ss.step_project = ss.project
        ss.step = next((k for k in ORDER if not done[k]), ORDER[-1])  # first unfinished step
    if ss.get("go_step") is not None:
        _go = ss.pop("go_step")
        if _go in ORDER:
            ss.step = _go
    if running and ss.step not in ("run", "data"):
        ss.step = "run"
    if not reachable(ss.step):
        ss.step = [k for k in ORDER if reachable(k)][-1]
    STEP = ss.step

    cols = st.columns(len(ORDER))
    for i, (col, key) in enumerate(zip(cols, ORDER)):
        mark = "✓ " if done[key] else ""
        if col.button(f"{i + 1} · {mark}{LABELS[key]}", key=f"step-{key}", width="stretch", disabled=not reachable(key),
                      type="primary" if key == STEP else "secondary"):
            ss.step = key
            st.rerun()
    if running:
        st.caption("🔒 A run is going in the background - the other steps open when it has finished.")
    st.divider()


    from wishbridge.warehouses.ssis.ui.steps import code, data, fix, report, run, settings, share

    {"settings": settings, "code": code, "run": run, "data": data, "results": report, "send": share,
     "fix": fix}[STEP].render(cfg, raw, packages)
