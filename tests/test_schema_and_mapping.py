"""Databricks table scripts from the database catalog, and the data mapping used by the copy."""

import pytest

from wishbridge.config import load_config
from wishbridge.data import build_plan
from wishbridge.dbx import SqlError, SqlResult
from wishbridge.inventory import connection_string, import_file
from wishbridge.mapping import check, compare_columns, propose
from wishbridge.schema import databricks_type
from wishbridge.ui.helpers import rows_to_tables

CSV = """schema_name,table_name,column_name,ordinal,data_type,max_length,precision,scale,is_nullable,row_count,size_mb
dbo,Orders,OrderId,1,int,4,10,0,NO,100,1.5
dbo,Orders,Amount,2,money,8,19,4,YES,100,1.5
dbo,Orders,Note,3,nvarchar,-1,0,0,YES,100,1.5
dbo,Orders,Odd,4,weirdtype,8,0,0,YES,100,1.5
dbo,Customers,Id,1,int,4,10,0,NO,5,0.1
"""


@pytest.fixture
def cfg(tmp_path):
    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "tables.sql").write_text("CREATE TABLE dbo.Customers (Id INT NOT NULL);\n", encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\ndatabricks: {catalog: main, schema: dev}\ndata: {source_catalog: fed, tables: []}\n",
                 encoding="utf-8")
    return load_config(p)


def _import(cfg, tmp_path):
    csv = tmp_path / "inv.csv"
    csv.write_text(CSV, encoding="utf-8")
    return import_file(cfg, csv, "sqlserver")


@pytest.mark.parametrize("db,t,p,s,want", [
    ("sqlserver", "int", 10, 0, "INT"), ("sqlserver", "tinyint", None, None, "SMALLINT"),
    ("sqlserver", "decimal", 18, 2, "DECIMAL(18,2)"), ("sqlserver", "datetime2", None, None, "TIMESTAMP"),
    ("sqlserver", "timestamp", None, None, "BINARY"), ("sqlserver", "bit", None, None, "BOOLEAN"),
    ("oracle", "NUMBER", None, None, "DECIMAL(38,10)"), ("oracle", "DATE", None, None, "TIMESTAMP"),
    ("oracle", "VARCHAR2", None, None, "STRING"), ("snowflake", "NUMBER", 38, 0, "DECIMAL(38,0)"),
    ("teradata", "CV", None, None, "STRING"), ("teradata", "D", 12, 2, "DECIMAL(12,2)"),
    ("netezza", "NUMERIC(10,2)", None, None, "DECIMAL(10,2)"), ("redshift", "character varying", None, None, "STRING"),
    ("bigquery", "INT64", None, None, "BIGINT"), ("postgresql", "timestamp with time zone", None, None, "TIMESTAMP"),
])
def test_types(db, t, p, s, want):
    assert databricks_type(db, t, p, s) == (want, True)


def test_unknown_type_is_string_and_flagged():
    assert databricks_type("sqlserver", "weirdtype") == ("STRING", False)


def test_scripts_skip_tables_the_code_creates(cfg, tmp_path):
    from wishbridge.state import load_state

    _import(cfg, tmp_path)
    sch = load_state(cfg)["schema"]
    assert sch["tables"] == 1 and sch["skipped"] == ["dbo.Customers"]
    text = open(sch["files"][0]["path"], encoding="utf-8").read()
    assert "CREATE TABLE IF NOT EXISTS `main`.`dev`.`Orders` (" in text
    assert "INT NOT NULL," in text and "DECIMAL(19,4)" in text
    assert "`Note`    STRING,\n  `Odd`     STRING  -- review: source type weirdtype\n);" in text  # no comma after a comment
    assert sch["unknown_types"] == {"dbo.Orders": ["Odd (weirdtype)"]}


def test_convert_puts_database_tables_first(cfg, tmp_path):
    from wishbridge.convert import schema_entries

    _import(cfg, tmp_path)
    entries = schema_entries(cfg, cfg.output_dir / "final")
    assert [e["file"] for e in entries] == ["_database_schema/dbo.sql"]
    assert entries[0]["converter"] == "database"
    assert (cfg.output_dir / "final" / "_database_schema" / "dbo.sql").is_file()


def test_connection_string_never_needs_a_saved_password():
    s = connection_string("srv", "db", True, driver="ODBC Driver 18 for SQL Server")
    assert "Trusted_Connection=yes" in s and "PWD" not in s and "ApplicationIntent=ReadOnly" in s
    s = connection_string("srv", "db", False, "u", "p;x}", True, driver="ODBC Driver 18 for SQL Server")
    assert "PWD={p;x}}}" in s and "TrustServerCertificate=yes" in s


def test_propose_pairs_existing_tables(cfg):
    rows = propose(cfg, ["dbo.Orders", "sales.Items", "dbo.New"], ["main.dev.orders", "main.other.Items"])
    assert rows[0] == {"copy": True, "source": "dbo.Orders", "target": "main.dev.orders", "exists": True}
    assert rows[1]["target"] == "main.other.Items" and rows[1]["exists"]
    assert rows[2] == {"copy": True, "source": "dbo.New", "target": "main.dev.New", "exists": False}


def test_compare_columns_uses_saved_mapping():
    src = {"id": "int", "cust_name": "string", "extra": "string"}
    tgt = {"id": "int", "customer_name": "string"}
    r = compare_columns(src, tgt)
    assert r["pairs"] == {"id": "id"} and r["target_without_source"] == ["customer_name"]
    r = compare_columns(src, tgt, {"customer_name": "cust_name"})
    assert r["pairs"] == {"id": "id", "customer_name": "cust_name"} and r["source_not_copied"] == ["extra"]


class Wh:
    def __init__(self, tables):
        self.tables = tables

    def run(self, sql, catalog=None, schema=None):
        name = sql.split()[-1]
        if name not in self.tables:
            raise SqlError("[TABLE_OR_VIEW_NOT_FOUND] no")
        return SqlResult([[c, "int", None] for c in self.tables[name]], [])


def test_check_and_load_with_column_mapping(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("""source: mssql
databricks: {catalog: main, schema: dev}
data:
  source_catalog: fed
  tables:
    - {source: dbo.A, target: main.dev.a, columns: {customer_name: cust_name, id: id}}
    - {source: dbo.B, target: main.dev.b}
    - {source: dbo.C, target: main.dev.missing}
""", encoding="utf-8")
    cfg = load_config(p)
    wh = Wh({"main.dev.a": ["id", "customer_name"], "fed.dbo.A": ["id", "cust_name"],
             "main.dev.b": ["id"], "fed.dbo.B": ["id", "x"]})
    res = {r["source"]: r for r in check(cfg, wh)}
    assert res["dbo.A"]["status"] == "ready"
    assert res["dbo.B"]["status"] == "check columns" and res["dbo.B"]["source_not_copied"] == ["x"]
    assert res["dbo.C"]["status"] == "no target table"
    plan = {t.source: s for t, s in build_plan(cfg)}
    assert plan["dbo.A"] == ["INSERT INTO main.dev.a (`customer_name`, `id`) SELECT `cust_name` AS `customer_name`, "
                             "`id` AS `id` FROM fed.dbo.A"]
    assert plan["dbo.B"] == ["INSERT INTO main.dev.b BY NAME SELECT * FROM fed.dbo.B"]


def test_saving_the_table_list_keeps_column_mappings():
    prev = [{"source": "dbo.A", "target": "x.y.a", "columns": {"c": "d"}}, "dbo.B"]
    out = rows_to_tables([{"source": "dbo.A", "target": "x.y.a2"}, {"source": "dbo.B", "target": ""}], prev)
    assert out == [{"source": "dbo.A", "target": "x.y.a2", "columns": {"c": "d"}}, "dbo.B"]
