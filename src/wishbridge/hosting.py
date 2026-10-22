"""Where WishBridge runs: on a laptop (option 1) or as a Databricks App in a workspace (option 3).

On a laptop nothing here changes anything: Databricks is reached with a saved login (profile) and projects are
folders on disk. As a Databricks App:

* Databricks is reached with the app's own identity (its service principal), whatever profile a project names;
* the app's disk is wiped when the app restarts, so every project is also kept as one zip on a Unity Catalog
  volume (WISHBRIDGE_PROJECTS_VOLUME) - saved after every change, and opened from there;
* the assessment stays on the laptop (it reads the client's code and database offline); the app opens its zip.
"""

from __future__ import annotations

import io
import os
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

APP_PROFILE = "databricks-app"  # shown instead of a profile name when the app's identity is used
PROJECTS_VOLUME_ENV = "WISHBRIDGE_PROJECTS_VOLUME"
_SKIP = {".wishbridge-incoming", "__pycache__"}


def is_app() -> bool:
    """True when running as a Databricks App (the platform sets DATABRICKS_APP_NAME), or when forced for tests."""
    return bool(os.environ.get("DATABRICKS_APP_NAME") or os.environ.get("WISHBRIDGE_HOSTED"))


def workspace_client(profile: str | None = None, **kwargs: Any):
    """The Databricks client: the saved login `profile` on a laptop, the app's own identity in a Databricks App."""
    from databricks.sdk import WorkspaceClient

    if is_app():
        return WorkspaceClient(**kwargs)
    return WorkspaceClient(profile=profile, **kwargs)


def app_host() -> str:
    host = os.environ.get("DATABRICKS_HOST", "").strip().rstrip("/")
    return host if not host or host.startswith("http") else f"https://{host}"


def app_warehouse_id() -> str:
    """The SQL warehouse given to the app as a resource (app.yaml maps it to this variable)."""
    return os.environ.get("WISHBRIDGE_WAREHOUSE_ID", "").strip()


def local_root() -> Path:
    """Working copies of the projects inside the app (rebuilt from the volume after a restart)."""
    root = Path(os.environ.get("WISHBRIDGE_HOME") or Path(tempfile.gettempdir()) / "wishbridge") / "projects"
    root.mkdir(parents=True, exist_ok=True)
    return root


def projects_volume() -> str:
    """/Volumes/<catalog>/<schema>/<volume>[/folder] where the app keeps the projects."""
    return os.environ.get(PROJECTS_VOLUME_ENV, "").strip().rstrip("/")


# ----------------------------------------------------------------- projects on the volume

def _remote(name: str) -> str:
    return f"{projects_volume()}/{name}.zip"


def _check_name(name: str) -> str:
    name = name.strip()
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", name) or name.startswith("."):
        raise ValueError(f"'{name}' is not a usable project name (letters, digits, _ . - only)")
    return name


def fingerprint(folder: Path) -> tuple[int, int, int]:
    """Changes when any file in the project is added, removed or changed (count, total size, newest change)."""
    count = size = newest = 0
    for p in _files(folder):
        s = p.stat()
        count, size, newest = count + 1, size + s.st_size, max(newest, s.st_mtime_ns)
    return count, size, newest


def _files(folder: Path):
    for p in sorted(folder.rglob("*")):
        if p.is_file() and not _SKIP.intersection(p.relative_to(folder).parts):
            yield p


def snapshot(folder: str | Path) -> bytes:
    """The whole project as one zip (code, fixes, settings, results; never a password - none is stored)."""
    folder = Path(folder)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in _files(folder):
            z.write(p, p.relative_to(folder).as_posix())
    return buf.getvalue()


def restore(data: bytes, folder: str | Path) -> Path:
    """Unpack a snapshot into `folder` (emptied first). Paths that would leave the folder are refused."""
    import shutil

    folder = Path(folder).resolve()
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        for n in names:
            if not (folder / n).resolve().is_relative_to(folder):
                raise ValueError(f"Unsafe path in the saved project: {n}")
        if folder.exists():
            shutil.rmtree(folder)
        folder.mkdir(parents=True)
        z.extractall(folder)
    return folder


@dataclass
class SavedProject:
    name: str
    size: int
    modified: str


def saved_projects(w=None) -> list[SavedProject]:
    """Projects kept on the volume, newest first."""
    from datetime import datetime, timezone

    w = w or workspace_client()
    out = []
    try:
        entries = list(w.files.list_directory_contents(projects_volume()))
    except Exception as e:  # noqa: BLE001 - not found / no permission: say which volume
        if "not" in str(e).lower() and "found" in str(e).lower():
            return []
        raise
    for e in entries:
        if e.is_directory or not (e.name or "").endswith(".zip"):
            continue
        when = datetime.fromtimestamp((e.last_modified or 0) / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        out.append(SavedProject(e.name[:-4], e.file_size or 0, when))
    return sorted(out, key=lambda p: p.modified, reverse=True)


def free_name(base: str, w=None) -> str:
    """A project name not used yet in the app (on the volume or in the working folder): base, base-2, base-3..."""
    taken = {p.name for p in saved_projects(w)} | {p.name for p in local_root().iterdir()}
    name, n = base, 1
    while name in taken:
        n += 1
        name = f"{base}-{n}"
    return name


def pull(name: str, w=None) -> Path:
    """Open a saved project: download its zip from the volume into the app's working folder."""
    name = _check_name(name)
    w = w or workspace_client()
    data = w.files.download(_remote(name)).contents.read()
    folder = restore(data, local_root() / name)
    _SAVED[str(folder)] = fingerprint(folder)
    return folder


_SAVED: dict[str, tuple[int, int, int]] = {}


def push(folder: str | Path, w=None, force: bool = False) -> bool:
    """Save the project to the volume if anything changed since the last save. True when it was saved."""
    folder = Path(folder).resolve()
    if not (folder / "project.yml").is_file():
        return False
    fp = fingerprint(folder)
    if not force and _SAVED.get(str(folder)) == fp:
        return False
    w = w or workspace_client()
    remote = _remote(_check_name(folder.name))
    w.files.create_directory(projects_volume())
    w.files.upload(remote, io.BytesIO(snapshot(folder)), overwrite=True)
    _SAVED[str(folder)] = fp
    return True


# ----------------------------------------------------------------- the app's own check (instead of the laptop one)

def check_app(w=None) -> list[dict[str, Any]]:
    """What the app needs in the workspace: its identity, a SQL warehouse, the projects volume."""
    rows: list[dict[str, Any]] = []
    try:
        w = w or workspace_client()
        me = w.current_user.me()
        who = me.display_name or me.user_name or "the app"
        rows.append({"name": "Databricks workspace", "ok": True, "detail": f"{app_host() or w.config.host} as {who}"})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": "Databricks workspace", "ok": False, "detail": _first(e),
                     "fix": "The app's service principal could not sign in. Redeploy the app or check its settings."})
        return rows
    wid = app_warehouse_id()
    try:
        whs = {x.id: x for x in w.warehouses.list()}
        if wid and wid in whs:
            rows.append({"name": "SQL warehouse", "ok": True, "detail": f"{whs[wid].name} (given to the app)"})
        elif whs:
            first = next(iter(whs.values()))
            rows.append({"name": "SQL warehouse", "ok": True,
                         "detail": f"{first.name} (no warehouse given to the app: the first one it can use)"})
        else:
            rows.append({"name": "SQL warehouse", "ok": False, "detail": "the app cannot see any SQL warehouse",
                         "fix": "Add a SQL warehouse to the app as a resource (CAN USE), then restart it."})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": "SQL warehouse", "ok": False, "detail": _first(e),
                     "fix": "Add a SQL warehouse to the app as a resource (CAN USE)."})
    vol = projects_volume()
    if not vol.startswith("/Volumes/"):
        rows.append({"name": "Project storage", "ok": False, "detail": f"{PROJECTS_VOLUME_ENV} is not set",
                     "fix": "Set it in app.yaml to a Unity Catalog volume folder, e.g. /Volumes/main/wishbridge/projects"})
        return rows
    try:
        w.files.create_directory(vol)
        probe = f"{vol}/.wishbridge-write-test"
        w.files.upload(probe, io.BytesIO(b"ok"), overwrite=True)
        w.files.delete(probe)
        rows.append({"name": "Project storage", "ok": True, "detail": vol})
    except Exception as e:  # noqa: BLE001
        rows.append({"name": "Project storage", "ok": False, "detail": f"{vol}: {_first(e)}",
                     "fix": "Give the app's service principal READ VOLUME and WRITE VOLUME on this volume "
                            "(and USE CATALOG / USE SCHEMA on its catalog and schema)."})
    return rows


def _first(e: Exception) -> str:
    return (str(e).strip().splitlines() or [type(e).__name__])[0][:300]
