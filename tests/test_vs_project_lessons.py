"""Lessons from a real Visual Studio (SSDT) SQL Server 2022 project."""

from pathlib import Path

from wishbridge.config import load_config
from wishbridge.convert import _score
from wishbridge.rules import Finding, detect, dropped_statement_check
from wishbridge.staging import source_files, staged


def rules(findings):
    return {f.rule for f in findings if not f.fixed}


def test_build_and_tool_folders_are_not_given_to_lakebridge(tmp_path):
    code = tmp_path / "DatabaseProject"
    for rel in ("dbo/Tables/Users.sql", "Script.PostDeployment.sql", "obj/Debug/postdeploy.sql", "obj/Debug/Model.xml",
                "bin/Debug/x.sql", ".vs/x.sql", "PRM.publish.xml", "PRM.sqlproj"):
        (code / rel).parent.mkdir(parents=True, exist_ok=True)
        (code / rel).write_text("SELECT 1;", encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text(f"source: mssql\ninput: '{code.as_posix()}'\n", encoding="utf-8")
    cfg = load_config(p)
    assert {p.as_posix() for p in source_files(cfg)} == {"Script.PostDeployment.sql", "dbo/Tables/Users.sql"}
    s = staged(cfg)
    assert sorted(f.relative_to(s.input_dir).as_posix() for f in s.input_dir.rglob("*") if f.is_file()) == \
        ["Script.PostDeployment.sql", "dbo/Tables/Users.sql"]
    assert (code / "obj" / "Debug" / "Model.xml").exists()  # the client's folder is untouched


def test_full_text_search_is_flagged():
    sql = ("CREATE FULLTEXT INDEX ON dbo.Users (FirstName) KEY INDEX PK_Users;\n"
           "SELECT * FROM Users U WHERE CONTAINS(U.FirstName, V_ResourceName);\n")
    f = [x for x in detect(sql, "mssql") if x.rule == "full-text-search"]
    assert [x.line for x in f] == [1, 2] and all(x.severity == "error" for x in f)
    assert "substring test" in f[1].message


def test_temporal_tables_and_system_catalog_are_flagged():
    sql = ("CREATE TABLE t (ValidFrom DATETIME2 GENERATED ALWAYS AS ROW START, PERIOD FOR SYSTEM_TIME (a, b))\n"
           "WITH (SYSTEM_VERSIONING = ON);\n"
           "IF NOT EXISTS (SELECT 1 FROM sys.fulltext_catalogs WHERE OBJECT_ID('dbo.x') IS NULL) SELECT 1;\n")
    assert {"temporal-table", "system-catalog"} <= rules(detect(sql, "mssql"))


def test_session_settings_do_not_count_as_dropped_statements():
    src = "CREATE PROCEDURE p AS BEGIN\n    SET NOCOUNT ON;\n    SELECT 1;\nEND\nGO\nSET ANSI_NULLS ON;\nGO\n"
    out = ("CREATE PROCEDURE p() AS BEGIN\n    -- SET NOCOUNT ON - Databricks SQL does not return row count messages\n"
           "    SELECT 1;\nEND;\n")
    assert dropped_statement_check(src, out, []) is None


def test_empty_fixme_next_to_an_explained_issue_is_dropped():
    f = detect("SELECT TOP 3 /*FIXME*/ a FROM t;\n", "mssql")
    assert rules(f) == {"top-clause"}


def test_scoring_counts_every_occurrence_and_damage():
    one_finding_three_lines = Finding("unbalanced-parentheses", "error", 1, "Unbalanced (also lines 7, 9)")
    honest = Finding("transpiler-note", "error", 2, "cannot be translated")
    assert _score([one_finding_three_lines]) == (3, 0, True)
    assert _score([honest, honest]) == (2, 0, False)


def test_honest_unconverted_output_is_not_damage():
    honest = Finding("unconverted-file", "error", 1, "-- internal error: source kept unchanged")
    assert _score([honest]) == (1, 0, False)


def test_sql_server_source_is_tidied_before_conversion(tmp_path):
    from wishbridge.staging import prepare_tsql, prepared_notes

    src = ("CREATE TABLE [dbo].[UserSkillMappings] (\n    [Id] INT NOT NULL,\n"
           "    [ValidFrom] DATETIME2 GENERATED ALWAYS AS ROW START HIDDEN NOT NULL,\n"
           "    [ValidTo] DATETIME2 GENERATED ALWAYS AS ROW END HIDDEN NOT NULL,\n"
           "    PERIOD FOR SYSTEM_TIME ([ValidFrom], [ValidTo])\n"
           ")\nWITH (SYSTEM_VERSIONING = ON (HISTORY_TABLE=[dbo].[UserSkillMappingsHistory], DATA_CONSISTENCY_CHECK=ON));\n"
           "GO\nALTER TABLE [dbo].[UserSkillMappings] WITH CHECK ADD CONSTRAINT [FK_A] FOREIGN KEY ([Id]) REFERENCES [dbo].[Users] ([Id]);\n"
           "GO\nALTER TABLE [dbo].[UserSkillMappings] CHECK CONSTRAINT [FK_A];\nGO\n")
    out, notes = prepare_tsql(src)
    assert "GENERATED" not in out and "PERIOD FOR" not in out and "SYSTEM_VERSIONING" not in out
    assert "ADD CONSTRAINT [FK_A] FOREIGN KEY" in out and "WITH CHECK" not in out and "CHECK CONSTRAINT" not in out
    assert "[ValidFrom] DATETIME2 NOT NULL" in out and out.count("(") == out.count(")")
    assert {n["rule"] for n in notes} == {"prep-temporal", "prep-with-check", "prep-check-constraint"}

    code = tmp_path / "db"
    (code / "dbo").mkdir(parents=True)
    (code / "dbo" / "t.sql").write_text(src, encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text(f"source: mssql\ninput: '{code.as_posix()}'\n", encoding="utf-8")
    cfg = load_config(p)
    s = staged(cfg)
    assert "SYSTEM_VERSIONING" not in (s.input_dir / "dbo" / "t.sql").read_text(encoding="utf-8")
    assert "SYSTEM_VERSIONING" in (code / "dbo" / "t.sql").read_text(encoding="utf-8")  # client copy untouched
    assert "dbo/t.sql" in prepared_notes(cfg)


def test_other_sources_are_copied_unchanged(tmp_path):
    code = tmp_path / "db"
    code.mkdir()
    (code / "t.sql").write_text("SELECT 1 WITH CHECK ADD CONSTRAINT x;\n", encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text(f"source: oracle\ninput: '{code.as_posix()}'\n", encoding="utf-8")
    s = staged(load_config(p))
    assert (s.input_dir / "t.sql").read_text(encoding="utf-8") == "SELECT 1 WITH CHECK ADD CONSTRAINT x;\n"
