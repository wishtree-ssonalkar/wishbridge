"""UI helpers (no Streamlit needed)."""

import pytest

from wishbridge.config import load_config
from wishbridge.ui import helpers as h


def test_create_open_and_save_project(tmp_path):
    root = h.create_project(tmp_path, "acme-dw", "oracle")
    raw = h.read_raw(root)
    assert raw["source"] == "oracle" and (root / "input").is_dir()

    raw["databricks"]["catalog"] = "workspace"
    raw["schema_map"] = h.rows_to_schema_map([{"source_schema": "HR", "target": "workspace.hr"}, {"source_schema": "", "target": ""}])
    raw["data"]["tables"] = h.rows_to_tables([{"source": "HR.EMP", "target": ""}, {"source": "HR.DEPT", "target": "workspace.hr.dept"},
                                              {"source": "", "target": "x"}])
    h.save_raw(root, raw)
    cfg = load_config(root / "project.yml")
    assert cfg.catalog == "workspace"
    assert [t.target for t in cfg.tables] == ["workspace.hr.EMP", "workspace.hr.dept"]
    assert h.table_rows(h.read_raw(root)) == [{"source": "HR.EMP", "target": ""}, {"source": "HR.DEPT", "target": "workspace.hr.dept"}]
    assert h.schema_map_rows(h.read_raw(root)) == [{"source_schema": "HR", "target": "workspace.hr"}]


def test_invalid_settings_are_not_saved(tmp_path):
    root = h.create_project(tmp_path, "p", "mssql")
    before = (root / "project.yml").read_text(encoding="utf-8")
    bad = h.read_raw(root)
    bad["data"]["method"] = "carrier-pigeon"
    with pytest.raises(Exception):
        h.save_raw(root, bad)
    assert (root / "project.yml").read_text(encoding="utf-8") == before


def test_create_project_rejects_bad_names(tmp_path):
    with pytest.raises(ValueError):
        h.create_project(tmp_path, "../escape", "mssql")
    with pytest.raises(ValueError):
        h.create_project(tmp_path, "ok", "cobol")


def test_uploads_cannot_escape_input_folder(tmp_path):
    root = h.create_project(tmp_path, "p", "mssql")
    dest = h.save_upload(root, "..\\..\\evil.sql", b"SELECT 1")
    assert dest == root / "input" / "evil.sql" or dest.parent == root / "input"
    assert [p.name for p in h.input_files(root)] == [dest.name]


def test_override_path_stays_in_project(tmp_path):
    root = h.create_project(tmp_path, "p", "mssql")
    assert h.override_path(root, "sub/a.sql") == (root / "overrides" / "sub" / "a.sql").resolve()
    with pytest.raises(ValueError):
        h.override_path(root, "../../outside.sql")


def test_environment_check_shape():
    checks = h.check_environment("DEFAULT")
    names = [c.name for c in checks]
    assert names[:3] == ["Databricks CLI", "Java", "LakeBridge"]
    assert any(n.startswith("Databricks login") for n in names)
