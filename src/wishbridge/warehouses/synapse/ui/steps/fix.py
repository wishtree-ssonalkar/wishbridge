"""Fix code (later, at Wishtree): the original next to the Databricks version; save hand fixes."""

from __future__ import annotations

import difflib
import threading
import time
from pathlib import Path

import streamlit as st

from wishbridge.warehouses.synapse import ai
from wishbridge.warehouses.synapse.convert import original_file
from wishbridge.warehouses.synapse.staging import read_source
from wishbridge.warehouses.synapse.state import load_state
from wishbridge.warehouses.synapse.ui import helpers as h
from wishbridge.warehouses.synapse.ui.common import STATUS_ICON

ss = st.session_state
_JOBS: dict[str, dict] = {}  # Claude requests running in the background: "<project>|<file>" -> job


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
    asked = ss.get(key)
    job_key = f"{ss.project}|{pick}"
    if st.button("✨ Ask again" if asked else "✨ Ask Claude", disabled=backend is None or job_key in _JOBS,
                 key=f"claude-ask:{pick}"):
        original = (read_source(orig) if orig is not None and orig.is_file()
                    else "(no original file: made from the table definitions read from the database)")
        issues = [f"line {x['line']} [{x['severity']}]: {x['message']}" for x in todo]
        context = ai.project_context(cfg, original, current)
        job = {"started": time.time(), "proc": None, "result": None, "done": False}

        def work(job=job):
            try:
                job["result"] = ai.ask_claude(cfg.source.label, f["kind"], original, current, issues, details, model,
                                              chosen, cfg.profile, endpoint, fast=speed == "fast", context=context,
                                              on_start=lambda p: job.__setitem__("proc", p))
            except Exception as e:  # noqa: BLE001 - keep the page alive, show what happened
                job["result"] = ai.Suggestion(None, f"Asking Claude failed: {e}", "failed")
            job["done"] = True

        _JOBS[job_key] = job
        threading.Thread(target=work, daemon=True).start()
        ss.pop(key, None)
        asked = None
    if job_key in _JOBS:
        _wait_for_claude(job_key, key)
        return
    if not asked:
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
