"""Text for handing over the assessment zip. WishBridge never sends e-mail itself: the page shows where the
zip is, what is in it and a ready message, and the client's team shares the file with Wishtree."""

from __future__ import annotations

import re
from typing import Any

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MAX_ATTACHMENT_MB = 20  # most mail servers refuse larger attachments

CONTENTS = [
    ("code_overview.html", "how the system is built: structure, tables, procedures, data flows"),
    ("report.html", "assessment, fit check, every converted file and its open items"),
    ("open_items.csv, files.csv, fit_check.csv", "the same details as spreadsheets"),
    ("original_code/ and converted_code/", "the code before and after conversion"),
    ("project/", "settings and results, so Wishtree can continue the work"),
]


def split_addresses(text: str) -> list[str]:
    return [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]


def invalid_addresses(addresses: list[str]) -> list[str]:
    return [a for a in addresses if not EMAIL.match(a)]


def default_message(project: str, state: dict[str, Any], zip_name: str) -> tuple[str, str]:
    """Subject and plain-text body the client's team can paste when they share the zip."""
    lines = ["Hello,", "", f"Please find attached the WishBridge assessment package for {project} ({zip_name}).", ""]
    ov, fit, a, c = (state.get(k) or {} for k in ("overview", "fit", "analyze", "convert"))
    if ov:
        parts = [f"{ov.get('files')} files", f"{ov.get('lines', 0):,} lines"] + [
            f"{ov[k]} {k}" for k in ("tables", "procedures", "views", "functions") if ov.get(k)]
        lines.append("- Code base: " + ", ".join(parts))
    if fit.get("headline"):
        lines.append(f"- Fit check: {fit['headline']}")
    if a.get("estimated_hours_baseline"):
        lines.append(f"- Manual rewrite estimate: {a['estimated_hours_baseline']} hours")
    if c.get("summary"):
        s = c["summary"]
        lines.append(f"- Conversion: {s['ready']} files ready, {s['review']} to review, {s['needs_fix']} needing a fix")
    lines += ["", "Open code_overview.html and report.html from the zip in a browser to see the details.", "",
              "Regards"]
    return f"WishBridge assessment - {project}", "\n".join(lines)
