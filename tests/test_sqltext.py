from wishbridge.sqltext import mask, split_statements, statement_kind


def test_mask_blanks_strings_and_comments_keeping_length():
    sql = "SELECT 'a;b' -- c;d\nFROM t /* x; */"
    m = mask(sql)
    assert len(m) == len(sql)
    assert ";" not in m
    assert m.count("\n") == 1


def test_split_simple_statements():
    assert split_statements("SELECT 1; SELECT 2;\n\n") == ["SELECT 1", "SELECT 2"]


def test_split_ignores_semicolons_in_strings_and_comments():
    stmts = split_statements("SELECT ';' AS x; -- a;b\nSELECT 2")
    assert stmts == ["SELECT ';' AS x", "-- a;b\nSELECT 2"]


def test_split_keeps_procedure_body_together():
    sql = """CREATE PROCEDURE p() LANGUAGE SQL AS BEGIN
      UPDATE t SET a = CASE WHEN b > 1 THEN 1 ELSE 0 END;
      IF x THEN SELECT 1; END IF;
      SELECT 2;
    END;
    SELECT 3;"""
    stmts = split_statements(sql)
    assert len(stmts) == 2
    assert stmts[0].startswith("CREATE PROCEDURE") and stmts[0].endswith("END")
    assert stmts[1] == "SELECT 3"


def test_comment_only_chunks_are_dropped():
    assert split_statements("SELECT 1;\n-- trailing note\n") == ["SELECT 1"]


def test_statement_kind():
    assert statement_kind("CREATE OR REPLACE TABLE t (a INT)") == "ddl"
    assert statement_kind("-- note\nWITH x AS (SELECT 1) SELECT * FROM x") == "query"
    assert statement_kind("INSERT INTO t SELECT 1") == "dml"
