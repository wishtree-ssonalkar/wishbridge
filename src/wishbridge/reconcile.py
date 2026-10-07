"""Step 5 - Reconcile: check that migrated data matches the source.

Quick mode (default) runs entirely in Databricks and compares, source
(federation catalog) vs target:
  - row count
  - SUM of every numeric column present on both sides
  - a whole-row checksum over every shared column (values compared as text),
    which catches changed strings, dates and numbers alike
Full mode delegates to `databricks labs lakebridge reconcile` for row/column-level
comparison (configure it once with `databricks labs lakebridge configure-reconcile`).
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from . import lakebridge
from .config import ProjectConfig
from .dbx import SqlError, Warehouse
from .state import save_step

_NUMERIC = ("tinyint", "smallint", "int", "bigint", "decimal", "double", "float", "long", "short", "byte")


def _columns(wh: Warehouse, table: str) -> dict[str, str]:
    """Column name (lower-case) -> data type, in table order."""
    cols: dict[str, str] = {}
    for row in wh.run(f"DESCRIBE TABLE {table}").rows:
        name, dtype = (row[0] or "").strip(), (row[1] or "").lower()
        if not name or name.startswith("#"):
            break
        cols[name.lower()] = dtype
    return cols


def _profile_sql(table: str, numeric: list[str], shared: list[str]) -> str:
    sums = "".join(f", SUM(CAST(`{c}` AS DECIMAL(38, 6)))" for c in numeric)
    checksum = ""
    if shared:
        args = ", ".join(f"CAST(`{c}` AS STRING)" for c in shared)
        checksum = f", SUM(CAST(xxhash64({args}) AS DECIMAL(38, 0)))"
    return f"SELECT COUNT(*){sums}{checksum} FROM {table}"


def _eq(a: Any, b: Any) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b)) if a is not None and b is not None else a == b
    except InvalidOperation:
        return str(a) == str(b)


def compare_table(wh: Warehouse, source: str, target: str) -> dict[str, Any]:
    tgt_cols = _columns(wh, target)
    src_cols = _columns(wh, source)
    shared = [c for c in tgt_cols if c in src_cols]
    numeric = [c for c in shared if tgt_cols[c].startswith(_NUMERIC) and src_cols[c].startswith(_NUMERIC)]
    s = wh.run(_profile_sql(source, numeric, shared)).rows[0]
    d = wh.run(_profile_sql(target, numeric, shared)).rows[0]

    checks = [{"check": "row_count", "source": s[0], "target": d[0], "match": _eq(s[0], d[0])}]
    checks += [{"check": f"sum({c})", "source": s[i + 1], "target": d[i + 1], "match": _eq(s[i + 1], d[i + 1])}
               for i, c in enumerate(numeric)]
    if shared:
        k = len(numeric) + 1
        checks.append({"check": f"row_checksum({len(shared)} columns)", "source": s[k], "target": d[k],
                       "match": _eq(s[k], d[k])})
    only_src = [c for c in src_cols if c not in tgt_cols]
    only_tgt = [c for c in tgt_cols if c not in src_cols]
    entry: dict[str, Any] = {"checks": checks, "status": "match" if all(c["match"] for c in checks) else "mismatch"}
    if only_src or only_tgt:
        entry["column_differences"] = {"only_in_source": only_src, "only_in_target": only_tgt}
    return entry


def run_reconcile(cfg: ProjectConfig, full: bool = False, wh: Warehouse | None = None) -> dict[str, Any]:
    if full:
        out = lakebridge.reconcile(cfg)
        result = {"mode": "full", "output": out[-4000:], "summary": {"tables": None, "matched": None}}
        save_step(cfg, "reconcile", result)
        return result

    if not cfg.source_catalog:
        raise SqlError("Quick reconcile reads the source through a federation catalog: set data.source_catalog, "
                       "or use --full for LakeBridge reconcile.")
    wh = wh or Warehouse(cfg)
    tables = []
    for t in cfg.tables:
        entry: dict[str, Any] = {"source": t.source, "target": t.target}
        try:
            entry.update(compare_table(wh, f"{cfg.source_catalog}.{t.source}", t.target))
        except SqlError as e:
            entry.update(status="error", error=str(e).splitlines()[0][:400])
        tables.append(entry)

    result = {
        "mode": "quick",
        "tables": tables,
        "summary": {
            "tables": len(tables),
            "matched": sum(t["status"] == "match" for t in tables),
            "mismatched": sum(t["status"] == "mismatch" for t in tables),
            "errors": sum(t["status"] == "error" for t in tables),
        },
    }
    save_step(cfg, "reconcile", result)
    return result
