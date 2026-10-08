"""Turning folders of client code (no project.yml) into WishBridge projects."""

from pathlib import Path

import pytest

from wishbridge import discover
from wishbridge.config import load_config

EXAMPLES = Path(__file__).parents[1] / "examples"


def write(path: Path, text: str = "SELECT 1;\n") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def vs_repo(tmp_path: Path) -> Path:
    """Shape of a Visual Studio database project repository with several databases."""
    repo = tmp_path / "client-repo"
    write(repo / ".github" / "workflows" / "scan-pr.yaml", "on: push\n")
    write(repo / ".git" / "config", "[core]\n")
    proj = repo / "SalesDB"
    write(proj / "SalesDB.sqlproj", "<Project><PropertyGroup><DSP>Microsoft.Data.Tools.Schema.Sql.Sql130DatabaseSchemaProvider"
                                    "</DSP></PropertyGroup></Project>")
    for db, n in (("TenantDB", 5), ("AuditDB", 3)):
        for i in range(n):
            write(proj / db / "Tables" / f"t{i}.sql", f"CREATE TABLE [dbo].[t{i}] (id INT)\nGO\n")
    write(proj / "Security" / "role.sql", "CREATE ROLE r;\n")
    return repo


def test_visual_studio_project_detected_with_databases(tmp_path):
    info = discover.inspect(vs_repo(tmp_path))
    assert (info.detection.source, info.detection.confidence) == ("mssql", "high")
    assert [(n, c) for n, _, c in info.databases] == [("TenantDB", 5), ("AuditDB", 3)]
    assert info.files == 9  # .git and .github are skipped; the .sqlproj is not code


@pytest.mark.parametrize("example, source", [("oracle", "oracle"), ("snowflake", "snowflake"), ("teradata", "teradata"),
                                             ("redshift", "redshift"), ("bigquery", "bigquery"), ("netezza", "netezza"),
                                             ("synapse", "synapse"), ("mssql", "mssql"), ("informatica", "informatica")])
def test_dialect_detected_from_the_code(example, source):
    det = discover.detect_source(EXAMPLES / f"{example}-demo" / "input")
    assert det.source == source, det
    assert det.confidence in ("high", "medium")


def test_unknown_code_is_low_confidence(tmp_path):
    write(tmp_path / "code" / "a.sql", "SELECT 1;\n")
    det = discover.detect_source(tmp_path / "code")
    assert det.confidence == "low"


def test_projects_are_created_outside_the_code_and_read_it_in_place(tmp_path):
    repo = vs_repo(tmp_path)
    info = discover.inspect(repo)
    made = discover.create_projects_for_code(info, tmp_path / "migrations", "acme", "mssql", split=True, catalog="workspace")
    assert [p.name for p in made] == ["acme-tenantdb", "acme-auditdb"]
    cfg = load_config(made[0] / "project.yml")
    assert cfg.input_dir == (repo / "SalesDB" / "TenantDB").resolve()
    assert cfg.target_schema == "workspace.wishbridge_acme_tenantdb"
    assert cfg.schema_map == {"dbo": "workspace.wishbridge_acme_tenantdb"}
    assert not any(p.name == "project.yml" for p in repo.rglob("*"))  # the client's folder is untouched

    single = discover.create_projects_for_code(info, tmp_path / "migrations", "acme-all", "mssql", split=False)
    assert load_config(single[0] / "project.yml").input_dir == repo.resolve()


def test_project_inside_the_code_folder_is_refused(tmp_path):
    repo = vs_repo(tmp_path)
    with pytest.raises(ValueError, match="outside the client's code folder"):
        discover.create_project_for_code(repo, repo, "inside", "mssql")


def test_empty_or_missing_folder(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        discover.inspect(tmp_path / "nope")
    (tmp_path / "empty").mkdir()
    with pytest.raises(ValueError, match="No SQL or ETL code"):
        discover.inspect(tmp_path / "empty")
