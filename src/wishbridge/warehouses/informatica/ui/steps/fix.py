"""Fix code (later, at Wishtree): the original next to the Databricks version; save hand fixes."""

from __future__ import annotations

from pathlib import Path

import streamlit as st

from wishbridge.warehouses.informatica.convert import original_file
from wishbridge.warehouses.informatica.staging import read_source
from wishbridge.warehouses.informatica.state import load_state
from wishbridge.warehouses.informatica.ui import helpers as h
from wishbridge.warehouses.informatica.ui.common import STATUS_ICON

ss = st.session_state


def render(cfg, raw, packages) -> None:
    state = load_state(cfg)
    conv = state.get("convert")
    st.subheader("Fix code (later)" if cfg.phase == "assessment" else "Fix code")
    st.markdown(
        "Some files cannot be converted completely by a tool. This tab is where the team finishes them - "
        "**later, at Wishtree, not at the client**.\n\n"
        "1. Pick a file. Files that need a fix come first; the red and yellow boxes say what to change and on which line.\n"
        "2. The client's **original** code is on the left, the **Databricks version** on the right. Edit the right side.\n"
        "3. Press **Save as manual fix**. Your version is kept in the project's `overrides` folder and used on every "
        "later run, so a new conversion never overwrites it.")
    if cfg.phase == "code-migration" and conv:
        st.caption("Fix what the assessment left open, then go on: the run converts the code again and keeps every "
                   "manual fix.")
        if st.button("Next: settings →", type="primary", key="next-fix"):
            ss[f"fix_done:{ss.project}"] = True
            ss.go_step = "settings"
            st.rerun()
    if cfg.phase == "assessment" and not ss.get("fixing_now"):
        st.info("At the client you don't need this tab: *Run > Assess and save everything* puts the code and the list "
                "of open items in one zip. Open that zip at Wishtree (sidebar > *Open a package*) and fix the code there.")
        if conv and st.button("I want to fix files now"):
            ss.fixing_now = True
            st.rerun()
    elif not conv:
        st.info("Run Convert first.")
    else:
        order = {"needs-fix": 0, "review": 1, "ready": 2}
        files = sorted(conv["files"], key=lambda f: (order.get(f["status"], 3), f["file"]))
        if not files:
            st.info("Nothing to fix: the conversion produced no files.")
            return
        pick = st.selectbox("File", [f["file"] for f in files],
                            format_func=lambda n: f"{n} — {next(STATUS_ICON.get(f['status'], f['status']) for f in files if f['file'] == n)}")
        f = next(x for x in files if x["file"] == pick)
        todo = [x for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
        for x in todo:
            (st.error if x["severity"] == "error" else st.warning)(f"Line {x['line']}: {x['message']}")
        left, right = st.columns(2)
        with left:
            st.markdown("**Original**")
            orig = Path(f["input"]) if f["input"] else None
            if f.get("converter") != "database" and (orig is None or not orig.is_file()):
                orig = original_file(cfg, f["file"])  # older runs, or a project opened from a zip elsewhere
            if f.get("converter") == "database":
                st.info("Made from the table definitions read from the database: there is no original file.")
            elif orig is None or not orig.is_file():
                st.warning(f"The original of {f['file']} is not in this project's input folder.")
            else:
                st.caption(str(orig.relative_to(cfg.input_dir)) if orig.is_relative_to(cfg.input_dir) else str(orig))
                st.code(read_source(orig), language="xml" if orig.suffix.lower() in (".dtsx", ".xml", ".dtproj") else "sql")
        with right:
            ovr = h.override_path(ss.project, f["file"], raw.get("overrides", "overrides"))
            final = Path(f["final"])
            if not ovr.exists() and not final.exists():
                st.info("The converted file is not on disk (a convert may be running). Run Convert again, then come back.")
            else:
                start = ovr.read_text(encoding="utf-8-sig") if ovr.exists() else final.read_text(encoding="utf-8-sig")
                st.markdown("**Databricks version** " + ("(manual fix)" if ovr.exists() else "(converted — edit to fix)"))
                edited = st.text_area("Databricks SQL", value=start, height=420, label_visibility="collapsed", key=f"edit-{pick}")
                b1, b2 = st.columns(2)
                if b1.button("💾 Save as manual fix", type="primary", disabled=edited == start):
                    ovr.parent.mkdir(parents=True, exist_ok=True)
                    ovr.write_text(edited, encoding="utf-8")
                    st.success("Saved. Run Convert again to apply it, then Deploy to check it on Databricks.")
                if ovr.exists() and b2.button("🗑 Remove manual fix"):
                    ovr.unlink()
                    st.success("Removed. The converted version will be used on the next run.")
                    st.rerun()
