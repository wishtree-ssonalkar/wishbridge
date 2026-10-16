"""Analyze and convert run on this computer only: no Databricks login, no network, no waiting on retries."""

import json
import subprocess
import urllib.error
import urllib.request

import pytest

from wishbridge.warehouses.ssis import lakebridge as lb
from wishbridge.warehouses.ssis.config import load_config


def test_offline_workspace_answers_who_am_i_and_nothing_else():
    with lb._OfflineWorkspace() as url:
        me = json.load(urllib.request.urlopen(f"{url}/api/2.0/preview/scim/v2/Me", timeout=5))
        assert me["userName"] == "wishbridge-offline"
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{url}/api/2.1/unity-catalog/catalogs", timeout=5)
        assert e.value.code == 404


@pytest.fixture
def toolkit(tmp_path, monkeypatch):
    home = tmp_path / "lakebridge"
    for f in ("state/venv/Scripts/python.exe", "state/venv/bin/python", "lib/src/databricks/labs/lakebridge/cli.py"):
        (home / f).parent.mkdir(parents=True, exist_ok=True)
        (home / f).write_text("")
    monkeypatch.setattr(lb, "_toolkit_home", lambda: home)
    return home


def test_offline_commands_call_the_toolkit_directly_without_the_real_login(tmp_path, toolkit, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen["cmd"], seen["env"] = cmd, kw["env"]
        seen["me"] = json.load(urllib.request.urlopen(kw["env"]["DATABRICKS_HOST"] + "/api/2.0/preview/scim/v2/Me"))
        return subprocess.CompletedProcess(cmd, 0, "Analyzer run completed.", "")

    monkeypatch.setattr(lb.subprocess, "run", fake_run)
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", "real.cfg")
    (tmp_path / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    cfg = load_config(tmp_path / "project.yml")
    lb.run(cfg, "analyze", "--source-directory", "in", "--report-file", "r.xlsx", "--source-tech", "SSIS")
    payload = json.loads(seen["cmd"][-1])
    assert payload == {"command": "analyze", "flags": {"source-directory": "in", "report-file": "r.xlsx",
                                                       "source-tech": "SSIS", "log_level": "info"}}
    assert seen["cmd"][1].endswith("cli.py") and "DATABRICKS_CONFIG_FILE" not in seen["env"]
    assert seen["env"]["DATABRICKS_HOST"].startswith("http://127.0.0.1:") and seen["me"]["userName"]


def test_missing_toolkit_is_a_clear_message(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "_toolkit_home", lambda: tmp_path / "none")
    (tmp_path / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    with pytest.raises(lb.LakeBridgeError, match="not installed"):
        lb.run(load_config(tmp_path / "project.yml"), "transpile", "--input-source", "x")


def test_too_long_path_says_what_to_do():
    err = lb._fail("analyze", "Cannot open file >C:/very/long/a.sql for writing: No such file or directory")
    assert "too long for Windows" in str(err)
