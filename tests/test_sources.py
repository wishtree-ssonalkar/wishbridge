"""Each warehouse's file is complete and the shared tables are built from them."""

from wishbridge.warehouses.sqlserver import config, inventory, rules, source_db, sources


def test_every_source_has_a_label_a_database_and_a_converter():
    for key, s in sources.SOURCES.items():
        assert sources.LABELS[key], key
        assert sources.DEFAULT_DB[key] in sources.INVENTORY_QUERIES, key  # its table-list query exists
        assert s.dialect in config.CONVERTER_DIALECTS[s.transpiler], key
        fed = sources.FEDERATION_FOR.get(key)
        assert fed is None or fed in sources.FEDERATION, key


def test_etl_tools_are_the_files_with_etl_targets():
    assert config.ETL_SOURCES == {"datastage", "informatica", "informatica-cloud", "ssis"}


def test_shared_modules_read_the_warehouse_files():
    assert config.SOURCES is sources.SOURCES
    assert inventory.QUERIES["oracle"] == sources.oracle.INVENTORY_QUERY
    assert source_db.DB_TYPES["snowflake"] is sources.snowflake.FEDERATION
    ids = [d.rule for d in rules._DETECTORS]
    assert ids[:len(sources.DETECTORS)] == [d.rule for d in sources.DETECTORS]  # warehouse checks, then shared ones
    assert {"rownum", "temp-table", "snowflake-flatten", "bteq-command", "netezza-groom"} <= set(ids)
    assert [f[0] for f in rules._SIMPLE_FIXES][-1] == "missing-semicolon"  # the shared fix comes last


def test_each_warehouse_has_its_own_screens():
    from types import SimpleNamespace

    from wishbridge.warehouses.sqlserver.ui.screens import default, etl, ssis, warehouse_ui

    def ui(key):
        return warehouse_ui(SimpleNamespace(source=SimpleNamespace(key=key)))

    assert ui("ssis") is ssis and ui("informatica") is etl and ui("datastage") is etl
    assert ui("mssql") is default and ui("oracle") is default  # SSIS screens never reach other warehouses
    for module in (default, etl, ssis):  # every file offers what the steps call
        for name in ("ASSESS_TOGETHER", "READ_CODE_DEFAULT", "database_text", "open_database_section",
                     "code_extras", "report_extras"):
            assert hasattr(module, name), (module.__name__, name)
