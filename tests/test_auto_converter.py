"""Automatic converter mode: try the other LakeBridge converter on files the main one left errors in."""

import pytest

from wishbridge import convert
from wishbridge.config import fallback_converter, load_config

BROKEN = "-- internal error\nSELECT * EXCEPT (email) FROM t;\n"   # main converter gave up on this file
CLEAN = "SELECT 1 AS one;\n"


def project(tmp_path, source, transpiler=None):
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / "project.yml"
    p.write_text(f"source: {source}\n" + (f"transpiler: {transpiler}\n" if transpiler else ""), encoding="utf-8")
    cfg = load_config(p)
    for name in ("a.sql", "b.sql"):
        (cfg.input_dir / name).parent.mkdir(parents=True, exist_ok=True)
        (cfg.input_dir / name).write_text("SELECT 1;\n", encoding="utf-8")
    raw = tmp_path / "output" / "converted"
    raw.mkdir(parents=True)
    (raw / "a.sql").write_text(BROKEN, encoding="utf-8")
    (raw / "b.sql").write_text(CLEAN, encoding="utf-8")
    return cfg, raw


def fake_transpile(result_text, calls):
    def transpile(cfg, out, log):
        calls.append(cfg)
        out.mkdir(parents=True, exist_ok=True)
        for f in cfg.input_dir.iterdir():
            (out / f.name).write_text(result_text, encoding="utf-8")
        log.write_text("", encoding="utf-8")
    return transpile


def test_defaults_and_overrides(tmp_path):
    assert fallback_converter(project(tmp_path / "1", "oracle")[0]) == "bladebridge"
    assert fallback_converter(project(tmp_path / "2", "teradata")[0]) == "morph"
    assert fallback_converter(project(tmp_path / "3", "snowflake")[0]) is None      # Morph only
    assert fallback_converter(project(tmp_path / "4", "oracle", "morph")[0]) is None  # converter fixed by the user


def test_better_fallback_result_is_kept_per_file(tmp_path, monkeypatch):
    cfg, raw = project(tmp_path, "oracle")
    calls = []
    monkeypatch.setattr(convert.lakebridge, "transpile", fake_transpile(CLEAN, calls))
    used = convert.choose_best_converter(cfg, raw, {})
    assert [c.transpiler for c in calls] == ["bladebridge"]
    assert [f.name for f in calls[0].input_dir.iterdir()] == ["a.sql"]   # only the file with errors is retried
    assert used == {"a.sql": "bladebridge", "b.sql": "morph"}
    assert (raw / "a.sql").read_text(encoding="utf-8") == CLEAN


def test_worse_fallback_result_is_ignored(tmp_path, monkeypatch):
    cfg, raw = project(tmp_path, "oracle")
    monkeypatch.setattr(convert.lakebridge, "transpile", fake_transpile(BROKEN + "SELECT ROWNUM FROM t;\n", []))
    assert convert.choose_best_converter(cfg, raw, {}) == {"a.sql": "morph", "b.sql": "morph"}
    assert (raw / "a.sql").read_text(encoding="utf-8") == BROKEN


def test_fewer_warnings_alone_do_not_switch_converter(tmp_path, monkeypatch):
    # main converter: 1 error + 1 warning; fallback: same error, no warning -> keep the main converter
    main = "CREATE SEQUENCE s START WITH 1;\n-- FIXME: Oracle SYSDATE uses the database server time zone\n"
    cfg, raw = project(tmp_path, "oracle")
    (raw / "a.sql").write_text(main, encoding="utf-8")
    monkeypatch.setattr(convert.lakebridge, "transpile", fake_transpile("CREATE SEQUENCE s START WITH 1;\n", []))
    assert convert.choose_best_converter(cfg, raw, {})["a.sql"] == "morph"


def test_oracle_date_columns_are_flagged():
    from wishbridge.rules import detect
    ddl = "CREATE TABLE t (\n    ID DECIMAL(10) NOT NULL,\n    CREATED_AT DATE DEFAULT current_date(),\n    D2 DATE\n);"
    f = [x for x in detect(ddl, "oracle") if x.rule == "oracle-date-column"]
    assert len(f) == 1 and f[0].line == 3 and "also line 4" in f[0].message
    assert not [x for x in detect("CREATE TABLE t (\n  C TIMESTAMP,\n  D DATE_FORMAT_X STRING\n);", "oracle")
                if x.rule == "oracle-date-column"]


def test_no_second_run_when_nothing_has_errors_or_converter_is_fixed(tmp_path, monkeypatch):
    monkeypatch.setattr(convert.lakebridge, "transpile", lambda *a: pytest.fail("must not run"))
    cfg, raw = project(tmp_path / "x", "oracle")
    (raw / "a.sql").write_text(CLEAN, encoding="utf-8")
    convert.choose_best_converter(cfg, raw, {})
    cfg, raw = project(tmp_path / "y", "oracle", "morph")
    convert.choose_best_converter(cfg, raw, {})
