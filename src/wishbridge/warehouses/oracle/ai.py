"""Optional AI-assisted fixes using Claude.

Suggestions are written next to the converted code for a human to review;
they never replace the converted file automatically.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path

from .rules import Finding

SYSTEM_PROMPT = """You are a senior data engineer migrating {source} code to Databricks SQL (Unity Catalog, Delta Lake).
You receive the original {source} file, the automatically converted Databricks SQL, and a list of open issues found in the conversion.
Return the complete corrected Databricks SQL file. Preserve the original behaviour exactly; where Databricks cannot express something
identically, choose the closest equivalent and add a `-- REVIEW:` comment explaining the difference.
Keep table and column names as they appear in the converted file.

Format your answer as:
```sql
<the full corrected file>
```
NOTES:
- <one bullet per change you made>"""


@dataclass
class Suggestion:
    sql: str | None
    notes: str
    status: str  # "ok", "skipped", "failed"
    seconds: float = 0.0  # how long Claude took (Fix code shows it)
    actions: list = field(default_factory=list)  # fixes the app can apply (Ask Claude about this problem)


class AssistError(RuntimeError):
    """A message for the person in Fix code: why Claude could not help this time."""


class SignInError(AssistError):
    """Claude Code's sign-in on this computer does not work: Fix code repairs it and asks again."""


def _client():
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("AI fixes need the 'anthropic' package: pip install \"wishbridge[ai]\"") from e
    return anthropic, anthropic.Anthropic()


def preflight() -> None:
    """Fail fast, with a clear message, when Claude credentials are missing or the API is unreachable."""
    try:
        import anthropic
    except ImportError as e:
        raise RuntimeError("AI fixes need the 'anthropic' package: pip install \"wishbridge[ai]\"") from e
    no_credentials = (TypeError, getattr(anthropic, "CredentialsError", TypeError))
    try:
        _, client = _client()
        client.models.list(limit=1)
    except no_credentials as e:  # raised by the SDK when no credential source resolves
        raise RuntimeError("AI fixes need Claude credentials: set ANTHROPIC_API_KEY (or run `ant auth login`).") from e
    except anthropic.AuthenticationError as e:
        raise RuntimeError("The Claude API rejected the credentials - check ANTHROPIC_API_KEY.") from e
    except anthropic.APIConnectionError as e:
        raise RuntimeError(f"Could not reach the Claude API (network or proxy problem): {e}") from e


def suggest_fix(source_name: str, original: str, converted: str, findings: list[Finding], model: str) -> Suggestion:
    open_issues = [f for f in findings if not f.fixed and f.severity in ("error", "warning")]
    if not open_issues:
        return Suggestion(None, "No open issues.", "skipped")

    anthropic, client = _client()
    issues = "\n".join(f"- line {f.line} [{f.severity}] {f.rule}: {f.message}" for f in open_issues)
    user = (
        f"<original_{source_name.replace(' ', '_').lower()}>\n{original}\n</original_{source_name.replace(' ', '_').lower()}>\n\n"
        f"<converted_databricks_sql>\n{converted}\n</converted_databricks_sql>\n\n"
        f"<open_issues>\n{issues}\n</open_issues>"
    )
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=SYSTEM_PROMPT.format(source=source_name),
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.RateLimitError as e:
        return Suggestion(None, f"Rate limited, try again later: {e}", "failed")
    except anthropic.APIStatusError as e:
        return Suggestion(None, f"API error {e.status_code}: {e.message}", "failed")
    except anthropic.APIConnectionError as e:
        return Suggestion(None, f"Could not reach the Claude API: {e}", "failed")

    if response.stop_reason == "refusal":
        return Suggestion(None, "The model declined this request.", "failed")
    text = "".join(b.text for b in response.content if b.type == "text")
    m = re.search(r"```sql\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE)
    if not m:
        return Suggestion(None, "No SQL block in the response.", "failed")
    notes = text[m.end():].strip()
    notes = re.sub(r"^NOTES:\s*", "", notes, flags=re.IGNORECASE)
    if response.stop_reason == "max_tokens":
        notes = "WARNING: response was truncated.\n" + notes
    return Suggestion(m.group(1).rstrip() + "\n", notes, "ok")


# ---------------------------------------------------------------- Fix code: "Ask Claude to fix this file"

ASSIST_PROMPT = """You are a senior data engineer migrating {source} code to Databricks (Unity Catalog, Delta Lake).
You receive the client's original {source} code, the current Databricks version ({target}), the open issues
WishBridge found, and notes from the engineer doing the migration.
Fix the open issues and anything the engineer asks. Preserve the original behaviour exactly; where Databricks cannot
express something identically, choose the closest equivalent and add a REVIEW comment explaining the difference.
Keep table and column names as in the Databricks version unless the engineer's notes say otherwise.
Use the project context (Databricks target, table definitions, other objects) to get names and types right.
Never invent tables, columns, connection details or credentials: where something is unknown, add a REVIEW comment.
You have no tools: answer with text only, and be brief.

Answer with ONLY the changes to the Databricks version, as edit blocks (never the whole file):
<<<<<<< SEARCH
exact lines copied from the Databricks version, just enough to be unique
=======
the new lines
>>>>>>> REPLACE
Use one block per change, in file order. SEARCH must match the Databricks version exactly, including indentation.
To add lines, put a nearby unchanged line in SEARCH and repeat it in REPLACE with the new lines.
After the blocks write:
NOTES:
- <one bullet per change>"""

MAX_CHARS = 200_000  # per file sent; very large SSIS packages are cut, with a note
_CLAUDE_CODE_OK: dict[str, float] = {}  # model -> when Claude Code last answered (skips the quick check for 10 min)


def _claude_code_exe() -> str | None:
    """Claude Code's program. On Windows npm installs a claude.cmd wrapper; call the real claude.exe behind it."""
    exe = shutil.which("claude")
    if not exe:
        return None
    p = Path(exe)
    if p.suffix.lower() in (".cmd", ".ps1", ""):
        real = p.parent / "node_modules" / "@anthropic-ai" / "claude-code" / "bin" / ("claude.exe" if os.name == "nt" else "claude")
        if real.is_file():
            return str(real)
    return exe


VIA = {"databricks": "Databricks workspace (the project's login)", "claude-code": "Claude Code on this computer",
       "api": "Claude API key (ANTHROPIC_API_KEY)"}


# Settings that send Claude Code somewhere else (a company gateway, a fixed model or token). WishBridge uses the
# person's own Claude sign-in, so they are left out: `--setting-sources project,local` skips ~/.claude/settings.json
# (its "env" block), and these are removed from the environment.
_ROUTING_ENV = ("ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS", "ANTHROPIC_MODEL", "ANTHROPIC_AUTH_TOKEN",
                "CLAUDE_CODE_USE_GATEWAY", "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX")


def _claude_code_env() -> dict[str, str]:
    return {k: v for k, v in os.environ.items() if k.upper() not in _ROUTING_ENV}


def assist_backend(via: str = "auto", profile: str = "", endpoint: str = "") -> tuple[str | None, str]:
    """Which Claude Fix code uses: ("databricks" | "api" | "claude-code" | None, what to tell the person).
    via: the project's choice; "auto" = the API key when set, else Claude Code."""
    from wishbridge.hosting import is_app

    if is_app() and via in ("claude-code", "auto"):  # a Databricks App has no Claude Code: the workspace's model
        via = "api" if via == "auto" and os.environ.get("ANTHROPIC_API_KEY") else "databricks"
    if via == "databricks":
        if not profile:
            return None, "not available: the project has no Databricks login (Settings > Databricks workspace)"
        if not endpoint:
            return None, "not ready: choose the Claude model served in the workspace (Find Claude models)"
        return "databricks", f"the Claude model `{endpoint}` in the Databricks workspace (login {profile})"
    if via == "claude-code":
        return (("claude-code", "Claude Code on this computer") if _claude_code_exe() else
                (None, "not available: Claude Code is not installed on this computer"))
    if via == "api":
        return (("api", "the Claude API (ANTHROPIC_API_KEY)") if os.environ.get("ANTHROPIC_API_KEY") else
                (None, "not available: ANTHROPIC_API_KEY is not set on this computer"))
    if os.environ.get("ANTHROPIC_API_KEY"):
        try:
            import anthropic  # noqa: F401
        except ImportError:
            pass
        else:
            return "api", "the Claude API (ANTHROPIC_API_KEY)"
    if _claude_code_exe():
        return "claude-code", "Claude Code on this computer"
    return None, ("not available: set ANTHROPIC_API_KEY, or install Claude Code and sign in with "
                  "`claude auth login`")


def _cap(text: str) -> str:
    return text if len(text) <= MAX_CHARS else text[:MAX_CHARS] + f"\n... (cut: {len(text) - MAX_CHARS:,} more characters)"


def _ask_api(system: str, user: str, model: str, effort: str = "high") -> str:
    anthropic, client = _client()
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=system,
            messages=[{"role": "user", "content": user}],
        ) as stream:
            response = stream.get_final_message()
    except anthropic.AuthenticationError as e:
        raise AssistError("The Claude API rejected the key - check ANTHROPIC_API_KEY.") from e
    except anthropic.RateLimitError as e:
        raise AssistError(f"Claude is busy (rate limited) - try again in a minute: {e}") from e
    except anthropic.APIStatusError as e:
        raise AssistError(f"Claude API error {e.status_code}: {e.message}") from e
    except anthropic.APIConnectionError as e:
        raise AssistError(f"Could not reach the Claude API (network or proxy): {e}") from e
    if response.stop_reason == "refusal":
        raise AssistError("Claude declined this request.")
    text = "".join(b.text for b in response.content if b.type == "text")
    return ("WARNING: the answer was cut short.\n" if response.stop_reason == "max_tokens" else "") + text


def _ask_claude_code(system: str, user: str, model: str, effort: str = "high", timeout: int = 600,
                     on_start=None) -> str:
    """Claude Code, headless, with every tool and MCP server switched off: it can only answer with text.
    on_start(process) lets Fix code cancel it."""
    exe = _claude_code_exe()
    if not exe:
        raise AssistError("Claude Code is not installed on this computer.")
    with tempfile.TemporaryDirectory(prefix="wishbridge-claude-", ignore_cleanup_errors=True) as empty:  # no files to see
        proc = subprocess.Popen(
            [exe, "-p", "--setting-sources", "project,local", "--model", model, "--effort", effort, "--tools", "",
             "--strict-mcp-config", "--no-session-persistence", "--output-format", "json"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8",
            errors="replace", cwd=empty, env=_claude_code_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if on_start is not None:
            on_start(proc)
        try:
            stdout, stderr = proc.communicate(system + "\n\n" + user, timeout=timeout)
        except subprocess.TimeoutExpired as e:
            proc.kill()
            proc.communicate()
            limit = f"{timeout // 60} minutes" if timeout >= 120 else f"{timeout} s"
            raise AssistError(f"Claude did not answer within {limit} - the Claude service may be busy "
                              "right now. Ask again in a moment, or choose Fast.") from e
    lines = [ln for ln in (stdout or "").splitlines() if ln.lstrip().startswith("{")]
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise AssistError("Claude Code gave no answer: " + ((stderr or stdout or "").strip()[-500:] or
                                                            f"exit code {proc.returncode}")) from None
    result = str(out.get("result") or "")
    if out.get("is_error"):
        # The real answer wins over `claude auth status`, which can say "signed in" for a sign-in Claude rejects.
        if re.search(r"\b401\b|invalid.{0,20}token|not logged in|/login|authenticat|oauth", result, re.IGNORECASE):
            raise SignInError("Claude Code's sign-in on this computer does not work (" + result[:160] + "). "
                              "Click **Sign in again** below (it signs out, updates Claude Code and signs in), "
                              "then click Test connection.")
        raise AssistError(f"Claude Code: {result or out.get('terminal_reason') or 'unknown error'}. Click Ask again; "
                          "if it keeps failing, choose another connection.")
    return result


def parse_answer(text: str) -> Suggestion:
    m = re.search(r"```[A-Za-z0-9_+-]*[ \t]*\r?\n(.*?)```", text, re.DOTALL)
    if not m:
        return Suggestion(None, "Claude answered without a corrected file:\n\n" + text.strip()[:1500], "failed")
    notes = re.sub(r"^NOTES:\s*", "", text[m.end():].strip(), flags=re.IGNORECASE)
    if text.startswith("WARNING:"):
        notes = text.splitlines()[0] + "\n" + notes
    return Suggestion(m.group(1).rstrip() + "\n", notes, "ok")


def ask_claude(source_name: str, kind: str, original: str, current: str, issues: list[str], details: str,
               model: str, via: str = "auto", profile: str = "", endpoint: str = "", fast: bool = True,
               context: str = "", on_start=None) -> Suggestion:
    """One corrected version of a file for a person to review in Fix code; never saved by itself."""
    backend, why = assist_backend(via, profile, endpoint)
    if backend is None:
        return Suggestion(None, f"Claude is {why}.", "failed")
    sql = kind == "sql"
    system = ASSIST_PROMPT.format(source=source_name, fence="sql" if sql else "python",
                                  target="Databricks SQL" if sql else "a Databricks notebook in Python / PySpark")
    issue_lines = "\n".join(f"- {i}" for i in issues) or "- none listed"
    user = (f"<original>\n{_cap(original)}\n</original>\n\n"
            f"<databricks_version>\n{_cap(current)}\n</databricks_version>\n\n"
            f"<open_issues>\n{issue_lines}\n</open_issues>\n\n"
            f"<engineer_notes>\n{details.strip() or 'none'}\n</engineer_notes>")
    if context.strip():
        user = context.strip() + "\n\n" + user
    started = time.time()
    text, failed = _send(backend, system, user, model, profile, endpoint, fast, on_start)
    if failed is not None:
        return failed
    s = apply_edits(current, text)
    s.seconds = time.time() - started
    return s


def _send(backend: str, system: str, user: str, model: str, profile: str, endpoint: str, fast: bool,
          on_start=None) -> tuple[str | None, Suggestion | None]:
    """Send one request with the chosen connection: (answer text, None) or (None, a failed Suggestion)."""
    started = time.time()
    if backend == "claude-code" and time.time() - _CLAUDE_CODE_OK.get(model, 0) > 600:
        # A broken Claude Code sign-in does not fail: it hangs retrying for minutes. A tiny request answers in ~5 s
        # when all is well, so check first and repair the sign-in at once instead of waiting.
        ok, msg = test_claude_code(model, seconds=25)
        if not ok:
            return None, Suggestion(None, "Claude Code did not answer a quick check, so its sign-in on this computer "
                                          f"most likely needs renewing. ({msg})", "signin", time.time() - started)
        _CLAUDE_CODE_OK[model] = time.time()
    try:
        if backend == "databricks":
            text = _ask_databricks(system, user, profile, endpoint)
        elif backend == "api":
            text = _ask_api(system, user, model, "medium" if fast else "high")
        else:
            text = _ask_claude_code(system, user, model, "medium" if fast else "high",
                                    timeout=300 if fast else 600, on_start=on_start)
    except SignInError as e:
        return None, Suggestion(None, str(e), "signin", time.time() - started)
    except AssistError as e:
        return None, Suggestion(None, str(e), "failed", time.time() - started)
    if backend == "claude-code":
        _CLAUDE_CODE_OK[model] = time.time()
    return text, None


QUESTION_PROMPT = """You are a senior data engineer helping a team migrate {source} code to Databricks (Unity Catalog,
Delta Lake). Answer the engineer's question clearly and briefly, in plain language (short paragraphs or bullets).
Use the project background, the list of project files with their status and open issues, and the open file.
Base every statement on what you were given; if something is not in it, say so instead of guessing. Never invent
tables, columns, connection details or credentials. You have no tools: answer with text only.
Only if the question asks you to change the open file, also give the changes as edit blocks after your answer:
<<<<<<< SEARCH
exact lines copied from the Databricks version, just enough to be unique
=======
the new lines
>>>>>>> REPLACE"""


def ask_question(question: str, source_name: str, kind: str, original: str, current: str, issues: list[str],
                 overview: str, context: str, model: str, via: str = "auto", profile: str = "", endpoint: str = "",
                 fast: bool = True, history: list[tuple[str, str]] | None = None, file: str = "",
                 on_start=None) -> Suggestion:
    """A free question about the project or the open file. notes = the answer; sql = the open file with the changes
    the person asked for (None when the answer has no changes)."""
    backend, why = assist_backend(via, profile, endpoint)
    if backend is None:
        return Suggestion(None, f"Claude is {why}.", "failed")
    issue_lines = "\n".join(f"- {i}" for i in issues) or "- none listed"
    earlier = "\n\n".join(f"Engineer: {q}\nYou: {a[:1500]}" for q, a in (history or [])[-3:])
    user = "\n\n".join(part for part in [
        context.strip(),
        f"<project_files>\n{overview.strip() or 'none listed'}\n</project_files>",
        f"<open_file name=\"{file}\">\n<original>\n{_cap(original)}\n</original>\n"
        f"<databricks_version>\n{_cap(current)}\n</databricks_version>\n"
        f"<open_issues>\n{issue_lines}\n</open_issues>\n</open_file>",
        f"<earlier_conversation>\n{earlier}\n</earlier_conversation>" if earlier else "",
        f"<question>\n{question.strip()}\n</question>"] if part)
    started = time.time()
    text, failed = _send(backend, QUESTION_PROMPT.format(source=source_name), user, model, profile, endpoint, fast,
                         on_start)
    if failed is not None:
        return failed
    answer = _EDIT.sub("", text).strip()
    sql = None
    if _EDIT.search(text):
        edited = apply_edits(current, text)
        if edited.status != "ok":
            answer += "\n\n(" + edited.notes + ")"
        elif edited.sql != current:
            sql = edited.sql
    return Suggestion(sql, answer or "(Claude gave no answer.)", "ok", time.time() - started)


# ---------------------------------------------------------------- Claude served in the Databricks workspace

def _first_line(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:300]


def databricks_claude_endpoints(profile: str) -> list[str]:
    """The Claude models the workspace serves (Serving > foundation models, or an external Anthropic model)."""
    from wishbridge.hosting import workspace_client

    try:
        endpoints = list(workspace_client(profile=profile).serving_endpoints.list())
    except Exception as e:  # the SDK raises many types; the message says what is wrong
        raise AssistError(f"Could not reach the Databricks workspace with the login '{profile}': {_first_line(e)}. "
                          "If the login expired, sign in again in Settings > Databricks workspace.") from e
    names = []
    for ep in endpoints:
        served = (ep.config.served_entities or []) if ep.config else []
        anthropic_external = any(getattr(getattr(s, "external_model", None), "provider", None) is not None
                                 and str(getattr(s.external_model.provider, "value", s.external_model.provider)).lower()
                                 == "anthropic" for s in served)
        if ep.name and ("claude" in ep.name.lower() or anthropic_external):
            names.append(ep.name)
    return sorted(names)


def _ask_databricks(system: str, user: str, profile: str, endpoint: str) -> str:
    from wishbridge.hosting import workspace_client
    from databricks.sdk.service.serving import ChatMessage, ChatMessageRole

    try:
        w = workspace_client(profile=profile, http_timeout_seconds=900)
        r = w.serving_endpoints.query(name=endpoint, max_tokens=32000, messages=[
            ChatMessage(role=ChatMessageRole.SYSTEM, content=system),
            ChatMessage(role=ChatMessageRole.USER, content=user)])
    except Exception as e:  # the SDK raises many types; the message says what is wrong
        raise AssistError(f"Databricks ({endpoint}, login {profile}): {_first_line(e)}") from e
    choice = (r.choices or [None])[0]
    content = choice.message.content if choice is not None and choice.message is not None else ""
    if isinstance(content, list):  # some endpoints answer in parts
        content = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in content)
    if not content:
        raise AssistError(f"The Databricks model {endpoint} gave an empty answer.")
    cut = choice.finish_reason == "length"
    return ("WARNING: the answer was cut short.\n" if cut else "") + str(content)


def claude_code_status() -> tuple[bool, str]:
    """Is Claude Code signed in on this computer? (signed in, what to tell the person). Its sign-in is separate
    from the Claude desktop app's."""
    exe = _claude_code_exe()
    if not exe:
        return False, "Claude Code is not installed on this computer."
    try:
        proc = subprocess.run([exe, "auth", "status"], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=60, env=_claude_code_env(),
                              creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        info = json.loads(proc.stdout or "{}")
    except (OSError, subprocess.TimeoutExpired, ValueError) as e:
        return False, f"Could not ask Claude Code whether it is signed in: {e}"
    if info.get("loggedIn"):
        who = info.get("email") or info.get("account") or info.get("authMethod") or ""
        return True, f"Claude Code is signed in{f' ({who})' if who else ''}."
    return False, "Claude Code is not signed in on this computer yet."


def open_claude_sign_in() -> str:
    """Open a terminal window running `claude auth login` (it opens the browser to sign in). Windows only;
    elsewhere the person runs the command."""
    exe = _claude_code_exe()
    if not exe:
        return "Claude Code is not installed on this computer."
    if os.name != "nt":
        return "Run `claude auth login` in a terminal."
    # A fresh sign-in: sign out (clears a sign-in Claude no longer accepts), update, sign in.
    script = (f'"{exe}" auth logout & echo. & echo Updating Claude Code... & "{exe}" update & echo. '
              f'& echo Sign in in the browser that opens next. & "{exe}" auth login')
    subprocess.Popen(f'start "Claude Code sign-in" cmd /k "{script}"', shell=True)
    return ""


_EDIT = re.compile(r"<<<<<<< SEARCH[ \t]*\r?\n(.*?)\r?\n?=======[ \t]*\r?\n(.*?)\r?\n?>>>>>>> REPLACE", re.DOTALL)


def _replace_lines(text: str, search: str, replace: str) -> str | None:
    """Replace `search` in `text`: whole lines first (ignoring trailing spaces, so `b = 2` never hits `b = 25`),
    else the exact text. None: not found."""
    want = [ln.rstrip() for ln in search.splitlines()]
    lines = text.splitlines(keepends=True)
    for i in range(len(lines) - len(want) + 1):
        if want and [ln.rstrip() for ln in lines[i:i + len(want)]] == want:
            tail = "\n" if lines[i + len(want) - 1].endswith("\n") else ""
            return "".join(lines[:i]) + replace + tail + "".join(lines[i + len(want):])
    if search and search in text:
        return text.replace(search, replace, 1)
    return None


def apply_edits(current: str, text: str) -> Suggestion:
    """Apply Claude's SEARCH/REPLACE blocks to the current Databricks version (a full file is accepted too)."""
    blocks = _EDIT.findall(text)
    tail = text[text.rfind(">>>>>>> REPLACE") + len(">>>>>>> REPLACE"):] if blocks else text
    notes = re.sub(r"^\s*NOTES:\s*", "", tail.strip(), flags=re.IGNORECASE)
    if text.startswith("WARNING:"):
        notes = text.splitlines()[0] + "\n" + notes
    if not blocks:
        if "```" in text:
            return parse_answer(text)  # an older-style answer with the whole file
        return Suggestion(current, notes or "Claude suggests no changes.", "ok")
    new = current
    for n, (search, replace) in enumerate(blocks, 1):
        done = _replace_lines(new, search, replace)
        if done is None:
            first = (search.strip().splitlines() or [""])[0][:80]
            return Suggestion(None, f"Claude's change {n} of {len(blocks)} does not match the file (it starts with "
                                    f"`{first}`). Click Ask again.", "failed")
        new = done
    return Suggestion(new, notes, "ok")


def project_context(cfg, *texts: str) -> str:
    """The project's background for Claude: the Databricks target, the definitions of the tables these texts use
    (read from the warehouse database), and the names of the other code objects and files. Code names only - no data."""
    import csv

    words = " ".join(texts).lower()
    lines = [f"Project: {cfg.name} - {cfg.source.label} code being migrated to Databricks.",
             f"Databricks target: the migrated tables and code live in `{cfg.catalog}.{cfg.schema}`."]
    if cfg.schema_map:
        lines.append("Schema mapping (source schema -> Databricks): "
                     + ", ".join(f"{k} -> {v}" for k, v in cfg.schema_map.items()))
    src = getattr(cfg, "source_catalog", "")
    lines.append(f"The source system is reachable in Databricks as the catalog `{src}` (Lakehouse Federation)." if src
                 else "No Databricks catalog is set yet for the source system: where the code reads the source "
                      "system, keep the names and add a REVIEW comment.")
    tables: dict[str, list[str]] = {}
    inv = cfg.output_dir / "inventory" / "inventory.csv"
    if inv.is_file():
        with open(inv, encoding="utf-8-sig", newline="") as fh:
            for r in csv.DictReader(fh):
                t = (r.get("table_name") or "").strip()
                if t and re.search(rf"\b{re.escape(t.lower())}\b", words):
                    null = "" if (r.get("is_nullable") or "YES").upper() == "YES" else " NOT NULL"
                    tables.setdefault(f"{r.get('schema_name')}.{t}", []).append(
                        f"{r.get('column_name')} {r.get('data_type')}{null}")
    if tables:
        lines.append("Tables this file uses, as defined in the warehouse database:")
        lines += [f"- {t} ({', '.join(cols)})" for t, cols in sorted(tables.items())]
    dbc = cfg.path.parent / "database_code"
    objs = sorted(p.stem for p in dbc.rglob("*.sql")) if dbc.is_dir() else []
    if objs:
        lines.append("Code objects in the warehouse database: " + ", ".join(objs[:200]))
    files = (sorted(p.relative_to(cfg.input_dir).as_posix() for p in cfg.input_dir.rglob("*") if p.is_file())
             if cfg.input_dir.is_dir() else [])
    if files:
        lines.append("Code files in the project: " + ", ".join(files[:200]))
    return "<project_context>\n" + "\n".join(lines) + "\n</project_context>"


def claude_code_version() -> str:
    exe = _claude_code_exe()
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--version"], capture_output=True, text=True, encoding="utf-8", errors="replace",
                             timeout=30, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    m = re.search(r"\d+\.\d+\.\d+", out or "")
    return m.group(0) if m else ""


def test_claude_code(model: str, seconds: int = 60) -> tuple[bool, str]:
    """A tiny real request with a short limit: does Claude Code answer on this computer right now?"""
    started = time.time()
    try:
        text = _ask_claude_code("Reply with exactly: OK", "", model, "low", timeout=seconds)
    except AssistError as e:
        return False, str(e)
    took = time.time() - started
    return True, f"Claude Code answered in {took:.0f} s{'' if 'OK' in text else ' (unexpected answer: ' + text[:60] + ')'}."


# ---------------------------------------------------------------- settings and data problems

SETTING_KEYS = ("databricks.profile", "databricks.catalog", "databricks.schema", "databricks.warehouse_id",
                "data.method", "data.files_root", "data.file_format", "data.source_catalog", "data.mode",
                "data.local.server", "data.local.database", "data.local.trust", "data.local.windows_login")

PROBLEM_PROMPT = """You help a data engineer with the "{page}" step of a migration to Databricks (Unity Catalog, Delta
Lake) in the WishBridge app. You get the problems shown on the screen, the project's current settings (passwords are
never included), the table mapping, table definitions and the project background.
First explain briefly, in plain language, what is wrong and why. Then say how to fix it.
Base everything on what you were given. Never invent catalog, schema, table, column, server or volume names: if a name
is not in what you were given, say what the engineer should check. You have no tools: answer with text only.
Where the app can apply a fix, end with ONE json block (leave out anything you are not sure of):
```json
{{"actions": [
  {{"type": "setting", "key": "<one of: {keys}>", "value": "<new value>", "why": "<short reason>"}},
  {{"type": "column", "table": "<Databricks table>", "column": "<Databricks column>", "source": "<source column>", "why": "..."}},
  {{"type": "filter", "table": "<Databricks table>", "where": "<SQL condition, or empty for all rows>", "why": "..."}},
  {{"type": "sql", "sql": "<one Databricks SQL statement>", "why": "..."}}
]}}
```
SQL may only be ALTER TABLE (never dropping anything), CREATE SCHEMA / CREATE VOLUME / CREATE TABLE ... IF NOT EXISTS,
or COMMENT ON. Never DROP, DELETE, TRUNCATE, UPDATE, INSERT, MERGE, GRANT or REVOKE."""

_FORBIDDEN_SQL = re.compile(r"\b(DROP|DELETE|TRUNCATE|UPDATE|INSERT|MERGE|GRANT|REVOKE|REPLACE|RENAME|OVERWRITE)\b",
                            re.IGNORECASE)
_ALLOWED_SQL = re.compile(r"^\s*(ALTER\s+TABLE|CREATE\s+(SCHEMA|VOLUME|TABLE)\s+IF\s+NOT\s+EXISTS|COMMENT\s+ON)\b",
                          re.IGNORECASE)


def safe_sql(sql: str) -> bool:
    """Only additive changes, one statement."""
    body = sql.strip().rstrip(";").strip()
    return bool(body) and ";" not in body and bool(_ALLOWED_SQL.match(body)) and not _FORBIDDEN_SQL.search(body)


def parse_actions(text: str) -> tuple[str, list[dict]]:
    """Split Claude's answer into the explanation and the fixes the app may apply (anything else is dropped)."""
    blocks = list(re.finditer(r"```json\s*\n(.*?)```", text, re.DOTALL | re.IGNORECASE))
    if not blocks:
        return text.strip(), []
    answer = (text[:blocks[-1].start()] + text[blocks[-1].end():]).strip()
    try:
        raw = json.loads(blocks[-1].group(1)).get("actions") or []
    except (ValueError, AttributeError):
        return answer, []
    actions = []
    for a in raw if isinstance(raw, list) else []:
        if not isinstance(a, dict):
            continue
        kind = a.get("type")
        if kind == "setting" and a.get("key") in SETTING_KEYS and "value" in a:
            actions.append(a)
        elif kind == "column" and a.get("table") and a.get("column") and a.get("source"):
            actions.append(a)
        elif kind == "filter" and a.get("table") and "where" in a:
            actions.append(a)
        elif kind == "sql" and safe_sql(str(a.get("sql") or "")):
            actions.append(a)
    return answer, actions


def ask_problem(page: str, problems: list[str], settings: str, tables: str, details: str, context: str, model: str,
                via: str = "auto", profile: str = "", endpoint: str = "", fast: bool = True,
                on_start=None) -> Suggestion:
    """Explain the problems on a settings or data screen; actions = fixes the person can tick and apply."""
    backend, why = assist_backend(via, profile, endpoint)
    if backend is None:
        return Suggestion(None, f"Claude is {why}.", "failed")
    user = "\n\n".join(part for part in [
        context.strip(),
        "<problems_on_screen>\n" + "\n".join(f"- {p}" for p in problems) + "\n</problems_on_screen>",
        f"<current_settings>\n{settings.strip()}\n</current_settings>",
        f"<tables>\n{tables.strip()}\n</tables>" if tables.strip() else "",
        f"<engineer_notes>\n{details.strip()}\n</engineer_notes>" if details.strip() else ""] if part)
    started = time.time()
    system = PROBLEM_PROMPT.format(page=page, keys=", ".join(SETTING_KEYS))
    text, failed = _send(backend, system, user, model, profile, endpoint, fast, on_start)
    if failed is not None:
        return failed
    answer, actions = parse_actions(text)
    return Suggestion(None, answer or "(Claude gave no answer.)", "ok", time.time() - started, actions)
