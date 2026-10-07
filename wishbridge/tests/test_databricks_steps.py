"""Deploy / load / reconcile against a fake warehouse (no Databricks connection needed)."""

import re

import pytest

from wishbridge.config import load_config
from wishbridge.data import run_load
from wishbridge.dbx import SqlError, SqlResult
from wishbridge.deploy import run_deploy
from wishbridge.reconcile import run_reconcile
from wishbridge.state import save_step


class FakeWarehouse:
    warehouse_id = "fake-wh"

    def __init__(self, fail_on=(), tables=None):
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.tables = tables or {}  # name -> (rowcount, {col: sum})

    def run(self, sql, catalog=None, schema=None):
        self.calls.append(sql)
        for pat in self.fail_on:
            if re.search(pat, sql):
                raise SqlError(f"[TABLE_OR_VIEW_NOT_FOUND] boom for {pat}\nmore detail")
        if sql.startswith("EXPLAIN"):
            return SqlResult([["== Physical Plan =="]], ["plan"])
        if sql.startswith("DESCRIBE TABLE"):
            name = sql.split()[-1]
            cols = self.tables[name][1]
            return SqlResult([[c, "decimal(19,4)", None] for c in cols] + [["", "", ""], ["# Partitioning", "", ""]], [])
        m = re.match(r"SELECT COUNT\(\*\).* FROM (\S+)$", sql)
        if m:
            name = m.group(1)
            count, sums = self.tables[name]
            return SqlResult([[str(count)] + [str(v) for v in sums.values()]], [])
        if sql.startswith("INSERT"):
            return SqlResult([["7", "7"]], ["num_affected_rows", "num_inserted_rows"])
        return SqlResult([], [])


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("""
source: mssql
databricks: {catalog: main, schema: dev}
data: {source_catalog: fed, tables: [dbo.Orders]}
""", encoding="utf-8")
    c = load_config(p)
    final = c.out("final", "a.sql")
    final.write_text("CREATE TABLE main.dev.Orders (id INT);\nSELECT * FROM main.dev.Orders;\nUPDATE main.dev.Orders SET id = 1;\n"
                     "SELECT * FROM missing_table;\n", encoding="utf-8")
    save_step(c, "convert", {"files": [{"file": "a.sql", "final": str(final)}]})
    return c


def test_deploy_runs_ddl_and_explains_the_rest(cfg):
    wh = FakeWarehouse(fail_on=[r"missing_table"])
    res = run_deploy(cfg, wh=wh)
    assert wh.calls[0] == "CREATE SCHEMA IF NOT EXISTS main.dev"
    assert wh.calls[1].startswith("CREATE TABLE")
    assert wh.calls[2].startswith("EXPLAIN SELECT")
    assert wh.calls[3].startswith("EXPLAIN UPDATE")  # DML not executed by default
    f = res["files"][0]
    assert (f["statements"], f["passed"], f["ok"]) == (4, 3, False)
    assert f["results"][3]["error"] == "[TABLE_OR_VIEW_NOT_FOUND] boom for missing_table"


def test_deploy_refuses_prod_schema(cfg):
    cfg.schema = "prod_sales"
    with pytest.raises(SqlError, match="production"):
        run_deploy(cfg, wh=FakeWarehouse())


def test_load_plan_only_by_default(cfg):
    wh = FakeWarehouse()
    res = run_load(cfg, wh=wh)
    assert wh.calls == []
    assert res["tables"][0]["status"] == "planned"
    assert "INSERT INTO main.dev.Orders BY NAME SELECT * FROM fed.dbo.Orders;" in open(res["plan_file"]).read()


def test_load_execute_records_rows(cfg):
    res = run_load(cfg, execute=True, wh=FakeWarehouse())
    assert res["tables"][0] == {**res["tables"][0], "status": "loaded", "rows": 7}


def test_reconcile_match_and_mismatch(cfg):
    wh = FakeWarehouse(tables={"main.dev.Orders": (10, {"amt": "5.5"}), "fed.dbo.Orders": (10, {"amt": "5.50"})})
    assert run_reconcile(cfg, wh=wh)["tables"][0]["status"] == "match"
    wh = FakeWarehouse(tables={"main.dev.Orders": (9, {"amt": "5.5"}), "fed.dbo.Orders": (10, {"amt": "5.5"})})
    t = run_reconcile(cfg, wh=wh)["tables"][0]
    assert t["status"] == "mismatch" and t["checks"][0] == {"check": "row_count", "source": "10", "target": "9", "match": False}
