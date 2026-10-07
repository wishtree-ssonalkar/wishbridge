"""Step 5 - Reconcile: check that migrated data matches the source.

Quick mode (default) runs entirely in Databricks: row counts plus SUM of
every numeric column, source (federation catalog) vs target. Full mode
delegates to `databricks labs lakebridge reconcile` for row/column-level
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


def _numeric_columns(wh: Warehouse, table: str) -> list[str]:
    cols = []
    for row in wh.run(f"DESCRIBE TABLE {table}").rows:
        name, dtype = (row[0] or "").strip(), (row[1] or "").lower()
        if not name or name.startswith("#"):
            break
        if dtype.startswith(_NUMERIC):
            cols.append(name)
    return cols


def _profile(wh: Warehouse, table: str, cols: list[str]) -> list[Any]:
    sums = "".join(f", SUM(CAST(`{c}` AS DECIMAL(38, 6)))" for c in cols)
    return wh.run(f"SELECT COUNT(*){sums} FROM {table}").rows[0]


def _eq(a: Any, b: Any) -> bool:
    try:
        return Decimal(str(a)) == Decimal(str(b)) if a is not None and b is not None else a == b
    except InvalidOperation:
        return str(a) == str(b)


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
        src = f"{cfg.source_catalog}.{t.source}"
        entry: dict[str, Any] = {"source": t.source, "target": t.target}
        try:
            cols = _numeric_columns(wh, t.target)
            s, d = _profile(wh, src, cols), _profile(wh, t.target, cols)
            checks = [{"check": "row_count", "source": s[0], "target": d[0], "match": _eq(s[0], d[0])}]
            checks += [{"check": f"sum({c})", "source": s[i + 1], "target": d[i + 1], "match": _eq(s[i + 1], d[i + 1])}
                       for i, c in enumerate(cols)]
            entry.update(checks=checks, status="match" if all(c["match"] for c in checks) else "mismatch")
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
