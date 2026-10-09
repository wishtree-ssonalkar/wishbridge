"""Turn any folder of client code into WishBridge projects.

Clients hand over code as repositories, Visual Studio database projects or plain exports - none of
which contain a project.yml. This module looks at such a folder, guesses the source system, finds
the separate databases inside it, and creates WishBridge projects that read the code where it is
(the client's folder is never written to).
"""

from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import SOURCES, load_config, render_template

CODE_EXTENSIONS = {".sql", ".ddl", ".prc", ".pls", ".pks", ".pkb", ".bteq", ".btq", ".xml", ".dsx", ".isx", ".dtsx", ".zip"}
SKIP_DIRS = {".git", ".github", ".vs", ".vscode", ".idea", "bin", "obj", "node_modules", "__pycache__", ".venv", "output"}
SAMPLE_FILES = 300          # files read for keyword detection
SAMPLE_BYTES = 20_000       # bytes read per file

# Keyword patterns that point at a source dialect (each hit counts once per file).
DIALECT_HINTS: dict[str, list[str]] = {
    "mssql": [r"\[dbo\]\.", r"^\s*GO\s*$", r"\bNVARCHAR\b", r"@@ROWCOUNT|@@IDENTITY|@@ERROR", r"\bIDENTITY\s*\(\s*\d",
              r"\bGETDATE\s*\(", r"\bSET\s+NOCOUNT\b", r"\bNOLOCK\b"],
    "synapse": [r"\bDISTRIBUTION\s*=\s*(?:HASH|ROUND_ROBIN|REPLICATE)", r"\bCLUSTERED\s+COLUMNSTORE\s+INDEX\b"],
    "oracle": [r"\bVARCHAR2\b", r"\bNUMBER\s*\(", r"\bNVL\s*\(", r"\bCREATE\s+OR\s+REPLACE\s+PACKAGE\b",
               r"\bSYSDATE\b", r"\bDBMS_\w+\.", r"\bROWNUM\b", r"^\s*/\s*$"],
    "snowflake": [r"\bVARIANT\b", r"\bIFF\s*\(", r"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:TASK|STREAM|STAGE)\b",
                  r"\bFLATTEN\s*\(", r"\bTIMESTAMP_NTZ\b", r"@\w+/"],
    "teradata": [r"\bMULTISET\b", r"\bPRIMARY\s+INDEX\b", r"^\s*SEL\b", r"^\s*\.(?:LOGON|QUIT|IF)\b",
                 r"\bCOLLECT\s+STAT", r"\bVOLATILE\s+TABLE\b"],
    "redshift": [r"\bDISTKEY\b", r"\bSORTKEY\b", r"\bDISTSTYLE\b", r"\bENCODE\s+\w+", r"\bUNLOAD\s*\("],
    "bigquery": [r"\bINT64\b", r"\bSTRUCT\s*<", r"\bARRAY\s*<", r"`[\w-]+\.[\w-]+\.[\w-]+`", r"\bSAFE_CAST\s*\(",
                 r"\bTIMESTAMP_(?:DIFF|SUB|ADD|TRUNC)\s*\(", r"\bSELECT\s+\*\s+EXCEPT\s*\(", r"\bPARTITION\s+BY\s+DATE\s*\(",
                 r"\bDATE_(?:DIFF|SUB|ADD)\s*\([^)]*\bINTERVAL\b"],
    "netezza": [r"\bDISTRIBUTE\s+ON\b", r"\bORGANIZE\s+ON\b", r"\bGROOM\s+TABLE\b", r"\bGENERATE\s+STATISTICS\b"],
}
_HINTS = {k: [re.compile(p, re.I | re.M) for p in v] for k, v in DIALECT_HINTS.items()}


@dataclass
class Detection:
    source: str                 # SOURCES key
    confidence: str             # "high" (project files) | "medium" | "low"
    reason: str
    scores: dict[str, int] = field(default_factory=dict)


@dataclass
class CodeFolder:
    path: Path
    files: int                  # code files (any CODE_EXTENSIONS)
    sql_files: int
    detection: Detection
    databases: list[tuple[str, Path, int]]  # (name, folder, code files) when the folder holds several databases


def _walk(folder: Path):
    """Code files under folder, skipping tool and build folders."""
    stack = [folder]
    while stack:
        d = stack.pop()
        try:
            entries = list(d.iterdir())
        except OSError:
            continue
        for e in entries:
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.name.startswith("."):
                    stack.append(e)
            elif e.suffix.lower() in CODE_EXTENSIONS:
                yield e


def _project_markers(folder: Path) -> Detection | None:
    """Source system from tool project files, which are decisive."""
    for sqlproj in list(folder.rglob("*.sqlproj"))[:5]:
        text = sqlproj.read_text(encoding="utf-8", errors="ignore")
        dsp = re.search(r"<DSP>([^<]+)</DSP>", text)
        if dsp and "SqlDw" in dsp.group(1):
            return Detection("synapse", "high", f"Visual Studio database project for Azure Synapse ({sqlproj.name})")
        return Detection("mssql", "high", f"Visual Studio SQL Server database project ({sqlproj.name})")
    if next(folder.rglob("*.dtsx"), None):
        return Detection("ssis", "high", "SSIS packages (.dtsx)")
    if next(folder.rglob("*.dsx"), None) or next(folder.rglob("*.isx"), None):
        return Detection("datastage", "high", "DataStage exports (.dsx/.isx)")
    for xml in list(folder.rglob("*.xml"))[:20] + list(folder.rglob("*.XML"))[:20]:
        head = xml.read_text(encoding="utf-8", errors="ignore")[:4000]
        if "<POWERMART" in head:
            return Detection("informatica", "high", f"Informatica PowerCenter export ({xml.name})")
        if "<DSExport" in head:
            return Detection("datastage", "high", f"DataStage XML export ({xml.name})")
    for z in list(folder.rglob("*.zip"))[:20]:
        if _is_iics_export(z):
            return Detection("informatica-cloud", "high", f"Informatica Cloud (IICS) export package ({z.name})")
    return None


def _is_iics_export(path: Path) -> bool:
    """Informatica Cloud exports are .zip packages with an exportMetadata JSON file."""
    import zipfile

    try:
        with zipfile.ZipFile(path) as z:
            return any(Path(n).name.lower().startswith("exportmetadata") for n in z.namelist())
    except (zipfile.BadZipFile, OSError):
        return False


def detect_source(folder: str | Path) -> Detection:
    folder = Path(folder)
    marker = _project_markers(folder)
    if marker:
        return marker
    scores: Counter[str] = Counter()
    for i, f in enumerate(_walk(folder)):
        if i >= SAMPLE_FILES:
            break
        try:
            text = f.open(encoding="utf-8", errors="ignore").read(SAMPLE_BYTES)
        except OSError:
            continue
        for key, patterns in _HINTS.items():
            scores[key] += sum(1 for p in patterns if p.search(text))
    # Synapse code is T-SQL too: only call it Synapse when its own markers are present.
    if scores["synapse"]:
        scores["synapse"] += scores["mssql"]
    if not scores or max(scores.values()) == 0:
        return Detection("mssql", "low", "No recognisable dialect keywords - please choose the source system", dict(scores))
    (best, top), *rest = scores.most_common(2) + [("", 0)]
    second = rest[0][1] if rest else 0
    confidence = "medium" if (top >= 3 and top >= 2 * second) or (top >= 2 and second == 0) else "low"
    return Detection(best, confidence, f"{SOURCES[best].analyzer_tech} keywords found in the code "
                                       f"({top} matches; next best {rest[0][0] or '-'} {second})", dict(scores))


def find_databases(folder: str | Path) -> list[tuple[str, Path, int]]:
    """Sub-folders that each hold a separate database's code (e.g. TenantDB, AuditDB).

    Wrapper folders with a single sub-folder and no code of their own are skipped. Returns [] when the
    code isn't split into several databases."""
    folder = Path(folder)
    for _ in range(3):  # descend through wrapper folders such as <repo>/<ProjectName>/
        subs = [d for d in folder.iterdir() if d.is_dir() and d.name not in SKIP_DIRS and not d.name.startswith(".")]
        own = [f for f in folder.iterdir() if f.is_file() and f.suffix.lower() in CODE_EXTENSIONS]
        with_code = [(d, sum(1 for _ in _walk(d))) for d in subs]
        with_code = [(d, n) for d, n in with_code if n]
        if len(with_code) == 1 and len(own) <= 1:
            folder = with_code[0][0]
            continue
        break
    else:
        return []
    # Only folders that look like databases: several of them, each with a fair share of objects.
    candidates = [(d.name, d, n) for d, n in with_code if n >= 3 and d.name.lower() not in {"security", "scripts", "docs"}]
    return sorted(candidates, key=lambda c: -c[2]) if len(candidates) >= 2 else []


def inspect(folder: str | Path) -> CodeFolder:
    folder = Path(folder).expanduser().resolve()
    if not folder.is_dir():
        raise ValueError(f"Folder not found: {folder}")
    files = list(_walk(folder))
    if not files:
        raise ValueError(f"No SQL or ETL code files found in {folder}")
    return CodeFolder(folder, len(files), sum(1 for f in files if f.suffix.lower() == ".sql"),
                      detect_source(folder), find_databases(folder))


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "project"


def create_project_for_code(code_dir: str | Path, parent: str | Path, name: str, source: str,
                            catalog: str = "main", host: str = "") -> Path:
    """Create a WishBridge project in parent/name that reads code_dir in place."""
    if source not in SOURCES:
        raise ValueError(f"Unknown source '{source}'")
    name = name.strip()
    if not name or any(c in name for c in '\\/:*?"<>|'):
        raise ValueError("Project name must be a plain folder name")
    code_dir = Path(code_dir).expanduser().resolve()
    root = Path(parent).expanduser().resolve() / name
    if root == code_dir or root in code_dir.parents or code_dir in root.parents:
        raise ValueError("Create the project outside the client's code folder (so their folder is never written to)")
    if (root / "project.yml").exists():
        raise ValueError(f"{root} already has a project.yml - open it instead, or choose another name")
    root.mkdir(parents=True, exist_ok=True)
    raw = yaml.safe_load(render_template(name, source))
    schema = "wishbridge_" + re.sub(r"[^a-z0-9_]", "_", name.lower())
    raw["input"] = str(code_dir)
    raw["databricks"].update({"catalog": catalog, "schema": schema, **({"host": host} if host else {})})
    raw["schema_map"] = {"dbo": f"{catalog}.{schema}"} if source in ("mssql", "synapse") else {}
    text = (f"# WishBridge project for the code in {code_dir}\n"
            "# The client's folder is only read; everything WishBridge produces goes into this folder.\n")
    (root / "project.yml").write_text(text + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    load_config(root / "project.yml")  # fail now if anything is off
    return root


def create_projects_for_code(info: CodeFolder, parent: str | Path, base_name: str, source: str,
                             split: bool, catalog: str = "main", host: str = "") -> list[Path]:
    """One project for the whole folder, or one per database when split is True."""
    if split and info.databases:
        return [create_project_for_code(path, parent, f"{base_name}-{_slug(db)}", source, catalog, host)
                for db, path, _ in info.databases]
    return [create_project_for_code(info.path, parent, base_name, source, catalog, host)]


def suggested_name(folder: str | Path) -> str:
    return _slug(Path(folder).name)
