"""Data migration: filters, copying some tables again, the check before copying, the sign-off file."""

import zipfile

import pytest

from wishbridge.warehouses.sqlserver.config import load_config
from wishbridge.warehouses.sqlserver.data import build_plan, run_load
from wishbridge.warehouses.sqlserver.dbx import SqlError, SqlResult
from wishbridge.warehouses.sqlserver.mapping import again_targets, preflight
from wishbridge.warehouses.sqlserver.package import build_signoff
from wishbridge.warehouses.sqlserver.state import load_state, save_step


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("""source: mssql
phase: data-migration
databricks: {catalog: main, schema: dev}
data:
  source_catalog: fed
  mode: overwrite
  tables:
    - {source: dbo.A, target: main.dev.a}
    - {source: dbo.B, target: main.dev.b, where: "ModifiedDate >= '2026-10-01'"}
""", encoding="utf-8")
    return load_config(p)


class Wh:
    def __init__(self, fail=(), missing=(), counts=None, unreachable=False):
        self.calls, self.fail, self.missing, self.counts, self.unreachable = [], fail, missing, counts or {}, unreachable

    def run(self, sql, catalog=None, schema=None, timeout_s=None):
        self.calls.append(sql)
        if sql.startswith("SHOW SCHEMAS"):
            if self.unreachable:
                raise SqlError("[FAILED_JDBC] Failed to connect")
            return SqlResult([["dbo"]], ["databaseName"])
        if sql.startswith("DESCRIBE TABLE"):
            if sql.split()[-1] in self.missing:
                raise SqlError("[TABLE_OR_VIEW_NOT_FOUND]")
            return SqlResult([["id", "int", None]], [])
        if sql.startswith("SELECT COUNT(*)"):
            return SqlResult([[self.counts.get(sql, 10)]], ["count"])
        if any(f in sql for f in self.fail):
            raise SqlError("[DELTA_FAILED] boom")
        if sql.startswith("INSERT"):
            return SqlResult([["5", "5"]], ["num_affected_rows", "num_inserted_rows"])
        return SqlResult([], [])


def test_a_filter_copies_only_matching_rows_and_never_empties_the_table(cfg):
    plan = {t.target: s for t, s in build_plan(cfg)}
    assert plan["main.dev.a"][0] == "TRUNCATE TABLE main.dev.a"  # overwrite: whole table replaced
    assert plan["main.dev.b"] == ["INSERT INTO main.dev.b BY NAME SELECT * FROM fed.dbo.B WHERE ModifiedDate >= '2026-10-01'"]


def test_progress_per_table_and_copy_again_only_some(cfg):
    seen = []
    first = run_load(cfg, execute=True, wh=Wh(fail=("main.dev.b",)), progress=lambda d, t, n: seen.append((d, t, n)))
    assert seen == [(0, 2, "main.dev.a"), (1, 2, "main.dev.b")]
    assert [t["status"] for t in first["tables"]] == ["loaded", "failed"]
    assert again_targets(load_state(cfg)) == ["main.dev.b"]
    wh = Wh()
    second = run_load(cfg, execute=True, wh=wh, only=["main.dev.b"])
    assert not any("main.dev.a" in c for c in wh.calls)  # only the failed table is copied again
    assert {t["target"]: t["status"] for t in second["tables"]} == {"main.dev.a": "loaded", "main.dev.b": "loaded"}
    assert again_targets(load_state(cfg)) == []


def test_tables_that_differ_from_the_source_are_copied_again(cfg):
    save_step(cfg, "reconcile", {"mode": "quick", "tables": [{"target": "main.dev.a", "status": "match"},
                                                            {"target": "main.dev.b", "status": "mismatch"}]})
    assert again_targets(load_state(cfg)) == ["main.dev.b"]


def test_check_before_copying(cfg):
    ok = preflight(cfg, Wh())
    assert ok["source_ok"] and ok["ready"] and ok["missing_targets"] == []
    assert [r["rows"] for r in ok["tables"]] == [10, 10] and ok["total_rows"] == 20
    bad = preflight(cfg, Wh(missing=("main.dev.b",), unreachable=True))
    assert not bad["source_ok"] and not bad["ready"] and bad["missing_targets"] == ["main.dev.b"]


def test_sign_off_file_has_no_data(cfg):
    run_load(cfg, execute=True, wh=Wh())
    save_step(cfg, "reconcile", {"mode": "quick", "summary": {"tables": 2, "matched": 2}, "tables": [
        {"target": "main.dev.a", "status": "match", "checks": []}, {"target": "main.dev.b", "status": "match", "checks": []}]})
    with zipfile.ZipFile(build_signoff(cfg)) as z:
        names = set(z.namelist())
        assert {"README.txt", "report.html", "copied_tables.csv", "comparison.csv", "mapping.yml", "load_plan.sql"} <= names
        assert "main.dev.b" in z.read("copied_tables.csv").decode() and "No data" in z.read("README.txt").decode()
        assert "Data migration report" in z.read("report.html").decode()


def test_no_sign_off_before_a_copy(cfg):
    with pytest.raises(ValueError, match="No data has been copied"):
        build_signoff(cfg)
