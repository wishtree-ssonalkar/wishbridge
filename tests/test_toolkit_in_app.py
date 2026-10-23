"""The migration toolkit inside the Databricks App (installed from PyPI there), without installing anything here."""

from pathlib import Path

import pytest

from wishbridge import toolkit_setup
from wishbridge.warehouses.ssis import lakebridge

DEMO = Path(__file__).parents[1] / "examples" / "mssql-demo"


@pytest.fixture
def app_home(monkeypatch, tmp_path):
    monkeypatch.setenv("WISHBRIDGE_HOSTED", "1")
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(toolkit_setup, "STATUS", tmp_path / ".wishbridge" / "toolkit_status.json")
    return tmp_path


def test_the_app_finds_the_toolkit_installed_as_a_package(app_home, monkeypatch):
    venv = app_home / ".databricks" / "labs" / "lakebridge" / "state" / "venv"
    py = venv / ("Scripts/python.exe" if __import__("os").name == "nt" else "bin/python")
    cli = venv / "lib" / "python3.11" / "site-packages" / "databricks" / "labs" / "lakebridge" / "cli.py"
    for f in (py, cli):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("", encoding="utf-8")
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(lakebridge.subprocess, "run", fake_run)
    lakebridge._run_offline("analyze", ["--source-directory", "x"], {"DATABRICKS_HOST": "https://app"})
    assert seen["cmd"][:2] == [str(py), str(cli)]
    assert "PYTHONPATH" not in seen["env"]  # the package, not a source folder


def test_a_toolkit_still_installing_says_so(app_home):
    toolkit_setup._save(state="installing", step="the BladeBridge converter", version=toolkit_setup.SETUP_VERSION)
    with pytest.raises(lakebridge.LakeBridgeError, match="still being installed in the app"):
        lakebridge._run_offline("analyze", [], {})


def test_the_java_converter_says_laptop_only_in_the_app(app_home):
    with pytest.raises(lakebridge.LakeBridgeError, match="Java program: on the laptop only"):
        lakebridge.transpiler_config_path("morph")


def test_automatic_mode_in_the_app_starts_with_the_python_converter(app_home, tmp_path):
    from wishbridge.warehouses.sqlserver.config import load_config

    (tmp_path / "p").mkdir()
    (tmp_path / "p" / "project.yml").write_text((DEMO / "project.yml").read_text(encoding="utf-8"), encoding="utf-8")
    cfg = load_config(tmp_path / "p" / "project.yml")
    assert cfg.source.key == "mssql" and cfg.transpiler == "bladebridge"
