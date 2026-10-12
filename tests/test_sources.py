"""Each warehouse's file is complete and the shared tables are built from them."""

from wishbridge import config, inventory, rules, source_db, sources


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
