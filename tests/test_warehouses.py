"""Every warehouse has its own complete copy of WishBridge, and the shared main screen hands over to it."""

import importlib
import pkgutil
import sys
from pathlib import Path

import pytest

from wishbridge import warehouses

WAREHOUSES = sorted(set(warehouses.WAREHOUSE_OF.values()))


def _modules(w: str) -> list[str]:
    pkg = importlib.import_module(f"wishbridge.warehouses.{w}")
    return [m.name for m in pkgutil.walk_packages(pkg.__path__, pkg.__name__ + ".")
            if not m.name.endswith((".__main__", ".ui.main"))]  # ui.main needs a running app


@pytest.mark.parametrize("w", WAREHOUSES)
def test_each_copy_is_complete_and_imports_on_its_own(w):
    names = _modules(w)
    for n in names:
        importlib.import_module(n)
    short = {n.split(f"wishbridge.warehouses.{w}.", 1)[1] for n in names}
    assert {"config", "convert", "rules", "deploy", "data", "report", "package", "runner", "cli",
            "ui.steps.settings", "ui.steps.report", "ui.screens", f"sources.{w}"} <= short
    root = Path(importlib.import_module(f"wishbridge.warehouses.{w}").__file__).parent
    assert (root / "README.md").is_file() and (root / "ui" / "main.py").is_file()


@pytest.mark.parametrize("source,w", sorted(warehouses.WAREHOUSE_OF.items()))
def test_each_source_opens_in_its_own_copy(tmp_path, source, w):
    (tmp_path / "project.yml").write_text(f"source: {source}\n", encoding="utf-8")
    config = warehouses.module(source, "config")
    assert config.__name__ == f"wishbridge.warehouses.{w}.config"
    assert config.load_config(tmp_path / "project.yml").source.key == source


def test_copies_do_not_share_code():
    a = importlib.import_module("wishbridge.warehouses.snowflake.rules")
    b = importlib.import_module("wishbridge.warehouses.sqlserver.rules")
    assert a is not b and a._DETECTORS is not b._DETECTORS  # a change to one copy cannot reach another
    assert Path(a.__file__).parent != Path(b.__file__).parent


def test_the_wishbridge_command_hands_project_commands_to_the_warehouse(tmp_path, monkeypatch):
    from wishbridge.cli import main

    (tmp_path / "project.yml").write_text("source: oracle\n", encoding="utf-8")
    called = {}
    oracle_cli = importlib.import_module("wishbridge.warehouses.oracle.cli")
    monkeypatch.setattr(oracle_cli.main, "main", lambda args, prog_name: called.update(args=args))
    monkeypatch.setattr(sys, "argv", ["wishbridge"])
    with pytest.raises(SystemExit) as done:
        main.main(args=["inventory", "-c", str(tmp_path / "project.yml"), "--script"], prog_name="wishbridge")
    assert done.value.code == 0
    assert called["args"] == ["inventory", "-c", str(tmp_path / "project.yml"), "--script"]


def test_unknown_source_is_refused():
    with pytest.raises(ValueError, match="No WishBridge for source"):
        warehouses.package_for("fabric")
