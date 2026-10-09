"""Handing over the assessment zip. WishBridge never sends e-mail by itself: it shows where the zip is, what is in
it and a ready message, and can open a new e-mail with everything filled in and the zip attached - the person at
the computer checks it and presses Send in their own mail program."""

from __future__ import annotations

import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlencode

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MAX_ATTACHMENT_MB = 20  # most mail servers refuse larger attachments

CONTENTS = [
    ("code_overview.html", "how the system is built: structure, tables, procedures, data flows"),
    ("report.html", "assessment, fit check, every converted file and its open items"),
    ("open_items.csv, files.csv, fit_check.csv", "the same details as spreadsheets"),
    ("original_code/ and converted_code/", "the code before and after conversion"),
    ("project/", "settings and results, so Wishtree can continue the work"),
]


def outlook_available() -> bool:
    if os.name != "nt":
        return False
    ps = ("try { $o = New-Object -ComObject Outlook.Application -ErrorAction Stop; "
          "[void][System.Runtime.InteropServices.Marshal]::ReleaseComObject($o); 'yes' } catch { 'no' }")
    try:
        out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", ps],
                             capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return False
    return out.strip().endswith("yes")


def _ps(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def open_mail_draft(to: list[str], cc: list[str], subject: str, body: str, attachment: Path) -> tuple[bool, str]:
    """Open a new e-mail with everything filled in and the zip attached. Nothing is sent: the person at the
    computer checks it and presses Send in their mail program."""
    attachment = Path(attachment).resolve()
    if outlook_available():
        script = "\n".join([
            "$ErrorActionPreference = 'Stop'",
            "$o = New-Object -ComObject Outlook.Application",
            "$m = $o.CreateItem(0)",
            f"$m.To = {_ps('; '.join(to))}",
            f"$m.CC = {_ps('; '.join(cc))}",
            f"$m.Subject = {_ps(subject)}",
            f"$m.Body = {_ps(body)}",
            f"[void]$m.Attachments.Add({_ps(str(attachment))})",
            "$m.Display()",
        ])
        try:
            p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                               capture_output=True, text=True, timeout=120)
        except (OSError, subprocess.TimeoutExpired) as e:
            return False, str(e)
        if p.returncode == 0:
            return True, "The e-mail is open in Outlook with the zip attached - check it and press Send."
        err = (p.stderr or p.stdout).strip().splitlines()
        return False, err[-1][:300] if err else "Outlook could not open the message."
    # No Outlook: the default mail program with To / CC / subject / text; mailto cannot carry attachments,
    # so the zip's folder is opened next to it to drag the file in.
    query = urlencode({"cc": ",".join(cc), "subject": subject, "body": body}, quote_via=quote)
    webbrowser.open(f"mailto:{','.join(to)}?{query}")
    if os.name == "nt":
        subprocess.Popen(["explorer", "/select,", str(attachment)])
    else:
        subprocess.Popen(["open" if sys.platform == "darwin" else "xdg-open", str(attachment.parent)])
    return True, ("Your mail program is open with the message filled in, and the zip's folder is open next to it: "
                  "drag the zip into the e-mail and press Send.")


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
