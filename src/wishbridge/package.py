"""One zip with everything a reviewer needs after the offline assessment - no WishBridge install required."""

from __future__ import annotations

import csv
import io
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .state import load_state

README = """WishBridge review package - {name}
Created {when} from the project in {root}

  report.html              Open in a browser: assessment, fit check, every converted file and its open items
  open_items.csv           One row per issue left in the converted code (file, line, severity, what to do)
  files.csv                Every converted file with its status (ready / review / needs-fix)
  fit_check.csv            Is this a data warehouse? Recommendation and reasons for every object
  inventory_tables.csv     Source tables with row counts and sizes (when the DBA inventory was imported)
  analysis.xlsx            LakeBridge analyzer workbook (complexity per file, functions used)
  converted_code/          The code converted for Databricks (hand fixes from overrides/ included)
  original_code/           The client's code exactly as received ({receipt})

Nothing in this package was sent to Databricks or to the client's database.
"""


def _csv(rows: list[dict[str, Any]], columns: list[str]) -> str:
    buf = io.StringIO()
    w = csv.DictWriter(buf, fieldnames=columns, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return buf.getvalue()


def build_package(cfg: ProjectConfig, include_original: bool = True) -> Path:
    from .report import build_report
    from .snapshot import receipt

    state = load_state(cfg)
    if not state.get("convert"):
        raise FileNotFoundError("Nothing to package yet - run Analyze and Convert first.")
    report = build_report(cfg)
    root = cfg.path.parent
    rec = receipt(root)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    out = cfg.out("review_package", f"{cfg.name}-review-{stamp}.zip")
    c, fit, inv, a = state["convert"], state.get("fit"), state.get("inventory"), state.get("analyze")
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("README.txt", README.format(
            name=cfg.name, when=datetime.now().strftime("%Y-%m-%d %H:%M"), root=root,
            receipt=(f"copied {rec['copied_at']} from {rec['copied_from']}, fingerprint {rec['fingerprint'][:16]}"
                     if rec else "read in place")))
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
        final = Path(c["final_dir"])
        for f in sorted(p for p in final.rglob("*") if p.is_file()):
            z.write(f, "converted_code/" + f.relative_to(final).as_posix())
        if include_original and cfg.input_dir.is_dir():
            from .snapshot import _files

            for f in _files(cfg.input_dir):
                z.write(f, "original_code/" + f.relative_to(cfg.input_dir).as_posix())
            if rec:
                z.write(root / "code_received.json", "original_code_receipt.json")
    return out
