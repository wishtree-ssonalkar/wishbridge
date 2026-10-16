"""Migration: map the source tables to the Databricks tables and copy the data."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from wishbridge.warehouses.redshift import runner
from wishbridge.warehouses.redshift.config import load_config
from wishbridge.warehouses.redshift.dbx import SqlError, Warehouse
from wishbridge.warehouses.redshift.state import load_state
from wishbridge.warehouses.redshift.ui import helpers as h
from wishbridge.warehouses.redshift.ui.common import next_button, run_progress, show_run_status

ss = st.session_state


def render(cfg, raw, packages) -> None:
    from wishbridge.warehouses.redshift import mapping

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

    def load_lists(propose: bool) -> None:
        with st.spinner("Reading the table lists..."):
            try:
                wh = Warehouse(cfg)
                targets = mapping.target_tables(cfg, wh)
                sources = mapping.source_tables(cfg, wh)
                ss.map_targets, ss.map_sources = targets, sources
                if propose:
                    ss.map_rows = mapping.propose(cfg, sources, targets)
                ss.map_manual = True
                ss.pop("map_check", None)
            except (SqlError, ValueError) as e:
                st.error(f"Could not read the tables: {e}")

    b1, b2, b3 = st.columns(3)
    if b1.button("🔄 Propose the mapping", width="stretch",
                 help="Lists the source tables and the Databricks tables and pairs them by name."):
        load_lists(propose=True)
    if b2.button("✏️ Map by hand", width="stretch",
                 help="Loads both table lists without pairing anything: add rows with + below and pick the tables "
                      "yourself. Your saved mapping is kept."):
        ss.pop("map_rows", None)
        load_lists(propose=False)
    if b3.button("🧹 Start with an empty list", width="stretch",
                 help="Clears the list on screen (the saved mapping changes only when you press Save)."):
        ss.map_rows, ss.map_manual = [], True
        ss.pop("map_check", None)
    wheres = {t["source"]: t.get("where", "") for t in (raw.get("data") or {}).get("tables") or [] if isinstance(t, dict)}
    rows = ss.get("map_rows")
    if rows is None:
        rows = [{"copy": True, "source": r["source"], "target": r["target"] or cfg.map_table(r["source"]),
                 "filter": wheres.get(r["source"], "")} for r in saved]
    if not rows and not ss.get("map_manual"):
        st.info("Press **Propose the mapping** to pair the source tables with the Databricks tables by name, or "
                "**Map by hand** to choose them yourself.")
    else:
        free = st.toggle("Type table names freely", key="map_free",
                         help="Type any source or Databricks table, e.g. one not in the lists yet. A short Databricks "
                              f"name like FactDailySales becomes {cfg.target_schema}.FactDailySales.")
        sources = sorted(set(ss.get("map_sources") or []) | {r["source"] for r in rows if r.get("source")})
        targets = sorted(set(ss.get("map_targets") or []) | {r["target"] for r in rows if r.get("target")})
        df = pd.DataFrame([{"copy": bool(r.get("copy", True)), "source": r.get("source") or "",
                            "target": r.get("target") or "", "filter": r.get("filter") or wheres.get(r.get("source"), "")}
                           for r in rows], columns=["copy", "source", "target", "filter"])
        if not federation and cfg.data_method != "local":
            df = df.drop(columns=["filter"])  # exported files are copied whole
        if free or not sources:
            source_col = st.column_config.TextColumn("Source table", required=True,
                                                     help="schema.table in the source, e.g. dw.FactDailySales")
        else:
            source_col = st.column_config.SelectboxColumn("Source table", options=sources, required=True)
        if free or not targets:
            target_col = st.column_config.TextColumn("Databricks table", required=True,
                                                     help="catalog.schema.table; a short name gets the target schema")
        else:
            target_col = st.column_config.SelectboxColumn("Databricks table", options=targets, required=True)
        edited = st.data_editor(
            df, hide_index=True, width="stretch", key="map_editor", num_rows="dynamic",
            column_config={
                "copy": st.column_config.CheckboxColumn("Copy now", width="small", default=True,
                                                        help="Untick the tables that should wait for a later copy."),
                "source": source_col,
                "target": target_col,
                "filter": st.column_config.TextColumn(
                    "Only rows where (optional)",
                    help="A condition on the source table, e.g. ModifiedDate >= '2026-10-01'. Empty: all rows. "
                         "A table with a condition is added to, never emptied - use it for new or changed rows."),
            })
        st.caption("Add a row with **+** at the bottom of the list, remove one by ticking its row and pressing the "
                   "bin. Rows without both tables are not saved.")
        if ss.get("map_rows"):
            missing = [r["target"] for r in ss.map_rows if not r.get("exists")]
            if missing:
                st.caption(f":red[{len(missing)} Databricks table(s) do not exist yet] (e.g. `{missing[0]}`): run "
                           "*Create the tables* in Run, or pick another table.")
        picked = []
        for r in edited.to_dict("records"):
            src, tgt = str(r.get("source") or "").strip(), str(r.get("target") or "").strip()
            if not (src and tgt) or r.get("copy") is False:
                continue
            parts = tgt.split(".")
            if len(parts) == 1:
                tgt = f"{cfg.target_schema}.{tgt}"
            elif len(parts) == 2:
                tgt = f"{cfg.catalog}.{tgt}"
            picked.append({**r, "copy": True, "source": src, "target": tgt})
        doubled = sorted({p["target"] for p in picked if [q["target"].lower() for q in picked].count(p["target"].lower()) > 1})
        if doubled:
            st.warning(f"More than one source goes into {', '.join(f'`{d}`' for d in doubled)} - check that is wanted "
                       "(with *Replace*, the last copy wins).")
        if st.button(f"💾 Save and check the columns ({len(picked)} table(s))", type="primary", disabled=not picked):
            new = dict(raw)
            tables = h.rows_to_tables(picked, (raw.get("data") or {}).get("tables"))
            filters = {r["source"]: str(r.get("filter") or "").strip() for r in picked}
            for i, t in enumerate(tables):
                entry = dict(t) if isinstance(t, dict) else {"source": t}
                entry.pop("where", None)
                if filters.get(entry["source"]):
                    entry["where"] = filters[entry["source"]]
                tables[i] = entry if len(entry) > 1 else entry["source"]
            new["data"] = {**(raw.get("data") or {}), "tables": tables}
            h.save_raw(ss.project, new)
            ss.pop("map_preflight", None)
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
            if not c.get("target_columns") or "pairs" not in c:
                continue  # no table to map yet, or a compare-only table
            problem = c["status"] in ("check columns", "no matching columns")
            with st.expander(f"{'⚠️ ' if problem else ''}Map the columns: {c['source']} → {c['target']}",
                             expanded=c["status"] == "no matching columns"):
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
        st.markdown("#### Copy")
        modes = {"append": "Add to what is in Databricks",
                 "overwrite": "Replace what is in Databricks (tables with a condition are only added to)"}
        st.radio("When copying", list(modes), format_func=modes.get, key="load_mode", horizontal=True,
                 index=list(modes).index(cfg.load_mode), on_change=_save_load_mode, args=(raw,))

        if cfg.data_method == "local" and not cfg.local_db.get("windows_login", True):
            import os

            from wishbridge.warehouses.redshift.localdb import PASSWORD_ENV

            pw = st.text_input(f"Password for {cfg.local_db.get('user')} on {cfg.local_db.get('server')}",
                               type="password", key="local_pw", help="Used for the check and the copy only; never saved.")
            if pw:
                os.environ[PASSWORD_ENV] = pw  # passed to the copy running in the background, never written anywhere
        if st.button("🔎 Check before copying",
                     help="The source is reachable, every Databricks table exists, and how many rows there are."):
            with st.spinner("Checking the source and the Databricks tables..."):
                try:
                    ss.map_preflight = mapping.preflight(cfg)
                except (SqlError, ValueError) as e:
                    st.error(f"Could not check: {e}")
        pf = ss.get("map_preflight")
        if pf:
            (st.success if pf["source_ok"] else st.error)(pf["source_message"] or "The source could not be reached.")
            if pf["missing_targets"]:
                st.error(f"{len(pf['missing_targets'])} Databricks table(s) do not exist (e.g. `{pf['missing_targets'][0]}`): "
                         "deploy them in the code migration first, or map another table.")
            st.dataframe(pd.DataFrame([{
                "source": r["source"], "Databricks table": r["target"], "exists": "✅" if r["target_exists"] else "❌",
                "only rows where": r["filter"] or "all rows", "rows": r["rows"], "size (MB)": r["size_mb"],
                "about (min)": r["minutes"]} for r in pf["tables"]]), hide_index=True, width="stretch")
            st.caption(f"About **{pf['total_rows']:,} rows**, roughly **{pf['minutes']} min** - a rough guide (about "
                       f"{mapping.ROWS_PER_MINUTE:,} rows a minute); the real speed depends on the network.")
        not_ready = bool(pf) and not pf["ready"]
        if not pf:
            st.caption("Tip: *Check before copying* first - it finds a missing table or an unreachable source in seconds.")
        if st.button("▶ Copy the data", type="primary", disabled=bool(blocking) or not_ready):
            runner.start(cfg, ["load", "reconcile", "report"], {"execute": True}, keep_going=False)
            st.rerun()
        again = mapping.again_targets(load_state(cfg))
        if again and st.button(f"↻ Copy again: only the {len(again)} failed or different table(s)",
                               help="After fixing the problem: copies just these tables again, then compares."):
            runner.start(cfg, ["load", "reconcile", "report"], {"execute": True, "only": again}, keep_going=False)
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
    _claude_data_problems(cfg, raw)


def _claude_data_problems(cfg, raw) -> None:
    """Problems of the check and of the last copy, for Claude to explain and fix."""
    from wishbridge.warehouses.redshift.ui.claude_panel import claude_problem_panel

    problems, targets = [], []
    pf = ss.get("map_preflight")
    if pf:
        if not pf["source_ok"]:
            problems.append(f"Check before copying - source: {pf['source_message'] or 'could not be reached'}")
        for t in pf.get("missing_targets") or []:
            problems.append(f"Check before copying - Databricks table does not exist: {t}")
            targets.append(t)
    ld = load_state(cfg).get("load") or {}
    if ld.get("executed"):
        for t in ld.get("tables", []):
            if t["status"] == "failed":
                problems.append(f"Copy failed for {t['source']} -> {t['target']}: {t.get('error', '')}")
                targets.append(t["target"])
    claude_problem_panel(cfg, raw, "Map the data", problems, targets)


def _save_load_mode(raw) -> None:
    new = dict(raw)
    new["data"] = {**(raw.get("data") or {}), "mode": ss.load_mode}
    h.save_raw(ss.project, new)
