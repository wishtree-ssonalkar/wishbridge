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
from dataclasses import dataclass
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


class AssistError(RuntimeError):
    """A message for the person in Fix code: why Claude could not help this time."""


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
Return the complete corrected Databricks version. Preserve the original behaviour exactly; where Databricks cannot
express something identically, choose the closest equivalent and add a REVIEW comment explaining the difference.
Keep table and column names as in the Databricks version unless the engineer's notes say otherwise.
Never invent tables, columns, connection details or credentials: where something is unknown, add a REVIEW comment.
You have no tools: answer with text only.

Format your answer exactly as:
```{fence}
<the full corrected file>
```
NOTES:
- <one bullet per change you made>"""

MAX_CHARS = 200_000  # per file sent; very large SSIS packages are cut, with a note


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


def assist_backend() -> tuple[str | None, str]:
    """Which Claude Fix code can use: ("api" | "claude-code" | None, what to tell the person)."""
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


def _ask_api(system: str, user: str, model: str) -> str:
    anthropic, client = _client()
    try:
        with client.beta.messages.stream(
            model=model,
            max_tokens=64000,
            thinking={"type": "adaptive"},
            output_config={"effort": "high"},
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


def _ask_claude_code(system: str, user: str, model: str) -> str:
    """Claude Code, headless, with every tool and MCP server switched off: it can only answer with text."""
    exe = _claude_code_exe()
    if not exe:
        raise AssistError("Claude Code is not installed on this computer.")
    with tempfile.TemporaryDirectory(prefix="wishbridge-claude-") as empty:  # no project files for it to see
        try:
            proc = subprocess.run(
                [exe, "-p", "--model", model, "--tools", "", "--strict-mcp-config", "--no-session-persistence",
                 "--output-format", "json"],
                input=system + "\n\n" + user, capture_output=True, text=True, encoding="utf-8", errors="replace",
                cwd=empty, timeout=900)
        except subprocess.TimeoutExpired as e:
            raise AssistError("Claude Code did not answer within 15 minutes.") from e
    lines = [ln for ln in (proc.stdout or "").splitlines() if ln.lstrip().startswith("{")]
    try:
        out = json.loads(lines[-1])
    except (IndexError, ValueError):
        raise AssistError("Claude Code gave no answer: " + ((proc.stderr or proc.stdout or "").strip()[-500:] or
                                                            f"exit code {proc.returncode}")) from None
    result = str(out.get("result") or "")
    if out.get("is_error"):
        if re.search(r"authenticat|not logged in|log ?in|oauth", result, re.IGNORECASE):
            raise AssistError("Claude Code is not signed in on this computer: run `claude auth login` in a terminal, "
                              "then ask again.")
        raise AssistError(f"Claude Code: {result or out.get('terminal_reason') or 'unknown error'}")
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
               model: str) -> Suggestion:
    """One corrected version of a file for a person to review in Fix code; never saved by itself."""
    backend, why = assist_backend()
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
    try:
        text = _ask_api(system, user, model) if backend == "api" else _ask_claude_code(system, user, model)
    except AssistError as e:
        return Suggestion(None, str(e), "failed")
    return parse_answer(text)
