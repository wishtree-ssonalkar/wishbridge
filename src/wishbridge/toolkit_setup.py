"""The migration toolkit inside the Databricks App: analyze and convert run there too.

On a laptop the toolkit is installed by the system check (Databricks CLI, `databricks labs install lakebridge`).
The Databricks App has no CLI, so it installs the same pieces from PyPI, each in its own Python environment (the
toolkit needs older versions of some packages than WishBridge), in the places the toolkit itself uses:

  ~/.databricks/labs/lakebridge/state/venv                       the toolkit and its analyzer (Linux build included)
  ~/.databricks/labs/remorph-transpilers/bladebridge/lib/.venv  the BladeBridge converter (+ lib/config.yml)
  ~/.databricks/labs/remorph-transpilers/databricks-morph-plugin/lib  the Morpheus converter (a Java program, from
                                                                  Maven Central, the version the laptop uses)
  ~/.wishbridge/java-venv                                        Java 21 for Morpheus (the jdk4py package, from PyPI;
                                                                  Eclipse Temurin from Adoptium when PyPI fails)

It runs in the background when the app starts (a few minutes) and writes its progress to a status file. With all
of it in place the app converts exactly like the laptop (same converters, same order).

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
MORPH_LIB = LABS / "remorph-transpilers" / "databricks-morph-plugin" / "lib"
MORPH_VERSION = "0.10.0"  # the version the laptop's install-transpile put in place
MORPH_URL = (f"https://repo.maven.apache.org/maven2/com/databricks/labs/databricks-morph-plugin/{MORPH_VERSION}/"
             f"databricks-morph-plugin-{MORPH_VERSION}.jar")
JAVA_HOME = Path.home() / ".wishbridge" / "java"
JAVA_VENV = Path.home() / ".wishbridge" / "java-venv"
JDK4PY = "jdk4py==21.0.8.2"  # Java 21 (Morpheus needs 21 or newer), as a Python package: installs like the toolkit
JAVA_URL = "https://api.adoptium.net/v3/binary/latest/21/ga/linux/{arch}/jre/hotspot/normal/eclipse"
STATUS = Path.home() / ".wishbridge" / "toolkit_status.json"
SETUP_VERSION = 3  # raise it when the setup changes: an app that installed an older one sets up again


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
    st = status()
    return st.get("state") == "ready" and st.get("version") == SETUP_VERSION and all(parts().values())


def java_bin() -> Path | None:
    """The app's own Java (for Morpheus), when it is installed."""
    for root, pattern in ((JAVA_VENV, "**/jdk4py/java-runtime/bin/java"), (JAVA_HOME, "*/bin/java")):
        found = next(root.glob(pattern), None) if root.is_dir() else None
        if found and found.is_file():
            return found
    return None


def morph_ready() -> bool:
    return java_bin() is not None and (MORPH_LIB / "databricks-morph-plugin.jar").is_file() and \
        (MORPH_LIB / "config.yml").is_file()


def parts() -> dict[str, bool]:
    """What is in place, part by part (shown in the app check)."""
    return {"toolkit and analyzer": _bin(TOOLKIT_VENV, "python").is_file(),
            "BladeBridge converter": (BB_LIB / "config.yml").is_file(),
            "Java 21": java_bin() is not None,
            "Morpheus converter": (MORPH_LIB / "databricks-morph-plugin.jar").is_file() and (MORPH_LIB / "config.yml").is_file()}


LOG = STATUS.with_name("toolkit_setup.log")


def _log(text: str) -> None:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG, "a", encoding="utf-8") as f:
        f.write(time.strftime("%H:%M:%S ") + text + "\n")


def log_tail(lines: int = 8) -> str:
    try:
        return "\n".join(LOG.read_text(encoding="utf-8").splitlines()[-lines:])
    except OSError:
        return ""


def _download(url: str, dest: Path) -> None:
    import urllib.request

    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "WishBridge/1.0 (+https://github.com/wishtree-ssonalkar/wishbridge)"})
    with urllib.request.urlopen(req, timeout=600) as r, open(tmp, "wb") as f:
        shutil.copyfileobj(r, f)
    tmp.replace(dest)


def _install_java() -> None:
    import platform
    import tarfile

    if java_bin():
        return
    try:  # Java as a Python package, from PyPI - which the app reaches for everything else too
        _venv(JAVA_VENV, JDK4PY)
        if java_bin():
            for j in JAVA_VENV.rglob("jdk4py/java-runtime/bin/*"):
                j.chmod(0o755)
            return
    except Exception as e:  # noqa: BLE001 - try Adoptium next
        _log(f"Java from PyPI failed ({e}); trying Adoptium")
    arch = {"x86_64": "x64", "amd64": "x64", "aarch64": "aarch64", "arm64": "aarch64"}.get(platform.machine().lower(), "x64")
    archive = JAVA_HOME.parent / "java-21.tar.gz"
    _download(JAVA_URL.format(arch=arch), archive)
    if JAVA_HOME.exists():
        shutil.rmtree(JAVA_HOME)
    JAVA_HOME.mkdir(parents=True)
    with tarfile.open(archive) as t:
        for m in t.getmembers():  # never write outside the Java folder
            if not (JAVA_HOME / m.name).resolve().is_relative_to(JAVA_HOME.resolve()):
                raise RuntimeError(f"Unsafe path in the Java download: {m.name}")
        t.extractall(JAVA_HOME)
    archive.unlink(missing_ok=True)
    if not java_bin():
        raise RuntimeError("Java was downloaded but java was not found in it")


def _save(**kw) -> None:
    _log(f"{kw.get('state')}: {kw.get('step') or kw.get('error') or ''}")
    STATUS.parent.mkdir(parents=True, exist_ok=True)
    STATUS.write_text(json.dumps({**kw, "updated": time.time()}), encoding="utf-8")


def ensure_started() -> None:
    """Databricks App: start the installation in the background, once per app start."""
    from .hosting import is_app

    if not is_app() or ready() or status().get("state") == "installing":
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
        _save(state="installing", step="Java 21 for the Morpheus converter")
        _install_java()
        _save(state="installing", step="the Morpheus converter")
        jar = MORPH_LIB / "databricks-morph-plugin.jar"
        if not jar.is_file():
            _download(MORPH_URL, jar)
        shutil.copy(Path(__file__).parent / "toolkit_files" / "morph-config.yml", MORPH_LIB / "config.yml")
        for analyzer in TOOLKIT_VENV.rglob("bladespector/Analyzer/Linux/analyzer"):
            analyzer.chmod(0o755)  # the analyzer is a program; make sure it may run
        # The toolkit names its loggers from the project folder: it looks for a pyproject.toml above its code,
        # which an install made by the Databricks CLI has and a package install does not.
        for venv in (TOOLKIT_VENV, BB_LIB / ".venv"):
            for site in venv.rglob("site-packages"):
                if site.is_dir() and not (site / "pyproject.toml").exists():
                    (site / "pyproject.toml").write_text('[project]\nname = "wishbridge-toolkit"\n', encoding="utf-8")
        _save(state="ready", step="", version=SETUP_VERSION)
    except Exception as e:  # noqa: BLE001 - every failure is reported in the app check
        _save(state="failed", error=(str(e).strip().splitlines() or [type(e).__name__])[-1][:400])


if __name__ == "__main__":
    install()
