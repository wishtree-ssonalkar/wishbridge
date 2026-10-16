"""A data migration still offers the code migration report: each phase report is rebuilt from the saved results."""

from wishbridge.warehouses.ssis.config import load_config
from wishbridge.warehouses.ssis.report import build_report, phase_reports
from wishbridge.warehouses.ssis.state import save_step


def test_every_phase_report_is_kept_and_offered(tmp_path):
    (tmp_path / "project.yml").write_text("source: ssis\nphase: data-migration\n", encoding="utf-8")
    cfg = load_config(tmp_path / "project.yml")
    save_step(cfg, "deploy", {"summary": {"target": "workspace.s", "warehouse_id": "w", "files": 1, "files_ok": 1,
                                          "statements": 1, "statements_ok": 1, "kept_existing": 0, "left_out": 0},
                              "files": [{"file": "a.sql", "statements": 1, "passed": 1, "ok": True,
                                         "results": [{"n": 1, "kind": "ddl", "action": "execute", "ok": True}]}]})
    save_step(cfg, "load", {"method": "local", "executed": True, "plan_file": "", "summary": {"tables": 1, "loaded": 1},
                            "tables": [{"source": "dw.A", "target": "workspace.s.A", "status": "loaded", "rows": 5,
                                        "statements": [], "compare_only": False}]})
    reports = phase_reports(cfg)
    assert [(p, t) for p, t, _ in reports] == [("code-migration", "Code migration report"),
                                               ("data-migration", "Data migration report")]
    assert "<h1>Code migration report" in reports[0][2].read_text(encoding="utf-8")
    current = build_report(cfg)  # the current phase also keeps its own copy
    assert current.name == "report.html" and (cfg.output_dir / "report-data-migration.html").is_file()


def test_everything_goes_into_one_zip(tmp_path):
    import zipfile

    from wishbridge.warehouses.ssis.package import build_complete_package

    (tmp_path / "input").mkdir()
    (tmp_path / "input" / "Load.dtsx").write_text("<DTS:Executable/>", encoding="utf-8")
    (tmp_path / "overrides").mkdir()
    (tmp_path / "overrides" / "Load.py").write_text("x = 1\n", encoding="utf-8")
    (tmp_path / "project.yml").write_text(
        "source: ssis\nphase: data-migration\ndata:\n  tables: [{source: dw.A, target: workspace.s.A}]\n", encoding="utf-8")
    cfg = load_config(tmp_path / "project.yml")
    save_step(cfg, "deploy", {"summary": {"target": "workspace.s", "warehouse_id": "w", "files": 1, "files_ok": 0,
                                          "statements": 1, "statements_ok": 0, "kept_existing": 0, "left_out": 0},
                              "files": [{"file": "Load.py", "statements": 1, "passed": 0, "ok": False,
                                         "results": [{"n": 1, "kind": "ddl", "action": "execute", "ok": False,
                                                      "error": "SCHEMA_NOT_FOUND"}]}]})
    save_step(cfg, "load", {"method": "local", "executed": True, "plan_file": "", "summary": {"tables": 1, "loaded": 1},
                            "tables": [{"source": "dw.A", "target": "workspace.s.A", "status": "loaded", "rows": 5,
                                        "statements": [], "compare_only": False}]})
    names = zipfile.ZipFile(build_complete_package(cfg)).namelist()
    for want in ["README.txt", "wishbridge_package.json", "baseline/Load.dtsx", "reports/code-migration-report.html",
                 "reports/data-migration-report.html", "code_migration/deployment_results.csv",
                 "code_migration/manual_fixes/Load.py", "data_migration/mapping.yml", "data_migration/copied_tables.csv"]:
        assert want in names, want
    assert not [p for p in (cfg.output_dir / "review_package").glob("*.zip") if "complete" not in p.name]
