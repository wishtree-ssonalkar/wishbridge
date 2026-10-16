"""Headless smoke tests of the Streamlit app (no browser, no Databricks)."""

import shutil
from pathlib import Path

import pytest

st_testing = pytest.importorskip("streamlit.testing.v1")

APP = str(Path(__file__).parents[1] / "src" / "wishbridge" / "ui" / "app.py")
DEMO = Path(__file__).parents[1] / "examples" / "mssql-demo"


@pytest.fixture(autouse=True)
def _signed_in_login(monkeypatch):
    """A saved, working Databricks login, whatever is (or isn't) in this computer's ~/.databrickscfg."""
    import importlib

    from wishbridge.warehouses import WAREHOUSE_OF

    login = [{"name": "DEFAULT", "host": "https://test.cloud.databricks.com", "valid": True}]
    for w in set(WAREHOUSE_OF.values()):
        helpers = importlib.import_module(f"wishbridge.warehouses.{w}.ui.helpers")
        monkeypatch.setattr(helpers, "list_profiles", lambda check_validity=True: login)


@pytest.fixture
def project(tmp_path):
    dest = tmp_path / "mssql-demo"
    shutil.copytree(DEMO, dest, ignore=shutil.ignore_patterns("output"))
    return dest


def ready_app(project=None):
    """The app after a successful system check (and with a project open)."""
    from wishbridge.system import Status

    at = st_testing.AppTest.from_file(APP, default_timeout=120)
    at.session_state["system"] = [Status("cli", "Databricks CLI", True, "x", "", "")]
    if project is not None:
        at.session_state["project"] = str(project)
    return at


def test_system_check_comes_first():
    at = st_testing.AppTest.from_file(APP, default_timeout=60).run()
    assert not at.exception
    assert any("Check this computer" in b.label for b in at.button)
    assert next(b for b in at.button if b.label == "Open").disabled  # no project before the check
    assert any("System check" in i.value for i in at.info)
    # what WishBridge does is visible before the check too
    assert any("How it works" in m.value for m in at.markdown)
    assert any("SQL Server used as a data warehouse" in str(t.value) for t in at.table)


def test_welcome_after_the_check():
    at = ready_app().run()
    assert not at.exception
    # first: assessment or migration; nothing opens before that
    assert any(m.value == "What do you want to do?" for m in at.subheader)
    assert next(t for t in at.text_input if t.label == "Project or client code folder").disabled
    next(b for b in at.button if b.label == "Start an assessment").click().run()
    assert at.session_state["goal"] == "assessment"
    starts = next(r for r in at.radio if r.key == "start_assessment")
    assert starts.options == ["Open a folder", "Start from the database"]
    assert next(b for b in at.button if b.label == "Open").disabled  # nothing typed yet
    next(t for t in at.text_input if t.label == "Project or client code folder").set_value(r"C:\code").run()
    assert not next(b for b in at.button if b.label == "Open").disabled
    assert any("warehouse code" in i.value for i in at.info)
    assert any("SQL Server used as a data warehouse" in str(t.value) for t in at.table)


def test_migration_starts_from_the_assessment_zip():
    at = ready_app().run()
    assert [b.label for b in at.button if b.key and b.key.startswith("goal-")] == [
        "Start an assessment", "Start a code migration", "Start a data migration"]
    next(b for b in at.button if b.label == "Start a code migration").click().run()
    assert not at.exception
    starts = next(r for r in at.radio if r.key == "start_code-migration")
    assert starts.options == ["Open the assessment zip", "Open a project", "Create new"]
    assert any("Open the assessment zip" in i.value for i in at.info)
    next(b for b in at.button if b.label == "Start a data migration").click().run()
    starts = next(r for r in at.radio if r.key == "start_data-migration")
    assert starts.options == ["Open the code migration project", "Data only: tables already in Databricks"]
    assert any("Open the code migration project" in i.value for i in at.info)


def test_opening_a_project_applies_the_chosen_goal(project):
    import yaml

    at = ready_app(project)
    at.session_state["goal"] = "assessment"  # chosen on the first page; the demo project is in migration
    at.run()
    assert not at.exception
    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    assert raw["phase"] == "assessment"
    steps = [b.label for b in at.button if b.key and b.key.startswith("step-")]
    assert steps[-1].endswith("Share the zip")


def test_steps_open_in_order(project):
    at = ready_app(project).run()
    assert not at.exception
    steps = {b.label.replace("✓ ", ""): b for b in at.button if b.key and b.key.startswith("step-")}
    assert list(steps) == ["1 · Settings", "2 · Code", "3 · Run", "4 · Deployment report", "5 · Fix code"]  # code migration
    assert steps["1 · Settings"].disabled is False
    # nothing opens before Settings are confirmed with Next
    assert all(steps[k].disabled for k in ("2 · Code", "3 · Run", "4 · Deployment report", "5 · Fix code"))
    assert at.selectbox[0].value == "mssql"


def _no_workspace_calls(monkeypatch, problems=()):
    """Settings' Next asks the workspace about the target; the tests answer for it."""
    from wishbridge.warehouses.sqlserver.ui import helpers as wh_helpers

    monkeypatch.setattr(wh_helpers, "check_target", lambda profile, catalog, warehouse_id="", **kw: list(problems))


def test_next_saves_settings_and_opens_code(project, monkeypatch):
    _no_workspace_calls(monkeypatch)
    at = ready_app(project).run()
    schema_input = next(t for t in at.text_input if t.label.startswith("Test schema"))
    schema_input.set_value("wishbridge_ui_test").run()
    next(b for b in at.button if b.label.startswith("Next: add the code")).click().run()
    assert not at.exception
    text = (project / "project.yml").read_text(encoding="utf-8")
    assert "schema: wishbridge_ui_test" in text
    assert "saved from the WishBridge UI" in text
    assert at.session_state["step"] == "code"  # Next saves and moves on
    steps = {b.label: b for b in at.button if b.key and b.key.startswith("step-")}
    assert steps["1 · ✓ Settings"].disabled is False and steps["2 · ✓ Code"].disabled is False
    assert steps["4 · Deployment report"].disabled  # nothing has run yet


def test_fix_code_handles_missing_converted_file(project):
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    summary = {"files": 1, "ready": 1, "review": 0, "needs_fix": 0, "auto_fixed": 0, "open_errors": 0, "open_warnings": 0}
    save_step(cfg, "convert", {"summary": summary, "transpiler": "morph", "final_dir": "", "files": [{
        "file": "a.sql", "input": str(project / "input" / "a.sql"), "final": str(project / "output" / "final" / "a.sql"),
        "status": "ready", "fixed": 0, "findings": []}]})
    at = ready_app(project)
    at.session_state["go_step"] = "fix"
    at.run()
    assert not at.exception
    assert any("not on disk" in i.value for i in at.info)


def test_code_migration_settings_need_no_source_data(project):
    at = ready_app(project).run()
    assert not at.exception
    headings = [m.value for m in at.markdown if m.value.startswith("#### ")]
    assert headings == ["#### 1 · Databricks workspace ✅", "#### 2 · Target in Databricks ✅"]  # no source data part
    assert any(b.label.startswith("Next: add the code") for b in at.button)


def test_data_migration_steps(project):
    _data_migration(project)
    at = ready_app(project).run()
    assert not at.exception
    steps = [b.label.replace("✓ ", "") for b in at.button if b.key and b.key.startswith("step-")]
    assert steps == ["1 · Settings", "2 · Map the data", "3 · Data report"]
    assert any(b.label.startswith("Next: map the data") for b in at.button)  # the demo has its source data


def test_migration_settings_need_target_and_source(project):
    import yaml

    raw = _data_migration(project)
    raw["data"].update(source_catalog="", tables=[])  # no source connection, nothing to copy yet
    (project / "project.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    at = ready_app(project).run()
    assert not at.exception
    # the parts appear one by one: workspace and target are filled in, the source data is not
    headings = [m.value for m in at.markdown if m.value.startswith("#### ")]
    assert headings == ["#### 3 · Source data"]  # workspace and target: one ticked line from the code migration
    assert any("Workspace and target" in s.value for s in at.success)
    assert not any(b.label.startswith("Next: add the code") for b in at.button)  # appears once all parts are done
    notes = " ".join(m.value for m in at.markdown)
    assert "create the connection" in notes  # red note under the field; tables are chosen in Map the data
    assert any("next part appears" in c.value for c in at.caption)
    assert any(t.label.endswith(":red[*]") for t in at.text_input)  # required fields carry a red *


def test_assessment_ends_with_sharing_the_zip(project):
    text = (project / "project.yml").read_text(encoding="utf-8")
    (project / "project.yml").write_text(text + "\nphase: assessment\n", encoding="utf-8")
    at = ready_app(project).run()
    assert not at.exception
    steps = [b.label.replace("✓ ", "") for b in at.button if b.key and b.key.startswith("step-")]
    assert steps == ["1 · Settings", "2 · Code", "3 · Run", "4 · Report", "5 · Share the zip"]
    assert not next(b for b in at.button if b.label.startswith("Next: add the code")).disabled  # nothing else needed


def test_share_page_switches_to_onedrive_for_big_zips(project, monkeypatch):
    import zipfile

    from wishbridge.warehouses.sqlserver import mailer
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    text = (project / "project.yml").read_text(encoding="utf-8")
    (project / "project.yml").write_text(text + "\nphase: assessment\n", encoding="utf-8")
    cfg = load_config(project / "project.yml")
    save_step(cfg, "fit", {"objects": [], "verdict": "warehouse", "headline": "Good fit"})
    pkg = cfg.out("review_package", "demo.zip")
    with zipfile.ZipFile(pkg, "w") as z:
        z.writestr("README.txt", "x")

    at = ready_app(project)
    at.session_state["go_step"] = "send"
    at.run()
    labels = [b.label for b in at.button]
    assert "📧 Send mail with the zip attached" in labels  # small zip: e-mail

    monkeypatch.setattr(mailer, "MAX_ATTACHMENT_MB", 0)  # pretend it is too big for e-mail
    at = ready_app(project)
    at.session_state["go_step"] = "send"
    at.run()
    labels = [b.label for b in at.button]
    assert "📧 Send mail with the OneDrive link" in labels and "📧 Send mail with the zip attached" not in labels
    assert any("OneDrive" in w.value for w in at.warning)
    assert next(b for b in at.button if "OneDrive link" in b.label).disabled  # no link pasted yet


def test_create_connection_waits_for_its_fields(project):
    _data_migration(project)
    at = ready_app(project).run()
    create = next(b for b in at.button if "Create connection" in b.label)
    assert create.disabled
    assert any("To create the connection, fill in" in c.value for c in at.caption)
    for label, value in (("Server (host)", "sql01.client.com"), ("Database", "SalesDW"),
                         ("User (read-only is enough)", "reader"), ("Password", "x")):
        next(t for t in at.text_input if t.label.startswith(label)).set_value(value)
    at.run()
    assert not next(b for b in at.button if "Create connection" in b.label).disabled


def _data_migration(project):
    """The demo project in the data migration phase (its code migration is done)."""
    import yaml

    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    raw["phase"] = "data-migration"
    (project / "project.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    return raw


def _after_deploy(project):
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    save_step(cfg, "deploy", {"summary": {}, "files": []})
    return cfg


def test_map_the_data_proposes_checks_and_copies(project, monkeypatch):
    from wishbridge.warehouses.sqlserver import dbx, mapping, runner

    _data_migration(project)
    _after_deploy(project)
    from wishbridge.warehouses.sqlserver.ui.steps import data as data_step

    monkeypatch.setattr(dbx, "Warehouse", lambda cfg: object())
    monkeypatch.setattr(data_step, "Warehouse", lambda cfg: object())  # the step keeps its own reference
    monkeypatch.setattr(mapping, "target_tables", lambda cfg, wh: ["workspace.wishbridge_demo.customers"])
    monkeypatch.setattr(mapping, "source_tables", lambda cfg, wh: ["wishbridge_demo_src.Customers", "dbo.Gone"])
    monkeypatch.setattr(mapping, "check", lambda cfg, wh=None: [
        {"source": "wishbridge_demo_src.Customers", "target": "workspace.wishbridge_demo.customers", "load": True,
         "status": "check columns", "source_columns": ["id", "cust_name"], "target_columns": ["id", "customer_name"],
         "pairs": {"id": "id"}, "target_without_source": ["customer_name"], "source_not_copied": ["cust_name"]}])
    started = []
    monkeypatch.setattr(runner, "start", lambda cfg, steps, opts, keep_going: started.append((steps, opts)))

    at = ready_app(project)
    at.session_state["go_step"] = "data"
    at.run()
    assert not at.exception
    assert at.session_state["step"] == "data"
    next(b for b in at.button if b.label.startswith("🔄 Propose the mapping")).click().run()
    assert not at.exception
    rows = at.session_state["map_rows"]
    assert rows[0]["target"] == "workspace.wishbridge_demo.customers" and rows[0]["exists"]
    assert not rows[1]["exists"]  # no Databricks table for it yet: flagged in red
    assert any("do not exist yet" in c.value for c in at.caption)
    next(b for b in at.button if b.label.startswith("💾 Save and check the columns")).click().run()
    assert not at.exception
    assert any("Map the columns" in e.label for e in at.expander)  # column mapping for the mismatch
    copy = next(b for b in at.button if b.label == "▶ Copy the data")
    assert not copy.disabled  # 'check columns' does not block; missing tables would
    copy.click().run()
    assert started == [(["load", "reconcile", "report"], {"execute": True})]


def test_migration_run_stops_before_copying(project):
    _after_deploy(project)
    at = ready_app(project)
    at.session_state["go_step"] = "run"
    at.run()
    assert not at.exception
    labels = [c.label for c in at.checkbox]
    assert not any("Copy the data" in x or "Reconcile" in x for x in labels)  # that is the next step
    assert any(x.startswith("3. Create the tables") for x in labels)


def _with_database_code(project):
    import shutil

    from wishbridge.warehouses.sqlserver import dbcode
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.inventory import import_file

    cfg = load_config(project / "project.yml")
    csv = project / "inv.csv"
    csv.write_text("schema_name,table_name,column_name,data_type\ndbo,T,id,int\n", encoding="utf-8")
    import_file(cfg, csv, "sqlserver")
    dbcode.write_objects(cfg, [("dbo", "usp_A", "P", "CREATE PROCEDURE dbo.usp_A AS SELECT 1", 0)], "srv / db")
    return cfg, shutil


def test_code_from_the_database_is_used_when_there_are_no_files(project):
    import yaml

    cfg, shutil = _with_database_code(project)
    shutil.rmtree(project / "input")
    at = ready_app(project)
    at.session_state["settings_done:" + str(project)] = True
    at.session_state["go_step"] = "code"
    at.run()
    assert not at.exception
    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    assert raw["input"] == "database_code"  # nothing else to assess
    at.run()
    assert any("1 procedure " in m.value for m in at.markdown)
    assert any(b.label == "Next: run →" for b in at.button)  # the code step is complete


def test_choose_between_code_files_and_database_code(project):
    import yaml

    from wishbridge.warehouses.sqlserver import dbcode

    cfg, _ = _with_database_code(project)
    dbcode.compare(cfg, project / "input")
    at = ready_app(project)
    at.session_state["settings_done:" + str(project)] = True
    at.session_state["go_step"] = "code"
    at.run()
    assert not at.exception
    assert any("Compared with the code files" in m.value for m in at.markdown)
    radio = next(r for r in at.radio if r.label == "Code to assess")
    assert radio.value == "The code files"
    radio.set_value("The code from the database").run()
    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    assert raw["input"] == "database_code" and raw["code_files_input"] == "input"
    next(r for r in at.radio if r.label == "Code to assess").set_value("The code files").run()
    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    assert raw["input"] == "input" and "code_files_input" not in raw


def test_migration_settings_start_with_the_workspace_only(project):
    import yaml

    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    raw["databricks"]["profile"] = "NOT_SET_UP_HERE"
    (project / "project.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    at = ready_app(project).run()
    assert not at.exception
    headings = [m.value for m in at.markdown if m.value.startswith("#### ")]
    assert headings == ["#### 1 · Databricks workspace"]  # target and source appear after a login is chosen
    assert not any(t.label.startswith("Test schema") for t in at.text_input)


def test_assessments_and_migrations_have_their_own_folders():
    from wishbridge.ui.helpers import default_parent

    assert default_parent("assessment").endswith("Assessments")
    assert default_parent("code-migration").endswith("Migrations")
    assert default_parent("data-migration") == default_parent("code-migration")  # the same project, both phases
    at = ready_app().run()
    next(b for b in at.button if b.label == "Start a code migration").click().run()
    assert next(t for t in at.text_input if t.label == "Create the project in").value == default_parent("code-migration")
    next(b for b in at.button if b.label == "Start an assessment").click().run()
    next(r for r in at.radio if r.key == "start_assessment").set_value("Start from the database").run()
    assert not at.exception
    assert next(t for t in at.text_input if t.label.startswith("Create in folder")).value == default_parent("assessment")


def _code_step(project):
    at = ready_app(project)
    at.session_state["settings_done:" + str(project)] = True
    at.session_state["go_step"] = "code"
    return at.run()


def test_folder_project_keeps_the_database_optional(project):
    at = _code_step(project)
    assert not at.exception
    titles = [e.label for e in at.expander]
    assert "Optional: add the table list from the database" in titles
    assert not next(c for c in at.checkbox if c.key == "live_code").value  # the code comes from the folder
    assert any(u.label == "Add files" for u in at.get("file_uploader"))
    assert "Open or start another project" in titles  # sidebar: starting another project is folded away


def test_database_project_leads_with_the_database(project):
    import yaml

    raw = yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))
    raw["code_from"] = "database"
    (project / "project.yml").write_text(yaml.safe_dump(raw), encoding="utf-8")
    at = _code_step(project)
    assert not at.exception
    titles = [e.label for e in at.expander]
    assert "Read the tables and code from the database" in titles
    assert "Optional: also add code files" in titles
    assert next(c for c in at.checkbox if c.key == "live_code").value


def test_etl_project_recommends_the_warehouse_database(tmp_path):
    import shutil

    dest = tmp_path / "ssis"
    shutil.copytree(Path(__file__).parents[1] / "examples" / "ssis-demo", dest, ignore=shutil.ignore_patterns("output"))
    at = _code_step(dest)
    assert not at.exception
    assert "Recommended: the warehouse database these packages load" in [e.label for e in at.expander]
    assert next(c for c in at.checkbox if c.key == "live_code").value  # procedures are where the data is updated
    assert any(m.value == "#### What the packages use" for m in at.markdown)
    assert any("Read the warehouse database" in i.value for i in at.info)


def test_code_migration_from_an_assessment_starts_with_fix_code(project):
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    save_step(cfg, "convert", {"summary": {"files": 0, "ready": 0, "review": 0, "needs_fix": 0, "auto_fixed": 0,
                                           "open_errors": 0, "open_warnings": 0}, "transpiler": "morph",
                               "final_dir": "", "files": []})
    at = ready_app(project).run()
    assert not at.exception
    steps = [b.label.replace("✓ ", "") for b in at.button if b.key and b.key.startswith("step-")]
    assert steps == ["1 · Fix code", "2 · Settings", "3 · Deploy", "4 · Deployment report"]  # no Code, Run only deploys
    assert at.session_state["step"] == "fix"  # an opened project starts at the first tab
    next(b for b in at.button if b.label == "Next: settings →").click().run()
    assert at.session_state["step"] == "settings"


def test_deployment_report_shows_what_was_created_and_offers_the_data_migration(project):
    import yaml

    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    save_step(cfg, "deploy", {"summary": {"target": "workspace.demo", "warehouse_id": "w", "files": 2, "files_ok": 2,
                                          "statements": 3, "statements_ok": 3, "kept_existing": 0, "left_out": 0},
                              "files": [{"file": "a.sql", "statements": 2, "passed": 2, "ok": True,
                                         "results": [{"n": 1, "ok": True}, {"n": 2, "ok": True}]},
                                        {"file": "b.sql", "statements": 1, "passed": 1, "ok": True,
                                         "results": [{"n": 1, "ok": True}]}]})
    at = ready_app(project)
    at.session_state["go_step"] = "results"
    at.run()
    assert not at.exception
    assert any(s.value == "Code migration report" for s in at.subheader)
    assert any("Everything was created" in s.value for s in at.success)
    next(b for b in at.button if b.label == "Start the data migration →").click().run()
    assert not at.exception
    assert yaml.safe_load((project / "project.yml").read_text(encoding="utf-8"))["phase"] == "data-migration"


def test_data_report_shows_rows_and_matches(project):
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    _data_migration(project)
    cfg = load_config(project / "project.yml")
    save_step(cfg, "load", {"executed": True, "method": "federation", "plan_file": "", "tables": [
        {"source": "s.a", "target": "t.a", "status": "loaded", "rows": 1200, "compare_only": False},
        {"source": "s.b", "target": "t.b", "status": "loaded", "rows": 34, "compare_only": False}],
        "summary": {"tables": 2, "loaded": 2, "failed": 0, "compare_only": 0}})
    save_step(cfg, "reconcile", {"mode": "quick", "summary": {"tables": 2, "matched": 1}, "tables": [
        {"target": "t.a", "status": "match", "checks": [{"check": "rows", "source": 1200, "target": 1200, "match": True}]},
        {"target": "t.b", "status": "differs", "checks": [{"check": "rows", "source": 35, "target": 34, "match": False}]}]})
    at = ready_app(project)
    at.session_state["go_step"] = "results"
    at.run()
    assert not at.exception
    assert any(s.value == "Data migration report" for s in at.subheader)
    metrics = {m.label: m.value for m in at.metric}
    assert metrics["Tables copied"] == "2/2" and metrics["Rows copied"] == "1,234" and metrics["Match the source"] == "1/2"
    assert any("1 table(s) differ" in w.value for w in at.warning)


def test_deploy_tab_after_an_assessment_does_not_convert_again(project, monkeypatch):
    from wishbridge.warehouses.sqlserver import runner
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    save_step(cfg, "convert", {"summary": {"files": 0, "ready": 0, "review": 0, "needs_fix": 0, "auto_fixed": 0,
                                           "open_errors": 0, "open_warnings": 0}, "transpiler": "morph",
                               "final_dir": "", "files": []})
    started = []
    monkeypatch.setattr(runner, "start", lambda cfg, steps, opts, keep_going: started.append(steps))
    at = ready_app(project)
    at.session_state["go_step"] = "run"
    at.session_state["fix_done:" + str(project)] = True
    at.run()
    assert not at.exception
    assert any(s.value == "Deploy to Databricks" for s in at.subheader)
    next(b for b in at.button if b.label == "▶ Deploy").click().run()
    assert started == [["deploy", "report"]]  # no analyze / convert again


def test_copy_again_only_the_failed_tables_and_make_the_sign_off(project, monkeypatch):
    from wishbridge.warehouses.sqlserver import runner
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    _data_migration(project)
    cfg = load_config(project / "project.yml")
    save_step(cfg, "load", {"executed": True, "method": "federation", "plan_file": "", "summary": {}, "tables": [
        {"source": "s.a", "target": "t.a", "status": "loaded", "rows": 5, "compare_only": False},
        {"source": "s.b", "target": "t.b", "status": "failed", "error": "boom", "compare_only": False}]})
    started = []
    monkeypatch.setattr(runner, "start", lambda cfg, steps, opts, keep_going: started.append(opts))
    at = ready_app(project)
    at.session_state["settings_done:" + str(project)] = True
    at.session_state["go_step"] = "data"
    at.session_state["map_check"] = [{"source": "s.a", "target": "t.a", "status": "ready"}]
    at.run()
    assert not at.exception
    assert any(r.label == "When copying" for r in at.radio)  # the load mode sits by the Copy button
    next(b for b in at.button if b.label.startswith("↻ Copy again: only the 1")).click().run()
    assert started == [{"execute": True, "only": ["t.b"]}]

    at = ready_app(project)
    at.session_state["settings_done:" + str(project)] = True
    at.session_state["go_step"] = "results"
    at.run()
    next(b for b in at.button if b.label.startswith("📦 Make the sign-off file")).click().run()
    assert not at.exception
    assert any(b.label.startswith("⬇ ") and "data-migration-signoff" in b.label for b in at.get("download_button"))



def test_next_after_settings_opens_deploy_and_checks_the_catalog_first(project, monkeypatch):
    from wishbridge.warehouses.sqlserver.config import load_config
    from wishbridge.warehouses.sqlserver.state import save_step

    cfg = load_config(project / "project.yml")
    save_step(cfg, "convert", {"summary": {"files": 0, "ready": 0, "review": 0, "needs_fix": 0, "auto_fixed": 0,
                                           "open_errors": 0, "open_warnings": 0}, "transpiler": "morph",
                               "final_dir": "", "files": []})
    _no_workspace_calls(monkeypatch, ["Catalog 'workspace' does not exist in this workspace. Choose one of: hive_metastore"])
    at = ready_app(project)
    at.session_state["fix_done:" + str(project)] = True
    at.session_state["go_step"] = "settings"
    at.run()
    nxt = next(b for b in at.button if b.label.startswith("Next: "))
    assert nxt.label == "Next: deploy →"  # the tab after Settings here is Deploy (no Code tab)
    nxt.click().run()
    assert not at.exception
    assert at.session_state["step"] == "settings"  # the target is wrong: it stays, and says why
    assert any("does not exist in this workspace" in e.value for e in at.error)

    _no_workspace_calls(monkeypatch)
    next(b for b in at.button if b.label == "Next: deploy →").click().run()
    assert at.session_state["step"] == "run"


def test_check_target_creates_a_new_catalog_and_schema(monkeypatch):
    """New catalog chosen in Settings: created on Next, then the schema; an existing one is only checked."""
    from types import SimpleNamespace as N

    import databricks.sdk
    from wishbridge.warehouses.sqlserver.ui import helpers as wh_helpers

    made = []

    class W:
        def __init__(self, profile=None):
            cats = [N(name="workspace")]
            self.catalogs = N(list=lambda: list(cats),
                              create=lambda name, comment=None: (made.append(("catalog", name)), cats.append(N(name=name))))
            self.schemas = N(list=lambda catalog_name: [N(name="existing")],
                             create=lambda name, catalog_name, comment=None: made.append(("schema", f"{catalog_name}.{name}")))
            self.warehouses = N(list=lambda: [N(id="w1")], get=lambda i: None)

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", W)
    assert wh_helpers.check_target("DEFAULT", "wb_demo", schema="sales", create_catalog=True) == []
    assert made == [("catalog", "wb_demo"), ("schema", "wb_demo.sales")]
    made.clear()
    problems = wh_helpers.check_target("DEFAULT", "nope", schema="sales")
    assert made == [] and "does not exist" in problems[0] and "New catalog" in problems[0]
    assert wh_helpers.check_target("DEFAULT", "workspace", schema="existing") == [] and made == []


def test_default_storage_workspace_explains_how_to_make_the_catalog(monkeypatch):
    from types import SimpleNamespace as N

    import databricks.sdk
    from wishbridge.warehouses.sqlserver.ui import helpers as wh_helpers

    def refuse(name, comment=None):
        raise RuntimeError("Metastore storage root URL does not exist. Default Storage is enabled in your account.")

    class W:
        def __init__(self, profile=None):
            self.config = N(host="https://dbc-x.cloud.databricks.com")
            self.catalogs = N(list=lambda: [N(name="workspace")], create=refuse)

    monkeypatch.setattr(databricks.sdk, "WorkspaceClient", W)
    (msg,) = wh_helpers.check_target("DEFAULT", "shubham", schema="s", create_catalog=True)
    assert "https://dbc-x.cloud.databricks.com/explore/data" in msg and "Default Storage" in msg and "`shubham`" in msg


def test_map_by_hand_adds_rows_and_completes_short_names(project, monkeypatch):
    """Map by hand: the lists load without pairing; a typed row with a short Databricks name is saved in full."""
    from wishbridge.warehouses.sqlserver import dbx, mapping

    _data_migration(project)
    _after_deploy(project)
    from wishbridge.warehouses.sqlserver.ui.steps import data as data_step

    monkeypatch.setattr(dbx, "Warehouse", lambda cfg: object())
    monkeypatch.setattr(data_step, "Warehouse", lambda cfg: object())
    monkeypatch.setattr(mapping, "target_tables", lambda cfg, wh: ["workspace.wishbridge_demo.customers"])
    monkeypatch.setattr(mapping, "source_tables", lambda cfg, wh: ["wishbridge_demo_src.Customers"])
    monkeypatch.setattr(mapping, "check", lambda cfg, wh=None: [])

    at = ready_app(project)
    at.session_state["go_step"] = "data"
    at.run()
    next(b for b in at.button if b.label.startswith("✏️ Map by hand")).click().run()
    assert not at.exception and at.session_state["map_sources"] == ["wishbridge_demo_src.Customers"]
    next(b for b in at.button if b.label.startswith("🧹 Start with an empty list")).click().run()
    at.toggle(key="map_free").set_value(True).run()
    at.session_state["map_rows"] = [{"copy": True, "source": "wishbridge_demo_src.Orders", "target": "orders"}]
    at.run()
    next(b for b in at.button if b.label.startswith("💾 Save and check the columns")).click().run()
    assert not at.exception
    saved = (project / "project.yml").read_text(encoding="utf-8")
    assert "wishbridge_demo_src.Orders" in saved and "workspace.wishbridge_demo.orders" in saved
