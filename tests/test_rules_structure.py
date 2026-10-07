"""Structural checks found while testing every source end to end."""

from wishbridge.rules import apply_rules, count_statements, detect, dropped_statement_check


def rules(findings):
    return {f.rule for f in findings if not f.fixed}


def test_unbalanced_parentheses_from_broken_netezza_ddl():
    # BladeBridge's output for `) DISTRIBUTE ON (CUSTOMER_ID) ORGANIZE ON (ORDER_DATE);`
    sql = "CREATE TABLE t (\n  ORDER_ID INTEGER NOT NULL,\n  STATUS STRING\n) CUSTOMER_ID)  (ORDER_DATE);\n"
    f = [x for x in detect(sql, "netezza") if x.rule == "unbalanced-parentheses"]
    assert len(f) == 1 and f[0].severity == "error" and f[0].line == 1
    assert "unbalanced-parentheses" not in rules(detect("SELECT ')' AS p, f(x) FROM t; -- (\n", "netezza"))


def test_converter_left_whole_file_unconverted():
    sql = "-- internal error\n-- Multiple errors: ...\nSELECT * EXCEPT (email) FROM t;\n"
    assert "unconverted-file" in rules(detect(sql, "bigquery"))


def test_count_statements_ignores_nesting_and_comments():
    assert count_statements("DECLARE x INT; UPDATE t SET a = x;") == 2
    assert count_statements("BEGIN\n DECLARE x INT;\n UPDATE t SET a = x;\nEND;") == 3
    assert count_statements("SELECT 1; -- SELECT 2;\n/* SELECT 3; */ SELECT ';'") == 2


def test_silently_dropped_statement_is_flagged():
    src = "UPDATE t SET s = 1;\nGROOM TABLE t;\nGENERATE STATISTICS ON t;\n"
    out = "UPDATE t SET s = 1;\n\nGROOM TABLE t;\n"
    _, f = apply_rules(out, {}, "netezza")
    d = dropped_statement_check(src, out, f)
    assert d and d.severity == "warning" and d.message.startswith("1 statement(s)")


def test_statements_commented_with_a_note_are_not_reported_as_dropped():
    src = "UPDATE t SET s = 1;\nVACUUM t;\n"
    out = "UPDATE t SET s = 1;\n/* VACUUM t */\n-- FIXME: REDSHIFT: Databricks SQL has no equivalent to the Redshift VACUUM command\n"
    _, f = apply_rules(out, {}, "redshift")
    assert dropped_statement_check(src, out, f) is None


def test_new_dialect_rules():
    assert "netezza-groom" in rules(detect("GROOM TABLE SALES.ORDERS;", "netezza"))
    assert "netezza-age" in rules(detect("SELECT AGE(created_at) FROM c;", "netezza"))
    assert "date-minus-number" in rules(detect("SELECT * FROM o WHERE d < CURRENT_TIMESTAMP - 30;", "teradata"))
    assert "date-minus-number" not in rules(detect("SELECT * FROM o WHERE d < CURRENT_TIMESTAMP - INTERVAL 30 DAYS;", "teradata"))
    out, f = apply_rules("SELECT COUNT_BIG(*) FROM t", {}, "synapse")
    assert out == "SELECT COUNT(*) FROM t" and "count-big" in {x.rule for x in f if x.fixed}


def test_explicit_null_removed_only_in_column_definitions():
    sql = ("CREATE TABLE t (a INT NOT NULL, b VARCHAR(255) NULL, c STRING DEFAULT NULL, d INT NULL,\n"
           "  CONSTRAINT ck CHECK (d IS NULL OR d > 0));\n"
           "SELECT COALESCE(a, NULL), CASE WHEN a THEN NULL END FROM t WHERE b IS NULL;\n"
           "INSERT INTO t VALUES (1, NULL, NULL, NULL);")
    out, f = apply_rules(sql, {}, "synapse")
    assert "b VARCHAR(255)," in out and "d INT," in out
    assert "a INT NOT NULL" in out and "c STRING DEFAULT NULL" in out and "d IS NULL OR" in out
    assert out.split("\n", 2)[2] == sql.split("\n", 2)[2]  # the SELECT and INSERT are untouched
    assert {x.rule for x in f if x.fixed} == {"explicit-null"}


def test_partition_by_expression_is_flagged():
    assert "partition-expression" in rules(detect("CREATE TABLE o (d TIMESTAMP) PARTITIONED BY (CAST(d AS DATE));", "bigquery"))
    assert "partition-expression" not in rules(detect("CREATE TABLE o (d DATE) PARTITIONED BY (d);", "bigquery"))


def test_unload_and_vacuum_get_guidance():
    f = detect("-- FIXME: REDSHIFT: Databricks SQL has no equivalent to the UNLOAD command, and it cannot be translated\n", "redshift")
    assert f[0].severity == "error" and "INSERT OVERWRITE DIRECTORY" in f[0].message
