"""Step 6 - Report: a self-contained HTML migration report built from state.json."""

from __future__ import annotations

import os
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from . import __version__
from .config import ProjectConfig
from .state import load_state

REVIEW_HOURS_PER_FILE = 0.25

CSS = """
:root{--bg:#f7f7f5;--card:#fff;--fg:#1d1d1b;--muted:#6b6b66;--line:#e4e4df;--ok:#1f7a4d;--warn:#a15c00;--err:#b42318;--accent:#2952a3}
@media (prefers-color-scheme:dark){:root{--bg:#161615;--card:#1f1f1d;--fg:#ececea;--muted:#a3a39d;--line:#33332f;--ok:#4cc38a;--warn:#f0a646;--err:#f97066;--accent:#7aa2f7}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px 64px}h1{font-size:26px;margin:0 0 4px}h2{font-size:18px;margin:36px 0 12px}
.sub{color:var(--muted);margin:0 0 24px}.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:12px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px 16px}.card .v{font-size:24px;font-weight:650}
.card .l{color:var(--muted);font-size:13px}.wrap{overflow-x:auto;background:var(--card);border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:14px}th,td{text-align:left;padding:8px 12px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;font-size:12px;text-transform:uppercase;letter-spacing:.03em}tr:last-child td{border-bottom:0}
.pill{display:inline-block;padding:1px 8px;border-radius:99px;font-size:12px;font-weight:600;border:1px solid currentColor}
.ok{color:var(--ok)}.warn{color:var(--warn)}.err{color:var(--err)}.muted{color:var(--muted)}code{font:13px ui-monospace,Consolas,monospace}
.skip{color:var(--muted);font-style:italic}footer{margin-top:48px;color:var(--muted);font-size:13px}
"""

_PILL = {"ready": "ok", "match": "ok", "loaded": "ok", "review": "warn", "planned": "warn", "mismatch": "err",
         "needs-fix": "err", "failed": "err", "error": "err", "info": "muted", "warning": "warn"}


def _pill(text: str) -> str:
    return f'<span class="pill {_PILL.get(text, "muted")}">{escape(text)}</span>'


def _card(value: Any, label: str) -> str:
    return f'<div class="card"><div class="v">{escape(str(value))}</div><div class="l">{escape(label)}</div></div>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    if not rows:
        return '<p class="skip">Nothing to show.</p>'
    head = "".join(f"<th>{escape(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in r) + "</tr>" for r in rows)
    return f'<div class="wrap"><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table></div>'


def _rel(path: str, base: Path) -> str:
    try:
        return Path(os.path.relpath(path, base)).as_posix()
    except ValueError:
        return path


def effort(cfg: ProjectConfig, state: dict[str, Any]) -> dict[str, float] | None:
    a, c = state.get("analyze"), state.get("convert")
    if not a or not c:
        return None
    baseline = a.get("estimated_hours_baseline", 0.0)
    open_issues = c["summary"]["open_errors"] + c["summary"]["open_warnings"]
    remaining = round(open_issues * cfg.hours_per_issue + c["summary"]["files"] * REVIEW_HOURS_PER_FILE, 1)
    return {"baseline": baseline, "remaining": remaining, "saved_pct": round(100 * max(0.0, 1 - remaining / baseline)) if baseline else 0}


def build_report(cfg: ProjectConfig) -> Path:
    state = load_state(cfg)
    base = cfg.output_dir
    parts: list[str] = []

    a, c, d, ld, r = (state.get(k) for k in ("analyze", "convert", "deploy", "load", "reconcile"))
    e = effort(cfg, state)

    cards = []
    if a:
        cards.append(_card(len(a["programs"]), "source files analysed"))
    if c:
        s = c["summary"]
        automatic = sum(1 for f in c["files"] if f["status"] == "ready" and not f.get("manual_override"))
        cards.append(_card(f'{round(100 * automatic / s["files"]) if s["files"] else 0}%', "files ready without manual work"))
        cards.append(_card(s["auto_fixed"], "issues auto-fixed by WishBridge"))
        if s.get("manual_overrides"):
            cards.append(_card(s["manual_overrides"], "files fixed by hand (overrides/)"))
        cards.append(_card(s["open_errors"] + s["open_warnings"], "open items for review"))
    if e:
        cards.append(_card(f'{e["baseline"]} h', "manual rewrite estimate"))
        cards.append(_card(f'{e["remaining"]} h', "estimated remaining effort"))
    if d:
        cards.append(_card(f'{d["summary"]["statements_ok"]}/{d["summary"]["statements"]}', "statements validated on Databricks"))
    if r and r.get("mode") == "quick":
        cards.append(_card(f'{r["summary"]["matched"]}/{r["summary"]["tables"]}', "tables reconciled"))
    parts.append(f'<div class="grid">{"".join(cards)}</div>' if cards else '<p class="skip">No steps have run yet.</p>')

    # 1. Assessment
    parts.append("<h2>1. Assessment</h2>")
    if a:
        cx = " · ".join(f"{escape(k)}: {v}" for k, v in sorted(a["complexity"].items()))
        totals = " · ".join(f"{escape(k)}: {v}" for k, v in a["totals"].items() if v)
        parts.append(f'<p>{totals}</p><p class="muted">Complexity — {cx}. Full analyzer workbook: '
                     f'<code>{escape(_rel(a["report_file"], base))}</code></p>')
        parts.append(_table(["File", "Category", "Lines", "Statements", "Complexity"],
                            [[f'<code>{escape(p["name"])}</code>', escape(p["category"]), str(p["lines"]),
                              str(p["statements"]), escape(p["complexity"])] for p in a["programs"]]))
        top = sorted(a["functions"].items(), key=lambda kv: -kv[1])[:12]
        if top:
            parts.append('<p class="muted">Most-used source functions: ' + ", ".join(f"<code>{escape(k)}</code>×{v}" for k, v in top) + "</p>")
    else:
        parts.append('<p class="skip">Not run — <code>wishbridge analyze</code></p>')

    # 1b. Migration fit
    fit = state.get("fit")
    if fit:
        from .fit import CATEGORY_LABELS

        parts.append("<h2>Migration fit: what belongs on Databricks</h2>")
        parts.append(f'<p><b>{escape(fit["headline"])}</b></p><ul>' +
                     "".join(f"<li>{escape(x)}</li>" for x in fit["advice"]) + "</ul>")
        for key, label in (("app_evidence", "Signs of an application database"),
                           ("warehouse_evidence", "Signs of reporting / warehouse use")):
            if fit.get(key):
                parts.append(f'<p class="muted">{label}: ' + "; ".join(escape(x) for x in fit[key]) + "</p>")
        parts.append('<p class="muted">' + " · ".join(f"{CATEGORY_LABELS[k]}: {v}" for k, v in fit["counts"].items() if v) +
                     f'. Deploy scope: <code>{escape(cfg.scope)}</code>'
                     + (" (objects to keep on the source or not needed are not deployed)" if cfg.scope == "recommended" else
                        " (set <code>scope: recommended</code> to deploy only what belongs on Databricks)") + "</p>")
        order = list(CATEGORY_LABELS)
        parts.append(_table(["Object", "Type", "Recommendation", "Why"], [
            [f'<code>{escape(o["file"])}</code>', escape(o["type"]), escape(CATEGORY_LABELS[o["category"]]),
             escape("; ".join(o["reasons"]))]
            for o in sorted(fit["objects"], key=lambda o: (order.index(o["category"]), o["file"]))]))

    # 2. Conversion
    parts.append("<h2>2. Code conversion</h2>")
    if c:
        parts.append(f'<p class="muted">Transpiler: {escape(c["transpiler"])} (Databricks Labs LakeBridge) + WishBridge rules. '
                     f'Converted code: <code>{escape(_rel(c["final_dir"], base))}</code></p>')
        conv_names = {"morph": "Morph", "bladebridge": "BladeBridge", "manual": "hand-written"}
        parts.append(_table(["File", "Converter", "Status", "Auto-fixed", "Open errors", "Open warnings", "AI suggestion"], [
            [f'<code>{escape(f["file"])}</code>',
             escape(conv_names.get(f.get("converter", c["transpiler"]), f.get("converter", ""))),
             _pill(f["status"]) + (' <span class="pill muted">manual fix</span>' if f.get("manual_override") else ""),
             str(f["fixed"]),
             str(sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "error")),
             str(sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "warning")),
             escape(f.get("ai", {}).get("status", "—"))]
            for f in c["files"]]))
        items = [[f'<code>{escape(f["file"])}:{x["line"]}</code>', _pill(x["severity"]), escape(x["rule"]), escape(x["message"])]
                 for f in c["files"] for x in f["findings"] if not x["fixed"] and x["severity"] != "info"]
        parts.append("<h2>Open items</h2>" + _table(["Location", "Severity", "Rule", "What to do"], items))
        notes = [[f'<code>{escape(f["file"])}:{x["line"]}</code>', escape(x["message"])]
                 for f in c["files"] for x in f["findings"] if not x["fixed"] and x["severity"] == "info"]
        if notes:
            parts.append("<h2>Notes (no action needed)</h2>" + _table(["Location", "Note"], notes))
        fixed = [[f'<code>{escape(f["file"])}:{x["line"]}</code>', escape(x["rule"]), escape(x["message"])]
                 for f in c["files"] for x in f["findings"] if x["fixed"]]
        if fixed:
            parts.append("<h2>Applied automatically</h2>" + _table(["Location", "Rule", "Change"], fixed))
    else:
        parts.append('<p class="skip">Not run — <code>wishbridge convert</code></p>')

    # 3. Deployment
    parts.append("<h2>3. Deployment &amp; validation</h2>")
    if d:
        parts.append(f'<p class="muted">Target <code>{escape(d["summary"]["target"])}</code> on warehouse '
                     f'<code>{escape(d["summary"]["warehouse_id"])}</code>. DDL executed, queries/DML checked with EXPLAIN.</p>')
        rows = []
        for f in d["files"]:
            errs = "<br>".join(f'#{x["n"]} {escape(x["error"])}' for x in f["results"] if not x["ok"])
            kept = sum(1 for x in f["results"] if x.get("action") == "exists")
            note = f'<span class="muted">{kept} existing object(s) kept</span>' if kept else ""
            note = next((f'<span class="muted">{escape(x["note"])}</span>' for x in f["results"]
                         if x.get("action") == "left-out"), note)
            rows.append([f'<code>{escape(f["file"])}</code>', _pill("ready" if f["ok"] else "failed"),
                         f'{f["passed"]}/{f["statements"]}', errs or note or "—"])
        parts.append(_table(["File", "Result", "Statements OK", "Errors"], rows))
    else:
        parts.append('<p class="skip">Not run — <code>wishbridge deploy</code></p>')

    # 4. Data
    parts.append("<h2>4. Data load</h2>")
    if ld:
        parts.append(f'<p class="muted">Method: {escape(ld["method"])}. Plan: <code>{escape(_rel(ld["plan_file"], base))}</code></p>')
        parts.append(_table(["Source", "Target", "Status", "Rows"], [
            [f'<code>{escape(t["source"])}</code>', f'<code>{escape(t["target"])}</code>', _pill(t["status"]),
             escape(str(t.get("rows", "—") if t.get("rows") is not None else t.get("error", "—")))] for t in ld["tables"]]))
    else:
        parts.append('<p class="skip">Not run — <code>wishbridge load</code></p>')

    # 4b. ETL notebooks (only for sources that convert to notebooks)
    ex = state.get("execute")
    if ex:
        parts.append("<h2>ETL notebooks</h2>")
        parts.append('<p class="muted">Converted ETL jobs run as Databricks notebook jobs; their output tables are '
                     'compared with the legacy ETL output in the reconciliation below.</p>')
        parts.append(_table(["Notebook", "Result", "Runs after", "Details"], [
            [f'<code>{escape(x["file"])}</code>',
             _pill({"succeeded": "loaded", "skipped": "skipped"}.get(x["status"], "failed")),
             escape(", ".join(x.get("depends_on") or [])) or "—",
             (f'<a href="{escape(x["url"])}">run</a> ' if x.get("url") else "") + escape(x.get("error") or x.get("note") or "")]
            for x in ex["runs"]]))
        if ex.get("job_definition"):
            parts.append(f'<p class="muted">Job definition to schedule the migrated workflow: '
                         f'<code>{escape(_rel(ex["job_definition"], base))}</code> '
                         f'(<code>databricks jobs create --json @file</code>).</p>')

    # 5. Reconciliation
    parts.append("<h2>5. Reconciliation</h2>")
    if r and r.get("mode") == "quick":
        parts.append('<p class="muted">Source vs target: row count, sum of every numeric column, and a checksum over '
                     'every shared column (catches changed text, dates and numbers).</p>')
        rows = []
        for t in r["tables"]:
            checks = t.get("checks", [])
            if t.get("error"):
                detail = escape(t["error"])
            else:
                failed = [f'{escape(ch["check"])}: {escape(str(ch["source"]))} → {escape(str(ch["target"]))}'
                          for ch in checks if not ch["match"]]
                detail = "<br>".join(failed) or f'{len(checks)} checks equal'
                diff = t.get("column_differences")
                if diff:
                    for side, label in (("only_in_source", "only in source"), ("only_in_target", "only in target")):
                        if diff.get(side):
                            detail += f'<br><span class="warn">Columns {label}: {escape(", ".join(diff[side]))}</span>'
            rows.append([f'<code>{escape(t["target"])}</code>', _pill(t["status"]), detail])
        parts.append(_table(["Table", "Result", "Details"], rows))
    elif r:
        parts.append('<p>Full LakeBridge reconciliation ran; results are in the reconcile tables and dashboard in Databricks.</p>')
    else:
        parts.append('<p class="skip">Not run — <code>wishbridge reconcile</code></p>')

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Migration Report — {escape(cfg.name)}</title><style>{CSS}</style></head><body><main>
<h1>Migration report: {escape(cfg.name)}</h1>
<p class="sub">{escape(cfg.source.analyzer_tech)} → Databricks · generated {generated} · Wishtree WishBridge {__version__}</p>
{''.join(parts)}
<footer>Effort figures are estimates based on the configured hours per file and per open item.
Prepared with Wishtree WishBridge by Wishtree Technologies. Conversion is powered by Databricks Labs LakeBridge; WishBridge adds rule-based fixes, validation, data loading and reconciliation.</footer>
</main></body></html>"""
    out = cfg.out("report.html")
    out.write_text(html, encoding="utf-8")
    return out
