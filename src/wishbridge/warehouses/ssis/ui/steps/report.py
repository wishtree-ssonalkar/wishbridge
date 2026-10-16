"""Step 4 - Report: at a glance, then every detail and the full report."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

from wishbridge.warehouses.ssis.state import load_state
from wishbridge.warehouses.ssis.ui.common import STATUS_ICON, STEP_ERRORS, next_button

from wishbridge.warehouses.ssis.ui.screens import warehouse_ui
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


def _assessment_report(cfg, raw, packages) -> None:
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
                from wishbridge.warehouses.ssis.package import build_package

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
            from wishbridge.warehouses.ssis.fit import CATEGORY_LABELS

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



def render(cfg, raw, packages) -> None:
    """The report of the project's phase."""
    if cfg.phase == "code-migration":
        _code_migration_report(cfg, raw, load_state(cfg))
    elif cfg.phase == "data-migration":
        _data_migration_report(cfg, raw, load_state(cfg))
    else:
        _assessment_report(cfg, raw, packages)


def _full_report(cfg) -> None:
    report = cfg.output_dir / "report.html"
    if report.exists():
        st.download_button("⬇ Download the report", report.read_bytes(), file_name=f"{cfg.name}-{cfg.phase}-report.html",
                           mime="text/html")
        with st.expander("The full report"):
            st.iframe(report.read_text(encoding="utf-8"), height=900)  # our own generated report, all values HTML-escaped


def _start_data_migration() -> None:
    from wishbridge.warehouses.ssis.ui import helpers as h

    raw = h.read_raw(ss.project)
    raw["phase"] = "data-migration"
    h.save_raw(ss.project, raw)
    ss.goal = "data-migration"
    ss.goal_applied = (ss.project, "data-migration")
    ss.pop("step", None)


def _code_migration_report(cfg, raw, state) -> None:
    """What was created in Databricks, what failed, what is still open in the code."""
    c, d = state.get("convert"), state.get("deploy")
    st.subheader("Code migration report")
    if not d:
        st.info("Nothing has been created in Databricks yet: in **Run**, tick *Create the tables and code in the test "
                "schema* and press Start.")
        return
    s = d["summary"]
    failed = [f for f in d["files"] if not f["ok"]]
    st.caption(f"Target: `{s['target']}`")
    m = st.columns(4)
    m[0].metric("Files created", f"{s['files_ok']}/{s['files']}")
    m[1].metric("Statements OK", f"{s['statements_ok']}/{s['statements']}")
    m[2].metric("Already there (kept)", s.get("kept_existing", 0))
    m[3].metric("Left out (fit check)", s.get("left_out", 0))
    if failed:
        st.warning(f"**{len(failed)} file(s) failed on Databricks.** Fix them in *Fix code*, then run again (the run "
                   "keeps every manual fix).")
        _claude_fix_deploy(cfg, raw, state, failed)
        next_button("Next: fix the failed files →", "fix", "next-code-report")
    else:
        st.success("Everything was created in Databricks.")
        st.button("Start the data migration →", type="primary", key="to-data-migration", on_click=_start_data_migration,
                  help="Switches this project to the data migration phase: map the tables and copy the data.")

    _auto_fixed(d)
    st.markdown("#### What was created")
    st.dataframe(pd.DataFrame([{
        "file": f["file"],
        "result": "✅ created" if f["ok"] else "❌ failed",
        "statements OK": f"{f['passed']}/{f['statements']}",
        "errors / notes": "; ".join(f"#{x['n']} {x['error']}" for x in f["results"] if not x["ok"])
                          or next((x.get("note", "") for x in f["results"] if x.get("note")), ""),
    } for f in d["files"]]), width="stretch", hide_index=True)
    if c:
        cs = c["summary"]
        st.markdown("#### The converted code")
        st.markdown(f"{cs['ready']} of {cs['files']} files ready · {cs['review']} to review · {cs['needs_fix']} need a "
                    f"fix · {cs.get('manual_overrides', 0)} fixed by hand")
        items = [{"file": f["file"], "line": x["line"], "severity": x["severity"], "what to do": x["message"]}
                 for f in c["files"] for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
        if items:
            with st.expander(f"Open items ({len(items)})"):
                st.dataframe(pd.DataFrame(items), width="stretch", hide_index=True)
    _full_report(cfg)


def _data_migration_report(cfg, raw, state) -> None:
    """What was copied, how many rows, and whether every table matches the source."""
    ld, r = state.get("load"), state.get("reconcile")
    st.subheader("Data migration report")
    if not ld or not ld.get("executed"):
        st.info("No data copied yet: map the tables and press **Copy the data** in *Map the data*.")
        return
    copied = [t for t in ld["tables"] if not t.get("compare_only")]
    loaded = [t for t in copied if t["status"] == "loaded"]
    failed = [t for t in copied if t["status"] == "failed"]
    quick = r if r and r.get("mode") == "quick" else None
    m = st.columns(4)
    m[0].metric("Tables copied", f"{len(loaded)}/{len(copied)}")
    m[1].metric("Rows copied", f"{sum(t.get('rows') or 0 for t in loaded):,}")
    m[2].metric("Failed", len(failed))
    m[3].metric("Match the source", f"{quick['summary']['matched']}/{quick['summary']['tables']}" if quick else "—")
    differ = quick["summary"]["tables"] - quick["summary"]["matched"] if quick else 0
    if failed:
        st.error(f"**{len(failed)} table(s) were not copied** — see the errors below, fix the mapping and copy again.")
    elif quick and not differ:
        st.success("**All copied tables match the source** (row counts, numeric totals and checksums).")
    elif quick:
        st.warning(f"**{differ} table(s) differ from the source** — see *Source vs Databricks* below.")
    else:
        st.info("The comparison with the source has not run yet.")
    if failed or differ:
        next_button("Back to Map the data →", "data", "next-data-report")
    if st.button("📦 Make the sign-off file for the client",
                 help="One zip with the report, every table's result and the comparison - no data in it."):
        from wishbridge.warehouses.ssis.package import build_signoff

        try:
            ss.signoff = str(build_signoff(cfg))
        except (OSError, ValueError) as e:
            st.error(str(e))
    if ss.get("signoff") and Path(ss.signoff).is_file():
        so = Path(ss.signoff)
        st.download_button(f"⬇ {so.name}", so.read_bytes(), file_name=so.name, mime="application/zip")
        st.caption(f"Also kept at `{so}`. The client approves the data migration from it.")

    st.markdown("#### Copied tables")
    st.dataframe(pd.DataFrame([{"source": t["source"], "Databricks table": t["target"], "status": t["status"],
                                "rows": t.get("rows"), "error": t.get("error", "")} for t in copied]),
                 width="stretch", hide_index=True)
    if quick:
        st.markdown("#### Source vs Databricks")
        st.dataframe(pd.DataFrame([{
            "table": t["target"], "result": t["status"],
            "details": t.get("error") or ", ".join(f"{ch['check']}: {ch['source']} → {ch['target']}"
                                                   for ch in t.get("checks", []) if not ch["match"]) or "all checks equal",
        } for t in quick["tables"]]), width="stretch", hide_index=True)
    problems = [f"Copy failed for {t['source']} -> {t['target']}: {t.get('error', '')}" for t in failed]
    targets = [t["target"] for t in failed]
    for t in (quick or {}).get("tables", []):
        if t["status"] != "match":
            problems.append(f"Source vs Databricks for {t['target']}: {t['status']} - " + (t.get("error") or ", ".join(
                f"{ch['check']}: source {ch['source']}, Databricks {ch['target']}"
                for ch in t.get("checks", []) if not ch["match"])))
            targets.append(t["target"])
    from wishbridge.warehouses.ssis.ui.claude_panel import claude_problem_panel

    claude_problem_panel(cfg, raw, "Data report", problems, targets)
    _full_report(cfg)


_SCHEMA_ONLY = ("SCHEMA_NOT_FOUND",)


def _claude_fix_deploy(cfg, raw, state, failed) -> None:
    """Send every failed file with its exact Databricks errors to Claude; save the chosen fixes in one go."""
    from wishbridge.warehouses.ssis import ai
    from wishbridge.warehouses.ssis.ui.steps import fix as fx

    from wishbridge.warehouses.ssis.deploy import deploy_schema_map

    ss = st.session_state
    old_names = [s.lower() for s in deploy_schema_map(cfg)]

    def schema_only_error(err: str) -> bool:
        """Fixed by deploying again: a missing schema, or a table named with a source schema (`dw`.`X`)."""
        low = err.lower()
        return any(c in err for c in _SCHEMA_ONLY) or ("TABLE_OR_VIEW_NOT_FOUND" in err and any(
            f"`{s}`." in low or f" {s}." in low for s in old_names))

    schema_only = [d for d in failed if all(r["ok"] or schema_only_error(r.get("error") or "") for r in d["results"])]
    to_fix = [d for d in failed if d not in schema_only and not d["file"].startswith("_database_schema/")]
    with st.container(border=True):
        st.markdown("#### ✨ Fix the failed files with Claude")
        flash = ss.pop("claude_flash", None)
        if flash:
            st.success(flash + " Then open **Deploy** and deploy again.")
        if schema_only:
            st.info(f"{len(schema_only)} file(s) failed only because a schema was not found "
                    f"({', '.join(d['file'] for d in schema_only[:4])}{' ...' if len(schema_only) > 4 else ''}). "
                    "WishBridge now maps the warehouse's schemas to the target and rebuilds the table scripts on "
                    "every deploy: **deploy again first** - these need no change.")
        if not cfg.assist_enabled:
            st.caption("Claude is off for this project: allow it in **Fix code > ✨ Ask Claude** once the client has "
                       "agreed that their code may be sent to Claude.")
            return
        model = "claude-sonnet-5-5" if cfg.assist_speed == "fast" else cfg.ai_model
        backend, how = ai.assist_backend(cfg.assist_via, cfg.profile, cfg.assist_endpoint)
        bkey = f"{ss.project}|deploy"
        batch = fx._BATCHES.get(bkey)
        if batch is None:
            if not to_fix:
                st.caption("No file needs Claude: deploy again.")
                return
            st.caption(f"Claude gets each failed file with the exact Databricks errors, its original and the project "
                       f"background, fixes {len(to_fix)} file(s) 3 at a time, and you save the fixes you want in one "
                       f"go. Uses {how}.")
            if st.button(f"✨ Fix {len(to_fix)} failed file(s) with Claude", type="primary", disabled=backend is None,
                         key="claude-fix-deploy"):
                conv = {f["file"]: f for f in (state.get("convert") or {}).get("files", [])}
                reqs = []
                for d in to_fix:
                    cf = conv.get(d["file"])
                    req = fx._file_request(cfg, raw, cf, "These errors came from deploying the file on Databricks: "
                                           "fix them.", cfg.assist_via, model, cfg.assist_speed,
                                           cfg.assist_endpoint) if cf else None
                    if req is None:
                        continue
                    errors = [f"Databricks error in statement {r['n']}: {r.get('error', '')}"
                              + (f" (statement starts: {r['statement'][:200]})" if r.get("statement") else "")
                              for r in d["results"] if not r["ok"]]
                    req["issues"] = errors + req["issues"]
                    reqs.append(req)
                fx._start_batch(bkey, reqs)
                st.rerun()
            return
        if not batch["done"]:
            fx._batch_progress(bkey)
            return
        fx._batch_results(raw, bkey)


def _auto_fixed(d) -> None:
    """What WishBridge fixed by itself during this deploy (no file was changed for it)."""
    a = d.get("auto_fixed")
    if not a or not (a.get("schema_map") or a.get("scripts_rebuilt")):
        return
    with st.container(border=True):
        st.markdown("#### 🛠 Fixed automatically by WishBridge")
        st.caption("Applied while deploying - your files keep the original names, so a later change of catalog or "
                   "schema in Settings just works. Nothing to save.")
        if a.get("schema_map"):
            st.markdown("**Source schemas mapped to Databricks:** " + ", ".join(
                f"`{s}` → `{t}`" for s, t in a["schema_map"].items()))
        if a.get("scripts_rebuilt"):
            st.markdown(f"**Table scripts rebuilt for `{a['target']}`:** " + ", ".join(
                f"{f} ({n} table{'s' if n != 1 else ''})" for f, n in a["scripts_rebuilt"].items()))
        renamed = a.get("renamed") or {}
        if renamed:
            st.markdown(f"**Names renamed in {len(renamed)} file(s)** ({sum(renamed.values())} in total):")
            st.dataframe(pd.DataFrame([{"file": f, "names renamed": n} for f, n in renamed.items()]),
                         width="stretch", hide_index=True)
