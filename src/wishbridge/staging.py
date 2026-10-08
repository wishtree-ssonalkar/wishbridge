"""Give LakeBridge a clean copy of just the source code.

Client folders carry build output (bin/, obj/ with Model.xml and copies of the scripts), tool folders
(.git, .vs) and project files (publish.xml, .dacpac). LakeBridge reads everything in the folder it is
given, which inflates the assessment and wastes conversion time, so WishBridge copies only the code
files - with their relative paths - into output/staged_input and points LakeBridge there. The
client's folder is never modified.
"""

from __future__ import annotations

import json
import re
import shutil
from dataclasses import replace
from pathlib import Path

from .config import ETL_SOURCES, ProjectConfig

TSQL_SOURCES = frozenset({"mssql", "synapse"})

SQL_SUFFIXES = {".sql", ".ddl", ".prc", ".pls", ".pks", ".pkb", ".bteq", ".btq"}
ETL_SUFFIXES = SQL_SUFFIXES | {".xml", ".dsx", ".isx", ".dtsx"}


def source_files(cfg: ProjectConfig) -> list[Path]:
    """Relative paths of the code files LakeBridge should see (build and tool folders excluded)."""
    from .discover import _walk

    allowed = ETL_SUFFIXES if cfg.source.key in ETL_SOURCES else SQL_SUFFIXES
    if not cfg.input_dir.exists():
        return []
    return sorted(p.relative_to(cfg.input_dir) for p in _walk(cfg.input_dir) if p.suffix.lower() in allowed)


# --- Safe tidy-ups of SQL Server source before conversion (only in the staged copy) ------------------------
# Generated (SSDT / SSMS) scripts use patterns LakeBridge's parser fails on, which makes it give up on the
# whole file. Each rewrite below keeps the meaning on Databricks and is reported per file.
I, M = re.IGNORECASE, re.MULTILINE
_TSQL_PREP: list[tuple[str, str, re.Pattern, str, str]] = [
    ("with-check", "info", re.compile(r"\bWITH\s+(?:NO)?CHECK\s+(ADD\s+CONSTRAINT)\b", I), r"\1",
     "Removed WITH CHECK / WITH NOCHECK from ADD CONSTRAINT (it only controls validation of existing rows)"),
    ("check-constraint", "info",
     re.compile(r"^[ \t]*ALTER\s+TABLE\s+[^\n;]*?\s(?:NO)?CHECK\s+CONSTRAINT\s+[^\n;]*;?[ \t]*\r?\n?", I | M), "",
     "Removed ALTER TABLE ... CHECK / NOCHECK CONSTRAINT (enabling or disabling constraints has no Databricks equivalent)"),
    ("temporal-columns", "warning", re.compile(r"\s+GENERATED\s+ALWAYS\s+AS\s+ROW\s+(?:START|END)(?:\s+HIDDEN)?", I), "",
     "Removed system-versioning (temporal table) clauses before conversion: Delta keeps history itself - review the "
     "period columns, drop the history table and query history with VERSION AS OF / TIMESTAMP AS OF"),
    ("temporal-period", "warning", re.compile(r",?\s*PERIOD\s+FOR\s+SYSTEM_TIME\s*\([^)]*\)", I), "", ""),
    ("temporal-versioning", "warning",
     re.compile(r"\s*WITH\s*\(\s*SYSTEM_VERSIONING\s*=\s*ON(?:\s*\([^()]*\))?\s*\)", I), "", ""),
]


def prepare_tsql(text: str) -> tuple[str, list[dict[str, str]]]:
    """Apply the T-SQL tidy-ups; returns the new text and one note per kind of change made."""
    notes: dict[str, dict[str, str]] = {}
    for key, severity, pattern, repl, message in _TSQL_PREP:
        text, n = pattern.subn(repl, text)
        if n:
            group = "temporal" if key.startswith("temporal") else key
            if group not in notes:
                notes[group] = {"rule": f"prep-{group}", "severity": severity,
                                "message": message or _TSQL_PREP[2][4]}
    return text, list(notes.values())


def prep_manifest(cfg: ProjectConfig) -> Path:
    return cfg.output_dir / "logs" / "prepared.json"


def staged(cfg: ProjectConfig) -> ProjectConfig:
    """Copy the code files to output/staged_input (tidying SQL Server source) and return a config reading from there."""
    files = source_files(cfg)
    if not files:
        raise FileNotFoundError(f"No source code files in {cfg.input_dir}")
    dest = cfg.output_dir / "staged_input"
    if dest.exists():
        shutil.rmtree(dest)
    manifest: dict[str, list[dict[str, str]]] = {}
    for rel in files:
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        if cfg.source.key in TSQL_SOURCES and rel.suffix.lower() == ".sql":
            text = (cfg.input_dir / rel).read_text(encoding="utf-8-sig", errors="replace")
            text, notes = prepare_tsql(text)
            target.write_text(text, encoding="utf-8")
            if notes:
                manifest[rel.as_posix()] = notes
        else:
            shutil.copy2(cfg.input_dir / rel, target)
    m = prep_manifest(cfg)
    m.parent.mkdir(parents=True, exist_ok=True)
    m.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return replace(cfg, input_dir=dest)


def prepared_notes(cfg: ProjectConfig) -> dict[str, list[dict[str, str]]]:
    m = prep_manifest(cfg)
    return json.loads(m.read_text(encoding="utf-8")) if m.exists() else {}
