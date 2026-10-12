"""Step 3 - Run: assess (one button) or migrate (chosen steps) in the background."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from wishbridge.warehouses.snowflake import runner
from wishbridge.warehouses.snowflake.state import load_state
from wishbridge.warehouses.snowflake.ui.common import next_button, run_progress, show_run_status


def render(cfg, raw, packages) -> None:
    offline = cfg.phase == "assessment"
    status = runner.read_status(cfg)
    if status and "load" in [x["name"] for x in status.get("steps", [])]:
        status = None  # the last run was the data copy: shown in Map the data
    if runner.is_running(cfg):
        st.subheader("Running…")
        run_progress(cfg)
    elif offline:
        st.subheader("First run: assess the code and save everything")
        st.markdown(
            "One button does it all, on this computer only (nothing goes to Databricks or the client's database):\n"
            "1. **Describe** the code: what is there and whether it really is a data warehouse.\n"
            "2. **Analyze** it: size, complexity and effort.\n"
            "3. **Convert** it to Databricks.\n"
            "4. Write the **report** and the **code overview**.\n"
            "5. Save **everything in one zip**: the code before and after conversion, the report, the code overview, "
            "the list of open items and **dummy data** - 5 made-up rows per table, built from the column types "
            "only (no client data).\n\n"
            "Nothing needs to be fixed now. A step that cannot run here is noted and skipped; the rest still runs.")
        if st.button("▶ Assess and save everything", type="primary"):
            runner.start(cfg, runner.ASSESSMENT_STEPS, {}, keep_going=True)
            st.rerun()
    else:
        st.subheader("Run the migration")
        c1, c2, c3 = st.columns(3)
        with c1:
            do_analyze = st.checkbox("1. Analyze the code", value=True)
            do_convert = st.checkbox("2. Convert to Databricks SQL", value=True)
            use_ai = st.checkbox("…with Claude suggestions", value=cfg.ai_enabled, disabled=not do_convert)
        with c2:
            do_deploy = st.checkbox("3. Create the tables and code in the test schema", value=True)
            recreate = st.checkbox("…rebuild objects that already exist", value=False, disabled=not do_deploy)
        with c3:
            do_report = st.checkbox("4. Build the report", value=True)
            st.caption("Copying the data comes next, in *Map the data*.")
        st.caption(f"Target: `{cfg.target_schema}` · conversion: {'automatic' if cfg.auto_converter else 'one method'}"
                   f" · source: {cfg.source.label}")
        chosen = [n for n, on in (("analyze", do_analyze), ("convert", do_convert), ("deploy", do_deploy),
                                  ("report", do_report)) if on]
        if st.button("▶ Start", type="primary", disabled=not chosen):
            runner.start(cfg, chosen, {"ai": use_ai, "recreate": recreate}, keep_going=False)
            st.rerun()
    if status and status.get("state") in ("done", "interrupted") and not runner.is_running(cfg):
        st.markdown("#### Last run")
        if status["state"] == "interrupted":
            st.warning("The last run stopped before it finished (the app or computer was closed). Start it again.")
        show_run_status(status)
        pkg = Path(status.get("package") or "")
        if status.get("package") and pkg.is_file():
            st.success(f"Everything is saved in **{pkg.name}** ({pkg.stat().st_size // 1024:,} KB), also kept at `{pkg}`.")
            st.download_button("⬇ Download the zip", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")
        if load_state(cfg):
            if cfg.phase == "assessment":
                next_button("Next: see the report →", "results", "next-run")
            else:
                next_button("Next: see the report →", "results", "next-run")
