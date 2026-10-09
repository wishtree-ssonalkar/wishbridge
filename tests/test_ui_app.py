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


def ready_app(project=None):
    """The app after a successful system check (and with a project open)."""
    from wishbridge.system import Status

    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["system"] = [Status("cli", "Databricks CLI", True, "x", "", "")]
    if project is not None:
        at.session_state["project"] = str(project)
    return at


def test_system_check_comes_first():
    at = st_testing.AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Check this computer" in b.label for b in at.button)
    assert next(b for b in at.button if b.label == "Open").disabled  # no project before the check
    assert any("System check" in i.value for i in at.info)


def test_welcome_after_the_check():
    at = ready_app().run()
    assert not at.exception
    assert not next(b for b in at.button if b.label == "Open").disabled
    assert any("warehouse code" in i.value for i in at.info)
    assert any("SQL Server used as a data warehouse" in str(t.value) for t in at.table)


def test_steps_open_in_order(project):
    at = ready_app(project).run()
    assert not at.exception
    steps = {b.label.replace("✓ ", ""): b for b in at.button if b.key and b.key.startswith("step-")}
    assert list(steps) == ["1 · Settings", "2 · Code", "3 · Run", "4 · Results", "5 · Fix code"]  # migration phase
    assert steps["1 · Settings"].disabled is False
    # nothing opens before Settings are confirmed with Next
    assert all(steps[k].disabled for k in ("2 · Code", "3 · Run", "4 · Results", "5 · Fix code"))
    assert at.selectbox[0].value == "mssql"


def test_next_saves_settings_and_opens_code(project):
    at = ready_app(project).run()
    schema_input = next(t for t in at.text_input if t.label == "Test schema")
    schema_input.set_value("wishbridge_ui_test").run()
    next(b for b in at.button if b.label.startswith("Next: add the code")).click().run()
    assert not at.exception
    text = (project / "project.yml").read_text(encoding="utf-8")
    assert "schema: wishbridge_ui_test" in text
    assert "saved from the WishBridge UI" in text
    assert at.session_state["step"] == "code"  # Next saves and moves on
    steps = {b.label: b for b in at.button if b.key and b.key.startswith("step-")}
    assert steps["1 · ✓ Settings"].disabled is False and steps["2 · ✓ Code"].disabled is False
    assert steps["4 · Results"].disabled  # nothing has run yet


def test_fix_code_handles_missing_converted_file(project):
    from wishbridge.config import load_config
    from wishbridge.state import save_step

    cfg = load_config(project / "project.yml")
    summary = {"files": 1, "ready": 1, "review": 0, "needs_fix": 0, "auto_fixed": 0, "open_errors": 0, "open_warnings": 0}
    save_step(cfg, "convert", {"summary": summary, "transpiler": "morph", "final_dir": "", "files": [{
        "file": "a.sql", "input": str(project / "input" / "a.sql"), "final": str(project / "output" / "final" / "a.sql"),
        "status": "ready", "fixed": 0, "findings": []}]})
    at = ready_app(project)
    at.session_state["go_step"] = "fix"
    at.run()
    assert not at.exception
    assert any("not on disk" in i.value for i in at.info)


def test_migration_settings_need_target_and_source(project):
    import yaml

    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    raw["data"].update(source_catalog="", tables=[])  # no source connection, nothing to copy yet
    (project / "project.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    at = ready_app(project).run()
    nxt = next(b for b in at.button if b.label.startswith("Next: add the code"))
    assert nxt.disabled
    msg = " ".join(e.value for e in at.error)
    assert "Fill these in" in msg and "Source database" in msg and "at least one table" in msg


def test_assessment_ends_with_sending_the_zip(project):
    text = (project / "project.yml").read_text(encoding="utf-8")
    (project / "project.yml").write_text(text + "\nphase: assessment\n", encoding="utf-8")
    at = ready_app(project).run()
    assert not at.exception
    steps = [b.label.replace("✓ ", "") for b in at.button if b.key and b.key.startswith("step-")]
    assert steps == ["1 · Settings", "2 · Code", "3 · Run", "4 · Send the zip"]
    assert not next(b for b in at.button if b.label.startswith("Next: add the code")).disabled  # nothing else needed
