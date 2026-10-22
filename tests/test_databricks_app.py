"""WishBridge as a Databricks App (option 3), simulated on this computer: no workspace is contacted."""

import io
import shutil
import types
from pathlib import Path

import pytest

from wishbridge import hosting

DEMO = Path(__file__).parents[1] / "examples" / "mssql-demo"
APP = str(Path(__file__).parents[1] / "src" / "wishbridge" / "ui" / "app.py")


class FakeFiles:
    """Stands in for WorkspaceClient.files: a dict of volume paths."""

    def __init__(self):
        self.store: dict[str, bytes] = {}

    def create_directory(self, path):
        pass

    def upload(self, path, data, overwrite=False):
        self.store[path] = data.read()

    def download(self, path):
        return types.SimpleNamespace(contents=io.BytesIO(self.store[path]))

    def delete(self, path):
        self.store.pop(path, None)

    def list_directory_contents(self, path):
        for p, b in self.store.items():
            if p.rsplit("/", 1)[0] == path:
                yield types.SimpleNamespace(name=p.rsplit("/", 1)[1], is_directory=False, file_size=len(b),
                                            last_modified=1_790_000_000_000)


@pytest.fixture
def in_app(monkeypatch, tmp_path):
    monkeypatch.setenv("WISHBRIDGE_HOSTED", "1")
    monkeypatch.setenv("WISHBRIDGE_HOME", str(tmp_path / "home"))
    monkeypatch.setenv("WISHBRIDGE_PROJECTS_VOLUME", "/Volumes/main/wishbridge/projects")
    monkeypatch.setenv("DATABRICKS_HOST", "client.cloud.databricks.com")
    w = types.SimpleNamespace(files=FakeFiles())
    monkeypatch.setattr(hosting, "workspace_client", lambda profile=None, **kw: w)
    return w


def _project(root: Path) -> Path:
    dest = root / "salesdw"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("output"))
    return dest


def test_laptop_is_unchanged(monkeypatch):
    monkeypatch.delenv("WISHBRIDGE_HOSTED", raising=False)
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    assert not hosting.is_app()


def test_the_platform_marks_an_app(monkeypatch):
    monkeypatch.delenv("WISHBRIDGE_HOSTED", raising=False)
    monkeypatch.setenv("DATABRICKS_APP_NAME", "wishbridge")
    assert hosting.is_app()


def test_the_app_uses_its_own_identity_not_a_profile(monkeypatch):
    import databricks.sdk

    seen = []
    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", lambda **kw: seen.append(kw) or kw)
    monkeypatch.setenv("WISHBRIDGE_HOSTED", "1")
    hosting.workspace_client(profile="DEFAULT")
    monkeypatch.delenv("WISHBRIDGE_HOSTED")
    monkeypatch.delenv("DATABRICKS_APP_NAME", raising=False)
    hosting.workspace_client(profile="DEFAULT")
    assert seen == [{}, {"profile": "DEFAULT"}]


def test_a_project_survives_an_app_restart(in_app, tmp_path):
    folder = _project(hosting.local_root())
    (folder / "overrides").mkdir(exist_ok=True)
    (folder / "overrides" / "fix.sql").write_text("SELECT 1", encoding="utf-8")
    assert hosting.push(folder) is True
    assert hosting.push(folder) is False  # nothing changed: not uploaded again
    assert list(in_app.files.store) == ["/Volumes/main/wishbridge/projects/salesdw.zip"]

    shutil.rmtree(hosting.local_root())  # the app restarted: its disk is empty
    assert [p.name for p in hosting.saved_projects()] == ["salesdw"]
    back = hosting.pull("salesdw")
    assert (back / "project.yml").is_file()
    assert (back / "overrides" / "fix.sql").read_text(encoding="utf-8") == "SELECT 1"


def test_a_saved_project_cannot_write_outside_its_folder(in_app, tmp_path):
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("../escape.txt", "x")
    in_app.files.store["/Volumes/main/wishbridge/projects/bad.zip"] = buf.getvalue()
    with pytest.raises(ValueError, match="Unsafe path"):
        hosting.pull("bad")
    with pytest.raises(ValueError):
        hosting.pull("../etc")


def test_the_app_offers_its_own_workspace_and_no_sign_in(in_app):
    from wishbridge.warehouses.ssis.ui import helpers

    assert helpers.list_profiles() == [{"name": hosting.APP_PROFILE, "host": "https://client.cloud.databricks.com",
                                        "valid": True}]


def test_claude_goes_through_the_workspace_in_the_app(in_app, monkeypatch):
    from wishbridge.warehouses.ssis import ai

    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    backend, _ = ai.assist_backend("claude-code", hosting.APP_PROFILE, "databricks-claude-sonnet-4")
    assert backend == "databricks"


def test_the_app_warehouse_comes_from_its_resource(in_app, monkeypatch, tmp_path):
    from wishbridge.warehouses.ssis import dbx
    from wishbridge.warehouses.ssis.config import load_config

    folder = _project(tmp_path)
    cfg = load_config(folder / "project.yml")
    cfg.phase, cfg.host, cfg.warehouse_id = "code-migration", "", ""
    monkeypatch.setenv("WISHBRIDGE_WAREHOUSE_ID", "abc123")
    monkeypatch.setattr(hosting, "workspace_client",
                        lambda profile=None, **kw: types.SimpleNamespace(config=types.SimpleNamespace(host="https://x")))
    assert dbx.Warehouse(cfg).warehouse_id == "abc123"


def test_a_project_from_wishtrees_workspace_is_moved_on_request(in_app, tmp_path):
    from wishbridge.warehouses.ssis import dbx

    cfg = types.SimpleNamespace(host="https://wishtree.cloud.databricks.com", profile="DEFAULT")
    with pytest.raises(dbx.SqlError, match="Move this project to https://client.cloud.databricks.com"):
        dbx.check_workspace(cfg, "https://client.cloud.databricks.com")


# ----------------------------------------------------------------- the screens

st_testing = pytest.importorskip("streamlit.testing.v1")


@pytest.fixture
def app_ready(in_app, monkeypatch):
    import importlib

    from wishbridge.warehouses import WAREHOUSE_OF

    monkeypatch.setattr(hosting, "check_app", lambda w=None: [{"name": "Databricks workspace", "ok": True, "detail": "x"}])
    login = [{"name": hosting.APP_PROFILE, "host": "https://client.cloud.databricks.com", "valid": True}]
    for w in set(WAREHOUSE_OF.values()):
        helpers = importlib.import_module(f"wishbridge.warehouses.{w}.ui.helpers")
        monkeypatch.setattr(helpers, "list_profiles", lambda check_validity=True: login)
    return in_app


def test_the_app_starts_with_code_and_data_migration_only(app_ready):
    at = st_testing.AppTest.from_file(APP, default_timeout=120).run()
    assert not at.exception
    assert any("Running in Databricks" in s.value for s in at.success)
    assert [b.label for b in at.button if b.key and b.key.startswith("goal-")] == [
        "Start a code migration", "Start a data migration"]
    next(b for b in at.button if b.label == "Start a code migration").click().run()
    starts = next(r for r in at.radio if r.key == "start_code-migration")
    assert starts.options == ["Open the assessment zip", "Open a saved project"]
    assert not [t for t in at.text_input if t.label == "Create the project in"]  # no folders to type in the app


def test_the_app_opens_a_saved_project_and_saves_changes(app_ready):
    folder = _project(hosting.local_root())
    raw = (folder / "project.yml").read_text(encoding="utf-8")
    (folder / "project.yml").write_text(raw.replace("phase: assessment", "phase: code-migration"), encoding="utf-8")
    hosting.push(folder, force=True)
    shutil.rmtree(folder)

    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["goal"] = "code-migration"
    at.run()
    next(r for r in at.radio if r.key == "start_code-migration").set_value("Open a saved project").run()
    assert next(s for s in at.selectbox if s.key == "saved_pick").value == "salesdw"
    next(b for b in at.button if b.key == "saved_open").click().run()
    assert not at.exception
    assert Path(at.session_state["project"]) == hosting.local_root() / "salesdw"
    assert any("Saved in the workspace" in c.value for c in at.caption)

    # Settings: the app's own workspace, no saved logins and no sign-in box
    (hosting.local_root() / "salesdw" / "notes.txt").write_text("changed", encoding="utf-8")
    at.run()
    assert not at.exception
    import zipfile

    saved = zipfile.ZipFile(io.BytesIO(app_ready.files.store["/Volumes/main/wishbridge/projects/salesdw.zip"]))
    assert "notes.txt" in saved.namelist()  # the change reached the volume


def test_a_windows_zip_opens_with_working_paths_in_the_app(tmp_path, monkeypatch):
    """Paths saved on the Windows laptop work after opening the zip in the Linux app."""
    import json
    import zipfile

    from wishbridge import open_package as op

    b = chr(92)  # a backslash, as in the paths a Windows laptop saves
    old = b.join(["C:", "WishBridge", "Assessments", "salesdw"])
    state = {"convert": {"files": [{"file": "sub/a.py", "final": b.join([old, "output", "final", "sub", "a.py"]),
                                    "input": b.join([old, "input", "a.dtsx"])}],
                         "staged": "C:/WishBridge/Assessments/salesdw/output/staged_input/a.dtsx"}}
    pkg = tmp_path / "salesdw-assessment.zip"
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr(op.MANIFEST, json.dumps({"name": "salesdw", "project_root": old, "input_dir": old + b + "input",
                                            "output_dir": old + b + "output", "overrides_dir": old + b + "overrides"}))
        z.writestr("project/project.yml", (DEMO / "project.yml").read_text(encoding="utf-8"))
        z.writestr("project/output/state.json", json.dumps(state))
        z.writestr("converted_code/sub/a.py", "# Databricks notebook source")
    monkeypatch.setattr(op.os, "sep", "/")  # as on the app's Linux server
    dest = op.open_package(pkg, tmp_path / "app")
    moved = json.loads((dest / "output" / "state.json").read_text(encoding="utf-8"))["convert"]
    assert moved["files"][0]["final"] == str(dest / "output") + "/final/sub/a.py"  # no backslash after the folder
    assert moved["staged"] == str(dest).replace(b, "/") + "/output/staged_input/a.dtsx"


def test_the_same_zip_opened_again_gets_a_new_name(in_app):
    _project(hosting.local_root())  # salesdw is open in the app already
    assert hosting.free_name("salesdw") == "salesdw-2"
    assert hosting.free_name("other") == "other"
