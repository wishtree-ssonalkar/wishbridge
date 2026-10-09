"""Code read from the database (procedures, views, functions, triggers) and the comparison with code files."""

import pytest

from wishbridge import dbcode
from wishbridge.config import load_config
from wishbridge.state import load_state

ROWS = [
    ("dbo", "usp_Load", "P", "CREATE PROCEDURE dbo.usp_Load AS\r\nSELECT 1", 0),
    ("dbo", "vSales", "V", "CREATE VIEW [dbo].[vSales] AS SELECT 2 AS x", 0),
    ("rpt", "fn_Total", "FN", "CREATE FUNCTION rpt.fn_Total() RETURNS INT AS BEGIN RETURN 3 END", 0),
    ("dbo", "trg_Audit", "TR", None, 1),            # encrypted
    ("dbo", "usp_Secret", "P", None, 0),            # no VIEW DEFINITION
    ("dbo", "sp_creatediagram", "P", "CREATE PROCEDURE dbo.sp_creatediagram AS SELECT 1", 0),  # SSMS's own
]


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\n", encoding="utf-8")
    return load_config(p)


def test_one_file_per_object(cfg):
    r = dbcode.write_objects(cfg, ROWS, "srv / db")
    assert r["counts"] == {"Procedures": 1, "Views": 1, "Functions": 1}
    assert r["encrypted"] == ["dbo.trg_Audit (trigger)"] and r["unreadable"] == ["dbo.usp_Secret (procedure)"]
    f = cfg.path.parent / "database_code" / "dbo" / "Procedures" / "dbo.usp_Load.sql"
    assert f.read_text(encoding="utf-8") == "CREATE PROCEDURE dbo.usp_Load AS\nSELECT 1\nGO\n"
    assert (cfg.path.parent / "database_code" / "rpt" / "Functions" / "rpt.fn_Total.sql").is_file()
    assert not any("diagram" in o["name"] for o in r["objects"])
    assert load_state(cfg)["dbcode"]["read_from"] == "srv / db"


def test_compare_with_code_files(cfg, tmp_path):
    dbcode.write_objects(cfg, ROWS, "srv / db")
    files = tmp_path / "files"
    files.mkdir()
    # same procedure written differently (brackets, CREATE OR ALTER, PROC, comments, GO), a changed view,
    # and a function that is only in the files
    (files / "all.sql").write_text(
        "-- loads\nCREATE OR ALTER PROC [dbo].[usp_Load] AS\n  SELECT 1;\nGO\n"
        "CREATE VIEW dbo.vSales AS SELECT 99 AS x\nGO\n"
        "CREATE FUNCTION dbo.fn_Old() RETURNS INT AS BEGIN RETURN 1 END\n", encoding="utf-8")
    r = dbcode.compare(cfg, files)
    assert r["same"] == ["dbo.usp_load"]
    assert r["different"] == ["dbo.vsales"]
    assert r["only_in_database"] == ["rpt.fn_total"]
    assert r["only_in_files"] == ["dbo.fn_old"]
