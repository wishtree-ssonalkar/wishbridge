"""Choosing the Databricks workspace, and refusing to run a project against the wrong one."""

import sys
from types import ModuleType, SimpleNamespace

import pytest

from wishbridge.config import load_config
from wishbridge.dbx import SqlError, Warehouse, check_workspace, normalise_host
from wishbridge.ui import helpers as h

CFG = """[DEFAULT]
host = https://dbc-wishtree.cloud.databricks.com/
auth_type = databricks-cli

[__settings__]
auth_storage = secure

[CLIENT_ACME]
host = https://adb-111.11.azuredatabricks.net
auth_type = databricks-cli

[NO_HOST]
auth_type = pat
"""


def test_list_profiles_reads_every_login(tmp_path, monkeypatch):
    path = tmp_path / ".databrickscfg"
    path.write_text(CFG, encoding="utf-8")
    monkeypatch.setenv("DATABRICKS_CONFIG_FILE", str(path))
    monkeypatch.setattr(h, "_validity", lambda: {"DEFAULT": True, "CLIENT_ACME": False})
    assert h.list_profiles() == [
        {"name": "DEFAULT", "host": "https://dbc-wishtree.cloud.databricks.com", "valid": True},
        {"name": "CLIENT_ACME", "host": "https://adb-111.11.azuredatabricks.net", "valid": False},
    ]


def test_sign_in_validates_input_before_running_anything(monkeypatch):
    monkeypatch.setattr(h.subprocess, "run", lambda *a, **k: pytest.fail("must not run the CLI"))
    assert h.sign_in("dbc-123.cloud.databricks.com", "ACME") == (False, "Enter the workspace URL, e.g. https://dbc-1234.cloud.databricks.com")
    assert h.sign_in("https://dbc-123.cloud.databricks.com", "bad name; rm") [0] is False


def test_normalise_host():
    assert normalise_host("HTTPS://dbc-1.cloud.databricks.com/") == "https://dbc-1.cloud.databricks.com"
    assert normalise_host("dbc-1.cloud.databricks.com") == "https://dbc-1.cloud.databricks.com"


def project(tmp_path, host=""):
    p = tmp_path / "project.yml"
    p.write_text(f"source: mssql\ndatabricks: {{profile: CLIENT_ACME, host: '{host}'}}\n", encoding="utf-8")
    return load_config(p)


def test_check_workspace(tmp_path):
    cfg = project(tmp_path, "https://adb-111.11.azuredatabricks.net")
    check_workspace(cfg, "https://adb-111.11.azuredatabricks.net/")
    with pytest.raises(SqlError, match="belongs to https://adb-111"):
        check_workspace(cfg, "https://dbc-wishtree.cloud.databricks.com")
    check_workspace(project(tmp_path, ""), "https://anything")  # older projects without a host still run


def test_warehouse_refuses_the_wrong_workspace(tmp_path, monkeypatch):
    fake_sdk = ModuleType("databricks.sdk")
    fake_sdk.WorkspaceClient = lambda profile: SimpleNamespace(config=SimpleNamespace(host="https://dbc-wishtree.cloud.databricks.com"))
    monkeypatch.setitem(sys.modules, "databricks.sdk", fake_sdk)
    with pytest.raises(SqlError, match="signed in to https://dbc-wishtree"):
        Warehouse(project(tmp_path, "https://adb-111.11.azuredatabricks.net"))
