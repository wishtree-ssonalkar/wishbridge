"""Pieces every step of the app uses: icons, the Next button, red notes, run progress, the project's code."""

from __future__ import annotations

import streamlit as st

from wishbridge.warehouses.ssis import runner
from wishbridge.warehouses.ssis.config import ConfigError, load_config
from wishbridge.warehouses.ssis.dbx import SqlError
from wishbridge.warehouses.ssis.lakebridge import LakeBridgeError
from wishbridge.warehouses.ssis.state import load_state
from wishbridge.warehouses.ssis.ui import helpers as h

ss = st.session_state


STEP_ERRORS = (LakeBridgeError, SqlError, ConfigError, RuntimeError, FileNotFoundError, ValueError)
STATUS_ICON = {"ready": "✅ ready", "review": "🟡 review", "needs-fix": "🔴 needs fix"}


def next_button(label: str, step: str, key: str) -> None:
    if st.button(label, type="primary", key=key):
        ss.go_step = step
        st.rerun()


def required(message: str) -> None:
    """A red note right under a required field that is missing or wrong."""
    st.markdown(f":red[{message}]")


ICON = {"pending": "⏸", "running": "⏳", "done": "✅", "failed": "❌", "skipped": "➖"}


def show_run_status(status: dict) -> None:
    for s in status.get("steps", []):
        line = f"{ICON.get(s['state'], '•')} **{s['label']}**"
        if s.get("summary"):
            line += f" — {s['summary']}"
        if s["state"] == "running" and not s.get("summary"):
            line += " — working…"
        st.markdown(line)
        if s.get("error"):
            st.caption(f"{'Skipped' if status.get('keep_going') else 'Stopped'}: {s['error']}")


@st.fragment(run_every="2s")
def run_progress(cfg) -> None:
    """Live progress of the background run; when it ends the whole page refreshes (step bar unlocks)."""
    status = runner.read_status(cfg) or {}
    if status.get("state") != "running":
        st.rerun()
    st.info("⏳ Running in the background. You can wait here; the steps above unlock when it is finished.")
    show_run_status(status)


def code_files_input(raw) -> str:
    """The folder of the client's code files (not the code read from the database)."""
    from wishbridge.warehouses.ssis.dbcode import FOLDER

    current = raw.get("input", "input")
    return raw.get("code_files_input", "input") if current == FOLDER else current


def has_code_files(raw) -> bool:
    return bool(h.input_files(ss.project, code_files_input(raw)))


def use_database_code(raw, on: bool) -> None:
    """Assess the code read from the database (on) or the client's code files; table scripts follow."""
    from wishbridge.warehouses.ssis import schema as schema_mod
    from wishbridge.warehouses.ssis.dbcode import FOLDER

    new = dict(raw)
    current = new.get("input", "input")
    if on and current != FOLDER:
        new["code_files_input"], new["input"] = current, FOLDER
    elif not on and current == FOLDER:
        new["input"] = new.pop("code_files_input", "input")
    else:
        return
    h.save_raw(ss.project, new)
    fresh = load_config(h.project_file(ss.project))
    if load_state(fresh).get("inventory"):
        schema_mod.build_scripts(fresh)  # skip the tables the chosen code creates


def code_from_database(raw) -> bool:
    """The project started from the database (no code folder), or has no code files at all."""
    return raw.get("code_from") == "database" or not has_code_files(raw)
