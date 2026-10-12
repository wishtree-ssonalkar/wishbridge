"""Migration: map the source tables to the Databricks tables and copy the data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from wishbridge.warehouses.informatica import runner
from wishbridge.warehouses.informatica.config import load_config
from wishbridge.warehouses.informatica.dbx import SqlError, Warehouse
from wishbridge.warehouses.informatica.state import load_state
from wishbridge.warehouses.informatica.ui import helpers as h
from wishbridge.warehouses.informatica.ui.common import next_button, run_progress, show_run_status

ss = st.session_state


def render(cfg, raw, packages) -> None:
    from wishbridge.warehouses.informatica import mapping

    st.subheader("Map the data and copy it")
    st.markdown("The tables are in Databricks (the code migration created them). Choose which source table goes into which Databricks "
                "table, check the columns, then copy the data. Nothing is copied until you press *Copy the data*.")
    if runner.is_running(cfg):
        st.markdown("#### Copying…")
        run_progress(cfg)
        st.stop()
    federation = cfg.data_method == "federation"
    if federation and not cfg.source_catalog:
        st.warning("Create the source database connection in **Settings › Source database** first.")
        st.stop()

    saved = h.table_rows(raw)
    if st.button("🔄 Propose the mapping", help="Lists the source tables and the Databricks tables and pairs them by name."):
        with st.spinner("Reading the table lists..."):
            try:
                wh = Warehouse(cfg)
                targets = mapping.target_tables(cfg, wh)
                ss.map_rows = mapping.propose(cfg, mapping.source_tables(cfg, wh), targets)
                ss.map_targets = targets
                ss.pop("map_check", None)
            except (SqlError, ValueError) as e:
                st.error(f"Could not read the tables: {e}")
    rows = ss.get("map_rows") or [{"copy": True, "source": r["source"], "target": r["target"] or cfg.map_table(r["source"])}
                                  for r in saved]
    if not rows:
        st.info("Press **Propose the mapping** to list the source tables and pair them with the Databricks tables.")
    else:
        targets = sorted(set(ss.get("map_targets") or []) | {r["target"] for r in rows if r["target"]})
        df = pd.DataFrame([{"copy": r["copy"], "source": r["source"], "target": r["target"]} for r in rows])
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key="map_editor", num_rows="fixed",
            column_config={
                "copy": st.column_config.CheckboxColumn("Copy", width="small"),
                "source": st.column_config.TextColumn("Source table", disabled=True),
                "target": st.column_config.SelectboxColumn("Databricks table", options=targets, required=True),
            })
        if ss.get("map_rows"):
            missing = [r["target"] for r in ss.map_rows if not r.get("exists")]
            if missing:
                st.caption(f":red[{len(missing)} Databricks table(s) do not exist yet] (e.g. `{missing[0]}`): run "
                           "*Create the tables* in Run, or pick another table.")
        picked = [r for r in edited.to_dict("records") if r["copy"] and r["target"]]
        if st.button(f"💾 Save and check the columns ({len(picked)} table(s))", type="primary", disabled=not picked):
            new = dict(raw)
            new["data"] = {**(raw.get("data") or {}),
                           "tables": h.rows_to_tables(picked, (raw.get("data") or {}).get("tables"))}
            h.save_raw(ss.project, new)
            with st.spinner("Reading the columns of both sides..."):
                try:
                    ss.map_check = mapping.check(load_config(h.project_file(ss.project)))
                except (SqlError, ValueError) as e:
                    st.error(f"Could not check the columns: {e}")
            st.rerun()

    check = ss.get("map_check")
    if check:
        icon = {"ready": "✅", "check columns": "⚠️", "compare only": "➖", "no target table": "❌",
                "no source table": "❌", "no matching columns": "❌"}
        st.markdown("**Columns**")
        st.dataframe(pd.DataFrame([{
            "": icon.get(c["status"], ""), "source": c["source"], "Databricks table": c["target"], "status": c["status"],
            "columns paired": f"{len(c.get('pairs', {}))}/{len(c.get('target_columns', []))}" if "pairs" in c else "",
            "note": c.get("detail") or (", ".join(f"no source for {x}" for x in c.get("target_without_source", []))
                                        + ("; " if c.get("target_without_source") and c.get("source_not_copied") else "")
                                        + ", ".join(f"{x} not copied" for x in c.get("source_not_copied", [])))}
            for c in check]), hide_index=True, width="stretch")
        for c in check:
            if c["status"] not in ("check columns", "no matching columns"):
                continue
            with st.expander(f"Map the columns: {c['source']} → {c['target']}"):
                cdf = pd.DataFrame([{"Databricks column": t, "source column": c["pairs"].get(t, "")}
                                    for t in c["target_columns"]])
                key = f"cols:{c['target']}"
                ed = st.data_editor(cdf, hide_index=True, width="stretch", key=key, num_rows="fixed", column_config={
                    "Databricks column": st.column_config.TextColumn(disabled=True),
                    "source column": st.column_config.SelectboxColumn(options=[""] + c["source_columns"]),
                })
                st.caption("Leave a column empty to keep it NULL (or its default).")
                if st.button("💾 Save these columns", key=f"save-{key}"):
                    chosen = {r["Databricks column"]: r["source column"] for r in ed.to_dict("records") if r["source column"]}
                    tables = [dict(t) if isinstance(t, dict) else {"source": t}
                              for t in (raw.get("data") or {}).get("tables") or []]
                    for t in tables:
                        if t["source"] == c["source"]:
                            t["columns"] = chosen
                    new = dict(raw)
                    new["data"] = {**(raw.get("data") or {}), "tables": tables}
                    h.save_raw(ss.project, new)
                    with st.spinner("Checking again..."):
                        ss.map_check = mapping.check(load_config(h.project_file(ss.project)))
                    st.rerun()

        blocking = [c for c in check if c["status"] in ("no target table", "no source table", "no matching columns")]
        if blocking:
            st.markdown(f":red[Fix the {len(blocking)} table(s) marked ❌ (or untick them above) before copying.]")
        mode = "replaces what is in the Databricks tables" if cfg.load_mode == "overwrite" else "adds to the Databricks tables"
        st.caption(f"The copy {mode} (load mode in Settings) and then compares source and target.")
        if st.button("▶ Copy the data", type="primary", disabled=bool(blocking)):
            runner.start(cfg, ["load", "reconcile", "report"], {"execute": True}, keep_going=False)
            st.rerun()

    status = runner.read_status(cfg)
    if status and "load" in [x["name"] for x in status.get("steps", [])] and status.get("state") != "running":
        st.markdown("#### Last copy")
        show_run_status(status)
        ld = load_state(cfg).get("load") or {}
        if ld.get("executed"):
            st.dataframe(pd.DataFrame([{"source": t["source"], "Databricks table": t["target"], "status": t["status"],
                                        "rows": t.get("rows"), "error": t.get("error", "")} for t in ld["tables"]]),
                         hide_index=True, width="stretch")
            next_button("Next: see the updated report →", "results", "next-data")
