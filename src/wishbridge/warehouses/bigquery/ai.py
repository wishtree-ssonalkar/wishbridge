"""Optional AI-assisted fixes using Claude.

Suggestions are written next to the converted code for a human to review;
they never replace the converted file automatically.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

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
