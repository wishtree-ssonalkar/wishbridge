"""Logic behind the UI that does not need Streamlit, so it can be tested on its own."""

from __future__ import annotations

import configparser
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from ..config import SOURCES, TRANSPILER_DIRS, load_config, render_template

INPUT_EXTENSIONS = ["sql", "txt", "ddl", "xml", "dsx", "isx", "dtsx", "bteq", "btq", "pls", "pkb", "pks", "prc"]


@dataclass
class Check:
    name: str
    ok: bool
    detail: str
    fix: str = ""


# ------------------------------------------------------------------ projects

def project_file(folder: str | Path) -> Path:
    return Path(folder).expanduser().resolve() / "project.yml"


def create_project(parent: str | Path, name: str, source: str) -> Path:
    if source not in SOURCES:
        raise ValueError(f"Unknown source '{source}'")
    name = name.strip()
    if not name or any(c in name for c in '\\/:*?"<>|'):
        raise ValueError("Project name must be a plain folder name")
    root = Path(parent).expanduser().resolve() / name
    if (root / "project.yml").exists():
        raise ValueError(f"{root} already has a project.yml")
    (root / "input").mkdir(parents=True, exist_ok=True)
    (root / "project.yml").write_text(render_template(name, source), encoding="utf-8")
    return root


def read_raw(folder: str | Path) -> dict[str, Any]:
    return yaml.safe_load(project_file(folder).read_text(encoding="utf-8-sig")) or {}


def save_raw(folder: str | Path, raw: dict[str, Any]) -> None:
    """Write project.yml from a settings dict, then load it once so a bad value is reported immediately."""
    path = project_file(folder)
    text = "# WishBridge project file (saved from the WishBridge UI). Paths are relative to this file.\n"
    text += yaml.safe_dump(raw, sort_keys=False, allow_unicode=True)
    backup = path.read_text(encoding="utf-8-sig") if path.exists() else None
    path.write_text(text, encoding="utf-8")
    try:
        load_config(path)
    except Exception:
        if backup is not None:
            path.write_text(backup, encoding="utf-8")
        raise


def schema_map_rows(raw: dict[str, Any]) -> list[dict[str, str]]:
    return [{"source_schema": k, "target": v} for k, v in (raw.get("schema_map") or {}).items()]


def rows_to_schema_map(rows: list[dict[str, Any]]) -> dict[str, str]:
    out = {}
    for r in rows:
        k, v = str(r.get("source_schema") or "").strip(), str(r.get("target") or "").strip()
        if k and v:
            out[k] = v
    return out


def table_rows(raw: dict[str, Any]) -> list[dict[str, str]]:
    rows = []
    for t in (raw.get("data") or {}).get("tables") or []:
        if isinstance(t, str):
            rows.append({"source": t, "target": ""})
        else:
            rows.append({"source": t.get("source", ""), "target": t.get("target", "") or ""})
    return rows


def rows_to_tables(rows: list[dict[str, Any]], previous: list[Any] | None = None) -> list[Any]:
    """data.tables from editor rows; settings saved earlier for the same source (column mapping, load) are kept."""
    extra = {t["source"]: {k: v for k, v in t.items() if k not in ("source", "target")}
             for t in previous or [] if isinstance(t, dict) and t.get("source")}
    tables: list[Any] = []
    for r in rows:
        src, tgt = str(r.get("source") or "").strip(), str(r.get("target") or "").strip()
        if src:
            entry = {"source": src, **({"target": tgt} if tgt else {}), **extra.get(src, {})}
            tables.append(entry if len(entry) > 1 else src)
    return tables


def input_files(folder: str | Path, input_dir: str = "input") -> list[Path]:
    d = Path(folder) / input_dir
    return sorted(p for p in d.rglob("*") if p.is_file()) if d.exists() else []


def save_upload(folder: str | Path, filename: str, content: bytes, input_dir: str = "input") -> Path:
    name = Path(filename).name  # never trust a path from the browser
    if not name:
        raise ValueError("Empty file name")
    dest = Path(folder) / input_dir / name
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(content)
    return dest


def override_path(folder: str | Path, rel: str, overrides_dir: str = "overrides") -> Path:
    p = (Path(folder) / overrides_dir / rel).resolve()
    if Path(folder).resolve() not in p.parents:
        raise ValueError("Override path escapes the project folder")
    return p


# --------------------------------------------------------------- environment

def check_environment(profile: str = "DEFAULT") -> list[Check]:
    checks: list[Check] = []
    cli = shutil.which("databricks")
    checks.append(Check("Databricks CLI", bool(cli), cli or "not found",
                        "winget install Databricks.DatabricksCLI"))
    java = shutil.which("java")
    checks.append(Check("Java", bool(java), java or "not found", "Install Java 11+ (adoptium.net)"))
    lb = Path.home() / ".databricks" / "labs" / "lakebridge"
    checks.append(Check("LakeBridge", lb.exists(), str(lb) if lb.exists() else "not installed",
                        "databricks labs install lakebridge"))
    for name, d in TRANSPILER_DIRS.items():
        cfg = Path.home() / ".databricks" / "labs" / "remorph-transpilers" / d / "lib" / "config.yml"
        checks.append(Check(f"Converter: {name}", cfg.exists(), "installed" if cfg.exists() else "not installed",
                            "databricks labs lakebridge install-transpile --interactive false"))
    checks.append(_login_check(profile, cli))
    return checks


def _login_check(profile: str, cli: str | None) -> Check:
    fix = f"databricks auth login --profile {profile}"
    if not cli:
        return Check(f"Databricks login ({profile})", False, "Databricks CLI missing", fix)
    try:
        proc = subprocess.run([cli, "auth", "profiles"], capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as e:
        return Check(f"Databricks login ({profile})", False, str(e), fix)
    for line in proc.stdout.splitlines():
        parts = line.split()
        if parts and parts[0] == profile:
            ok = parts[-1].upper() == "YES"
            return Check(f"Databricks login ({profile})", ok, parts[1] if len(parts) > 2 else line, fix)
    return Check(f"Databricks login ({profile})", False, "profile not found", fix)


# ---------------------------------------------------------------- workspace

def databrickscfg_path() -> Path:
    return Path(os.environ.get("DATABRICKS_CONFIG_FILE") or Path.home() / ".databrickscfg")


def _validity() -> dict[str, bool]:
    """Profile name -> login still valid, from `databricks auth profiles`."""
    cli = shutil.which("databricks")
    if not cli:
        return {}
    try:
        out = subprocess.run([cli, "auth", "profiles"], capture_output=True, text=True, timeout=60).stdout
    except (OSError, subprocess.TimeoutExpired):
        return {}
    result = {}
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            result[parts[0]] = parts[-1].upper() == "YES"
    return result


def list_profiles(check_validity: bool = True) -> list[dict[str, Any]]:
    """Saved Databricks logins on this computer: name, workspace URL and whether the login still works."""
    cp = configparser.ConfigParser(default_section="__wishbridge_none__", interpolation=None)
    path = databrickscfg_path()
    if path.exists():
        cp.read(path, encoding="utf-8")
    valid = _validity() if check_validity else {}
    profiles = []
    for name in cp.sections():
        host = cp.get(name, "host", fallback="")
        if name.startswith("__") or not host:
            continue
        profiles.append({"name": name, "host": host.rstrip("/"), "valid": valid.get(name)})
    return profiles


def sign_in(host: str, profile: str) -> tuple[bool, str]:
    """Run `databricks auth login` (opens a browser on this computer). Returns (ok, message)."""
    host = host.strip().rstrip("/")
    profile = profile.strip()
    if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?(/.*)?", host):
        return False, "Enter the workspace URL, e.g. https://dbc-1234.cloud.databricks.com"
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", profile):
        return False, "Profile name: letters, digits, '-', '_' or '.'"
    cli = shutil.which("databricks")
    if not cli:
        return False, "Databricks CLI not found"
    try:
        proc = subprocess.run([cli, "auth", "login", "--host", host, "--profile", profile],
                              capture_output=True, text=True, timeout=300)
    except subprocess.TimeoutExpired:
        return False, "Sign-in timed out - finish it in the browser within 5 minutes"
    if proc.returncode != 0:
        return False, (proc.stderr or proc.stdout).strip().splitlines()[-1] if (proc.stderr or proc.stdout) else "Sign-in failed"
    return True, f"Signed in to {host} as profile '{profile}'"


def list_workspace(profile: str) -> dict[str, Any]:
    """Who we are, where, and the warehouses and catalogs visible - for the settings screen."""
    from databricks.sdk import WorkspaceClient

    w = WorkspaceClient(profile=profile)
    user = w.current_user.me().user_name
    warehouses = [{"id": wh.id, "name": wh.name, "state": str(wh.state.value if wh.state else "")}
                  for wh in w.warehouses.list()]
    catalogs = [{"name": c.name, "type": str(c.catalog_type.value if c.catalog_type else "")}
                for c in w.catalogs.list() if c.name]
    return {"profile": profile, "host": w.config.host.rstrip("/"), "user": user,
            "warehouses": warehouses, "catalogs": catalogs}
