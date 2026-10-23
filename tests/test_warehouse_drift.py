"""Each warehouse may work differently, but only on purpose: a file a warehouse changes is listed in its
CUSTOMIZED.txt; every other file must match the reference copy (ssis/). This catches a fix applied to one
warehouse and forgotten in the others. Fix: python tools/sync_warehouses.py <file>, or list the file."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location("sync_warehouses", Path(__file__).parents[1] / "tools" / "sync_warehouses.py")
sync = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(sync)


@pytest.mark.parametrize("warehouse", [w for w in sync.warehouses() if w != sync.REFERENCE])
def test_shared_files_match_the_reference(warehouse):
    drifted = sync.drift(warehouse)
    assert not drifted, (f"{warehouse}: {', '.join(drifted)} differ from ssis/. Copy the fix with "
                         f"`python tools/sync_warehouses.py <file>`, or - when it is on purpose - add the file to "
                         f"src/wishbridge/warehouses/{warehouse}/CUSTOMIZED.txt with the reason.")


def test_every_customized_file_exists_and_says_why():
    for w in sync.warehouses():
        for rel, why in sync.customized(w).items():
            assert (sync.WAREHOUSES / w / rel).is_file(), f"{w}/CUSTOMIZED.txt lists {rel}, which does not exist"
            assert why, f"{w}/CUSTOMIZED.txt: say why {rel} differs"


def test_a_forgotten_fix_is_caught_and_a_listed_one_is_not(tmp_path, monkeypatch):
    for w in ("ssis", "teradata"):
        (tmp_path / w / "ui").mkdir(parents=True)
        (tmp_path / w / "ai.py").write_text(f"from wishbridge.warehouses.{w} import x\n", encoding="utf-8")
        (tmp_path / w / "data.py").write_text("COPY = 1\n", encoding="utf-8")
    monkeypatch.setattr(sync, "WAREHOUSES", tmp_path)
    assert sync.drift("teradata") == []
    (tmp_path / "ssis" / "data.py").write_text("COPY = 2  # the fix\n", encoding="utf-8")
    assert sync.drift("teradata") == ["data.py"]
    assert sync.sync(dry_run=True) == ["teradata/data.py"]
    (tmp_path / "teradata" / "CUSTOMIZED.txt").write_text("data.py - Teradata copies with FastExport\n", encoding="utf-8")
    assert sync.drift("teradata") == [] and sync.sync() == []
    assert (tmp_path / "teradata" / "data.py").read_text(encoding="utf-8") == "COPY = 1\n"  # its own version is kept
