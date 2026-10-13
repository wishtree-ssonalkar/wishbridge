"""Run every example project end to end on Databricks and write examples/RESULTS.md.

    python examples/run_all.py                 # all examples
    python examples/run_all.py oracle teradata # some of them
    python examples/run_all.py --cleanup       # drop the demo schemas afterwards

Per example: create the stand-in source (setup_demo_source.sql), then analyze, convert,
deploy (--recreate), load, execute notebooks (ETL examples), reconcile and report.
Uses the DEFAULT Databricks profile and the `workspace` catalog set in each project.yml.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path

from wishbridge.analysis import run_analyze
from wishbridge.config import load_config
from wishbridge.convert import run_convert
from wishbridge.data import run_load
from wishbridge.dbx import Warehouse
from wishbridge.deploy import run_deploy
from wishbridge.execute import notebooks_to_run, run_execute
from wishbridge.reconcile import run_reconcile
from wishbridge.report import build_report
from wishbridge.sqltext import split_statements

HERE = Path(__file__).parent
ORDER = ["mssql", "synapse", "oracle", "snowflake", "teradata", "redshift", "bigquery", "netezza", "informatica", "ssis"]


def run_example(name: str, wh_cache: dict) -> dict:
    folder = HERE / f"{name}-demo"
    cfg = load_config(folder / "project.yml")
    wh = wh_cache.setdefault("wh", Warehouse(cfg))
    row = {"example": name, "source": cfg.source.analyzer_tech, "converter": cfg.transpiler}
    t0 = time.monotonic()
    try:
        for stmt in split_statements((folder / "setup_demo_source.sql").read_text(encoding="utf-8")):
            wh.run(stmt)
        a = run_analyze(cfg)
        row["files"] = len(a["programs"]) or "-"
        c = run_convert(cfg)
        s = c["summary"]
        auto_ready = sum(1 for f in c["files"] if f["status"] == "ready" and not f.get("manual_override"))
        row["converted"] = f"{auto_ready} ready, {s['manual_overrides']} hand-fixed, {s['files'] - auto_ready - s['manual_overrides']} other"
        row["open"] = s["open_errors"] + s["open_warnings"]
        d = run_deploy(cfg, recreate=True, wh=wh)
        row["deployed"] = f"{d['summary']['statements_ok']}/{d['summary']['statements']}"
        ld = run_load(cfg, execute=True, wh=wh)
        row["loaded"] = f"{ld['summary']['loaded']}/{ld['summary']['tables']}"
        if notebooks_to_run(cfg):
            e = run_execute(cfg, wh=wh)
            row["notebooks"] = f"{e['summary']['succeeded']}/{e['summary']['notebooks']}"
        r = run_reconcile(cfg, wh=wh)
        row["reconciled"] = f"{r['summary']['matched']}/{r['summary']['tables']}"
        build_report(cfg)
        ok = (d["summary"]["statements_ok"] == d["summary"]["statements"] and ld["summary"]["failed"] == 0
              and r["summary"]["matched"] == r["summary"]["tables"])
        row["result"] = "PASS" if ok else "CHECK"
    except Exception as exc:  # keep going with the other examples
        row["result"] = f"ERROR: {str(exc).splitlines()[0][:150]}"
    row["seconds"] = round(time.monotonic() - t0)
    return row


def cleanup(names: list[str], wh: Warehouse) -> None:
    for name in names:
        cfg = load_config(HERE / f"{name}-demo" / "project.yml")
        for schema in {cfg.target_schema, f"{cfg.catalog}.{cfg.schema}_src" if name != "mssql" else f"{cfg.catalog}.wishbridge_demo_src"}:
            wh.run(f"DROP SCHEMA IF EXISTS {schema} CASCADE")
            print("dropped", schema)


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    names = [n for n in ORDER if not args or n in args]
    cache: dict = {}
    if "--cleanup" in sys.argv:
        cleanup(names, Warehouse(load_config(HERE / "mssql-demo" / "project.yml")))
        return
    rows = []
    for name in names:
        print(f"== {name} ...", flush=True)
        row = run_example(name, cache)
        print("   ", row, flush=True)
        rows.append(row)
    cols = ["example", "source", "converter", "files", "converted", "open", "deployed", "loaded", "notebooks",
            "reconciled", "result", "seconds"]
    lines = [f"# WishBridge example results", "",
             f"Run {datetime.now():%Y-%m-%d %H:%M} with `python examples/run_all.py` on a Databricks SQL warehouse.", "",
             "| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    lines += ["| " + " | ".join(str(r.get(c, "-")) for c in cols) + " |" for r in rows]
    lines += ["", "*converted*: files ready straight from LakeBridge + WishBridge rules / files fixed by hand in "
              "`overrides/` / files still open. *open*: open errors and warnings on the remaining files. "
              "*deployed*: statements created or EXPLAIN-checked on Databricks. *reconciled*: tables whose counts, "
              "sums and row checksums match the source."]
    (HERE / "RESULTS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
