"""Migration fit check: what belongs on Databricks and what stays with the application."""

from pathlib import Path

import pytest

from wishbridge.config import ConfigError, load_config
from wishbridge.fit import classify, excluded_files
from wishbridge.staging import read_source, staged

APP_DB = {
    "dbo/Tables/Users.sql": "CREATE TABLE [dbo].[Users] ([Id] INT IDENTITY, [Email] NVARCHAR(200), [CreatedBy] INT, "
                            "[ModifiedOn] DATETIME2, [RowVer] ROWVERSION NOT NULL);",
    "dbo/Tables/__EFMigrationsHistory.sql": "CREATE TABLE [dbo].[__EFMigrationsHistory] ([MigrationId] NVARCHAR(150));",
    "dbo/StoredProcedures/InsertUser.sql": "CREATE PROCEDURE dbo.InsertUser @Email NVARCHAR(200) AS BEGIN "
                                           "INSERT INTO dbo.Users (Email) VALUES (@Email); SELECT SCOPE_IDENTITY(); END",
    "dbo/StoredProcedures/SearchUsersPaged.sql": "CREATE PROCEDURE dbo.SearchUsersPaged @PageSize INT, @PageNumber INT AS "
                                                 "SELECT * FROM dbo.Users ORDER BY Id OFFSET @PageNumber ROWS FETCH NEXT @PageSize ROWS ONLY;",
    "dbo/StoredProcedures/GetUserById.sql": "CREATE PROCEDURE dbo.GetUserById @Id INT AS SELECT * FROM dbo.Users WHERE Id = @Id;",
    "dbo/StoredProcedures/GetUserActivityReport.sql": (
        "CREATE PROCEDURE dbo.GetUserActivityReport AS SELECT u.Email, COUNT(*) AS n, SUM(a.Minutes) FROM dbo.Users u "
        "JOIN dbo.Activity a ON a.UserId = u.Id GROUP BY u.Email;"),
    "dbo/Functions/fn_WorkingDays.sql": "CREATE FUNCTION dbo.fn_WorkingDays (@a DATE, @b DATE) RETURNS INT AS BEGIN RETURN 1; END",
    "dbo/Functions/fn_ActiveUserCount.sql": "CREATE FUNCTION dbo.fn_ActiveUserCount () RETURNS INT AS BEGIN "
                                            "RETURN (SELECT COUNT(*) FROM dbo.Users WHERE dbo.fn_WorkingDays(a, b) > 0); END",
    "Security/AppUser.sql": "CREATE USER [AppUser] FOR LOGIN [AppUser];",
    "Script.PostDeployment.sql": "INSERT INTO dbo.Roles VALUES (1, 'Admin');",
    "Seed/roles.sql": "INSERT INTO dbo.Roles VALUES (2, 'User');",
}

WAREHOUSE = {
    "tables/fact_sales.sql": "CREATE TABLE dw.fact_sales (sale_id BIGINT, amount DECIMAL(18,2));",
    "tables/dim_customer.sql": "CREATE TABLE dw.dim_customer (customer_id BIGINT, name VARCHAR(100));",
    "procs/usp_load_fact_sales.sql": "CREATE PROCEDURE dw.usp_load_fact_sales AS BEGIN TRUNCATE TABLE dw.fact_sales; "
                                     "INSERT INTO dw.fact_sales SELECT * FROM stg.sales; END",
    "procs/usp_sales_summary.sql": "CREATE PROCEDURE dw.usp_sales_summary AS SELECT region, SUM(amount), AVG(amount) "
                                   "FROM dw.fact_sales GROUP BY region;",
    "procs/usp_merge_customers.sql": "CREATE PROCEDURE dw.usp_merge_customers AS MERGE INTO dw.dim_customer t USING "
                                     "stg.customer s ON t.customer_id = s.customer_id WHEN MATCHED THEN UPDATE SET t.name = s.name;",
    "views/v_sales.sql": "CREATE VIEW dw.v_sales AS SELECT * FROM dw.fact_sales;",
}


def project(tmp_path: Path, files: dict[str, str], source: str = "mssql", extra: str = ""):
    code = tmp_path / "code"
    for rel, text in files.items():
        (code / rel).parent.mkdir(parents=True, exist_ok=True)
        (code / rel).write_text(text, encoding="utf-8")
    p = tmp_path / "project.yml"
    p.write_text(f"source: {source}\ninput: '{code.as_posix()}'\n{extra}", encoding="utf-8")
    return load_config(p)


def categories(fit):
    return {o["file"]: o["category"] for o in fit["objects"]}


def test_application_database_is_recognised(tmp_path):
    fit = classify(project(tmp_path, APP_DB))
    cat = categories(fit)
    assert fit["verdict"] == "application"
    assert cat["dbo/StoredProcedures/InsertUser.sql"] == "keep"
    assert cat["dbo/StoredProcedures/SearchUsersPaged.sql"] == "keep"
    assert cat["dbo/StoredProcedures/GetUserById.sql"] == "keep"
    assert cat["dbo/StoredProcedures/GetUserActivityReport.sql"] == "migrate"
    assert cat["dbo/Functions/fn_ActiveUserCount.sql"] == "migrate"
    assert cat["dbo/Functions/fn_WorkingDays.sql"] == "migrate"  # helper used by an object that moves
    assert cat["dbo/Tables/Users.sql"] == "data"
    assert cat["dbo/Tables/__EFMigrationsHistory.sql"] == "skip"
    assert cat["Security/AppUser.sql"] == "skip"
    assert cat["Script.PostDeployment.sql"] == "skip"
    assert cat["Seed/roles.sql"] == "data"
    assert any("rowversion" in e for e in fit["app_evidence"])
    assert "Keep the database" in fit["advice"][0]


def test_warehouse_is_a_good_fit(tmp_path):
    fit = classify(project(tmp_path, WAREHOUSE))
    assert fit["verdict"] == "warehouse"
    assert set(categories(fit).values()) == {"migrate", "data"}
    by_file = {o["file"]: o for o in fit["objects"]}
    assert by_file["procs/usp_load_fact_sales.sql"]["purpose"] == "ETL / data movement"
    assert by_file["procs/usp_sales_summary.sql"]["purpose"] == "reporting / analytics"


def test_report_name_outweighs_paging(tmp_path):
    sql = ("CREATE PROCEDURE dbo.GetBenchListReport @PageSize INT AS SELECT d.Name, COUNT(*) FROM a JOIN b ON 1=1 "
           "JOIN c ON 1=1 JOIN d ON 1=1 GROUP BY d.Name OFFSET 0 ROWS FETCH NEXT @PageSize ROWS ONLY;")
    assert categories(classify(project(tmp_path, {"p.sql": sql})))["p.sql"] == "migrate"


def test_utf16_scripts_are_read_and_staged_as_utf8(tmp_path):
    cfg = project(tmp_path, {})
    cfg.input_dir.mkdir(parents=True, exist_ok=True)
    text = "CREATE PROCEDURE dbo.usp_email_exist @Email NVARCHAR(100) AS SELECT 1 FROM Users WHERE Email = @Email;"
    (cfg.input_dir / "u.sql").write_bytes(text.encode("utf-16"))  # with BOM, as SSMS / Redgate save it
    (cfg.input_dir / "w.sql").write_bytes("SELECT 'café';".encode("cp1252"))
    assert read_source(cfg.input_dir / "u.sql") == text
    assert read_source(cfg.input_dir / "w.sql") == "SELECT 'café';"
    s = staged(cfg)
    assert (s.input_dir / "u.sql").read_text(encoding="utf-8") == text
    assert categories(classify(cfg))["u.sql"] == "keep"


def test_recommended_scope_leaves_out_keep_and_skip(tmp_path):
    cfg = project(tmp_path, APP_DB, extra="scope: recommended\n")
    left = excluded_files(cfg, classify(cfg))
    assert "dbo/StoredProcedures/InsertUser.sql" in left and "Security/AppUser.sql" in left
    assert "dbo/Tables/Users.sql" not in left and "dbo/StoredProcedures/GetUserActivityReport.sql" not in left
    everything = project(tmp_path / "b", APP_DB)
    assert excluded_files(everything, classify(everything)) == {}


def test_scope_must_be_known(tmp_path):
    with pytest.raises(ConfigError):
        project(tmp_path, {"a.sql": "SELECT 1;"}, extra="scope: some\n")
