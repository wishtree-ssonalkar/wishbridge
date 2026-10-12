"""ETL projects: the warehouse database's code is assessed together with the packages."""

import shutil
from pathlib import Path

import pytest

from wishbridge import companion, dbcode
from wishbridge.config import load_config
from wishbridge.inventory import import_file

DEMO = Path(__file__).parents[1] / "examples" / "ssis-demo"


@pytest.fixture
def cfg(tmp_path):
    dest = tmp_path / "ssis"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("output"))
    f = dest / "input" / "SalesDW_ETL" / "LoadFactSales.dtsx"
    text = f.read_text(encoding="utf-8")
    f.write_text(text.replace('SqlStatementSource="DELETE FROM dw.FactDailySales',
                              'SqlStatementSource="EXEC [dw].[usp_Refresh];&#10;EXECUTE dw.usp_Missing @Days = 7;'
                              '&#10;DELETE FROM dw.FactDailySales', 1), encoding="utf-8")
    return load_config(dest / "project.yml")


def _read_database(cfg):
    csv = cfg.path.parent / "inv.csv"
    csv.write_text("schema_name,table_name,column_name,data_type\ndw,DimCustomer,CustomerKey,int\n"
                   "dw,FactDailySales,Amount,decimal\nstg,Customer,CustomerID,int\n", encoding="utf-8")
    import_file(cfg, csv, "sqlserver")
    dbcode.write_objects(cfg, [("dw", "usp_Refresh", "P", "CREATE PROCEDURE dw.usp_Refresh AS SELECT 1", 0)], "t")


def test_what_the_packages_use(cfg):
    deps = {d["name"]: d for d in companion.dependencies(cfg)}
    assert deps["dw.usp_Refresh"]["kind"] == "procedure" and deps["dw.usp_Refresh"]["how"] == "calls"
    assert deps["dw.DimCustomer"]["how"] == "reads, writes"
    assert deps["stg.Customer"]["packages"] == "LoadDimCustomer"
    assert all(d["found"] is None for d in deps.values())  # nothing read from the database yet


def test_found_and_missing_after_reading_the_database(cfg):
    _read_database(cfg)
    deps = companion.dependencies(cfg)
    assert deps[0] == {"name": "dw.usp_Missing", "kind": "procedure", "how": "calls", "packages": "LoadFactSales",
                       "found": False}  # missing procedures come first
    found = {d["name"]: d["found"] for d in deps}
    assert found["dw.usp_Refresh"] and found["dw.FactDailySales"] and found["stg.Customer"]
    assert found["Sales.Orders"] is False  # only read: probably the source system


def test_database_code_is_assessed_as_sql_next_to_the_packages(cfg):
    assert not companion.applies(cfg)
    _read_database(cfg)
    assert companion.applies(cfg)
    sub = companion.sub_config(cfg)
    assert sub.source.key == "mssql" and sub.input_dir == dbcode.folder(cfg)
    assert sub.output_dir == cfg.output_dir / "database_code_run" and sub.target_technology == ""


def test_not_for_projects_whose_code_is_the_database(cfg):
    import yaml

    _read_database(cfg)
    raw = yaml.safe_load(cfg.path.read_text(encoding="utf-8"))
    raw["input"] = "database_code"
    cfg.path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    assert not companion.applies(load_config(cfg.path))
