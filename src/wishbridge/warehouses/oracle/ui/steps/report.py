"""Step 4 - Report: at a glance, then every detail and the full report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from wishbridge.warehouses.oracle.state import load_state
from wishbridge.warehouses.oracle.ui.common import STATUS_ICON, STEP_ERRORS, next_button

from wishbridge.warehouses.oracle.ui.screens import warehouse_ui
ss = st.session_state


def report_overview(cfg, state) -> None:
    """At a glance: what the code is, whether it belongs on Databricks, how the conversion went, the effort."""
    a, c, d, r, fit, ov = (state.get(k) for k in ("analyze", "convert", "deploy", "reconcile", "fit", "overview"))
    st.subheader("Report — at a glance")
    lines = []
    if ov:
        lines.append(f"**The code:** {ov['files']} files, {ov['lines']:,} lines — {ov['tables']} tables, "
                     f"{ov['procedures']} procedures, {ov['views']} views, {ov['functions']} functions"
                     + (f" (schemas: {', '.join(ov['schemas'][:6])})" if ov.get("schemas") else "") + ".")
    if fit:
        lines.append(f"**Is it a data warehouse?** {fit['headline']}")
    if c:
        s = c["summary"]
        lines.append(f"**Conversion:** {s['ready']} of {s['files']} files ready, {s['review']} to review, "
                     f"{s['needs_fix']} need a fix ({s['open_errors']} open errors, {s['open_warnings']} warnings).")
    if a:
        lines.append(f"**Effort:** about {a['estimated_hours_baseline']} hours of manual work without WishBridge.")
    if d:
        lines.append(f"**Deployed:** {d['summary']['statements_ok']} of {d['summary']['statements']} statements ran in "
                     f"`{d['summary']['target']}`.")
    if r and r.get("summary"):
        lines.append(f"**Data:** {r['summary']['matched']} of {r['summary']['tables']} tables match the source.")
    if lines:
        box = st.warning if fit and fit.get("verdict") == "application" else st.info
        box("\n\n".join(lines))
    if ov and ov.get("headline") and ov["headline"] != (fit or {}).get("headline"):
        st.caption(ov["headline"])
    b1, b2 = st.columns(2)
    ovf = Path(ov["file"]) if ov and ov.get("file") else None
    if ovf and ovf.is_file():
        b1.download_button("📘 Code overview (how the system is built)", ovf.read_bytes(), file_name=ovf.name,
                           mime="text/html", width="stretch")
    report = cfg.output_dir / "report.html"
    if report.exists():
        b2.download_button("⬇ Download the report", report.read_bytes(), file_name=f"{cfg.name}-report.html",
                           mime="text/html", width="stretch")


def render(cfg, raw, packages) -> None:
    state = load_state(cfg)
    if not state:
        st.info("Nothing has run yet. Use the Run tab.")
    else:
        report_overview(cfg, state)
        if cfg.phase == "assessment":
            next_button("Next: share the zip →", "send", "next-report")
        elif cfg.phase == "code-migration" and state.get("convert"):
            next_button("Next: fix code →", "fix", "next-report")
        if cfg.phase != "assessment" and state.get("convert"):
            if st.button("📦 Save everything in one zip"):
                from wishbridge.warehouses.oracle.package import build_package

                try:
                    ss.review_package = str(build_package(cfg))
                except STEP_ERRORS as e:
                    st.error(str(e))
            if ss.get("review_package") and Path(ss.review_package).is_file():
                pkg = Path(ss.review_package)
                st.download_button(f"Download {pkg.name}", pkg.read_bytes(), file_name=pkg.name, mime="application/zip")
                st.caption("Report, code overview, open items, and the code before and after conversion in one zip.")
        a, c, d, ld, r = (state.get(k) for k in ("analyze", "convert", "deploy", "load", "reconcile"))
        m = st.columns(5)
        if a:
            m[0].metric("Files analysed", len(a["programs"]), help=f"Manual estimate {a['estimated_hours_baseline']} h")
        if c:
            s = c["summary"]
            m[1].metric("Ready", f"{s['ready']}/{s['files']}")
            m[2].metric("Open items", s["open_errors"] + s["open_warnings"])
        if d:
            m[3].metric("Statements deployed", f"{d['summary']['statements_ok']}/{d['summary']['statements']}")
        if r and r.get("mode") == "quick":
            m[4].metric("Tables reconciled", f"{r['summary']['matched']}/{r['summary']['tables']}")

        fit = state.get("fit")
        if fit:
            from wishbridge.warehouses.oracle.fit import CATEGORY_LABELS

            st.markdown("#### Is this a data warehouse? (fit check)")
            (st.warning if fit["verdict"] == "application" else st.info if fit["verdict"] == "mixed" else st.success)(
                f"**{fit['headline']}**\n\n" + "\n".join(f"- {x}" for x in fit["advice"]))
            fm = st.columns(len(CATEGORY_LABELS))
            for col, (k, label) in zip(fm, CATEGORY_LABELS.items()):
                col.metric(label, fit["counts"].get(k, 0))
            with st.expander("Why, and every object's recommendation"):
                for key, label in (("app_evidence", "Signs of an application database"),
                                   ("warehouse_evidence", "Signs of reporting / warehouse use")):
                    if fit.get(key):
                        st.markdown(f"**{label}:** " + "; ".join(fit[key]))
                show = st.multiselect("Show", list(CATEGORY_LABELS), default=list(CATEGORY_LABELS),
                                      format_func=CATEGORY_LABELS.get, key="fit_filter")
                st.dataframe(pd.DataFrame([{
                    "file": o["file"], "type": o["type"], "recommendation": CATEGORY_LABELS[o["category"]],
                    "why": "; ".join(o["reasons"])} for o in fit["objects"] if o["category"] in show]),
                    width="stretch", hide_index=True)
            if cfg.scope != "recommended":
                st.caption("To deploy only what belongs on Databricks, set **What to deploy** in Settings.")

        if c:
            st.markdown("#### Files")
            st.dataframe(pd.DataFrame([{
                "file": f["file"],
                "status": STATUS_ICON.get(f["status"], f["status"]) + (" · manual fix" if f.get("manual_override") else ""),
                "auto-fixed": f["fixed"],
                "open errors": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "error"),
                "open warnings": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "warning"),
            } for f in c["files"]]), width="stretch", hide_index=True)
            items = [{"file": f["file"], "line": x["line"], "severity": x["severity"], "what to do": x["message"]}
                     for f in c["files"] for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
            if items:
                st.markdown("#### Open items")
                st.dataframe(pd.DataFrame(items), width="stretch", hide_index=True)
        if d:
            errs = [{"file": f["file"], "statement": x["n"], "error": x["error"]}
                    for f in d["files"] for x in f["results"] if not x["ok"]]
            if errs:
                st.markdown("#### Deployment errors")
                st.dataframe(pd.DataFrame(errs), width="stretch", hide_index=True)
        if r and r.get("mode") == "quick":
            st.markdown("#### Reconciliation")
            st.dataframe(pd.DataFrame([{
                "table": t["target"], "result": t["status"],
                "details": t.get("error") or ", ".join(f"{ch['check']}: {ch['source']} → {ch['target']}"
                                                       for ch in t.get("checks", []) if not ch["match"]) or "all checks equal",
            } for t in r["tables"]]), width="stretch", hide_index=True)

        warehouse_ui(cfg).report_extras(cfg, state)  # per warehouse: ui/warehouses/
        dummy = state.get("dummy_data")
        if dummy and Path(dummy["folder"]).is_dir():
            st.markdown("#### Dummy data")
            st.caption(f"**Made-up values, not client data:** {dummy['rows']} rows per table, built from "
                       f"{dummy['origin']} (types, lengths, NULL rules and column names). Key columns are 1..{dummy['rows']} so "
                       "tables join; the last row is NULL wherever a column allows it. It is in the zip, and "
                       "`dummy_data.sql` loads it into the Databricks tables to try the converted code.")
            folder = Path(dummy["folder"])
            d1, d2 = st.columns([2, 1])
            pick = d1.selectbox("Table", [t["table"] for t in dummy["tables"]], key="dummy_pick")
            sql_file = folder / "dummy_data.sql"
            if sql_file.is_file():
                d2.write("")
                d2.download_button("⬇ dummy_data.sql", sql_file.read_bytes(), file_name=f"{cfg.name}-dummy_data.sql",
                                   mime="text/plain", width="stretch")
            csv_file = folder / next(t["file"] for t in dummy["tables"] if t["table"] == pick)
            if csv_file.is_file():
                st.dataframe(pd.read_csv(csv_file, dtype=str, keep_default_na=False), hide_index=True, width="stretch")

        report = cfg.output_dir / "report.html"
        if report.exists():
            st.markdown("#### The full report")
            st.iframe(report.read_text(encoding="utf-8"), height=900)  # our own generated report, all values HTML-escaped
