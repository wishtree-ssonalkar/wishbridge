"""Made-up sample rows built from the column definitions (never from client data)."""

import csv

import pytest

from wishbridge.warehouses.sqlserver import dummy_data
from wishbridge.warehouses.sqlserver.config import load_config
from wishbridge.warehouses.sqlserver.inventory import import_file

CSV = """schema_name,table_name,column_name,ordinal,data_type,max_length,precision,scale,is_nullable,row_count,size_mb
dbo,Customer,CustomerId,1,int,4,10,0,NO,100,1
dbo,Customer,FullName,2,nvarchar,20,0,0,NO,100,1
dbo,Customer,Email,3,varchar,10,0,0,YES,100,1
dbo,Customer,ProjectName,4,nvarchar,100,0,0,YES,100,1
dbo,Customer,StartDate,5,date,3,10,0,YES,100,1
dbo,Customer,EndDate,6,date,3,10,0,YES,100,1
dbo,Customer,Rate,7,decimal,5,4,2,YES,100,1
dbo,Customer,IsActive,8,bit,1,1,0,NO,100,1
dbo,Customer,RowGuid,9,uniqueidentifier,16,0,0,YES,100,1
dbo,Orders,OrderId,1,int,4,10,0,NO,5,1
dbo,Orders,CustomerId,2,int,4,10,0,NO,5,1
dbo,Orders,Note,3,nvarchar,-1,0,0,YES,5,1
"""


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\ndatabricks: {catalog: main, schema: dev}\n", encoding="utf-8")
    c = load_config(p)
    f = tmp_path / "inv.csv"
    f.write_text(CSV, encoding="utf-8")
    import_file(c, f, "sqlserver")  # writes the dummy data too
    return c


def _rows(cfg, table):
    with open(cfg.output_dir / "dummy_data" / f"{table}.csv", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def test_rows_fit_the_columns(cfg):
    rows = _rows(cfg, "dbo.Customer")
    assert len(rows) == 5
    assert [r["CustomerId"] for r in rows] == ["1", "2", "3", "4", "5"]  # keys 1..5 so tables join
    assert rows[0]["FullName"] == "Asha Patil" and len(rows[0]["FullName"]) <= 10  # nvarchar(20) = 10 characters
    assert all(len(r["Email"]) <= 10 for r in rows)  # cut to varchar(10)
    assert rows[0]["ProjectName"] == "Project A"
    assert rows[0]["EndDate"] > rows[0]["StartDate"]  # end after start
    assert rows[0]["Rate"] == "99.99"  # decimal(4,2) never overflows
    assert rows[0]["IsActive"] == "True"
    assert len(rows[0]["RowGuid"]) == 36


def test_last_row_is_null_where_allowed(cfg):
    last = _rows(cfg, "dbo.Customer")[-1]
    assert last["Email"] == "" and last["StartDate"] == ""  # nullable
    assert last["FullName"] and last["CustomerId"] == "5" and last["IsActive"]  # NOT NULL / key


def test_foreign_keys_match(cfg):
    assert [r["CustomerId"] for r in _rows(cfg, "dbo.Orders")] == [r["CustomerId"] for r in _rows(cfg, "dbo.Customer")]


def test_insert_script_for_databricks(cfg):
    sql = (cfg.output_dir / "dummy_data" / "dummy_data.sql").read_text(encoding="utf-8")
    assert "MADE-UP VALUES, NOT CLIENT DATA" in sql
    assert "INSERT INTO `main`.`dev`.`Customer` (`CustomerId`, `FullName`" in sql
    assert "DATE'2024-01-15'" in sql and "TRUE" in sql and "NULL" in sql


def test_tables_from_the_code_when_there_is_no_inventory(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "t.sql").write_text(
        "CREATE TABLE dbo.Item (ItemId INT NOT NULL, ItemName NVARCHAR(30), Price DECIMAL(10,2));", encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\n", encoding="utf-8")
    r = dummy_data.build(load_config(p))
    assert r["origin"] == "the CREATE TABLE statements in the code"
    rows = _rows(load_config(p), "dbo.Item")
    assert rows[1] == {"ItemId": "2", "ItemName": "Monitor", "Price": "99.99"}


def test_nothing_without_table_definitions(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\n", encoding="utf-8")
    assert dummy_data.build(load_config(p)) is None
