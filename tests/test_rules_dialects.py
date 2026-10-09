"""Rules for Snowflake, Oracle and Teradata output, and the cross-dialect fixes."""

from pathlib import Path

from wishbridge.convert import _transpile_notes
from wishbridge.rules import apply_rules, detect


def rules(findings, fixed=None):
    return {f.rule for f in findings if fixed is None or f.fixed == fixed}


def by_rule(findings):
    return {f.rule: f for f in findings}


# ------------------------------------------------------------- cross-dialect fixes

def test_empty_create_shell_is_commented_out():
    sql = "SELECT 1;\nCREATE OR REPLACE /* STREAM A.S ON TABLE A.T */;\n-- FIXME: cannot currently convert the CREATE STREAM command\n"
    out, f = apply_rules(sql, {}, "snowflake")
    assert "-- [WishBridge] not converted - rewrite manually: CREATE STREAM A.S ON TABLE A.T" in out
    assert "CREATE OR REPLACE /*" not in out
    assert "empty-create" in rules(f, fixed=True)


def test_missing_semicolon_between_statements():
    sql = "INSERT INTO t VALUES (1, 'a')CREATE OR REPLACE PROCEDURE p() AS BEGIN END;"
    out, f = apply_rules(sql, {}, "oracle")
    assert "VALUES (1, 'a');\nCREATE OR REPLACE PROCEDURE" in out
    assert "missing-semicolon" in rules(f, fixed=True)


def test_untranslatable_notes_are_errors_with_guidance():
    f = detect("-- FIXME: SNOWFLAKE: Databricks SQL has no equivalent to the CREATE TASK command, and it cannot be translated\n", "snowflake")
    assert f[0].severity == "error"
    assert f[0].message.endswith("-> Databricks: schedule the statement as a Databricks Job (Lakeflow Jobs)")


def test_review_notes_stay_warnings_and_empty_fixme_gets_text():
    f = detect("SELECT 1 -- FIXME: Databricks does not support PRINT: PRINT 'x'\n", "mssql")
    assert f[0].severity == "warning" and "drop it" in f[0].message
    f = detect("SELECT 1 /*FIXME*/ FROM t\n", "oracle")
    assert f[0].message == "Transpiler marked this line for review"


def test_raise_error_placeholder():
    sql = "INSERT INTO t VALUES (RAISE_ERROR('Oracle S.NEXTVAL would advance and return sequence value'), 1);"
    assert "raise-error-placeholder" in rules(detect(sql, "oracle"))
    assert "raise-error-placeholder" not in rules(detect("SELECT RAISE_ERROR('bad input')", "oracle"))


# ---------------------------------------------------------------------- Snowflake

def test_snowflake_max_varchar_becomes_string():
    out, f = apply_rules("SELECT CAST(p:c AS VARCHAR(16777216)) AS c FROM t", {}, "snowflake")
    assert out == "SELECT CAST(p:c AS STRING) AS c FROM t"
    assert "snowflake-max-varchar" in rules(f, fixed=True)


def test_snowflake_detectors():
    sql = ("COPY INTO t FROM @RAW_STAGE/sales/;\n"
           "SELECT f.value FROM t, LATERAL FLATTEN(input => t.v) f;\n"
           "CREATE TABLE x (ts TIMESTAMP_LTZ);\n"
           "SELECT 'mail@example.com' AS e;\n")
    f = by_rule(detect(sql, "snowflake"))
    assert f["snowflake-stage"].line == 1 and "@RAW_STAGE/sales/" in f["snowflake-stage"].message
    assert f["snowflake-flatten"].line == 2
    assert f["snowflake-tz-timestamp"].line == 3
    assert sum(1 for x in detect(sql, "snowflake") if x.rule == "snowflake-stage") == 1  # not the e-mail in a string


# ------------------------------------------------------------------------- Oracle

def test_oracle_detectors():
    sql = """SELECT EMP_ID, ROWNUM FROM HR.E WHERE ROWNUM <= 10;
SELECT * FROM HR.E START WITH MGR IS NULL CONNECT BY PRIOR ID = MGR;
SELECT * FROM a, b WHERE a.id = b.id(+);
CREATE SEQUENCE S START WITH 1000;
INSERT INTO t VALUES (S.NEXTVAL);
BEGIN DBMS_OUTPUT.PUT_LINE('x' || SQL%ROWCOUNT); END;
SELECT TO_CHAR(d, 'YYYY-MM-DD HH24:MI') FROM t;
"""
    f = by_rule(detect(sql, "oracle"))
    assert f["rownum"].line == 1
    assert f["connect-by"].line == 2
    assert f["outer-join-plus"].line == 3
    assert f["sequence"].line == 4 and "also line 5" in f["sequence"].message
    assert f["plsql-package"].line == 6 and "DBMS_OUTPUT.PUT_LINE" in f["plsql-package"].message
    assert f["plsql-cursor-attr"].line == 6
    assert f["oracle-date-mask"].line == 7


def test_oracle_date_mask_ignores_java_patterns():
    assert "oracle-date-mask" not in rules(detect("SELECT DATE_FORMAT(d, 'yyyy-MM-dd HH:mm') FROM t", "oracle"))


def test_sequence_start_with_is_not_a_hierarchical_query():
    assert "connect-by" not in rules(detect("CREATE SEQUENCE S START WITH 1000 INCREMENT BY 1;", "oracle"))


# ----------------------------------------------------------------------- Teradata

def test_teradata_detectors():
    sql = """.LOGON tdpid/user,pwd
CREATE MULTISET VOLATILE TABLE vt AS (SEL * FROM t) WITH DATA PRIMARY INDEX (id);
SEL id FROM t;
COLLECT STATISTICS ON t COLUMN (id);
.QUIT
"""
    found = rules(detect(sql, "teradata"))
    assert {"bteq-command", "teradata-table-kind", "primary-index", "sel-abbrev", "collect-stats"} <= found


def test_dialect_rules_do_not_leak():
    sql = "SELECT ROWNUM, @@ROWCOUNT FROM t WITH (NOLOCK)"
    assert not rules(detect(sql, "snowflake")) & {"rownum", "system-variable"}


# ------------------------------------------------------------- transpile error log

def test_transpile_log_parsing(tmp_path: Path):
    log = tmp_path / "errors.log"
    log.write_text(
        "TranspileError(code=FAILURE, kind=PARSING, severity=ERROR, path='C:\\in\\a.sql', message='input is not parsable '(+'')\n"
        "TranspileError(code=FAILURE, kind=PARSING, severity=ERROR, path='C:\\in\\a.sql', message=''WHEN' was unexpected\n"
        "expecting one of: ASSIGN')\n"
        "TranspileError(code=None, kind=INTERNAL, severity=WARNING, path='C:\\in\\a.sql', message='No equivalent to X')\n"
        "TranspileError(code=None, kind=INTERNAL, severity=INFO, path='C:\\in\\b.sql', message='harmless')\n",
        encoding="utf-8")
    notes = _transpile_notes(log)
    assert list(notes) == ["a.sql"]
    assert notes["a.sql"][0].startswith("The converter could not parse 2 part(s) of this file")
    assert "'WHEN' was unexpected expecting one of: ASSIGN" in notes["a.sql"][0]
    assert notes["a.sql"][1] == "No equivalent to X"
