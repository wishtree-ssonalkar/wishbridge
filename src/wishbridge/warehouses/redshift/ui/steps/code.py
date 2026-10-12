"""Step 2 - Code: the code overview, reading from the database, adding files."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from wishbridge.warehouses.redshift.staging import read_source
from wishbridge.warehouses.redshift.state import load_state
from wishbridge.warehouses.redshift.ui import helpers as h
from wishbridge.warehouses.redshift.ui.screens import warehouse_ui
from wishbridge.warehouses.redshift.ui.common import code_files_input, code_from_database, next_button
from wishbridge.warehouses.redshift.ui.database import database_tables

ss = st.session_state


def render(cfg, raw, packages) -> None:

    st.subheader("Code overview")
    ov = load_state(cfg).get("overview")
    if ov:
        st.markdown(f"**{ov['files']} files** · {ov['lines']:,} lines · {ov['tables']} tables · {ov['procedures']} "
                    f"procedures · {ov['views']} views · {ov['functions']} functions"
                    + (f" · schemas: {', '.join(ov['schemas'])}" if ov.get("schemas") else ""))
        if ov.get("headline"):
            st.caption(ov["headline"])
        ovf = Path(ov["file"])
        if ovf.is_file():
            st.download_button("📘 Open the code overview (structure, tables, procedures, data flows)",
                               ovf.read_bytes(), file_name=ovf.name, mime="text/html")
    else:
        st.caption("Run Analyze to get a description of the code base: structure, tables, procedures, data flows "
                   "and what needs attention.")

    database_tables(cfg, raw)
    warehouse_ui(cfg).code_extras(cfg, raw)  # per warehouse: ui/warehouses/
    # Folder projects add files here directly; projects that start from the database keep it folded away.
    with (st.expander("Optional: also add code files") if code_from_database(raw) else st.container()):
        uploads = st.file_uploader("Add files", accept_multiple_files=True, type=h.INPUT_EXTENSIONS)
        if uploads and st.button(f"Add {len(uploads)} file(s) to the project"):
            for up in uploads:
                h.save_upload(ss.project, up.name, up.getvalue(), code_files_input(raw))
            st.success(f"Added {len(uploads)} file(s)")
            st.rerun()
    files = h.input_files(ss.project, raw.get("input", "input"))
    if not files:
        st.info("No code yet. Read it from the database above, upload files, or copy them into the input folder.")
    else:
        next_button("Next: run →", "run", "next-code")
        st.dataframe(pd.DataFrame([{"file": str(p.relative_to(cfg.input_dir)), "size (KB)": round(p.stat().st_size / 1024, 1)}
                                   for p in files]), width="stretch", hide_index=True)
        pick = st.selectbox("Preview", [str(p.relative_to(cfg.input_dir)) for p in files])
        st.code(read_source(cfg.input_dir / pick)[:20000], language="sql")
