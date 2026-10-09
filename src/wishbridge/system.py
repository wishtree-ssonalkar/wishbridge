"""Check this computer, install what the work needs, and keep a record - without ever blocking the work.

What is needed depends on what you are doing:
  always            Databricks CLI (runs LakeBridge) and LakeBridge (Databricks Labs' migration toolkit)
  for the project   the converter(s) its source system uses (Morph and/or BladeBridge) and, for Morph, Java 11+
A Databricks login is only needed in the migration phase; it is checked there, not here.

Installs use winget (Windows), Homebrew (macOS) or the Databricks CLI itself, and run in the background
(`wishbridge setup --status-file ...`): the app keeps working, and anything that does not need the missing
piece still runs. Every check and install attempt - versions found, compatibility, command output - is saved to
~/.wishbridge/system_check.json (and into a project's logs when one is open), so a failed install can be fixed
later from the record instead of being lost.
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

from .config import TRANSPILER_DIRS

LABS = Path.home() / ".databricks" / "labs"
REPORT = Path.home() / ".wishbridge" / "system_check.json"
NETWORK_HINT = ("If you are on an office network that inspects HTTPS (e.g. Sophos), downloads can be blocked: "
                "try another network (e.g. a mobile hotspot) and press Install again.")
CORE = ("cli", "lakebridge")


@dataclass
class Requirement:
    key: str
    name: str
    why: str
    check: Callable[[], str | None]          # where it was found, or None
    install: Callable[[], list[list[str]]]   # commands to run, in order ([] = cannot install automatically)
    manual: str                              # what to do by hand
    after: tuple[str, ...] = ()              # keys that must be present first
    version: Callable[[], str] = lambda: ""
    minimum: str = ""                        # human-readable minimum, checked by `compatible`
    compatible: Callable[[str], bool] = lambda v: True


@dataclass
class Status:
    key: str
    name: str
    ok: bool
    detail: str
    why: str
    manual: str
    version: str = ""
    compatible: bool = True
    minimum: str = ""
    needed: bool = True
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
        extra.append(str(Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "WinGet" / "Links"))
        base = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
        extra += [str(p) for p in sorted(base.glob("Eclipse Adoptium/*/bin")) + sorted(base.glob("Java/*/bin"))]
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


def _out(cmd: list[str]) -> str:
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=30, stdin=subprocess.DEVNULL)
        return ((p.stdout or "") + (p.stderr or "")).strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""


def _state_version(folder: Path) -> str:
    try:
        return json.loads((folder / "state" / "version.json").read_text(encoding="utf-8")).get("version", "")
    except (OSError, ValueError):
        return ""


def _java_version() -> str:
    m = re.search(r'version "?(\d+)(?:\.(\d+))?', _out(["java", "-version"]))
    if not m:
        return ""
    major = int(m.group(1))
    return str(int(m.group(2)) if major == 1 and m.group(2) else major)  # "1.8" -> 8


def _major(v: str) -> int:
    m = re.search(r"\d+", v or "")
    return int(m.group(0)) if m else 0


def requirements() -> list[Requirement]:
    osn = _os()
    reqs = [
        Requirement(
            "cli", "Databricks CLI", "runs the migration toolkit",
            lambda: shutil.which("databricks"),
            lambda: _winget("Databricks.DatabricksCLI") if osn == "windows" else
            [["brew", "tap", "databricks/tap"], ["brew", "install", "databricks"]] if osn == "mac" and shutil.which("brew") else [],
            {"windows": "winget install Databricks.DatabricksCLI",
             "mac": "brew tap databricks/tap && brew install databricks",
             "linux": "curl -fsSL https://raw.githubusercontent.com/databricks/setup-cli/main/install.sh | sudo sh"}[osn],
            version=lambda: (re.search(r"v?\d+\.\d+\.\d+", _out(_cli() + ["--version"])) or [""])[0],
            minimum="0.205 (the new Go CLI)", compatible=lambda v: not v or tuple(map(int, re.findall(r"\d+", v)[:2])) >= (0, 205)),
        Requirement(
            "java", "Java", "the Morph converter runs on Java",
            lambda: shutil.which("java"),
            lambda: _winget("EclipseAdoptium.Temurin.17.JDK") if osn == "windows" else
            [["brew", "install", "--cask", "temurin@17"]] if osn == "mac" and shutil.which("brew") else [],
            {"windows": "winget install EclipseAdoptium.Temurin.17.JDK",
             "mac": "brew install --cask temurin@17",
             "linux": "sudo apt install openjdk-17-jre-headless   (or: sudo dnf install java-17-openjdk-headless)"}[osn],
            version=_java_version, minimum="11", compatible=lambda v: not v or _major(v) >= 11),
        Requirement(
            "lakebridge", "Migration toolkit", "Databricks Labs analyzer and converters",
            lambda: str(LABS / "lakebridge") if (LABS / "lakebridge").exists() else None,
            lambda: [_cli() + ["labs", "install", "lakebridge"]],
            "databricks labs install lakebridge", after=("cli",),
            version=lambda: _state_version(LABS / "lakebridge")),
    ]
    for name, d in TRANSPILER_DIRS.items():
        folder = LABS / "remorph-transpilers" / d
        reqs.append(Requirement(
            f"converter-{name}", f"Converter: {'Morph' if name == 'morph' else 'BladeBridge'}",
            "converts the SQL / ETL code",
            lambda folder=folder: str(folder) if (folder / "lib" / "config.yml").exists() else None,
            lambda: [_cli() + ["labs", "lakebridge", "install-transpile", "--interactive", "false"]],
            "databricks labs lakebridge install-transpile --interactive false", after=("cli", "lakebridge"),
            version=lambda folder=folder: _state_version(folder)))
    return reqs


def needed_for(cfg=None) -> set[str]:
    """Requirements this work needs: the core always; a project adds its converter(s) and, for Morph, Java."""
    keys = set(CORE)
    if cfg is not None:
        from .config import fallback_converter

        converters = {cfg.transpiler} | ({fallback_converter(cfg)} - {None})
        keys |= {f"converter-{c}" for c in converters}
        if "morph" in converters:
            keys.add("java")
    return keys


def check(needed: set[str] | None = None) -> list[Status]:
    refresh_path()
    out = []
    for r in requirements():
        where = r.check()
        v = r.version() if where else ""
        out.append(Status(r.key, r.name, bool(where), where or "not installed", r.why, r.manual, version=v,
                          compatible=r.compatible(v) if where else True, minimum=r.minimum,
                          needed=needed is None or r.key in needed))
    return out


def ready(statuses: list[Status]) -> bool:
    return all(s.ok and s.compatible for s in statuses if s.needed)


def _run(cmd: list[str], timeout: int = 1800) -> tuple[bool, str]:
    env = {**os.environ, "PYTHONUTF8": "1"}
    if Path(cmd[0]).stem.lower() == "databricks":
        # Installing LakeBridge and its converters needs no workspace, but the CLI insists on choosing a login
        # profile (and stops when there are several). Give it WishBridge's placeholder profile instead.
        from .lakebridge import offline_profile

        for key in [k for k in env if k.startswith("DATABRICKS_") and k != "DATABRICKS_CLI_PATH"]:
            env.pop(key)
        env.update(DATABRICKS_CONFIG_FILE=str(offline_profile()), DATABRICKS_CONFIG_PROFILE="DEFAULT")
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=timeout, env=env)
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, str(e)
    text = ((p.stdout or "") + (p.stderr or "")).strip()
    return p.returncode == 0, text[-1500:]


def install_missing(progress: Callable[[str], None] = lambda _m: None, needed: set[str] | None = None) -> list[Status]:
    """Install what is missing (only what is needed), in dependency order. Returns every requirement's status."""
    reqs = {r.key: r for r in requirements()}
    logs: dict[str, list[str]] = {}
    done_cmds: set[tuple[str, ...]] = set()  # install-transpile installs both converters in one go
    for r in reqs.values():
        refresh_path()
        if (needed is not None and r.key not in needed) or r.check():
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
    final = check(needed)
    for s in final:
        s.log = logs.get(s.key, [])
    return final


def report(statuses: list[Status], state: str = "done", current: str = "") -> dict:
    """What was checked and installed on this computer - kept for later reference."""
    return {
        "checked_at": datetime.now().isoformat(timespec="seconds"),
        "state": state,                     # running | done
        "current": current,
        "ready": ready(statuses),
        "computer": {"os": platform.platform(), "machine": platform.machine(), "python": sys.version.split()[0],
                     "user": os.environ.get("USERNAME") or os.environ.get("USER", "")},
        "requirements": [asdict(s) for s in statuses],
    }


def save_report(data: dict, *paths: Path) -> None:
    for p in (REPORT, *paths):
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass


def load_report(path: Path = REPORT) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def start_background_install(needed: set[str], status_file: Path, also: list[Path] = ()) -> subprocess.Popen:
    """Run `wishbridge setup` in the background; it writes progress and the result to status_file."""
    cmd = [sys.executable, "-m", "wishbridge", "setup", "--need", ",".join(sorted(needed)),
           "--status-file", str(status_file)] + [x for p in also for x in ("--also-save", str(p))]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                            creationflags=flags)
