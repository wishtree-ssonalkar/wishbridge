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

PHASE_FOLDERS = {"assessment": "Assessments", "code-migration": "Migrations", "data-migration": "Migrations",
                 "migration": "Migrations"}


def projects_root() -> Path:
    """Where WishBridge keeps its projects: C:\\WishBridge on Windows, ~/WishBridge elsewhere."""
    return Path("C:/WishBridge") if os.name == "nt" else Path.home() / "WishBridge"


def default_parent(phase: str | None) -> str:
    """Assessments and migrations get their own folder, so they are easy to tell apart later."""
    return str(projects_root() / PHASE_FOLDERS.get(phase or "", PHASE_FOLDERS["assessment"]))


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
    from wishbridge.hosting import APP_PROFILE, app_host, is_app

    if is_app():  # a Databricks App has one workspace - its own - reached with the app's identity
        return [{"name": APP_PROFILE, "host": app_host(), "valid": True}]
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


def check_target(profile: str, catalog: str, warehouse_id: str = "", schema: str = "",
                 create_catalog: bool = False) -> list[str]:
    """What is wrong with the Databricks target, asked from the workspace itself (empty: all fine).
    A new catalog (when the person chose New catalog) and a new schema are created here."""
    from wishbridge.hosting import workspace_client

    try:
        w = workspace_client(profile=profile)
        names = [c.name for c in w.catalogs.list() if c.name]
    except Exception as e:  # the SDK raises many types; the message says what is wrong
        return [f"Could not reach the workspace with this login: {str(e).splitlines()[0][:200]}"]
    problems = []
    if catalog not in names and create_catalog:
        try:
            w.catalogs.create(name=catalog, comment="Created by WishBridge")
            names.append(catalog)
        except Exception as e:  # noqa: BLE001 - permission, storage location, ...
            msg = str(e).splitlines()[0]
            if "default storage" in msg.lower():
                # Databricks Default Storage: a new catalog can be made only in the Databricks UI
                host = (getattr(w.config, "host", "") or "").rstrip("/")
                problems.append(
                    f"This workspace keeps its data in Databricks Default Storage, where a new catalog can only be "
                    f"created in Databricks itself: open {host}/explore/data → **+** → **Create a catalog** → name "
                    f"`{catalog}`, storage **Default Storage** → **Create**. Then click *Load warehouses and "
                    "catalogs* here and pick it from the list. Or keep an existing catalog (e.g. `workspace`) and "
                    "type a new schema name - WishBridge creates the schema for you.")
            else:
                problems.append(f"Could not create catalog '{catalog}': {msg[:300]}. Creating a catalog needs the "
                                "CREATE CATALOG permission (and, in some workspaces, a storage location) - ask the "
                                "workspace admin, or pick an existing catalog.")
            return problems
    if catalog not in names:
        problems.append(f"Catalog '{catalog}' does not exist in this workspace. Choose one of: "
                        + ", ".join(sorted(names)[:15]) + " (*Load warehouses and catalogs* lists them), or "
                        "choose *New catalog* to create it.")
    elif schema:
        try:
            if schema.lower() not in {s.name.lower() for s in w.schemas.list(catalog_name=catalog) if s.name}:
                w.schemas.create(name=schema, catalog_name=catalog, comment="Created by WishBridge")
        except Exception as e:  # noqa: BLE001 - permission on the catalog
            problems.append(f"Could not create schema '{catalog}.{schema}': {str(e).splitlines()[0][:300]}. It needs "
                            "the CREATE SCHEMA permission on the catalog.")
    try:
        if warehouse_id:
            w.warehouses.get(warehouse_id)
        elif not any(True for _ in w.warehouses.list()):
            problems.append("This workspace has no SQL warehouse: create one in Databricks (SQL > SQL Warehouses).")
    except Exception:  # noqa: BLE001
        problems.append(f"SQL warehouse '{warehouse_id}' was not found in this workspace.")
    return problems


def list_workspace(profile: str) -> dict[str, Any]:
    """Who we are, where, and the warehouses and catalogs visible - for the settings screen."""
    from wishbridge.hosting import workspace_client

    w = workspace_client(profile=profile)
    user = w.current_user.me().user_name
    warehouses = [{"id": wh.id, "name": wh.name, "state": str(wh.state.value if wh.state else "")}
                  for wh in w.warehouses.list()]
    catalogs = [{"name": c.name, "type": str(c.catalog_type.value if c.catalog_type else "")}
                for c in w.catalogs.list() if c.name]
    return {"profile": profile, "host": w.config.host.rstrip("/"), "user": user,
            "warehouses": warehouses, "catalogs": catalogs}


def is_local_host(host: str) -> bool:
    """True for a server name that means 'this computer' - unreachable from Databricks in the cloud."""
    import socket

    name = (host or "").strip().lower().split("\\")[0].split(",")[0].split(":")[0]
    if (host or "").strip().startswith(".") or name in ("", ".", "(local)", "localhost", "127.0.0.1", "::1"):
        return bool((host or "").strip())
    try:
        return name == socket.gethostname().lower()
    except OSError:
        return False


def test_local_db(local_db: dict, password: str = "") -> tuple[bool, str]:
    """Connect to the database on this computer (read-only) and count its tables per schema."""
    from types import SimpleNamespace

    from wishbridge.warehouses.synapse import localdb

    try:
        conn = localdb.connect(SimpleNamespace(local_db=local_db), password)
        try:
            rows = conn.cursor().execute(
                "SELECT TABLE_SCHEMA, COUNT(*) FROM INFORMATION_SCHEMA.TABLES WHERE TABLE_TYPE = 'BASE TABLE' "
                "AND TABLE_SCHEMA NOT IN ('sys', 'INFORMATION_SCHEMA') GROUP BY TABLE_SCHEMA ORDER BY TABLE_SCHEMA").fetchall()
        finally:
            conn.close()
    except Exception as e:  # noqa: BLE001 - ODBC raises its own types
        msg = str(e.args[-1] if getattr(e, "args", None) else e)
        if "certificate" in msg.lower():
            msg += " - tick 'Trust the server certificate'."
        elif "login failed" in msg.lower():
            msg = msg.split("(18456")[0].strip() + (" - check the database name (SQL Server says 'login failed' when it "
                                                     "does not exist) and that this login may open it.")
        return False, f"Could not connect to {local_db.get('database')} on {local_db.get('server')}: {msg[:400]}"
    total = sum(n for _, n in rows)
    per = ", ".join(f"{s}: {n}" for s, n in rows) or "no tables"
    return True, (f"Connected to {local_db.get('database')} on {local_db.get('server')}: {total} tables ({per}). "
                  "Only read - nothing is copied until you press Copy the data.")


def check_volume(profile: str, files_root: str) -> tuple[bool, str]:
    """Is the upload folder's volume there (or can WishBridge create it)? Asked from the workspace."""
    from wishbridge.hosting import workspace_client

    parts = [p for p in files_root.split("/") if p]
    if len(parts) < 4 or parts[0] != "Volumes":
        return False, "The folder must be in a Unity Catalog volume: /Volumes/<catalog>/<schema>/<volume>/..."
    catalog, schema, volume = parts[1:4]
    try:
        w = workspace_client(profile=profile)
    except Exception as e:  # noqa: BLE001
        return False, f"Could not reach the workspace with the login '{profile}': {str(e).splitlines()[0][:200]}"
    try:
        w.volumes.read(f"{catalog}.{schema}.{volume}")
        return True, f"The volume `{catalog}.{schema}.{volume}` exists - the tables are uploaded there when you copy."
    except Exception:  # noqa: BLE001 - not there yet
        pass
    try:
        w.schemas.get(f"{catalog}.{schema}")
        where = f"schema `{catalog}.{schema}` exists"
    except Exception:  # noqa: BLE001
        try:
            w.catalogs.get(catalog)
        except Exception:  # noqa: BLE001
            return False, f"Catalog `{catalog}` does not exist in this workspace - pick another upload folder."
        where = f"schema `{catalog}.{schema}` is created too"
    return True, f"The volume `{catalog}.{schema}.{volume}` is created when you copy ({where})."


def check_files_folder(profile: str, files_root: str) -> tuple[bool, str]:
    """What is in the exported-files folder: one sub-folder per table is expected."""
    from wishbridge.hosting import workspace_client

    try:
        w = workspace_client(profile=profile)
        entries = list(w.files.list_directory_contents(files_root.rstrip("/")))
    except Exception as e:  # noqa: BLE001 - not found, no permission, ...
        return False, f"Could not read {files_root}: {str(e).splitlines()[0][:250]}"
    folders = sorted(e.name for e in entries if e.is_directory and e.name)
    files = [e.name for e in entries if not e.is_directory and e.name]
    if not folders:
        return False, (f"{files_root} has no table folders" + (f" (only files: {', '.join(files[:5])})" if files else "")
                       + ". Expected one folder per table, e.g. " + files_root.rstrip("/") + "/DimCustomer/.")
    return True, f"Found {len(folders)} table folder(s) in {files_root}: {', '.join(folders[:12])}" + (
        " ..." if len(folders) > 12 else "") + "."
