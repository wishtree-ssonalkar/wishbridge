from pathlib import Path

import pytest

from wishbridge.analysis import estimate_hours, parse_report
from wishbridge.config import ConfigError, load_config, looks_like_prod, render_template
from wishbridge.data import build_plan

FIXTURES = Path(__file__).parent / "fixtures"


def write(tmp_path, text):
    p = tmp_path / "project.yml"
    p.write_text(text, encoding="utf-8")
    return p


def test_template_round_trips(tmp_path):
    cfg = load_config(write(tmp_path, render_template("Acme DW", "mssql")))
    assert cfg.source.dialect == "mssql" and cfg.transpiler == "morph"
    assert cfg.schema == "wishbridge_acme_dw"
    assert cfg.input_dir == (tmp_path / "input").resolve()


def test_template_schema_map_only_for_tsql(tmp_path):
    assert load_config(write(tmp_path, render_template("x", "mssql"))).schema_map == {"dbo": "main.wishbridge_x"}
    assert load_config(write(tmp_path, render_template("x", "snowflake"))).schema_map == {}


def test_project_file_saved_with_bom(tmp_path):
    p = tmp_path / "project.yml"
    p.write_bytes("﻿source: mssql\n".encode("utf-8"))
    assert load_config(p).source.key == "mssql"


def test_unknown_source_rejected(tmp_path):
    with pytest.raises(ConfigError):
        load_config(write(tmp_path, "source: cobol\n"))


def test_table_targets_follow_schema_map(tmp_path):
    cfg = load_config(write(tmp_path, """
source: mssql
databricks: {catalog: main, schema: dev}
schema_map: {dbo: main.sales}
data:
  source_catalog: fed
  mode: overwrite
  tables: [dbo.Orders, other.Items, {source: dbo.X, target: c.s.x}]
"""))
    assert [t.target for t in cfg.tables] == ["main.sales.Orders", "main.dev.Items", "c.s.x"]
    plan = build_plan(cfg)
    assert plan[0][1] == ["TRUNCATE TABLE main.sales.Orders", "INSERT INTO main.sales.Orders BY NAME SELECT * FROM fed.dbo.Orders"]


def test_files_plan(tmp_path):
    cfg = load_config(write(tmp_path, """
source: oracle
data: {method: files, files_root: /Volumes/main/l/x/, file_format: csv, tables: [HR.EMP]}
"""))
    (_, stmts), = build_plan(cfg)
    assert stmts[0].startswith("COPY INTO main.wishbridge.EMP FROM '/Volumes/main/l/x/EMP/' FILEFORMAT = CSV")
    assert "'header' = 'true'" in stmts[0]


def test_prod_detection():
    assert looks_like_prod("main.prod_sales") and looks_like_prod("production") and looks_like_prod("cat.sales-prod")
    assert not looks_like_prod("main.producer") and not looks_like_prod("main.wishbridge_demo")


def test_parse_lakebridge_report(tmp_path):
    rep = parse_report(FIXTURES / "analysis_mssql.xlsx")
    assert rep["totals"]["SQL Scripts"] == 3
    assert rep["totals"]["Procedures"] == 1
    assert [p["name"] for p in rep["programs"]] == ["01_create_tables.sql", "02_reports.sql", "03_procedure.sql"]
    assert rep["complexity"] == {"LOW": 3}
    assert rep["functions"]["GETDATE"] == 3
    cfg = load_config(write(tmp_path, "source: mssql\n"))
    assert estimate_hours(cfg, rep["programs"]) == 1.5
