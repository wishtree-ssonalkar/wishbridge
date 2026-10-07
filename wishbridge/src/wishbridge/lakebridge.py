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


def run(cfg: ProjectConfig, *args: str) -> str:
    env = {**os.environ, "DATABRICKS_CONFIG_PROFILE": cfg.profile}
    cmd = [_databricks_cli(), "labs", "lakebridge", *args]
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    out = _ANSI.sub("", (proc.stdout or "") + (proc.stderr or ""))
    if proc.returncode != 0 or re.search(r"^(\d\d:\d\d:\d\d\s+)?ERROR\b|^Error:", out, re.MULTILINE):
        tail = "\n".join(out.strip().splitlines()[-15:])
        raise LakeBridgeError(f"`lakebridge {args[0]}` failed:\n{tail}")
    return out


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
    return run(
        cfg,
        "transpile",
        "--transpiler-config-path", str(transpiler_config_path(cfg.transpiler)),
        "--source-dialect", cfg.source.dialect,
        "--input-source", str(cfg.input_dir),
        "--output-folder", str(output_folder),
        "--error-file-path", str(error_file),
        "--skip-validation", "true",
    )


def reconcile(cfg: ProjectConfig) -> str:
    return run(cfg, "reconcile")
