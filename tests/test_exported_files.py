"""Copy and compare from files the client exported (data.method: files), with a stand-in workspace."""

from wishbridge.warehouses.ssis import exported_files as ef
from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.dbx import SqlError, SqlResult

YML = """source: ssis
databricks: {catalog: test, schema: sales}
data:
  method: files
  mode: overwrite
  files_root: /Volumes/test/landing/files
  tables:
    - {source: dw.DimDate, target: test.sales.dimdate}
"""


class Wh:
    def __init__(self, missing=False):
        self.missing, self.sql = missing, []

    def run(self, sql, catalog=None, schema=None):
        self.sql.append(sql)
        if sql.startswith("DESCRIBE QUERY SELECT * FROM read_files"):
            if self.missing:
                raise SqlError("[PATH_NOT_FOUND] Path does not exist: dbfs:/Volumes/test/landing/files/DimDate")
            return SqlResult([["DateKey", "bigint", None], ["FullDate", "timestamp", None], ["_rescued_data", "string", None]], [])
        if sql.startswith("DESCRIBE TABLE"):
            return SqlResult([["datekey", "int", None], ["fulldate", "date", None], ["# Partition", "", ""]], [])
        return SqlResult([], [])


def _cfg(tmp_path):
    (tmp_path / "project.yml").write_text(YML, encoding="utf-8")
    return load_config(tmp_path / "project.yml")


def test_exported_files_are_copied_with_the_tables_types(tmp_path):
    cfg = _cfg(tmp_path)
    sql = ef.copy_sql(cfg, Wh(), cfg.tables[0])
    assert "CAST(`DateKey` AS int) AS `datekey`" in sql and "CAST(`FullDate` AS date) AS `fulldate`" in sql
    assert "FROM '/Volumes/test/landing/files/DimDate/'" in sql and "'force' = 'true'" in sql  # Replace reads again


def test_a_table_without_files_says_so(tmp_path):
    import pytest

    cfg = _cfg(tmp_path)
    with pytest.raises(SqlError, match="No exported files in /Volumes/test/landing/files/DimDate/"):
        ef.copy_sql(cfg, Wh(missing=True), cfg.tables[0])


def test_the_comparison_reads_the_files_as_the_table_would(tmp_path):
    cfg = _cfg(tmp_path)
    q = ef.source_query(cfg, Wh(), cfg.tables[0])
    assert q.startswith("(SELECT CAST(`DateKey` AS int) AS `datekey`") and "read_files('/Volumes/test/landing/files/DimDate/'" in q
