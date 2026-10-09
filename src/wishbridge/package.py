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
OUTPUT_PARTS = ("state.json", "report.html", "analysis", "converted", "inventory", "logs", "ai_suggestions")

README = """WishBridge package - {name}
Created {when} on {machine}

Read it now (nothing to install):
  report.html              Open in a browser: assessment, fit check, every converted file and its open items
  open_items.csv           One row per issue left in the converted code (file, line, severity, what to do)
  files.csv                Every converted file with its status (ready / review / needs-fix)
  fit_check.csv            Is this a data warehouse? Recommendation and reasons for every object
  inventory_tables.csv     Source tables with row counts and sizes (when the DBA inventory was imported)
  analysis.xlsx            LakeBridge analyzer workbook (complexity per file, functions used)
  original_code/           The client's code exactly as received ({receipt})
  converted_code/          The code converted for Databricks (hand fixes from overrides/ included)

Continue the work (on any computer with WishBridge):
  project/                 The rest of the project: settings, hand fixes (overrides/), inventory and results.
                           Opening the package puts original_code/ and converted_code/ back into it.
  Open it with:  wishbridge open-package <this zip> --dir C:\\migrations
  or in the app: sidebar > Open a package.

Nothing in this package was sent to Databricks or to the client's database.
"""


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
    if not state.get("convert"):
        raise FileNotFoundError("Nothing to package yet - run Analyze and Convert first.")
    report = build_report(cfg)
    root = cfg.path.parent
    rec = receipt(root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = cfg.out("review_package", f"{cfg.name}-{stamp}.zip")
    c, fit, inv, a = state["convert"], state.get("fit"), state.get("inventory"), state.get("analyze")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        import platform

        z.writestr("README.txt", README.format(
            name=cfg.name, when=datetime.now().strftime("%Y-%m-%d %H:%M"), machine=platform.node(),
            receipt=(f"copied {rec['copied_at']} from {rec['copied_from']}" if rec else "copied from the project's input")
            if include_original else "left out of this package"))
        z.write(report, "report.html")
        z.writestr("files.csv", _csv([{**f, "open_errors": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "error"),
                                       "open_warnings": sum(1 for x in f["findings"] if not x["fixed"] and x["severity"] == "warning")}
                                      for f in c["files"]],
                                     ["file", "kind", "converter", "status", "manual_override", "fixed", "open_errors", "open_warnings"]))
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
                z.write(f, "original_code/" + f.relative_to(cfg.input_dir).as_posix())
        final = Path(c["final_dir"])
        if final.is_dir():
            _add_tree(z, final, "converted_code")
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
        places = {"project/": dest, "original_code/": dest / "input", "converted_code/": dest / "output" / "final"}
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
    return dest
