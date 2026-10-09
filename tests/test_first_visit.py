"""First visit: offline assessment, code copy, DBA inventory, review package, phase lock."""

import zipfile
from pathlib import Path

import pytest

from wishbridge import inventory, lakebridge
from wishbridge.config import load_config
from wishbridge.dbx import SqlError, Warehouse
from wishbridge.rules import apply_rules, boolean_columns, check_notebook

CSV = ("schema_name,table_name,column_name,ordinal,data_type,max_length,precision,scale,is_nullable,row_count,size_mb\n"
       "dbo,Users,UserID,1,int,4,10,0,NO,1250,0.52\n"
       "dbo,Users,IsActive,2,bit,1,1,0,NO,1250,0.52\n"
       "sales,Orders,OrderID,1,int,4,10,0,NO,98000,12.5\n")


def project(tmp_path, extra=""):
    (tmp_path / "input").mkdir(exist_ok=True)
    p = tmp_path / "project.yml"
    p.write_text(f"source: mssql\n{extra}", encoding="utf-8")
    return load_config(p)


def test_inventory_query_and_import(tmp_path):
    cfg = project(tmp_path)
    name, text = inventory.script(cfg)
    assert name == "wishbridge_inventory_sqlserver.sql" and "READ-ONLY" in text and "sys.tables" in text
    for db in inventory.QUERIES:
        assert "schema_name" in inventory.script(cfg, db)[1]
    f = tmp_path / "dba.csv"
    f.write_text(CSV, encoding="utf-8")
    res = inventory.import_file(cfg, f)
    assert res["summary"] == {"tables": 2, "columns": 3, "rows": 99250, "size_mb": 13.0, "schemas": ["dbo", "sales"]}
    assert res["tables"][0]["table"] == "Orders"  # biggest first
    assert inventory.table_list(res) == ["sales.Orders", "dbo.Users"]
    assert (cfg.output_dir / "inventory" / "inventory.csv").exists()
    with pytest.raises(ValueError, match="header row"):
        inventory.parse("a,b\n1,2\n")


def test_bit_columns_are_compared_with_true_false():
    ddl = "CREATE TABLE t (`IsActive` BOOLEAN NOT NULL, Status INT);"
    cols = boolean_columns(ddl)
    assert cols == {"isactive"}
    fixed, findings = apply_rules("SELECT * FROM t u WHERE u.IsActive = 1 AND Status = 1 AND IsActive <> 0;",
                                  {}, "mssql", frozenset(cols))
    assert "u.IsActive = true" in fixed and "IsActive <> false" in fixed and "Status = 1" in fixed
    assert any(f.rule == "boolean-compare" and f.fixed for f in findings)
    assert not any(f.rule == "bit-flag-compare" for f in findings if not f.fixed)
    nb, nf = check_notebook('q = f"""SELECT * FROM c WHERE c.IsActive = 1"""\n', {}, frozenset(cols))
    assert "c.IsActive = true" in nb and any(f.rule == "boolean-compare" for f in nf)


def test_assessment_phase_blocks_everything_that_leaves_the_computer(tmp_path):
    cfg = project(tmp_path, "phase: assessment\n")
    with pytest.raises(SqlError, match="assessment phase"):
        Warehouse(cfg)
    older = tmp_path / "older"
    older.mkdir()
    assert project(older).phase == "migration"  # projects without the setting keep working as before


def test_offline_steps_never_see_the_real_databricks_login(tmp_path, monkeypatch):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])

        class P:
            returncode, stdout, stderr = 0, "", ""
        return P()

    monkeypatch.setattr(lakebridge, "_databricks_cli", lambda: "databricks")
    monkeypatch.setattr(lakebridge.subprocess, "run", fake_run)
    monkeypatch.setattr(lakebridge.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setenv("DATABRICKS_HOST", "https://client.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "secret")
    cfg = project(tmp_path)
    lakebridge.run(cfg, "analyze", "--source-directory", ".")
    assert "DATABRICKS_HOST" not in seen and "DATABRICKS_TOKEN" not in seen
    assert Path(seen["DATABRICKS_CONFIG_FILE"]).read_text(encoding="utf-8").count("wishbridge-offline.invalid") == 1
    lakebridge.run(cfg, "reconcile")
    assert seen["DATABRICKS_HOST"] == "https://client.cloud.databricks.com"  # online steps use the real login


def test_review_package(tmp_path):
    from wishbridge.package import build_package
    from wishbridge.state import save_step

    cfg = project(tmp_path)
    (cfg.input_dir / "a.sql").write_text("SELECT 1;", encoding="utf-8")
    final = cfg.output_dir / "final"
    final.mkdir(parents=True)
    (final / "a.sql").write_text("SELECT 1;", encoding="utf-8")
    save_step(cfg, "convert", {"summary": {"files": 1, "ready": 0, "review": 1, "needs_fix": 0, "auto_fixed": 0,
                                           "open_errors": 0, "open_warnings": 1},
                               "transpiler": "morph", "final_dir": str(final),
                               "files": [{"file": "a.sql", "kind": "sql", "converter": "morph", "status": "review",
                                          "fixed": 0, "manual_override": False,
                                          "findings": [{"rule": "r", "severity": "warning", "line": 1,
                                                        "message": "check", "fixed": False}]}]})
    out = build_package(cfg)
    names = zipfile.ZipFile(out).namelist()
    assert {"README.txt", "report.html", "files.csv", "open_items.csv", "converted_code/a.sql", "original_code/a.sql"} <= set(names)
    assert "original_code/a.sql" not in zipfile.ZipFile(build_package(cfg, include_original=False)).namelist()
