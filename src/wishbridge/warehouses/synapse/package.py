"""One zip with everything from the first visit - to review later, on any computer.

The package holds:
  * reviewer files at the top (report, CSVs of open items / file status / fit check / inventory, analyzer
    workbook) - readable with nothing installed;
  * project/ - the complete WishBridge project: settings, the client's code as received (with its receipt),
    hand fixes, inventory and all results. `open_package` turns it back into a working project on another
    computer, so the team can fix and review the code at Wishtree without the client's systems.
"""

from __future__ import annotations

import csv
import io
import json
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .config import ProjectConfig
from .state import load_state

MANIFEST = "wishbridge_package.json"
BASELINE = "baseline/"  # the code before conversion (a neutral name: the zip is shared with the client)
OUTPUT_PARTS = ("state.json", "report.html", "analysis", "converted", "inventory", "logs", "ai_suggestions",
                "database_schema", "dummy_data")

README = """WishBridge package - {name}
Created {when} on {machine}

Read it now (nothing to install):
  code_overview.html       Open in a browser: how the system is built - structure, tables, procedures, data flows
  report.html              Open in a browser: assessment, fit check, every converted file and its open items
  open_items.csv           One row per issue left in the converted code (file, line, severity, what to do)
  files.csv                Every converted file with its status (ready / review / needs-fix)
  fit_check.csv            Is this a data warehouse? Recommendation and reasons for every object
  inventory_tables.csv     Source tables with row counts and sizes (when the DBA inventory was imported)
  analysis.xlsx            Analyzer workbook (complexity per file, functions used)
  baseline/                Reference files for the before/after comparison
  converted_code/          The code converted for Databricks (hand fixes from overrides/ included)
  database_code/           Procedures, views, functions and triggers read from the warehouse database
                           (ETL projects: assessed together with the packages)
  dummy_data/              MADE-UP sample rows (5 per table, from the column types only - not client data):
                           one CSV per table and dummy_data.sql to load them into the Databricks tables

Continue the work (on any computer with WishBridge):
  project/                 The rest of the project: settings, hand fixes (overrides/), inventory and results.
                           Opening the package puts baseline/ and converted_code/ back into it.
  Open it with:  wishbridge open-package <this zip> --dir C:\\WishBridge\\Migrations
  or in the app: sidebar > Open a package.

Nothing in this package was sent to Databricks or to the client's database.
"""


def _stamp() -> str:
    """A readable date and time for file names: 2026-10-16_0901."""
    return datetime.now().strftime("%Y-%m-%d_%H%M")


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def _add_tree(z: zipfile.ZipFile, folder: Path, arc: str) -> None:
    if folder.is_file():
        z.write(folder, arc)
        return
    for f in sorted(p for p in folder.rglob("*") if p.is_file()):
        z.write(f, f"{arc}/{f.relative_to(folder).as_posix()}")


def build_package(cfg: ProjectConfig, include_original: bool = True) -> Path:
    from .report import build_report
    from .snapshot import RECEIPT, receipt

    state = load_state(cfg)
    if not cfg.input_dir.is_dir() and not state:
        raise FileNotFoundError("Nothing to package yet - there is no code and nothing has run.")
    # Whatever is there goes in: a first visit may end before conversion (e.g. a converter could not be installed).
    report = build_report(cfg)
    root = cfg.path.parent
    rec = receipt(root)
    stamp = _stamp()
    out = cfg.out("review_package", f"{cfg.name}-{cfg.phase}-{stamp}.zip")
    c, fit, inv, a = state.get("convert") or {"files": [], "final_dir": ""}, state.get("fit"), state.get("inventory"), state.get("analyze")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        import platform

        z.writestr("README.txt", README.format(name=cfg.name, when=datetime.now().strftime("%Y-%m-%d %H:%M"),
                                              machine=platform.node()))
        z.write(report, "report.html")
        if (cfg.output_dir / "code_overview.html").is_file():
            z.write(cfg.output_dir / "code_overview.html", "code_overview.html")
        z.writestr("files.csv", _csv([{**f, "open_errors": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "error"),
                                       "open_warnings": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "warning")}
                                      for f in c["files"]],
                                     ["file", "kind", "status", "manual_override", "fixed", "open_errors", "open_warnings"]))
        z.writestr("open_items.csv", _csv([{"file": f["file"], **x} for f in c["files"] for x in f["findings"]
                                           if not x["fixed"] and x["severity"] != "info"],
                                          ["file", "line", "severity", "rule", "message"]))
        if fit:
            z.writestr("fit_check.csv", _csv([{**o, "reasons": "; ".join(o["reasons"])} for o in fit["objects"]],
                                             ["file", "name", "type", "category", "purpose", "reasons"]))
        if inv:
            z.writestr("inventory_tables.csv", _csv([{**t, "columns": len(t["columns"])} for t in inv["tables"]],
                                                    ["schema", "table", "rows", "size_mb", "columns"]))
        if a and Path(a.get("report_file", "")).is_file():
            z.write(a["report_file"], "analysis.xlsx")

        # The complete project, so it can be opened again elsewhere.
        raw = yaml.safe_load(cfg.path.read_text(encoding="utf-8-sig")) or {}
        raw["input"], raw["output"], raw["overrides"] = "input", "output", "overrides"
        z.writestr("project/project.yml", "# Restored from a WishBridge package.\n"
                   + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True))
        if rec:
            z.write(root / RECEIPT, f"project/{RECEIPT}")
        if include_original and cfg.input_dir.is_dir():
            from .snapshot import _files

            for f in _files(cfg.input_dir):
                z.write(f, BASELINE + f.relative_to(cfg.input_dir).as_posix())
        final = Path(c["final_dir"]) if c["final_dir"] else None
        if final is not None and final.is_dir():
            _add_tree(z, final, "converted_code")
        from .dbcode import folder as database_code_folder

        dbc = database_code_folder(cfg)
        if dbc.is_dir() and cfg.input_dir.resolve() != dbc.resolve():
            _add_tree(z, dbc, "database_code")
        if (cfg.output_dir / "dummy_data").is_dir():
            _add_tree(z, cfg.output_dir / "dummy_data", "dummy_data")
        if cfg.overrides_dir and cfg.overrides_dir.is_dir():
            _add_tree(z, cfg.overrides_dir, "project/overrides")
        for part in OUTPUT_PARTS:
            p = cfg.output_dir / part
            if p.exists():
                _add_tree(z, p, f"project/output/{part}")
        z.writestr(MANIFEST, json.dumps({
            "format": 1, "name": cfg.name, "created": datetime.now().isoformat(timespec="seconds"),
            "project_root": str(root), "input_dir": str(cfg.input_dir), "output_dir": str(cfg.output_dir),
            "overrides_dir": str(cfg.overrides_dir or ""), "includes_original": include_original,
        }, indent=2))
    return out


def open_package(zip_path: str | Path, parent: str | Path, name: str | None = None) -> Path:
    """Recreate the project from a package in parent/name and point its results at the new location."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if MANIFEST not in names or "project/project.yml" not in names:
            raise ValueError(f"{zip_path.name} is not a WishBridge package (made with `wishbridge package`)")
        man = json.loads(z.read(MANIFEST))
        dest = Path(parent).expanduser().resolve() / (name or man["name"])
        if dest.exists() and any(dest.iterdir()):
            raise ValueError(f"{dest} already exists - choose another name or folder")
        places = {"project/": dest, BASELINE: dest / "input", "original_code/": dest / "input",  # older packages
                  "converted_code/": dest / "output" / "final", "database_code/": dest / "database_code"}
        for n in names:
            prefix = next((k for k in places if n.startswith(k)), None)
            if prefix is None or n.endswith("/"):
                continue
            target = (places[prefix] / n[len(prefix):]).resolve()
            if dest not in target.parents:  # never write outside the new project
                raise ValueError(f"Unsafe path in package: {n}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
    # Results store absolute paths: move them from the old project to the new one.
    state = dest / "output" / "state.json"
    if state.exists():
        import re

        moves: dict[str, str] = {}
        for old, new in ((man["input_dir"], dest / "input"), (man["output_dir"], dest / "output"),
                         (man.get("overrides_dir") or "", dest / "overrides"), (man["project_root"], dest)):
            if old:  # paths appear with backslashes (Windows) and with forward slashes (LakeBridge's analyzer)
                moves[json.dumps(str(old))[1:-1]] = json.dumps(str(new))[1:-1]
                moves[str(old).replace("\\", "/")] = str(new).replace("\\", "/")
        # One pass, longest paths first, so a new path is never rewritten again.
        pattern = re.compile("|".join(re.escape(k) for k in sorted(moves, key=len, reverse=True)))
        text = pattern.sub(lambda m: moves[m.group(0)], state.read_text(encoding="utf-8"))
        state.write_text(text, encoding="utf-8")
    from .config import load_config

    load_config(dest / "project.yml")  # fail now if anything is off
    # Remember where it came from: the app then offers "Fix code" for this project.
    (dest / "opened_from_package.json").write_text(json.dumps({**man, "package": str(zip_path.resolve())}, indent=2),
                                                   encoding="utf-8")
    return dest


SIGNOFF_README = """WishBridge data migration sign-off - {name}
Created {when}

  report.html          The data migration report: tables and rows copied, and the comparison with the source
  copied_tables.csv    Every table: source, Databricks table, result, rows copied, error
  comparison.csv       Every table: matches the source or not, and which checks differ
  mapping.yml          Which source table went into which Databricks table (columns, filters)
  load_plan.sql        The statements that copied the data

No data is in this file: only names, counts and check results.
"""


def build_signoff(cfg: ProjectConfig) -> Path:
    """One zip the client can approve the data migration from (no data in it)."""
    from .report import build_report

    state = load_state(cfg)
    ld, rec = state.get("load") or {}, state.get("reconcile") or {}
    if not ld.get("executed"):
        raise ValueError("No data has been copied yet.")
    report = build_report(cfg)
    stamp = _stamp()
    out = cfg.out("review_package", f"{cfg.name}-data-migration-signoff-{stamp}.zip")
    raw = yaml.safe_load(cfg.path.read_text(encoding="utf-8-sig")) or {}
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", SIGNOFF_README.format(name=cfg.name, when=datetime.now().strftime("%Y-%m-%d %H:%M")))
        z.write(report, "report.html")
        from .report import phase_reports

        for phase, _title, path in phase_reports(cfg):  # the code migration report too, not only the data one
            z.write(path, f"reports/{phase}-report.html")
        z.writestr("copied_tables.csv", _csv([t for t in ld.get("tables", []) if not t.get("compare_only")],
                                             ["source", "target", "status", "rows", "error"]))
        if rec.get("mode") == "quick":
            z.writestr("comparison.csv", _csv([{
                "target": t["target"], "status": t["status"],
                "differences": t.get("error") or "; ".join(f"{c['check']}: {c['source']} -> {c['target']}"
                                                           for c in t.get("checks", []) if not c["match"])}
                for t in rec.get("tables", [])], ["target", "status", "differences"]))
        z.writestr("mapping.yml", yaml.safe_dump({"tables": (raw.get("data") or {}).get("tables") or []},
                                                 sort_keys=False, allow_unicode=True))
        plan = Path(ld.get("plan_file") or "")
        if plan.is_file():
            z.write(plan, "load_plan.sql")
    return out


COMPLETE_README = """WishBridge - everything for {name}
Created {when}. No client data and no passwords are in this zip.

Open in a browser (nothing to install):
  reports/                 Every report of the project: {reports}
  code_overview.html       How the system is built: structure, tables, procedures, data flows

Assessment details:
  files.csv, open_items.csv, fit_check.csv, inventory_tables.csv, analysis.xlsx

Code:
  baseline/                The code as received, for the before/after comparison
  converted_code/          The code for Databricks (manual fixes included)
  database_code/           Procedures, views, functions and triggers read from the warehouse database

Code migration:
  code_migration/deployment_results.csv   Every file and statement created in Databricks, and the errors
  code_migration/table_scripts/           CREATE TABLE scripts for the Databricks tables
  code_migration/manual_fixes/            Files fixed by hand or with Claude (they replace the converted ones)
  code_migration/jobs/                    The Databricks job definition (the ETL run order), to schedule it

Data migration:
  data_migration/mapping.yml              Which source table went into which Databricks table (columns, filters)
  data_migration/copied_tables.csv        Tables copied and their row counts
  data_migration/comparison.csv           Source vs Databricks: row counts and totals per table
  data_migration/load_plan.sql            The copy statements that were run

  dummy_data/              MADE-UP sample rows (5 per table, from the column types only - not client data)
  project/                 The complete project, to open again in WishBridge: sidebar > Open a package,
                           or  wishbridge open-package <this zip> --dir C:\\WishBridge\\Migrations
"""


def build_complete_package(cfg: ProjectConfig) -> Path:
    """Everything of the project in one zip: the assessment package (it opens again in WishBridge) plus every
    report and the code-migration and data-migration results."""
    from .report import phase_reports

    state = load_state(cfg)
    base_zip = build_package(cfg)
    reports = phase_reports(cfg)
    stamp = _stamp()
    out = cfg.out("review_package", f"{cfg.name}-{cfg.phase}-complete-{stamp}.zip")
    with zipfile.ZipFile(base_zip) as src, zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        for item in src.infolist():
            if item.filename not in ("README.txt", "report.html"):  # replaced by this README and reports/
                z.writestr(item, src.read(item.filename))
        z.writestr("README.txt", COMPLETE_README.format(
            name=cfg.name, when=datetime.now().strftime("%Y-%m-%d %H:%M"),
            reports=", ".join(title.lower() for _, title, _ in reports) or "none yet"))
        for phase, _title, path in reports:
            z.write(path, f"reports/{phase}-report.html")

        dep = state.get("deploy") or {}
        if dep.get("files"):
            z.writestr("code_migration/deployment_results.csv", _csv([
                {"file": f["file"], "result": "created" if f["ok"] else "failed", "statement": r.get("n"),
                 "kind": r.get("kind"), "action": r.get("action"), "ok": r.get("ok"),
                 "error": r.get("error") or r.get("note") or ""}
                for f in dep["files"] for r in f.get("results", [])],
                ["file", "result", "statement", "kind", "action", "ok", "error"]))
        for folder, arc in [(cfg.output_dir / "database_schema", "code_migration/table_scripts"),
                            (cfg.output_dir / "jobs", "code_migration/jobs")]:
            if folder.is_dir():
                _add_tree(z, folder, arc)
        if cfg.overrides_dir and cfg.overrides_dir.is_dir() and any(cfg.overrides_dir.rglob("*")):
            _add_tree(z, cfg.overrides_dir, "code_migration/manual_fixes")

        ld, rec = state.get("load") or {}, state.get("reconcile") or {}
        raw = yaml.safe_load(cfg.path.read_text(encoding="utf-8-sig")) or {}
        if (raw.get("data") or {}).get("tables"):
            z.writestr("data_migration/mapping.yml", yaml.safe_dump({"tables": raw["data"]["tables"]}, sort_keys=False,
                                                                    allow_unicode=True))
        if ld.get("executed"):
            z.writestr("data_migration/copied_tables.csv", _csv(
                [t for t in ld.get("tables", []) if not t.get("compare_only")],
                ["source", "target", "status", "rows", "error"]))
            plan = Path(ld.get("plan_file") or "")
            if plan.is_file():
                z.write(plan, "data_migration/load_plan.sql")
        if rec.get("mode") == "quick":
            z.writestr("data_migration/comparison.csv", _csv([{
                "target": t["target"], "status": t["status"],
                "checks": "; ".join(f"{c['check']}: {c['source']} -> {c['target']}" for c in t.get("checks", [])),
                "differences": t.get("error") or "; ".join(f"{c['check']}: {c['source']} -> {c['target']}"
                                                           for c in t.get("checks", []) if not c["match"])}
                for t in rec.get("tables", [])], ["target", "status", "checks", "differences"]))
    base_zip.unlink(missing_ok=True)  # only a step on the way
    return out
