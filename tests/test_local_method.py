"""Data migration from a database on this computer: export, upload, copy with casts (no real database needed)."""

from wishbridge.warehouses.ssis import localdb
from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.data import build_plan
from wishbridge.warehouses.ssis.dbx import SqlResult

YML = """source: ssis
databricks: {catalog: workspace, schema: sales}
data:
  method: local
  files_root: /Volumes/workspace/sales/wishbridge_landing
  local: {server: '.\\SQLEXPRESS', database: DemoSalesDW, windows_login: true, trust: true}
  tables:
    - {source: dw.FactDailySales, target: workspace.sales.FactDailySales, where: "OrderDate >= '2026-01-01'"}
"""


def _cfg(tmp_path):
    (tmp_path / "project.yml").write_text(YML, encoding="utf-8")
    return load_config(tmp_path / "project.yml")


def test_local_method_loads_and_plans_a_forced_parquet_copy(tmp_path):
    cfg = _cfg(tmp_path)
    assert cfg.data_method == "local" and cfg.local_db["database"] == "DemoSalesDW"
    (_, stmts), = build_plan(cfg)
    assert stmts[-1].startswith("COPY INTO workspace.sales.FactDailySales FROM '/Volumes/workspace/sales/wishbridge_landing/")
    assert "FILEFORMAT = PARQUET" in stmts[-1] and "'force' = 'true'" in stmts[-1]


def test_copy_casts_every_column_to_the_table_type(tmp_path):
    cfg = _cfg(tmp_path)

    class Wh:
        def run(self, sql, catalog=None, schema=None):
            return SqlResult([["orderdate", "date", None], ["totalamount", "decimal(18,4)", None], ["#", "", ""]], [])

    sql = localdb.copy_into_sql(cfg, Wh(), cfg.tables[0], ["OrderDate", "TotalAmount", "Extra"])
    assert "CAST(`OrderDate` AS date) AS `orderdate`" in sql and "CAST(`TotalAmount` AS decimal(18,4))" in sql
    assert "Extra" not in sql and sql.endswith("COPY_OPTIONS ('force' = 'true')")


def test_volume_name_comes_from_the_folder():
    assert localdb.volume_of("/Volumes/workspace/sales/landing/sub") == "workspace.sales.landing"
