"""Check this computer and install what WishBridge needs - automatically.

Needed for the offline assessment (analyze + convert):
  Databricks CLI   - runs LakeBridge (`databricks labs lakebridge ...`)
  Java 11+         - the Morph converter runs on Java
  LakeBridge       - Databricks Labs' migration toolkit (analyzer, converters, reconcile)
  Morph, BladeBridge - LakeBridge's two converters
A Databricks login is only needed for the migration phase; it is checked there, not here.

Installs use the operating system's package manager (winget on Windows, Homebrew on macOS) or the
Databricks CLI itself. Downloads need internet access; office networks that inspect HTTPS traffic can
block them - the message then says so.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .config import TRANSPILER_DIRS

LABS = Path.home() / ".databricks" / "labs"
NETWORK_HINT = ("If you are on an office network that inspects HTTPS (e.g. Sophos), downloads can be blocked: "
                "try another network (e.g. a mobile hotspot) and press Check again.")


@dataclass
class Requirement:
    key: str
    name: str
    why: str
    check: Callable[[], str | None]          # returns where it was found, or None
    install: Callable[[], list[list[str]]]   # commands to run, in order ([] = cannot install automatically)
    manual: str                              # what to do by hand
    after: tuple[str, ...] = ()              # keys that must be present first


@dataclass
class Status:
    key: str
    name: str
    ok: bool
    detail: str
    why: str
    manual: str
    log: list[str] = field(default_factory=list)


def _os() -> str:
    return {"Windows": "windows", "Darwin": "mac"}.get(platform.system(), "linux")


def refresh_path() -> None:
    """Pick up programs installed a moment ago (Windows keeps the old PATH in running processes)."""
    extra: list[str] = []
    if _os() == "windows":
        try:
            import winreg

            for root, key in ((winreg.HKEY_LOCAL_MACHINE, r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"),
                              (winreg.HKEY_CURRENT_USER, "Environment")):
                with winreg.OpenKey(root, key) as k:
                    extra += os.path.expandvars(winreg.QueryValueEx(k, "Path")[0]).split(";")
        except OSError:
            pass
        local = os.environ.get("LOCALAPPDATA", "")
        extra.append(str(Path(local) / "Microsoft" / "WinGet" / "Links"))
        for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),):
            for jdk in sorted(Path(base).glob("Eclipse Adoptium/*/bin")) + sorted(Path(base).glob("Java/*/bin")):
                extra.append(str(jdk))
    else:
        extra += ["/opt/homebrew/bin", "/usr/local/bin", str(Path.home() / ".local" / "bin")]
    current = os.environ.get("PATH", "").split(os.pathsep)
    os.environ["PATH"] = os.pathsep.join(dict.fromkeys([p for p in current + extra if p]))


def _winget(package: str) -> list[list[str]]:
    if not shutil.which("winget"):
        return []
    return [["winget", "install", "--id", package, "--exact", "--silent",
             "--accept-source-agreements", "--accept-package-agreements"]]


def _cli() -> list[str]:
    return [shutil.which("databricks") or "databricks"]


def requirements() -> list[Requirement]:
    osn = _os()
    reqs = [
        Requirement(
            "cli", "Databricks CLI", "runs LakeBridge",
            lambda: shutil.which("databricks"),
            lambda: _winget("Databricks.DatabricksCLI") if osn == "windows" else
            [["brew", "tap", "databricks/tap"], ["brew", "install", "databricks"]] if osn == "mac" and shutil.which("brew") else [],
            {"windows": "winget install Databricks.DatabricksCLI",
             "mac": "brew tap databricks/tap && brew install databricks",
             "linux": "curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh"}[osn]),
        Requirement(
            "java", "Java 17", "the Morph converter runs on Java",
            lambda: shutil.which("java"),
            lambda: _winget("EclipseAdoptium.Temurin.17.JDK") if osn == "windows" else
            [["brew", "install", "--cask", "temurin@17"]] if osn == "mac" and shutil.which("brew") else [],
            {"windows": "winget install EclipseAdoptium.Temurin.17.JDK",
             "mac": "brew install --cask temurin@17",
             "linux": "sudo apt install openjdk-17-jre-headless   (or: sudo dnf install java-17-openjdk-headless)"}[osn]),
        Requirement(
            "lakebridge", "LakeBridge", "Databricks' migration toolkit (analyzer and converters)",
            lambda: str(LABS / "lakebridge") if (LABS / "lakebridge").exists() else None,
            lambda: [_cli() + ["labs", "install", "lakebridge"]],
            "databricks labs install lakebridge", after=("cli",)),
    ]
    for name, d in TRANSPILER_DIRS.items():
        cfg = LABS / "remorph-transpilers" / d / "lib" / "config.yml"
        reqs.append(Requirement(
            f"converter-{name}", f"Converter: {'Morph' if name == 'morph' else 'BladeBridge'}",
            "converts the SQL / ETL code",
            lambda cfg=cfg: str(cfg.parent.parent) if cfg.exists() else None,
            lambda: [_cli() + ["labs", "lakebridge", "install-transpile", "--interactive", "false"]],
            "databricks labs lakebridge install-transpile --interactive false", after=("cli", "lakebridge")))
    return reqs


def check() -> list[Status]:
    refresh_path()
    out = []
    for r in requirements():
        where = r.check()
        out.append(Status(r.key, r.name, bool(where), where or "not installed", r.why, r.manual))
    return out


def _run(cmd: list[str], timeout: int = 1800) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    text = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, text[-1500:]


def install_missing(progress: Callable[[str], None] = lambda _m: None) -> list[Status]:
    """Install everything missing, in dependency order. Returns the final status of every requirement."""
    reqs = {r.key: r for r in requirements()}
    logs: dict[str, list[str]] = {}
    done_cmds: set[tuple[str, ...]] = set()  # install-transpile installs both converters in one go
    for r in reqs.values():
        refresh_path()
        if r.check():
            continue
        if any(not reqs[a].check() for a in r.after):
            logs[r.key] = [f"waiting for {', '.join(reqs[a].name for a in r.after if not reqs[a].check())}"]
            continue
        cmds = r.install()
        if not cmds:
            logs[r.key] = [f"cannot be installed automatically here - run: {r.manual}"]
            continue
        progress(f"Installing {r.name}...")
        for cmd in cmds:
            if tuple(cmd) in done_cmds:
                continue
            ok, text = _run(cmd)
            done_cmds.add(tuple(cmd))
            logs.setdefault(r.key, []).append(f"$ {' '.join(cmd)}\n{text}")
            if not ok:
                if any(w in text.lower() for w in ("certificate", "tls", "ssl", "x509", "timed out", "connection")):
                    logs[r.key].append(NETWORK_HINT)
                break
        refresh_path()
    final = check()
    for s in final:
        s.log = logs.get(s.key, [])
    return final
