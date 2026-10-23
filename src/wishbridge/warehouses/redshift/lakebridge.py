"""Thin wrapper around the `databricks labs lakebridge` CLI.

WishBridge calls LakeBridge as an external program (it is installed with
`databricks labs install lakebridge`), so it is used, not redistributed.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import TRANSPILER_DIRS, ProjectConfig

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


class LakeBridgeError(Exception):
    pass


def _databricks_cli() -> str:
    exe = shutil.which("databricks")
    if not exe:
        raise LakeBridgeError("Databricks CLI not found on PATH. Install it: https://docs.databricks.com/dev-tools/cli/install")
    return exe


def _fail(command: str, out: str) -> LakeBridgeError:
    tail = "\n".join(out.strip().splitlines()[-15:])
    if re.search(r"Cannot open file .* for writing", out):
        # Windows refuses paths over 260 characters; the analyzer adds long file names under the project folder.
        tail = ("The project folder path is too long for Windows. Move the project to a shorter folder "
                "(for example C:\\WishBridge\\projects) and run again.\n" + tail)
    return LakeBridgeError(f"The {command} step failed:\n{tail}")


OFFLINE_COMMANDS = frozenset({"analyze", "transpile"})


def offline_profile() -> Path:
    """A Databricks config file whose only profile points nowhere (used for the offline steps)."""
    path = Path.home() / ".wishbridge" / "offline.databrickscfg"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Used by WishBridge for analyze/convert, which run offline.\n"
                        "[DEFAULT]\nhost = https://wishbridge-offline.invalid\ntoken = offline\n", encoding="utf-8")
    return path


class _OfflineWorkspace:
    """A stand-in Databricks workspace on this computer, for the offline steps.

    Analyze and transpile do their work locally, but the toolkit still asks the workspace who the user is (and the
    Databricks CLI insists on a real, signed-in login). This answers that one question at once, and "not found" to
    anything else, so the steps never need a login or a network and never wait on retries.
    """

    class _Handler(BaseHTTPRequestHandler):
        def _reply(self) -> None:
            if self.path.split("?")[0].endswith("/scim/v2/Me"):
                code, body = 200, {"id": "0", "userName": "wishbridge-offline", "displayName": "WishBridge (offline)"}
            else:
                code, body = 404, {"error_code": "NOT_FOUND", "message": "offline: no Databricks workspace"}
            data = json.dumps(body).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        do_GET = do_POST = do_PUT = do_PATCH = do_DELETE = _reply

        def log_message(self, *args) -> None:  # quiet
            pass

    def __enter__(self) -> str:
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._Handler)
        threading.Thread(target=self._server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self._server.server_address[1]}"

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()


def _toolkit_home() -> Path:
    return Path.home() / ".databricks" / "labs" / "lakebridge"


def _run_offline(command: str, flags: list[str], env: dict[str, str]) -> subprocess.CompletedProcess:
    """Run an offline toolkit command with its own Python, bypassing the Databricks CLI's login (see above)."""
    home = _toolkit_home()
    python = home / "state" / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    entry = home / "lib" / "src" / "databricks" / "labs" / "lakebridge" / "cli.py"
    from_source = entry.is_file()
    if not from_source:  # the Databricks App installs the toolkit as a package (wishbridge/toolkit_setup.py)
        entry = next((home / "state" / "venv").rglob("site-packages/databricks/labs/lakebridge/cli.py"), entry)
    if not python.is_file() or not entry.is_file():
        from wishbridge.hosting import is_app

        if is_app():
            from wishbridge import toolkit_setup

            st = toolkit_setup.status()
            raise LakeBridgeError(
                "The migration toolkit is still being installed in the app (a few minutes after it starts) - run "
                "again shortly." if st.get("state") in ("installing", "missing") else
                f"The migration toolkit could not be installed in the app: {st.get('error') or 'unknown error'}")
        raise LakeBridgeError("The migration toolkit is not installed on this computer: run Step 0 - System check, "
                              "then Install missing.")
    named = {}
    for i in range(0, len(flags), 2):
        named[flags[i].removeprefix("--")] = flags[i + 1] if i + 1 < len(flags) else "true"
    named["log_level"] = "info"
    payload = json.dumps({"command": command, "flags": named})
    env = {k: v for k, v in env.items() if not k.startswith("DATABRICKS_")}
    # The toolkit's own source folder first: its logger looks for pyproject.toml above the running file and loops
    # forever on Windows when it runs from the installed copy instead.
    if from_source:
        env["PYTHONPATH"] = str(home / "lib" / "src")
    with _OfflineWorkspace() as url:
        env.update(DATABRICKS_HOST=url, DATABRICKS_TOKEN="offline", DATABRICKS_AUTH_TYPE="pat")
        return subprocess.run([str(python), str(entry), payload], capture_output=True, text=True, encoding="utf-8",
                              errors="replace", env=env, stdin=subprocess.DEVNULL)


def run(cfg: ProjectConfig, *args: str, strict: bool = True) -> str:
    """Run a lakebridge command. strict=False tolerates per-file ERROR lines (the caller checks the output)."""
    # UTF-8 mode: on Windows LakeBridge otherwise writes files in the ANSI code page and crashes on characters such
    # as non-breaking or em spaces, which real client code contains (it then stops converting the remaining files).
    env = {**os.environ, "DATABRICKS_CONFIG_PROFILE": cfg.profile, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if args and args[0] in OFFLINE_COMMANDS:
        # Analyze and transpile work on this computer only: no Databricks login, no network (see _run_offline).
        proc = _run_offline(args[0], list(args[1:]), env)
    else:
        proc = subprocess.run([_databricks_cli(), "labs", "lakebridge", *args], capture_output=True, text=True,
                              encoding="utf-8", errors="replace", env=env)
    out = _ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    try:  # the toolkit's whole answer, for when a step fails without saying why on screen
        log = cfg.out("logs", f"toolkit_{args[0]}.log") if args else None
        if log is not None:
            log.write_text(f"exit code {proc.returncode}\n{out}", encoding="utf-8")
    except OSError:
        pass
    if strict and (proc.returncode != 0 or re.search(r"^(\d\d:\d\d:\d\d\s+)?ERROR\b|^Error:", out, re.MULTILINE)):
        raise _fail(args[0], out if out.strip() else f"(no output, exit code {proc.returncode})")
    return out


_FATAL = re.compile(r"^(?:\d\d:\d\d:\d\d\s+)?ERROR\s+\[[^\]]*\]\s*(.+)$", re.MULTILINE)


def fatal_error(output: str) -> str:
    """The first ERROR line LakeBridge printed (it can stop part-way through the files after one), or ''."""
    m = _FATAL.search(output or "")
    return m.group(1).strip()[:300] if m else ""


def transpiler_config_path(transpiler: str) -> Path:
    path = Path.home() / ".databricks" / "labs" / "remorph-transpilers" / TRANSPILER_DIRS[transpiler] / "lib" / "config.yml"
    if not path.exists():
        from wishbridge.hosting import LAPTOP_ONLY, is_app

        if is_app():
            raise LakeBridgeError(f"The converter '{transpiler}' is a Java program: {LAPTOP_ONLY.lower()}. Convert "
                                  "this project on the laptop and open its zip here.")
        raise LakeBridgeError(
            f"Transpiler '{transpiler}' is not installed ({path} missing).\n"
            "Run: databricks labs lakebridge install-transpile --interactive false"
        )
    return path


def analyze(cfg: ProjectConfig, report_file: Path) -> str:
    return run(
        cfg,
        "analyze",
        "--source-directory", str(cfg.input_dir),
        "--report-file", str(report_file),
        "--source-tech", cfg.source.analyzer_tech,
    )


def transpile(cfg: ProjectConfig, output_folder: Path, error_file: Path) -> str:
    """Transpile; parse errors in individual statements are not fatal - they are reported per file."""
    args = [
        "transpile",
        "--transpiler-config-path", str(transpiler_config_path(cfg.transpiler)),
        "--source-dialect", cfg.source.dialect,
        "--input-source", str(cfg.input_dir),
        "--output-folder", str(output_folder),
        "--error-file-path", str(error_file),
        "--skip-validation", "true",
    ]
    if cfg.target_technology:
        args += ["--target-technology", cfg.target_technology]
    out = run(cfg, *args, strict=False)
    if not output_folder.exists() or not any(p.is_file() for p in output_folder.rglob("*")):
        if cfg.source.key == "informatica-cloud" and ("INFACLOUD" in out or "registered its transpile capability" in out):
            raise LakeBridgeError(
                "Informatica Cloud cannot be converted yet (two issues in the Databricks Labs converter): it "
                "only generates PySpark for Informatica Cloud but is started without that choice ('No mapping for "
                "source tech INFACLOUD and target tech SQL'), and the transpiler reads the .zip export packages as "
                "text and skips them. Analyze works. Until Databricks fixes this, rebuild the mappings by hand in "
                "overrides/ (WishBridge still deploys, runs and reconciles them) or ask your Databricks contact "
                "about an update of the Databricks Labs migration toolkit.")
        raise _fail("transpile", out)
    return out


def reconcile(cfg: ProjectConfig) -> str:
    return run(cfg, "reconcile")
