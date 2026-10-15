"""Deploy maps the warehouse's own schemas to the target and rebuilds the table scripts for today's catalog."""

from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.deploy import deploy_schema_map
from wishbridge.warehouses.ssis.rules import map_schemas, map_schemas_in_notebook


def _cfg(tmp_path, schema_map="{}"):
    (tmp_path / "database_code" / "dw" / "Views").mkdir(parents=True)
    (tmp_path / "project.yml").write_text(
        f"source: ssis\ndatabricks: {{catalog: workspace, schema: sales}}\nschema_map: {schema_map}\n", encoding="utf-8")
    return load_config(tmp_path / "project.yml")


def test_warehouse_schemas_go_to_the_target_unless_mapped(tmp_path):
    assert deploy_schema_map(_cfg(tmp_path)) == {"dw": "workspace.sales"}
    other = tmp_path / "other"
    other.mkdir()
    assert deploy_schema_map(_cfg(other, "{DW: hr.core}")) == {"DW": "hr.core"}


def test_names_are_mapped_in_sql_and_notebooks_not_in_comments_or_strings():
    smap = {"dw": "workspace.sales"}
    sql = "-- reads dw.Orders\nCREATE VIEW dw.v AS SELECT 'dw.x' AS s FROM [dw].Orders JOIN Sales.Customer c ON 1=1"
    out = map_schemas(sql, smap)
    assert "CREATE VIEW workspace.sales.v" in out and "FROM workspace.sales.Orders" in out
    assert "-- reads dw.Orders" in out and "'dw.x'" in out and "Sales.Customer" in out
    nb = 'x = 1\nq = f"""SELECT * FROM dw.FactDailySales"""\nspark.sql(q)\n'
    assert map_schemas_in_notebook(nb, smap) == 'x = 1\nq = f"""SELECT * FROM workspace.sales.FactDailySales"""\nspark.sql(q)\n'


def test_local_server_names_are_recognised():
    from wishbridge.warehouses.ssis.ui.helpers import is_local_host

    assert is_local_host(".\SQLEXPRESS") and is_local_host("localhost") and is_local_host("(local)")
    assert is_local_host("127.0.0.1,1433")
    assert not is_local_host("sqlprod01.client.com") and not is_local_host("")
