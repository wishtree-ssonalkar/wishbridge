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


def test_an_empty_table_is_not_copied_from_typeless_parquet(tmp_path, monkeypatch):
    from wishbridge.warehouses.ssis.data import run_load

    cfg = _cfg(tmp_path)
    ran = []

    class Wh:
        def run(self, sql, catalog=None, schema=None):
            ran.append(sql)
            return SqlResult([], [])

    monkeypatch.setattr(localdb, "ensure_volume", lambda c, w: None)
    monkeypatch.setattr(localdb, "export_upload", lambda c, w, source, where="": (0, []))
    entry, = run_load(cfg, execute=True, wh=Wh())["tables"]
    assert not [s for s in ran if s.startswith("COPY INTO")]
    assert entry["status"] == "loaded" and entry["rows"] == 0


def test_volume_name_comes_from_the_folder():
    assert localdb.volume_of("/Volumes/workspace/sales/landing/sub") == "workspace.sales.landing"


def test_mapping_lists_the_local_database_not_federation(tmp_path, monkeypatch):
    from wishbridge.warehouses.ssis import mapping

    cfg = _cfg(tmp_path)
    monkeypatch.setattr(localdb, "list_tables", lambda c: ["dw.DimCustomer", "dw.FactDailySales"])
    assert mapping.source_tables(cfg) == ["dw.DimCustomer", "dw.FactDailySales"]


def test_settings_test_buttons_report_clearly(monkeypatch):
    from wishbridge.warehouses.ssis.ui import helpers as h

    def refuse(cfg, password=None):
        raise RuntimeError("[28000] Login failed for user 'PC\\me'. (18456) (SQLDriverConnect)")

    monkeypatch.setattr(localdb, "connect", refuse)
    ok, msg = h.test_local_db({"server": "srv", "database": "NoSuchDb"})
    assert not ok and "check the database name" in msg
    assert not h.check_volume("DEFAULT", "/tmp/export")[0]  # must be a Unity Catalog volume
