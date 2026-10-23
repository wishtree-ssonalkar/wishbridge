"""The migration toolkit inside the Databricks App: analyze and convert run there too.

On a laptop the toolkit is installed by the system check (Databricks CLI, `databricks labs install lakebridge`).
The Databricks App has no CLI, so it installs the same pieces from PyPI, each in its own Python environment (the
toolkit needs older versions of some packages than WishBridge), in the places the toolkit itself uses:

  ~/.databricks/labs/lakebridge/state/venv                       the toolkit and its analyzer (Linux build included)
  ~/.databricks/labs/remorph-transpilers/bladebridge/lib/.venv  the BladeBridge converter (+ lib/config.yml)

It runs in the background when the app starts (a few minutes) and writes its progress to a status file. The
second converter (Morpheus) is a Java program: it stays on the laptop.

    python -m wishbridge.toolkit_setup      # what the app starts
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

LAKEBRIDGE = "databricks-labs-lakebridge==0.15.2"
BLADEBRIDGE = "databricks-bb-plugin==0.3.0"
LABS = Path.home() / ".databricks" / "labs"
TOOLKIT_VENV = LABS / "lakebridge" / "state" / "venv"
BB_LIB = LABS / "remorph-transpilers" / "bladebridge" / "lib"
STATUS = Path.home() / ".wishbridge" / "toolkit_status.json"


def _bin(venv: Path, name: str) -> Path:
    return venv / ("Scripts" if os.name == "nt" else "bin") / (name + (".exe" if os.name == "nt" else ""))


def status() -> dict:
    """{"state": "ready" | "installing" | "failed" | "missing", "step": ..., "error": ...}"""
    try:
        data = json.loads(STATUS.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"state": "missing"}
    if data.get("state") == "installing" and time.time() - data.get("updated", 0) > 1800:
        data["state"], data["error"] = "failed", "the installation stopped (the app restarted while it ran)"
    return data


def ready() -> bool:
    return status().get("state") == "ready" and _bin(TOOLKIT_VENV, "python").is_file()


def _save(**kw) -> None:
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({**kw, "updated": time.time()}), encoding="utf-8")


def ensure_started() -> None:
    """Databricks App: start the installation in the background, once per app start."""
    from .hosting import is_app

    if not is_app() or status().get("state") in ("ready", "installing"):
        return
    _save(state="installing", step="starting")
    subprocess.Popen([sys.executable, "-m", "wishbridge.toolkit_setup"], stdin=subprocess.DEVNULL,
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=True)


def _venv(path: Path, *packages: str) -> None:
    if not _bin(path, "python").is_file():
        if path.exists():
            shutil.rmtree(path)
        subprocess.run([sys.executable, "-m", "venv", str(path)], check=True, capture_output=True, text=True)
    pip = [str(_bin(path, "python")), "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    proc = subprocess.run(pip + list(packages), capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout).strip().splitlines()[-1][:400])


def install() -> None:
    try:
        _save(state="installing", step="the migration toolkit and its analyzer")
        _venv(TOOLKIT_VENV, LAKEBRIDGE)
        _save(state="installing", step="the BladeBridge converter")
        _venv(BB_LIB / ".venv", BLADEBRIDGE)
        shutil.copy(Path(__file__).parent / "toolkit_files" / "bladebridge-config.yml", BB_LIB / "config.yml")
        for analyzer in TOOLKIT_VENV.rglob("bladespector/Analyzer/Linux/analyzer"):
            analyzer.chmod(0o755)  # the analyzer is a program; make sure it may run
        _save(state="ready", step="")
    except Exception as e:  # noqa: BLE001 - every failure is reported in the app check
        _save(state="failed", error=(str(e).strip().splitlines() or [type(e).__name__])[-1][:400])


if __name__ == "__main__":
    install()
