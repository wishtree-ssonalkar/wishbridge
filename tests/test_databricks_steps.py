"""Deploy / load / reconcile against a fake warehouse (no Databricks connection needed)."""

import re

import pytest

from wishbridge.config import load_config
from wishbridge.data import run_load
from wishbridge.dbx import SqlError, SqlResult
from wishbridge.deploy import or_replace, run_deploy
from wishbridge.reconcile import run_reconcile
from wishbridge.state import save_step


class FakeWarehouse:
    warehouse_id = "fake-wh"

    def __init__(self, fail_on=(), tables=None, errors=None):
        self.calls: list[str] = []
        self.fail_on = fail_on
        self.errors = errors or {}  # regex -> error message
        self.tables = tables or {}  # name -> (rowcount, {col: (type, sum or None)}, checksum)

    def run(self, sql, catalog=None, schema=None):
        self.calls.append(sql)
        for pat in self.fail_on:
            if re.search(pat, sql):
                raise SqlError(f"[TABLE_OR_VIEW_NOT_FOUND] boom for {pat}\nmore detail")
        for pat, msg in self.errors.items():
            if re.search(pat, sql):
                raise SqlError(msg)
        if sql.startswith("EXPLAIN"):
            return SqlResult([["== Physical Plan =="]], ["plan"])
        if sql.startswith("DESCRIBE TABLE"):
            cols = self.tables[sql.split()[-1]][1]
            return SqlResult([[c, t, None] for c, (t, _) in cols.items()] + [["", "", ""], ["# Partitioning", "", ""]], [])
        m = re.match(r"SELECT COUNT\(\*\).* FROM (\S+)$", sql)
        if m:
            count, cols, checksum = self.tables[m.group(1)]
            sums = [str(v) for _, v in cols.values() if v is not None]
            return SqlResult([[str(count)] + sums + [str(checksum)]], [])
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


def test_deploy_keeps_existing_objects_on_rerun(cfg):
    wh = FakeWarehouse(errors={r"^CREATE TABLE": "[TABLE_OR_VIEW_ALREADY_EXISTS] Cannot create table"})
    res = run_deploy(cfg, wh=wh)
    first = res["files"][0]["results"][0]
    assert first["ok"] and first["action"] == "exists"
    assert res["summary"]["kept_existing"] == 1


def test_deploy_recreate_uses_or_replace(cfg):
    wh = FakeWarehouse()
    run_deploy(cfg, recreate=True, wh=wh)
    assert wh.calls[1].startswith("CREATE OR REPLACE TABLE main.dev.Orders")


def test_or_replace_rewrite():
    assert or_replace("-- note\nCREATE\n  PROCEDURE p() AS BEGIN END") == "-- note\nCREATE OR REPLACE PROCEDURE p() AS BEGIN END"
    assert or_replace("CREATE OR REPLACE VIEW v AS SELECT 1") == "CREATE OR REPLACE VIEW v AS SELECT 1"
    assert or_replace("CREATE TEMPORARY VIEW v AS SELECT 1") == "CREATE TEMPORARY VIEW v AS SELECT 1"
    assert or_replace("CREATE SCHEMA s") == "CREATE SCHEMA s"


def test_load_plan_only_by_default(cfg):
    wh = FakeWarehouse()
    res = run_load(cfg, wh=wh)
    assert wh.calls == []
    assert res["tables"][0]["status"] == "planned"
    assert "INSERT INTO main.dev.Orders BY NAME SELECT * FROM fed.dbo.Orders;" in open(res["plan_file"]).read()


def test_load_execute_records_rows(cfg):
    res = run_load(cfg, execute=True, wh=FakeWarehouse())
    assert res["tables"][0] == {**res["tables"][0], "status": "loaded", "rows": 7}


SRC_COLS = {"id": ("int", "10"), "amt": ("decimal(19,4)", "5.5"), "name": ("string", None)}
TGT_COLS = {"id": ("bigint", "10"), "amt": ("decimal(19,4)", "5.50"), "name": ("string", None)}


def test_reconcile_match_and_mismatch(cfg):
    wh = FakeWarehouse(tables={"main.dev.Orders": (10, TGT_COLS, 123), "fed.dbo.Orders": (10, SRC_COLS, 123)})
    t = run_reconcile(cfg, wh=wh)["tables"][0]
    assert t["status"] == "match"
    assert [c["check"] for c in t["checks"]] == ["row_count", "sum(id)", "sum(amt)", "row_checksum(3 columns)"]
    assert "xxhash64(CAST(`id` AS STRING), CAST(`amt` AS STRING), CAST(`name` AS STRING))" in wh.calls[-1]

    wh = FakeWarehouse(tables={"main.dev.Orders": (9, TGT_COLS, 123), "fed.dbo.Orders": (10, SRC_COLS, 123)})
    t = run_reconcile(cfg, wh=wh)["tables"][0]
    assert t["status"] == "mismatch"
    assert t["checks"][0] == {"check": "row_count", "source": "10", "target": "9", "match": False}


def test_reconcile_checksum_catches_text_changes(cfg):
    wh = FakeWarehouse(tables={"main.dev.Orders": (10, TGT_COLS, 999), "fed.dbo.Orders": (10, SRC_COLS, 123)})
    t = run_reconcile(cfg, wh=wh)["tables"][0]
    assert t["status"] == "mismatch"
    assert [c["check"] for c in t["checks"] if not c["match"]] == ["row_checksum(3 columns)"]


def test_reconcile_reports_column_differences(cfg):
    src = {**SRC_COLS, "legacy_flag": ("string", None)}
    wh = FakeWarehouse(tables={"main.dev.Orders": (10, TGT_COLS, 1), "fed.dbo.Orders": (10, src, 1)})
    t = run_reconcile(cfg, wh=wh)["tables"][0]
    assert t["column_differences"] == {"only_in_source": ["legacy_flag"], "only_in_target": []}
