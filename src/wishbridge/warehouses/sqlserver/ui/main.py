"""SQL Server / Azure SQL: everything a project does once it is open - the step bar and the steps.

The shared main screen (wishbridge/ui/app.py) opens the project and hands over to run(). Changing anything
in this folder changes SQL Server / Azure SQL projects only.
"""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from wishbridge.warehouses.sqlserver.config import ConfigError, load_config
from wishbridge.warehouses.sqlserver.state import load_state
from wishbridge.warehouses.sqlserver.ui import helpers as h

ss = st.session_state


def run(raw: dict) -> None:
    try:
        cfg = load_config(h.project_file(ss.project))  # this warehouse's own rules for project.yml
    except (ConfigError, KeyError, TypeError, ValueError) as e:
        st.error(f"This project's settings file can't be read: {str(e).splitlines()[0]}")
        st.markdown(f"Open `{h.project_file(ss.project)}` in a text editor and correct it.")
        st.stop()
    from wishbridge.warehouses.sqlserver import runner

    LABELS = {"settings": "Settings", "code": "Code", "run": "Run", "data": "Map the data",
              "results": {"code-migration": "Deployment report", "data-migration": "Data report"}.get(cfg.phase, "Report"),
              "fix": "Fix code", "send": "Share the zip"}
    from_package = (Path(ss.project) / "opened_from_package.json").exists()
    if cfg.phase == "assessment":
        # At the client: everything ends up in the zip, so the last step is sending it. Fixing happens later,
        # in the project opened from that zip.
        ORDER = ["settings", "code", "run", "results"] + (["fix"] if from_package else []) + ["send"]
    elif cfg.phase == "code-migration":
        # From an assessment (its converted code is here): fix the open items first, then settings, run (it converts
        # again and keeps the hand fixes) and the report. A new project has nothing to fix yet: fixing comes last.
        fix_first = "convert" in load_state(cfg)
        # From an assessment the code is already analyzed and converted: no Code tab, and Run only deploys.
        ORDER = (["fix", "settings", "run", "results"] if fix_first
                 else ["settings", "code", "run", "results", "fix"])
        if fix_first:
            LABELS["run"] = "Deploy"
    else:
        # Data migration: the tables exist in Databricks (code migration); map, copy, then the report.
        ORDER = ["settings", "data", "results"]
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
        "fix": bool(ss.get(f"fix_done:{ss.project}")) or "deploy" in state_now,  # Next on Fix code, or deployed
        "send": bool(ss.get(f"sent:{ss.project}")),
    }


    def reachable(key: str) -> bool:
        if running:
            return key in ("run", "data")  # while a run (or the data copy) is going, stay there to follow it
        if key == "results":
            if cfg.phase == "data-migration":
                return done["settings"] and any(k in state_now for k in ("load", "reconcile", "deploy", "convert"))
            return all(done[k] for k in ("settings", "code", "run"))  # copying the data is not required to look
        if key == "fix":
            if ORDER[0] == "fix":
                return "convert" in state_now  # the first tab: open as soon as there is converted code
            return "convert" in state_now and all(done[k] for k in ("settings", "code"))
        if key == "send":
            return bool(packages) and all(done[k] for k in ("settings", "code", "run"))
        return all(done[k] for k in ORDER[:ORDER.index(key)])


    if ss.get("step") not in ORDER or ss.get("step_project") != ss.project:
        ss.step_project = ss.project
        ss.step = ORDER[0]  # an opened project starts at the first tab
    if ss.get("go_step") is not None:
        _go = ss.pop("go_step")
        if _go in ORDER:
            ss.step = _go
    if running and ss.step not in ("run", "data"):
        ss.step = "run"
    if not reachable(ss.step):
        ss.step = [k for k in ORDER if reachable(k)][-1]
    STEP = ss.step
    ss.step_order, ss.step_labels = ORDER, LABELS  # Settings' Next goes to the tab that really comes next

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


    from wishbridge.warehouses.sqlserver.ui.steps import code, data, fix, report, run, settings, share

    {"settings": settings, "code": code, "run": run, "data": data, "results": report, "send": share,
     "fix": fix}[STEP].render(cfg, raw, packages)
