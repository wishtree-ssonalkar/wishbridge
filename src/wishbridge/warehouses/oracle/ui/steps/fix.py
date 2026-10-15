"""Fix code (later, at Wishtree): the original next to the Databricks version; save hand fixes."""

from __future__ import annotations

import difflib
import threading
import time
from pathlib import Path

import streamlit as st

from wishbridge.warehouses.oracle import ai
from wishbridge.warehouses.oracle.convert import original_file
from wishbridge.warehouses.oracle.staging import read_source
from wishbridge.warehouses.oracle.state import load_state
from wishbridge.warehouses.oracle.ui import helpers as h
from wishbridge.warehouses.oracle.ui.common import STATUS_ICON

ss = st.session_state
_JOBS: dict[str, dict] = {}  # Claude requests running in the background: "<project>|<file>" -> job
_BATCHES: dict[str, dict] = {}  # "fix all files" runs: project -> batch (kept until saved or discarded)
_SCOPES = {"file": (), "fix": ("needs-fix",), "fix+review": ("needs-fix", "review")}


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
                st.code(read_source(orig), language="xml" if orig.suffix.lower() in (".dtsx", ".xml", ".dtproj") else "sql",
                        height=420)  # same height as the editor on the right; both scroll
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
        current = ss.get(f"edit-{pick}")
        if current is not None:
            _claude_help(cfg, raw, f, pick, orig, current, todo, ovr)


def _save_autofix(raw, **changes) -> None:
    new = dict(raw)
    new["autofix"] = {**(raw.get("autofix") or {}), **changes}
    h.save_raw(ss.project, new)


def _claude_help(cfg, raw, f, pick, orig, current, todo, ovr) -> None:
    """Ask Claude for a corrected version of this file; the person checks it and decides whether to use it."""
    st.divider()
    st.markdown("#### ✨ Ask Claude to fix this file")
    flash = ss.pop("claude_flash", None)
    if flash:
        st.success(flash)
    if not cfg.assist_enabled:
        st.caption("Off for this project: Claude only sees a client's code once the client has agreed.")
        agreed = st.checkbox("The client agreed that their code may be sent to Claude (code only, never data)",
                             key="claude-agreed")
        if st.button("Allow Claude for this project", disabled=not agreed, key="claude-allow"):
            _save_autofix(raw, assist=True, via=cfg.assist_via if cfg.assist_via != "auto" else "databricks")
            st.rerun()
        return

    vias = list(ai.VIA)
    via = cfg.assist_via if cfg.assist_via in vias else "databricks"
    c1, c2 = st.columns([3, 2])
    chosen = c1.radio("Connect with", vias, index=vias.index(via), format_func=ai.VIA.get, horizontal=True,
                      key="claude-via")
    if chosen != cfg.assist_via:
        _save_autofix(raw, via=chosen)
        st.rerun()
    endpoint = cfg.assist_endpoint
    if chosen == "databricks":
        with c2:
            st.caption(f"Login **{cfg.profile}** - the one this project deploys with.")
            if st.button("🔎 Find Claude models", key="claude-find"):
                with st.spinner("Asking the workspace..."):
                    try:
                        ss.claude_endpoints = ai.databricks_claude_endpoints(cfg.profile)
                    except ai.AssistError as e:
                        ss.claude_endpoints = None
                        st.error(str(e))
        found = ss.get("claude_endpoints")
        if found == []:
            st.warning("This workspace serves no Claude model. In Databricks open **Serving** and check the foundation "
                       "models (e.g. databricks-claude-sonnet-4), or choose another connection.")
        options = sorted(set((found or []) + ([endpoint] if endpoint else [])))
        if options:
            pick_ep = st.selectbox("Claude model in the workspace", options,
                                   index=options.index(endpoint) if endpoint in options else 0, key="claude-endpoint")
            if pick_ep != endpoint:
                _save_autofix(raw, endpoint=pick_ep)
                st.rerun()
    speeds = {"fast": "Fast - Claude Sonnet (usually under a minute)", "thorough": "Most thorough - Claude Opus (slower)"}
    speed = cfg.assist_speed if cfg.assist_speed in speeds else "fast"
    if chosen != "databricks":  # in Databricks the chosen served model decides
        picked = st.radio("Speed", list(speeds), index=list(speeds).index(speed), format_func=speeds.get,
                          horizontal=True, key="claude-speed")
        if picked != speed:
            _save_autofix(raw, speed=picked)
            st.rerun()
    model = "claude-sonnet-5-5" if speed == "fast" else cfg.ai_model
    signed_in = True
    if chosen == "claude-code":
        if "claude_code_status" not in ss:
            with st.spinner("Checking Claude Code..."):
                ss.claude_code_status = ai.claude_code_status()
        signed_in, msg = ss.claude_code_status
        if signed_in:
            t1, t2 = st.columns([4, 1])
            tested = ss.get("claude_code_test")
            if tested is None:
                t1.caption(f"✅ {msg} Not tested yet: **Test connection** checks that Claude really answers.")
            elif tested[0]:
                t1.caption(f"✅ {msg} {tested[1]}")
            else:
                t1.error(tested[1])
            if t2.button("🔌 Test connection", key="claude-test", type="primary" if tested is None else "secondary"):
                with st.spinner("Sending a tiny request to Claude (up to 60 s)..."):
                    ss.claude_code_test = ai.test_claude_code(model)
                st.rerun()
            if tested is not None and not tested[0]:
                signed_in = False
                _setup_steps("claude-code", "Claude Code did not answer the test.")
        else:
            _setup_steps("claude-code", msg)
    backend, how = ai.assist_backend(chosen, cfg.profile, endpoint)
    if backend is None and chosen in ("databricks", "api") and not (chosen == "databricks" and cfg.profile
                                                                   and not endpoint):
        _setup_steps(chosen, f"Claude is {how}.")
    if not signed_in:
        backend = None
    st.caption(f"Sends this file's original, the Databricks version above (with your unsaved edits) and the open "
               f"issues - never data. Uses {how}.")
    key = f"claude:{pick}"
    details = st.text_area("Extra details for Claude (optional)", key=f"claude-notes:{pick}", height=90,
                           placeholder="e.g. The order database is the catalog oltp_fed. "
                                       "Remove the call to usp_SendLoadReport.")
    scope = st.radio("What to fix", list(_SCOPES), format_func=lambda s: _scope_label(cfg, s, pick), horizontal=True,
                     key="claude-scope")
    if scope != "file":
        _fix_all(cfg, raw, scope, details, backend, chosen, model, speed, endpoint)
        return
    asked = ss.get(key)
    job_key = f"{ss.project}|{pick}"
    if st.button("✨ Ask again" if asked else "✨ Ask Claude", disabled=backend is None or job_key in _JOBS,
                 key=f"claude-ask:{pick}"):
        original = (read_source(orig) if orig is not None and orig.is_file()
                    else "(no original file: made from the table definitions read from the database)")
        issues = [f"line {x['line']} [{x['severity']}]: {x['message']}" for x in todo]
        req = {"source": cfg.source.label, "kind": f["kind"], "original": original, "current": current,
               "issues": issues, "details": details, "model": model, "via": chosen, "profile": cfg.profile,
               "endpoint": endpoint, "fast": speed == "fast", "context": ai.project_context(cfg, original, current)}
        ss[f"claude-req:{pick}"] = req  # kept so WishBridge can ask again by itself after repairing the sign-in
        ss.pop("claude_signin_opened", None)
        _start_job(job_key, req)
        ss.pop(key, None)
        asked = None
    if job_key in _JOBS:
        _wait_for_claude(job_key, key)
        return
    if not asked:
        return
    if asked.status == "signin":
        _repair_sign_in(job_key, key, pick, model)
        return
    took = f" ({asked.seconds:.0f} s)" if asked.seconds else ""
    if asked.status != "ok":
        st.error(asked.notes + took)
        return
    st.success(f"Claude's suggestion{took} is below. Check it before using it: nothing is saved until you choose.")
    if asked.notes:
        st.markdown(asked.notes)
    diff = "".join(difflib.unified_diff(current.splitlines(keepends=True), asked.sql.splitlines(keepends=True),
                                        "current", "Claude's suggestion"))
    st.code(diff or "(Claude suggests no changes)", language="diff")
    c1, c2 = st.columns(2)
    if c1.button("✅ Use this fix", type="primary", key=f"claude-use:{pick}", disabled=not diff):
        ovr.parent.mkdir(parents=True, exist_ok=True)
        ovr.write_text(asked.sql, encoding="utf-8")
        ss.pop(key, None)
        ss.pop(f"edit-{pick}", None)  # the editor shows the saved fix
        ss.claude_flash = "Saved as a manual fix. Run Convert (or Deploy) again to apply it."
        st.rerun()
    if c2.button("🗑 Discard", key=f"claude-discard:{pick}"):
        ss.pop(key, None)
        st.rerun()


def _setup_steps(via: str, why: str) -> None:
    """One-time setup on this computer, step by step, for the chosen connection."""
    with st.container(border=True):
        st.markdown(f"**⚙️ One-time setup on this computer** - {why}")
        if via == "claude-code":
            st.markdown(
                "Claude Code has its own sign-in, separate from the Claude desktop app. Do this once per computer:\n"
                "1. Click **Sign in again** below (or run the commands in a terminal). A window opens, signs out, "
                "updates Claude Code, then opens your browser.\n"
                "2. Sign in with your Claude account (Wishtree's Claude plan) and allow access.\n"
                "3. When the window says you are signed in, close it and click **↻ Check again**.")
            st.code("claude auth login", language="bash")
            st.markdown("If it says **401 Invalid access token**, sign out first, update Claude Code, then sign in:")
            st.code("claude auth logout\nclaude update\nclaude auth login", language="bash")
            version = ai.claude_code_version()
            if version:
                st.caption(f"Claude Code on this computer: version {version}.")
            c1, c2 = st.columns(2)
            if c1.button("🔑 Sign in again (sign out, update, sign in)", key="claude-sign-in"):
                problem = ai.open_claude_sign_in()
                (st.warning if problem else st.info)(problem or "A sign-in window has opened. Finish the sign-in in "
                                                                "the browser, then click Check again.")
            if c2.button("↻ Check again", key="claude-recheck"):
                ss.pop("claude_code_status", None)
                ss.pop("claude_code_test", None)
                st.rerun()
            st.caption("Not installed? Install Claude Code from claude.com/claude-code, then restart WishBridge.")
        elif via == "databricks":
            st.markdown(
                "Uses the Databricks login this project deploys with. Do this once per computer:\n"
                "1. Sign in to the workspace: **Settings > Databricks workspace > Sign in**, or run the command below.\n"
                "2. Click **🔎 Find Claude models** and pick one (e.g. databricks-claude-sonnet-4).\n"
                "3. None found? The workspace serves no Claude model: ask its admin to enable the Claude foundation "
                "models (Databricks > Serving), or choose another connection.")
            st.code("databricks auth login --profile DEFAULT", language="bash")
        else:
            st.markdown(
                "Uses a Claude API key from the Claude Console. Do this once per computer:\n"
                "1. Get a key at console.anthropic.com > API keys (or from your Wishtree admin).\n"
                "2. Save it for your Windows user with the command below (put your key in place of the dots).\n"
                "3. Close and restart WishBridge so it sees the key.")
            st.code("setx ANTHROPIC_API_KEY \"sk-ant-...\"", language="bash")


@st.fragment(run_every=2)
def _wait_for_claude(job_key: str, key: str) -> None:
    """While Claude works: a live timer and Cancel; the page stays usable, and only one request runs per file."""
    job = _JOBS.get(job_key)
    if job is None:
        return
    if job["done"]:
        ss[key] = job["result"]
        _JOBS.pop(job_key, None)
        st.rerun()
    c1, c2 = st.columns([5, 1])
    c1.info(f"⏳ Claude is working on the file... {time.time() - job['started']:.0f} s (usually 10-60 s).")
    if c2.button("✖ Cancel", key=f"claude-cancel:{job_key}"):
        proc = job.get("proc")
        if proc is not None and proc.poll() is None:
            proc.kill()
        _JOBS.pop(job_key, None)
        st.rerun()


def _start_job(job_key: str, req: dict) -> None:
    """Ask Claude in a background thread; _wait_for_claude shows the timer and picks up the answer."""
    job = {"started": time.time(), "proc": None, "result": None, "done": False}

    def work():
        try:
            job["result"] = ai.ask_claude(req["source"], req["kind"], req["original"], req["current"], req["issues"],
                                          req["details"], req["model"], req["via"], req["profile"], req["endpoint"],
                                          fast=req["fast"], context=req["context"],
                                          on_start=lambda p: job.__setitem__("proc", p))
        except Exception as e:  # noqa: BLE001 - keep the page alive, show what happened
            job["result"] = ai.Suggestion(None, f"Asking Claude failed: {e}", "failed")
        job["done"] = True

    _JOBS[job_key] = job
    threading.Thread(target=work, daemon=True).start()


@st.fragment(run_every=10)
def _repair_sign_in(job_key: str, key: str, pick: str, model: str) -> None:
    """Claude Code's sign-in stopped working: open the sign-in window once, wait for the person to finish it in the
    browser, test every 10 s, and ask Claude again by itself when it works."""
    if "claude_signin_opened" not in ss:
        problem = ai.open_claude_sign_in()
        ss.claude_signin_opened = time.time()
        if problem:
            st.warning(problem)
    waited = time.time() - ss.claude_signin_opened
    if waited > 600:
        st.error("Claude Code is still not signed in after 10 minutes. Click **Open the sign-in window again**, or "
                 "choose another connection above (Databricks workspace or a Claude API key).")
    else:
        st.warning(f"🔑 Claude Code's sign-in on this computer had stopped working, so WishBridge opened a sign-in "
                   f"window (it signs out, updates Claude Code and opens your browser). **Finish the sign-in in the "
                   f"browser** - WishBridge checks every 10 s and asks Claude again by itself. ({waited:.0f} s)")
        if waited > 20:  # give the window time to sign out and update first
            ok, _ = ai.test_claude_code(model, seconds=30)
            if ok:
                req = ss.get(f"claude-req:{pick}")
                for k in (key, "claude_signin_opened", "claude_code_test", "claude_code_status"):
                    ss.pop(k, None)
                if req:
                    _start_job(job_key, req)
                st.rerun()
    c1, c2 = st.columns(2)
    if c1.button("Open the sign-in window again", key=f"claude-reopen:{pick}"):
        ss.pop("claude_signin_opened", None)
        st.rerun()
    if c2.button("Stop", key=f"claude-stop:{pick}"):
        for k in (key, "claude_signin_opened"):
            ss.pop(k, None)
        st.rerun()


# ---------------------------------------------------------------- fix all files at once

def _batch_files(cfg, scope: str) -> list[dict]:
    conv = load_state(cfg).get("convert") or {}
    return [f for f in conv.get("files", []) if f.get("status") in _SCOPES[scope]]


def _scope_label(cfg, scope: str, pick: str) -> str:
    if scope == "file":
        return f"This file ({Path(pick).name})"
    n = len(_batch_files(cfg, scope))
    return {"fix": f"All files that need a fix ({n})", "fix+review": f"All files that need a fix or a review ({n})"}[scope]


def _file_request(cfg, raw, f: dict, details: str, chosen: str, model: str, speed: str, endpoint: str) -> dict | None:
    """Everything Claude needs for one file: its original, the current Databricks version (a saved manual fix if
    there is one), its open issues and the project background."""
    ovr = h.override_path(ss.project, f["file"], raw.get("overrides", "overrides"))
    cur = ovr if ovr.exists() else Path(f["final"])
    if not cur.is_file():
        return None
    current = cur.read_text(encoding="utf-8-sig")
    orig = None
    if f.get("converter") != "database":
        orig = Path(f["input"]) if f.get("input") else None
        if orig is None or not orig.is_file():
            orig = original_file(cfg, f["file"])
    original = (read_source(orig) if orig is not None and orig.is_file()
                else "(no original file: made from the table definitions read from the database)")
    issues = [f"line {x['line']} [{x['severity']}]: {x['message']}" for x in f.get("findings", [])
              if not x.get("fixed") and x.get("severity") != "info"]
    return {"file": f["file"], "status": f.get("status", ""), "source": cfg.source.label, "kind": f.get("kind", "sql"),
            "original": original, "current": current, "issues": issues, "details": details, "model": model,
            "via": chosen, "profile": cfg.profile, "endpoint": endpoint, "fast": speed == "fast",
            "context": ai.project_context(cfg, original, current)}


def _start_batch(bkey: str, reqs: list[dict], workers: int = 3) -> None:
    """Ask Claude about every file in the background, a few at a time; the first one also checks the connection."""
    from concurrent.futures import ThreadPoolExecutor

    batch = {"started": time.time(), "finished": None, "total": len(reqs), "results": [], "done": False,
             "cancel": False, "procs": [], "error": ""}

    def one(req):
        if batch["cancel"]:
            return None
        try:
            s = ai.ask_claude(req["source"], req["kind"], req["original"], req["current"], req["issues"],
                              req["details"], req["model"], req["via"], req["profile"], req["endpoint"],
                              fast=req["fast"], context=req["context"], on_start=batch["procs"].append)
        except Exception as e:  # noqa: BLE001 - one file failing must not stop the others
            s = ai.Suggestion(None, f"Asking Claude failed: {e}", "failed")
        batch["results"].append((req, s))
        return s

    def run():
        try:
            first = one(reqs[0]) if reqs else None
            if first is not None and first.status == "signin":
                batch["error"] = first.notes
            elif len(reqs) > 1:
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(pool.map(one, reqs[1:]))
        finally:
            batch["finished"] = time.time()
            batch["done"] = True

    _BATCHES[bkey] = batch
    threading.Thread(target=run, daemon=True).start()


def _fix_all(cfg, raw, scope, details, backend, chosen, model, speed, endpoint) -> None:
    bkey = ss.project
    batch = _BATCHES.get(bkey)
    if batch is None:
        files = _batch_files(cfg, scope)
        st.caption("Claude reviews and fixes every file in the list, 3 at a time. You then see one list with each "
                   "file's changes, tick the ones to keep and save them in one go - nothing is saved before that.")
        if st.button(f"✨ Review and fix {len(files)} files", type="primary", disabled=backend is None or not files,
                     key="claude-fix-all"):
            reqs = [r for r in (_file_request(cfg, raw, f, details, chosen, model, speed, endpoint) for f in files) if r]
            _start_batch(bkey, reqs)
            st.rerun()
        return
    if not batch["done"]:
        _batch_progress(bkey)
        return
    _batch_results(raw, bkey)


@st.fragment(run_every=2)
def _batch_progress(bkey: str) -> None:
    b = _BATCHES.get(bkey)
    if b is None:
        return
    if b["done"]:
        st.rerun()
    n = len(b["results"])
    st.progress(n / max(b["total"], 1), text=f"⏳ Claude is reviewing and fixing {b['total']} files, 3 at a time... "
                                             f"{n} done ({time.time() - b['started']:.0f} s)")
    if st.button("✖ Cancel", key="claude-batch-cancel"):
        b["cancel"] = True
        for proc in list(b["procs"]):
            if proc.poll() is None:
                proc.kill()
        b["finished"], b["done"] = time.time(), True  # keep the files already done
        st.rerun()


def _batch_results(raw, bkey: str) -> None:
    """One list: each file's changes and notes, a Save tick box each, and one Save button for all."""
    b = _BATCHES[bkey]
    if b["error"]:
        st.error(b["error"])
        st.info("Choose **This file** above and ask Claude about one file - it repairs the connection - then come "
                "back and fix all files.")
        if st.button("Close", key="claude-batch-close"):
            _BATCHES.pop(bkey, None)
            st.rerun()
        return
    order = {"needs-fix": 0, "review": 1}
    rows = sorted(b["results"], key=lambda rs: (order.get(rs[0]["status"], 2), rs[0]["file"]))
    changed = [(r, s) for r, s in rows if s.status == "ok" and s.sql and s.sql != r["current"]]
    same = [r for r, s in rows if s.status == "ok" and (not s.sql or s.sql == r["current"])]
    failed = [(r, s) for r, s in rows if s.status != "ok"]
    secs = (b["finished"] or time.time()) - b["started"]
    st.success(f"Claude reviewed {len(rows)} of {b['total']} files in {secs:.0f} s: **{len(changed)} with fixes**, "
               f"{len(same)} need no change, {len(failed)} could not be done. Untick any fix you don't want, then "
               "save them all at once.")
    for r, s in changed:
        with st.container(border=True):
            c1, c2 = st.columns([1, 20])
            c1.checkbox("Save", value=True, key=f"bsave:{r['file']}", label_visibility="collapsed")
            c2.markdown(f"**{r['file']}** · {STATUS_ICON.get(r['status'], r['status'])} · {s.seconds:.0f} s")
            if s.notes:
                c2.markdown(s.notes)
            with c2.expander("Show the changes"):
                diff = "".join(difflib.unified_diff(r["current"].splitlines(keepends=True),
                                                    s.sql.splitlines(keepends=True), "current", "Claude's fix"))
                st.code(diff, language="diff")
    if same:
        st.caption("No change needed: " + ", ".join(r["file"] for r in same))
    for r, s in failed:
        st.warning(f"**{r['file']}**: {s.notes}")
    picked = [(r, s) for r, s in changed if ss.get(f"bsave:{r['file']}", True)]
    b1, b2 = st.columns(2)
    if b1.button(f"💾 Save selected ({len(picked)})", type="primary", disabled=not picked, key="claude-batch-save"):
        for r, s in picked:
            ovr = h.override_path(ss.project, r["file"], raw.get("overrides", "overrides"))
            ovr.parent.mkdir(parents=True, exist_ok=True)
            ovr.write_text(s.sql, encoding="utf-8")
            ss.pop(f"edit-{r['file']}", None)  # the editor shows the saved fix
        for r, _ in changed:
            ss.pop(f"bsave:{r['file']}", None)
        _BATCHES.pop(bkey, None)
        ss.claude_flash = (f"Saved {len(picked)} fixes as manual fixes ({', '.join(r['file'] for r, _ in picked)}). "
                           "Run Convert (or Deploy) again to apply them.")
        st.rerun()
    if b2.button("🗑 Discard all", key="claude-batch-discard"):
        for r, _ in changed:
            ss.pop(f"bsave:{r['file']}", None)
        _BATCHES.pop(bkey, None)
        st.rerun()
