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


def test_projects_are_created_outside_the_code_with_a_copy_of_it(tmp_path):
    from wishbridge.snapshot import check_copy, receipt

    repo = vs_repo(tmp_path)
    info = discover.inspect(repo)
    made = discover.create_projects_for_code(info, tmp_path / "migrations", "acme", "mssql", split=True, catalog="workspace")
    assert [p.name for p in made] == ["acme-tenantdb", "acme-auditdb"]
    cfg = load_config(made[0] / "project.yml")
    assert cfg.input_dir == (made[0] / "input").resolve()
    assert cfg.phase == "assessment"  # new projects start offline
    rec = receipt(made[0])
    assert rec["copied_from"] == str((repo / "SalesDB" / "TenantDB").resolve()) and rec["files"] >= 3
    assert all(len(h) == 64 for h in rec["sha256"].values())
    assert check_copy(made[0], cfg.input_dir) == {"changed": [], "missing": [], "added": []}
    first = next(iter(rec["sha256"]))
    (cfg.input_dir / first).write_text("changed", encoding="utf-8")
    assert check_copy(made[0], cfg.input_dir)["changed"] == [first]
    assert cfg.target_schema == "workspace.wishbridge_acme_tenantdb"
    assert cfg.schema_map == {"dbo": "workspace.wishbridge_acme_tenantdb"}
    assert not any(p.name == "project.yml" for p in repo.rglob("*"))  # the client's folder is untouched

    single = discover.create_projects_for_code(info, tmp_path / "migrations", "acme-all", "mssql", split=False,
                                               copy_code=False)
    assert load_config(single[0] / "project.yml").input_dir == repo.resolve()


def test_existing_project_can_take_a_copy_later(tmp_path):
    from wishbridge.snapshot import copy_project_code

    repo = vs_repo(tmp_path)
    root = discover.create_project_for_code(repo, tmp_path / "m", "late", "mssql", copy_code=False)
    rec = copy_project_code(root / "project.yml")
    cfg = load_config(root / "project.yml")
    assert cfg.input_dir == (root / "input").resolve() and rec["files"] == sum(1 for p in cfg.input_dir.rglob("*") if p.is_file())
    with pytest.raises(ValueError, match="already inside"):
        copy_project_code(root / "project.yml")


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


def test_suggested_name_is_free(tmp_path):
    (tmp_path / "ssis").mkdir()
    (tmp_path / "ssis" / "project.yml").write_text("source: ssis\n", encoding="utf-8")
    assert discover.suggested_name(tmp_path / "client" / "SSIS", tmp_path) == "ssis-2"
    assert discover.suggested_name(tmp_path / "client" / "Other", tmp_path) == "other"


def test_existing_projects_are_found_for_the_code(tmp_path, monkeypatch):
    monkeypatch.setattr(discover, "REGISTRY", tmp_path / "registry.json")
    monkeypatch.setattr(discover, "DEFAULT_PARENTS", ())
    repo = vs_repo(tmp_path)
    assert discover.existing_projects(repo) == []
    made = discover.create_projects_for_code(discover.inspect(repo), tmp_path / "elsewhere", "acme", "mssql", split=True)
    found = discover.existing_projects(repo)  # found through the registry, wherever they were saved
    assert sorted(p["name"] for p in found) == sorted(p.name for p in made)  # one per database inside the folder
    assert discover.existing_projects(repo / "SalesDB" / "TenantDB")[0]["name"] == "acme-tenantdb"
    assert discover.existing_projects(tmp_path / "other") == []
