"""Files the converter never produced (it can stop part-way) must be reported, never silently dropped."""

from wishbridge import convert, lakebridge
from wishbridge.config import load_config


def test_fatal_error_line_is_extracted():
    out = ("13:02:50     INFO [d.l.l.transpiler.execute] Processed file: a.sql (errors: 0)\n"
           "13:02:56    ERROR [src/databricks/labs/lakebridge.transpile] UnicodeEncodeError: 'charmap' codec can't "
           "encode character '\\u2003'\nError: unexpected end of JSON input\n")
    assert lakebridge.fatal_error(out).startswith("UnicodeEncodeError: 'charmap' codec")
    assert lakebridge.fatal_error("INFO only\n") == ""


def test_converter_runs_in_utf8_mode(monkeypatch, tmp_path):
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw["env"])
        return type("P", (), {"returncode": 0, "stdout": "", "stderr": ""})()

    monkeypatch.setattr(lakebridge.subprocess, "run", fake_run)
    monkeypatch.setattr(lakebridge, "_databricks_cli", lambda: "databricks")
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\n", encoding="utf-8")
    lakebridge.run(load_config(p), "transpile")
    assert seen["PYTHONUTF8"] == "1" and seen["PYTHONIOENCODING"] == "utf-8"


def test_missing_outputs_become_needs_fix_entries(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("source: mssql\n", encoding="utf-8")
    cfg = load_config(p)
    for rel in ("Tables/a.sql", "Tables/b.sql", "Stored Procedure/c.sql", "readme.md"):
        f = cfg.input_dir / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("SELECT 1;", encoding="utf-8")
    from pathlib import Path
    entries = convert.not_converted_entries(cfg, {Path("Tables/a.sql")}, "UnicodeEncodeError: 'charmap'")
    assert [e["file"] for e in entries] == ["Stored Procedure/c.sql", "Tables/b.sql"]
    assert all(e["status"] == "needs-fix" and e["final"] == "" for e in entries)
    assert "It stopped with: UnicodeEncodeError" in entries[0]["findings"][0]["message"]


def test_etl_sources_are_not_checked_file_by_file(tmp_path):
    p = tmp_path / "project.yml"
    p.write_text("source: informatica\n", encoding="utf-8")
    cfg = load_config(p)
    (cfg.input_dir / "x.sql").parent.mkdir(parents=True, exist_ok=True)
    (cfg.input_dir / "x.sql").write_text("SELECT 1;", encoding="utf-8")
    assert convert.not_converted_entries(cfg, set(), "") == []
