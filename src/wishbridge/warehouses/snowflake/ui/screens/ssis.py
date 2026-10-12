"""Screens for SSIS: as every ETL tool (etl.py), plus what the packages read, write and call."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from .etl import ASSESS_TOGETHER, READ_CODE_DEFAULT, database_text, open_database_section  # noqa: F401


def package_dependencies(cfg) -> None:
    """SSIS: what the packages read, write and call - found or missing in the warehouse database."""
    from wishbridge.warehouses.snowflake.companion import dependencies

    deps = dependencies(cfg)
    if not deps:
        return
    st.markdown("#### What the packages use")
    missing = [d for d in deps if d["found"] is False]
    if deps[0]["found"] is None:
        st.info("Read the warehouse database (above) to check that every table and procedure the packages use is "
                "there.")
    elif missing:
        procs = [d["name"] for d in missing if d["kind"] == "procedure"]
        st.warning(f"**{len(missing)} object(s) the packages use are not in what was read from the database**"
                   + (f" — including procedure(s) {', '.join(procs[:5])}" if procs else "")
                   + ". Ask the client for them (or check the database name). Tables that are only read may belong "
                     "to the source system.")
    else:
        st.success("Everything the packages use is in what was read from the database.")
    icon = {True: "✅ found", False: "❌ missing", None: "— not checked"}
    st.dataframe(pd.DataFrame([{"object": d["name"], "kind": d["kind"], "how": d["how"], "packages": d["packages"],
                                "in the database": icon[d["found"]]} for d in deps]), hide_index=True, width="stretch")


def code_extras(cfg, raw) -> None:
    package_dependencies(cfg)


def report_extras(cfg, state) -> None:
    package_dependencies(cfg)
