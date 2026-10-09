"""Send the assessment zip by e-mail.

Two ways, both started by the user:
  * Outlook (Windows): WishBridge opens a new Outlook message with the zip attached and To / CC / subject /
    text filled in; the user reviews it and presses Send in Outlook. No password involved.
  * SMTP: send directly through the company's mail server; the password is typed each time and never saved.
"""

from __future__ import annotations

import os
import re
import smtplib
import subprocess
from email.message import EmailMessage
from pathlib import Path
from typing import Any

EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[^@\s,;]+$")
MAX_ATTACHMENT_MB = 20  # most mail servers refuse larger attachments


def split_addresses(text: str) -> list[str]:
    return [a.strip() for a in re.split(r"[,;\s]+", text or "") if a.strip()]


def invalid_addresses(addresses: list[str]) -> list[str]:
    return [a for a in addresses if not EMAIL.match(a)]


def default_message(project: str, state: dict[str, Any], zip_name: str) -> tuple[str, str]:
    """Subject and plain-text body summarising the assessment."""
    lines = [f"Hello,", "", f"Please find attached the WishBridge assessment package for {project} ({zip_name}).", ""]
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


def _ps_quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def open_outlook_draft(to: list[str], cc: list[str], subject: str, body: str, attachment: Path) -> tuple[bool, str]:
    """Open a new Outlook message with everything filled in; the user presses Send."""
    script = "\n".join([
        "$ErrorActionPreference = 'Stop'",
        "$o = New-Object -ComObject Outlook.Application",
        "$m = $o.CreateItem(0)",
        f"$m.To = {_ps_quote('; '.join(to))}",
        f"$m.CC = {_ps_quote('; '.join(cc))}",
        f"$m.Subject = {_ps_quote(subject)}",
        f"$m.Body = {_ps_quote(body)}",
        f"[void]$m.Attachments.Add({_ps_quote(str(Path(attachment).resolve()))})",
        "$m.Display()",
    ])
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
                           capture_output=True, text=True, timeout=120)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    if p.returncode != 0:
        return False, (p.stderr or p.stdout).strip().splitlines()[-1][:300] if (p.stderr or p.stdout).strip() else "Outlook failed"
    return True, "The message is open in Outlook with the zip attached - check it and press Send."


def send_smtp(host: str, port: int, user: str, password: str, sender: str, to: list[str], cc: list[str],
              subject: str, body: str, attachment: Path, starttls: bool = True) -> None:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Subject"] = sender, ", ".join(to), subject
    if cc:
        msg["Cc"] = ", ".join(cc)
    msg.set_content(body)
    msg.add_attachment(Path(attachment).read_bytes(), maintype="application", subtype="zip",
                       filename=Path(attachment).name)
    with smtplib.SMTP(host, port, timeout=60) as s:
        if starttls:
            s.starttls()
        if user:
            s.login(user, password)
        s.send_message(msg)
