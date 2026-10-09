"""Read the code that lives inside the database (SQL Server, Azure SQL, Synapse): procedures, views, functions
and triggers, one .sql file per object in <project>/database_code/.

Many warehouses have no up-to-date repository: what runs is what is in the database. The definitions come
from the catalog (sys.sql_modules) over the same read-only connection as the table list; no data is read.
When the project also has code files, WishBridge compares the two so the team sees what differs, and the
team chooses which one the run assesses.
"""

from __future__ import annotations

import re
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import ProjectConfig
from .state import save_step

FOLDER = "database_code"

QUERY = """SELECT s.name AS schema_name, o.name AS object_name, RTRIM(o.type) AS type,
       m.definition, CAST(ISNULL(OBJECTPROPERTY(o.object_id, 'IsEncrypted'), 0) AS int) AS encrypted
FROM sys.objects o
JOIN sys.schemas s ON s.schema_id = o.schema_id
LEFT JOIN sys.sql_modules m ON m.object_id = o.object_id
WHERE o.is_ms_shipped = 0 AND o.type IN ('P', 'V', 'FN', 'IF', 'TF', 'TR')
ORDER BY s.name, o.type, o.name"""

# Created by SSMS for database diagrams, not by the client
TOOL_OBJECTS = frozenset({"fn_diagramobjects", "sp_alterdiagram", "sp_creatediagram", "sp_dropdiagram",
                          "sp_helpdiagramdefinition", "sp_helpdiagrams", "sp_renamediagram", "sp_upgraddiagrams"})

KINDS = {"P": "Procedures", "V": "Views", "FN": "Functions", "IF": "Functions", "TF": "Functions", "TR": "Triggers"}
LABEL = {"Procedures": "procedure", "Views": "view", "Functions": "function", "Triggers": "trigger"}


def _safe(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "_", name).strip() or "_"


def folder(cfg: ProjectConfig) -> Path:
    return cfg.path.parent / FOLDER


def write_objects(cfg: ProjectConfig, rows: list[tuple], read_from: str) -> dict[str, Any]:
    """Save each definition as <schema>/<kind>/<schema>.<name>.sql (the folder is WishBridge's own: rewritten)."""
    dest = folder(cfg)
    if dest.exists():
        shutil.rmtree(dest)
    counts: dict[str, int] = {}
    encrypted, unreadable, objects = [], [], []
    for schema, name, otype, definition, is_encrypted in rows:
        if str(name).lower() in TOOL_OBJECTS:
            continue
        kind = KINDS.get(str(otype).strip(), "Other")
        full = f"{schema}.{name}"
        if not definition:
            # NULL definition: encrypted (WITH ENCRYPTION) or the login lacks VIEW DEFINITION
            (encrypted if is_encrypted else unreadable).append(f"{full} ({LABEL.get(kind, kind)})")
            continue
        rel = Path(_safe(schema)) / kind / f"{_safe(schema)}.{_safe(name)}.sql"
        path = dest / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        text = definition.replace("\r\n", "\n").strip("\n")
        path.write_text(f"{text}\nGO\n", encoding="utf-8")
        counts[kind] = counts.get(kind, 0) + 1
        objects.append({"name": full, "kind": kind, "file": rel.as_posix()})
    result = {"read_from": read_from, "read_at": datetime.now().isoformat(timespec="seconds"), "folder": str(dest),
              "counts": counts, "objects": objects, "encrypted": encrypted, "unreadable": unreadable}
    save_step(cfg, "dbcode", result)
    return result


def read(cfg: ProjectConfig, conn, read_from: str) -> dict[str, Any]:
    cur = conn.cursor()
    cur.execute(QUERY)
    return write_objects(cfg, [tuple(r) for r in cur.fetchall()], read_from)


# --- Compare with the code files the client gave -----------------------------------------------------------
_CREATE = re.compile(r"\bCREATE\s+(?:OR\s+ALTER\s+)?(PROC(?:EDURE)?|VIEW|FUNCTION|TRIGGER)\s+((?:\[[^\]]+\]|\"[^\"]+\"|[\w@#$]+)"
                     r"(?:\s*\.\s*(?:\[[^\]]+\]|\"[^\"]+\"|[\w@#$]+))?)", re.IGNORECASE)


def _name(raw: str) -> str:
    parts = [p.strip().strip('[]"').lower() for p in raw.split(".")]
    return ".".join(parts if len(parts) > 1 else ["dbo"] + parts)


def _norm(text: str) -> str:
    """Text without comments, brackets, case and spacing differences (CREATE OR ALTER counts as CREATE)."""
    text = re.sub(r"--[^\n]*|/\*.*?\*/", " ", text, flags=re.DOTALL)
    text = re.sub(r"[\[\]\"]", "", text.lower())
    text = re.sub(r"\bcreate\s+or\s+alter\b", "create", text)
    text = re.sub(r"\bproc\b", "procedure", text)
    text = re.sub(r"\bgo\b", " ", text)
    return re.sub(r"[\s;]+", " ", text).strip()


def _split_objects(text: str) -> dict[str, str]:
    """name -> normalized text of each CREATE PROC/VIEW/FUNCTION/TRIGGER in a file (up to the next one)."""
    found = list(_CREATE.finditer(text))
    out = {}
    for i, m in enumerate(found):
        end = found[i + 1].start() if i + 1 < len(found) else len(text)
        out[_name(m.group(2))] = _norm(text[m.start():end])
    return out


def compare(cfg: ProjectConfig, files_dir: Path) -> dict[str, Any]:
    """Objects in the database vs the code files: same, different, only in the database, only in the files."""
    from .staging import read_source

    in_files: dict[str, str] = {}
    for p in sorted(files_dir.rglob("*.sql")) if files_dir.is_dir() else []:
        try:
            in_files.update(_split_objects(read_source(p)))
        except OSError:
            continue
    in_db: dict[str, str] = {}
    for p in sorted(folder(cfg).rglob("*.sql")):
        in_db.update(_split_objects(p.read_text(encoding="utf-8")))
    same, different, only_db = [], [], []
    for name, text in sorted(in_db.items()):
        if name not in in_files:
            only_db.append(name)
        elif text in in_files[name] or in_files[name] in text:
            same.append(name)
        else:
            different.append(name)
    only_files = sorted(n for n in in_files if n not in in_db)
    result = {"same": same, "different": different, "only_in_database": only_db, "only_in_files": only_files}
    save_step(cfg, "dbcode_compare", result)
    return result
