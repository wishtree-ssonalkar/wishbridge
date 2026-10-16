"""'✨ Ask Claude about this problem' for Settings, Map the data and the Data report.

Claude explains what is wrong and why, and proposes fixes the app can apply: a setting, a column mapping, a filter or
one additive Databricks SQL statement. Nothing is applied until the person ticks it and presses Apply.
"""

from __future__ import annotations

import yaml
import streamlit as st

from wishbridge.warehouses.synapse import ai
from wishbridge.warehouses.synapse.state import load_state
from wishbridge.warehouses.synapse.ui import helpers as h
from wishbridge.warehouses.synapse.ui.steps import fix as fx

ss = st.session_state


def _settings_text(raw: dict) -> str:
    """The settings Claude may see: never a password (project.yml holds none; the local password is not in it)."""
    keep = {"source": raw.get("source"), "phase": raw.get("phase"),
            "databricks": {k: v for k, v in (raw.get("databricks") or {}).items()
                           if k in ("profile", "host", "catalog", "schema", "warehouse_id")},
            "schema_map": raw.get("schema_map") or {},
            "data": {k: v for k, v in (raw.get("data") or {}).items() if k != "tables"},
            "source_db": {k: v for k, v in (raw.get("source_db") or {}).items() if "pass" not in k.lower()}}
    return yaml.safe_dump(keep, sort_keys=False, allow_unicode=True)


def _tables_text(cfg, targets: list[str]) -> str:
    """The mapping, the source columns (from what was read from the database) and, when reachable, the Databricks
    columns of the tables with a problem."""
    lines = ["Mapping (source -> Databricks table, column mapping, filter):"]
    for t in cfg.tables:
        lines.append(f"- {t.source} -> {t.target}" + (f"; columns {t.columns}" if t.columns else "")
                     + (f"; only rows where {t.where}" if t.where else "") + ("" if t.load else "; compare only"))
    inv = (load_state(cfg).get("inventory") or {}).get("tables") or []
    wanted = {x.lower() for x in targets}
    sources = {t.source.lower() for t in cfg.tables if t.target.lower() in wanted}
    for t in inv:
        name = f"{t.get('schema')}.{t.get('table')}"
        if name.lower() in sources:
            cols = ", ".join(f"{c['name']} {c['type']}" for c in t.get("columns", []))
            lines.append(f"Source {name}: {cols}")
    if targets:
        try:
            from wishbridge.warehouses.synapse.dbx import Warehouse
            from wishbridge.warehouses.synapse.reconcile import _columns

            wh = Warehouse(cfg)
            for target in targets[:6]:
                try:
                    cols = _columns(wh, target)
                    lines.append(f"Databricks {target}: " + ", ".join(f"{c} {t}" for c, t in cols.items()))
                except Exception as e:  # noqa: BLE001 - a missing table is part of the problem
                    lines.append(f"Databricks {target}: could not be read ({str(e).splitlines()[0][:160]})")
        except Exception:  # noqa: BLE001 - no workspace: Claude still has the rest
            pass
    return "\n".join(lines)


def _set_key(raw: dict, key: str, value) -> None:
    node = raw
    *path, last = key.split(".")
    for p in path:
        node = node.setdefault(p, {})
    if last in ("trust", "windows_login"):
        value = str(value).strip().lower() in ("true", "1", "yes")
    node[last] = value


def _table_entry(raw: dict, table: str) -> dict | None:
    tables = (raw.setdefault("data", {})).setdefault("tables", [])
    for i, t in enumerate(tables):
        target = t.get("target") if isinstance(t, dict) else None
        if target and target.lower() == table.lower():
            return t
        if isinstance(t, str) and t.split(".")[-1].lower() == table.split(".")[-1].lower():
            tables[i] = {"source": t, "target": table}
            return tables[i]
    return None


def _apply(cfg, raw: dict, actions: list[dict]) -> list[str]:
    """Apply the ticked fixes: settings and mapping into project.yml, SQL on the workspace. Returns what happened."""
    import copy

    new = copy.deepcopy(raw)
    done, changed = [], False
    for a in actions:
        kind = a["type"]
        if kind == "setting":
            _set_key(new, a["key"], a["value"])
            done.append(f"Setting {a['key']} = {a['value']}")
            changed = True
        elif kind in ("column", "filter"):
            entry = _table_entry(new, a["table"])
            if entry is None:
                done.append(f"Not applied: {a['table']} is not in the mapping")
                continue
            if kind == "column":
                entry.setdefault("columns", {})[a["column"]] = a["source"]
                done.append(f"{a['table']}: column {a['column']} <- {a['source']}")
            else:
                if str(a["where"]).strip():
                    entry["where"] = str(a["where"]).strip()
                else:
                    entry.pop("where", None)
                done.append(f"{a['table']}: " + (f"only rows where {a['where']}" if str(a["where"]).strip() else "all rows"))
            changed = True
        elif kind == "sql" and ai.safe_sql(a["sql"]):
            from wishbridge.warehouses.synapse.dbx import SqlError, Warehouse

            try:
                Warehouse(cfg).run(a["sql"].strip().rstrip(";"))
                done.append(f"Ran on Databricks: {a['sql'].strip()[:120]}")
            except SqlError as e:
                done.append(f"Databricks refused: {a['sql'].strip()[:80]} - {str(e).splitlines()[0][:200]}")
    if changed:
        h.save_raw(ss.project, new)
    return done


def _describe(a: dict) -> str:
    if a["type"] == "setting":
        return f"Change setting **{a['key']}** to `{a['value']}`"
    if a["type"] == "column":
        return f"**{a['table']}**: fill column `{a['column']}` from source column `{a['source']}`"
    if a["type"] == "filter":
        return f"**{a['table']}**: " + (f"copy only rows where `{a['where']}`" if str(a["where"]).strip() else "copy all rows")
    return f"Run on Databricks: `{a['sql'].strip()}`"


def claude_problem_panel(cfg, raw, page: str, problems: list[str], targets: list[str] | None = None) -> None:
    """Show the panel when there are problems on the page."""
    problems = [p for p in problems if p]
    if not problems:
        return
    key, job_key = f"claude-problem:{page}", f"{ss.project}|problem:{page}"
    with st.container(border=True):
        st.markdown("#### ✨ Ask Claude about this problem")
        flash = ss.pop(f"claude-problem-flash:{page}", None)
        if flash:
            st.success(flash)
        if not cfg.assist_enabled:
            st.caption("Claude can explain these problems and propose fixes - it only sees settings, table and column "
                       "names, never data or passwords.")
            agreed = st.checkbox("The client agreed that their code and settings may be sent to Claude",
                                 key=f"claude-agreed:{page}")
            if st.button("Allow Claude for this project", disabled=not agreed, key=f"claude-allow:{page}"):
                new = dict(raw)
                new["autofix"] = {**(raw.get("autofix") or {}), "assist": True}
                h.save_raw(ss.project, new)
                st.rerun()
            return
        model = "claude-sonnet-5-5" if cfg.assist_speed == "fast" else cfg.ai_model
        backend, how = ai.assist_backend(cfg.assist_via, cfg.profile, cfg.assist_endpoint)
        if backend is None:
            fx._setup_steps(cfg.assist_via if cfg.assist_via != "auto" else "claude-code", f"Claude is {how}.")
            return
        st.caption(f"Claude gets the {len(problems)} problem(s) below, the settings (no passwords), the mapping and the "
                   f"table definitions - never data. Uses {how}.")
        with st.expander(f"Problems sent to Claude ({len(problems)})"):
            st.markdown("\n".join(f"- {p}" for p in problems))
        details = st.text_input("Anything to add? (optional)", key=f"claude-problem-notes:{page}",
                                placeholder="e.g. the client's warehouse is reachable only through a VPN")
        asked = ss.get(key)
        if st.button("✨ Ask again" if asked else "✨ Explain and fix", type="primary",
                     disabled=job_key in fx._JOBS, key=f"claude-problem-ask:{page}"):
            req = {"problems": problems, "page": page, "settings": _settings_text(raw),
                   "tables": _tables_text(cfg, targets or []), "details": details, "model": model,
                   "via": cfg.assist_via, "profile": cfg.profile, "endpoint": cfg.assist_endpoint,
                   "fast": cfg.assist_speed == "fast", "context": ai.project_context(cfg, "\n".join(problems))}
            fx._start_job(job_key, req)
            ss.pop(key, None)
        if job_key in fx._JOBS:
            fx._wait_for_claude(job_key, key)
            return
        asked = ss.get(key)
        if not asked:
            return
        if asked.status != "ok":
            st.error(asked.notes)
            if asked.status == "signin":
                fx._setup_steps("claude-code", "Claude Code's sign-in on this computer needs renewing.")
            return
        st.markdown(asked.notes)
        st.caption(f"Answered in {asked.seconds:.0f} s.")
        if not asked.actions:
            st.caption("Claude proposed no change the app can apply - follow the steps above.")
            return
        st.markdown("**Fixes WishBridge can apply** - tick the ones you want:")
        picked = []
        for i, a in enumerate(asked.actions):
            label = _describe(a) + (f" - {a['why']}" if a.get("why") else "")
            if st.checkbox(label, value=a["type"] != "sql", key=f"claude-action:{page}:{i}:{asked.seconds}"):
                picked.append(a)
        if any(a["type"] == "sql" for a in asked.actions):
            st.caption("SQL runs on your Databricks workspace only when ticked; only additive statements are allowed "
                       "(never DROP, DELETE or TRUNCATE).")
        c1, c2 = st.columns(2)
        if c1.button(f"✅ Apply selected ({len(picked)})", type="primary", disabled=not picked,
                     key=f"claude-apply:{page}"):
            done = _apply(cfg, raw, picked)
            ss.pop(key, None)
            ss.pop("map_preflight", None)  # check again with the new settings
            ss[f"claude-problem-flash:{page}"] = "Applied: " + "; ".join(done) + ". Check again or copy again."
            st.rerun()
        if c2.button("🗑 Discard", key=f"claude-discard:{page}"):
            ss.pop(key, None)
            st.rerun()
