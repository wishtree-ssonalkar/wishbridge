"""Headless smoke tests of the Streamlit app (no browser, no Databricks)."""

import shutil
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

APP = str(Path(__file__).parents[1] / "src" / "wishbridge" / "ui" / "app.py")
DEMO = Path(__file__).parents[1] / "examples" / "mssql-demo"


@pytest.fixture
def project(tmp_path):
    dest = tmp_path / "mssql-demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("output"))
    return dest


def test_welcome_without_project():
    at = st_testing.AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Open a project folder" in i.value for i in at.info)


def test_all_tabs_render_for_a_project(project):
    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["project"] = str(project)
    at.run()
    assert not at.exception
    assert [t.label for t in at.tabs] == ["1 · Settings", "2 · Code", "3 · Run", "4 · Results", "5 · Manual fixes", "Environment"]
    assert at.selectbox[0].value == "mssql"
    assert any(i.value.startswith("Nothing has run yet") for i in at.info)


def test_manual_fixes_handles_missing_converted_file(project):
    from wishbridge.config import load_config
    from wishbridge.state import save_step

    cfg = load_config(project / "project.yml")
    summary = {"files": 1, "ready": 1, "review": 0, "needs_fix": 0, "auto_fixed": 0, "open_errors": 0, "open_warnings": 0}
    save_step(cfg, "convert", {"summary": summary, "transpiler": "morph", "final_dir": "", "files": [{
        "file": "a.sql", "input": str(project / "input" / "a.sql"), "final": str(project / "output" / "final" / "a.sql"),
        "status": "ready", "fixed": 0, "findings": []}]})
    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["project"] = str(project)
    at.run()
    assert not at.exception
    assert any("not on disk" in i.value for i in at.info)


def test_save_settings_writes_project_yml(project):
    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["project"] = str(project)
    at.run()
    schema_input = next(t for t in at.text_input if t.label == "Test schema")
    schema_input.set_value("wishbridge_ui_test").run()
    next(b for b in at.button if b.label.startswith("💾 Save settings")).click().run()
    assert not at.exception
    text = (project / "project.yml").read_text(encoding="utf-8")
    assert "schema: wishbridge_ui_test" in text
    assert "saved from the WishBridge UI" in text
