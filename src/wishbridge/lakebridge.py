"""Thin wrapper around the `databricks labs lakebridge` CLI.

WishBridge calls LakeBridge as an external program (it is installed with
`databricks labs install lakebridge`), so it is used, not redistributed.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
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


def run(cfg: ProjectConfig, *args: str, strict: bool = True) -> str:
    """Run a lakebridge command. strict=False tolerates per-file ERROR lines (the caller checks the output)."""
    # UTF-8 mode: on Windows LakeBridge otherwise writes files in the ANSI code page and crashes on characters such
    # as non-breaking or em spaces, which real client code contains (it then stops converting the remaining files).
    env = {**os.environ, "DATABRICKS_CONFIG_PROFILE": cfg.profile, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
    if args and args[0] in OFFLINE_COMMANDS:
        # Analyze and transpile never call Databricks, but LakeBridge refuses to start without a login profile.
        # Give them a placeholder one: they work on a laptop with no Databricks login, and cannot reach a workspace.
        for key in [k for k in env if k.startswith("DATABRICKS_") and k != "DATABRICKS_CLI_PATH"]:
            env.pop(key)
        env.update(DATABRICKS_CONFIG_FILE=str(offline_profile()), DATABRICKS_CONFIG_PROFILE="DEFAULT")
    cmd = [_databricks_cli(), "labs", "lakebridge", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = _ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    if strict and (proc.returncode != 0 or re.search(r"^(\d\d:\d\d:\d\d\s+)?ERROR\b|^Error:", out, re.MULTILINE)):
        raise _fail(args[0], out)
    return out


_FATAL = re.compile(r"^(?:\d\d:\d\d:\d\d\s+)?ERROR\s+\[[^\]]*\]\s*(.+)$", re.MULTILINE)


def fatal_error(output: str) -> str:
    """The first ERROR line LakeBridge printed (it can stop part-way through the files after one), or ''."""
    m = _FATAL.search(output or "")
    return m.group(1).strip()[:300] if m else ""


def transpiler_config_path(transpiler: str) -> Path:
    path = Path.home() / ".databricks" / "labs" / "remorph-transpilers" / TRANSPILER_DIRS[transpiler] / "lib" / "config.yml"
    if not path.exists():
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
