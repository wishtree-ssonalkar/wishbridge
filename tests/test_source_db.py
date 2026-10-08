"""Creating the source-database connection (Lakehouse Federation) - against stand-ins, no real database."""

from types import SimpleNamespace

import pytest

from wishbridge import source_db as sd
from wishbridge.config import load_config
from wishbridge.dbx import SqlError, SqlResult


class FakeSecrets:
    def __init__(self):
        self.scopes, self.secrets = set(), {}

    def list_scopes(self):
        return [SimpleNamespace(name=s) for s in self.scopes]

    def create_scope(self, scope):
        self.scopes.add(scope)

    def put_secret(self, scope, key, string_value):
        self.secrets[(scope, key)] = string_value

    def delete_secret(self, scope, key):
        self.secrets.pop((scope, key), None)


class FakeWarehouse:
    def __init__(self, rows=None, fail=None):
        self.calls, self.rows, self.fail = [], rows or [], fail
        self.w = SimpleNamespace(secrets=FakeSecrets())

    def run(self, sql, catalog=None, schema=None, timeout_s=None):
        self.calls.append(sql)
        if self.fail == "timeout" and "SHOW SCHEMAS" in sql:
            raise SqlError(f"Timed out after {timeout_s}s")
        if self.fail and self.fail in sql:
            raise SqlError("[CANNOT_ESTABLISH_CONNECTION] Cannot establish connection to remote database")
        return SqlResult(self.rows, [])


@pytest.fixture
def cfg(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("name: Acme DW\nsource: mssql\n", encoding="utf-8")
    return load_config(p)


def test_object_names(cfg):
    assert sd.object_names(cfg) == {"connection": "wb_acme_dw_conn", "catalog": "wb_acme_dw_source",
                                    "scope": "wishbridge", "key": "acme_dw-source-password"}


def test_connection_sql_uses_secret_never_the_password():
    sql = sd.connection_sql(sd.DB_TYPES["sqlserver"], "wb_x_conn", "sql01.client.com", 1433, "reader", "wishbridge", "x-pw")
    assert sql == ("CREATE CONNECTION `wb_x_conn` TYPE sqlserver\nOPTIONS (\n  host 'sql01.client.com',\n  port '1433',\n"
                   "  user 'reader',\n  password secret('wishbridge', 'x-pw')\n)")


def test_catalog_sql_per_database_type():
    assert sd.catalog_sql(sd.DB_TYPES["sqlserver"], "c", "k", "SalesDB") == \
        "CREATE FOREIGN CATALOG `c` USING CONNECTION `k` OPTIONS (database 'SalesDB')"
    assert sd.catalog_sql(sd.DB_TYPES["oracle"], "c", "k", "ORCLPDB1") == \
        "CREATE FOREIGN CATALOG `c` USING CONNECTION `k` OPTIONS (service_name 'ORCLPDB1')"
    assert sd.catalog_sql(sd.DB_TYPES["teradata"], "c", "k", "") == "CREATE FOREIGN CATALOG `c` USING CONNECTION `k`"
    with pytest.raises(SqlError, match="Database is required"):
        sd.catalog_sql(sd.DB_TYPES["redshift"], "c", "k", "")


def test_snowflake_needs_a_warehouse():
    with pytest.raises(SqlError, match="Snowflake warehouse is required"):
        sd.connection_sql(sd.DB_TYPES["snowflake"], "k", "acme.snowflakecomputing.com", 443, "u", "s", "p")
    sql = sd.connection_sql(sd.DB_TYPES["snowflake"], "k", "acme.snowflakecomputing.com", 443, "u", "s", "p",
                            {"sfWarehouse": "ETL_WH"})
    assert "sfWarehouse 'ETL_WH'" in sql


def test_values_are_quoted_and_names_validated():
    sql = sd.connection_sql(sd.DB_TYPES["mysql"], "k", "h", 3306, "o'brien", "s", "p")
    assert "user 'o\\'brien'" in sql
    with pytest.raises(SqlError):
        sd.catalog_sql(sd.DB_TYPES["mysql"], "bad name; DROP", "k", "")


def test_create_connection_stores_password_in_secrets_then_creates_objects(cfg):
    wh = FakeWarehouse()
    settings = {"type": "sqlserver", "host": "sql01.client.com", "port": 1433, "database": "SalesDB", "user": "reader"}
    names = sd.create_connection(cfg, settings, "s3cret!", wh)
    assert wh.w.secrets.secrets == {("wishbridge", "acme_dw-source-password"): "s3cret!"}
    assert wh.calls == [
        "DROP CATALOG IF EXISTS `wb_acme_dw_source` CASCADE",
        "DROP CONNECTION IF EXISTS `wb_acme_dw_conn`",
        sd.connection_sql(sd.DB_TYPES["sqlserver"], "wb_acme_dw_conn", "sql01.client.com", 1433, "reader",
                          "wishbridge", "acme_dw-source-password"),
        "CREATE FOREIGN CATALOG `wb_acme_dw_source` USING CONNECTION `wb_acme_dw_conn` OPTIONS (database 'SalesDB')",
    ]
    assert not any("s3cret" in c for c in wh.calls)
    assert names["catalog"] == "wb_acme_dw_source"


def test_create_connection_validates_input(cfg):
    with pytest.raises(SqlError, match="password"):
        sd.create_connection(cfg, {"type": "sqlserver", "host": "h", "user": "u", "database": "d"}, "", FakeWarehouse())
    with pytest.raises(SqlError, match="database type"):
        sd.create_connection(cfg, {"type": "db2", "host": "h", "user": "u"}, "p", FakeWarehouse())


def test_test_connection_and_list_tables():
    wh = FakeWarehouse(rows=[["sales"], ["information_schema"], ["dbo"]])
    assert sd.test_connection("wb_x_source", wh) == ["dbo", "sales"]
    wh = FakeWarehouse(rows=[["dbo", "Orders", False], ["dbo", "Customers", False]])
    assert sd.list_tables("wb_x_source", "dbo", wh) == ["Customers", "Orders"]
    with pytest.raises(SqlError, match="CANNOT_ESTABLISH_CONNECTION"):
        sd.test_connection("wb_x_source", FakeWarehouse(fail="SHOW SCHEMAS"))
    with pytest.raises(SqlError, match="cannot reach it over the network"):
        sd.test_connection("wb_x_source", FakeWarehouse(fail="timeout"))


def test_every_source_has_a_default_or_a_files_hint():
    from wishbridge.config import SOURCES
    for key in ("mssql", "synapse", "oracle", "snowflake", "teradata", "redshift"):
        assert sd.DEFAULT_FOR_SOURCE[key] in sd.DB_TYPES
    assert sd.DEFAULT_FOR_SOURCE["bigquery"] is None and sd.DEFAULT_FOR_SOURCE["netezza"] is None
    assert set(sd.DEFAULT_FOR_SOURCE) <= set(SOURCES)
