"""Migration fit: which objects belong on Databricks and which should stay with the application.

Databricks is the home for analytics - reporting, ETL, dashboards, AI. It is not a replacement for the
database behind a live application (many small reads and writes, row locking, identity keys handed
back to the app). Client folders often hold both kinds of code, and LakeBridge converts whatever it is
given. This step sorts every object into:

    migrate - reporting / analytics / ETL logic: move it to Databricks
    data    - tables (and seed-data scripts): create them on Databricks and copy the data
    keep    - application logic (CRUD, lookups for screens, paging, login, e-mail): keep it on the source
    skip    - not needed on Databricks (security, indexes, deployment scripts, framework tables)
    review  - not clear from the code: decide by hand

and gives a verdict for the whole database. Everything is a heuristic based on names and code patterns;
each decision lists its reasons so a person can overrule it.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .config import ETL_SOURCES, ProjectConfig
from .sqltext import mask
from .staging import read_source, source_files
from .state import save_step

I = re.IGNORECASE

CATEGORY_LABELS = {
    "migrate": "Move to Databricks",
    "data": "Copy the data",
    "keep": "Keep on source",
    "skip": "Not needed",
    "review": "Decide",
}

_CREATE = re.compile(
    r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?(?:MATERIALIZED\s+|SECURE\s+)?"
    r"(TABLE|VIEW|PROC(?:EDURE)?|FUNCTION|TRIGGER|USER|ROLE|LOGIN|SCHEMA|TYPE|SEQUENCE|SYNONYM|MACRO|PACKAGE(?:\s+BODY)?|"
    r"(?:UNIQUE\s+)?(?:(?:NON)?CLUSTERED\s+)?(?:COLUMNSTORE\s+)?INDEX|FULLTEXT\s+(?:INDEX|CATALOG)|PARTITION\s+(?:FUNCTION|SCHEME))\b\s*"
    r"(?:IF\s+NOT\s+EXISTS\s+)?([^\s(;]*)", I)
_SECURITY = re.compile(r"\b(?:ALTER\s+ROLE|sp_addrolemember|GRANT|DENY|REVOKE)\b", I)
_WRITES = re.compile(r"\b(?:INSERT|UPDATE|DELETE|MERGE)\b", I)

# Name words (camelCase / snake_case split) that say what a routine is for.
REPORT_WORDS = {"report", "reports", "rpt", "dashboard", "summary", "analytics", "analytic", "analysis", "kpi", "kpis",
                "metric", "metrics", "statistics", "stats", "trend", "trends", "aggregate", "aggregates", "utilization",
                "forecast", "count", "counts", "total", "totals", "bench", "aging", "insight", "insights"}
ETL_WORDS = {"etl", "load", "migrate", "migration", "sync", "import", "export", "stage", "staging", "refresh",
             "rollout", "populate", "transform", "merge", "bulk", "batch", "archive", "purge", "snapshot", "extract",
             "ingest", "feed", "recalculate", "rebuild", "postprocessing", "preprocessing"}
APP_WORDS = {"insert", "add", "create", "update", "upsert", "save", "delete", "remove", "set", "login", "logout",
             "authenticate", "auth", "validate", "verify", "exists", "exist", "email", "password", "token", "session",
             "paged", "paging", "search", "register", "approve", "reject", "assign", "notify", "send", "lock", "unlock"}

_APP_BODY = [
    (re.compile(r"\bSCOPE_IDENTITY\s*\(|@@IDENTITY\b|\bIDENT_CURRENT\s*\(", I), 2, "returns the new row's identity to the caller"),
    (re.compile(r"\bOUTPUT\s+INSERTED\.", I), 1, "returns inserted rows to the caller (OUTPUT INSERTED)"),
    (re.compile(r"\bINSERT\s+(?:INTO\s+)?[\w.\[\]`\"]+\s*(?:\([^)]*\)\s*)?VALUES\s*\(\s*[@:]", I), 2,
     "inserts single rows from parameters"),
    (re.compile(r"\bOFFSET\s+[@:]?\w+\s+ROWS\s+FETCH\b|[@:]Page(?:Size|Number|Index|No)\b", I), 2, "pages results for a screen"),
    (re.compile(r"\bsp_send_dbmail\b|\bHASHBYTES\s*\(|\bPWDENCRYPT\b", I), 2, "sends e-mail or hashes passwords"),
    (re.compile(r"\b(?:UPDLOCK|ROWLOCK|HOLDLOCK|sp_getapplock|FOR\s+UPDATE)\b", I), 1, "takes row locks"),
    (re.compile(r"\b(?:UPDATE|DELETE)\b[^;]*?\bWHERE\b[^;]*?=\s*[@:]\w+", I), 1, "updates or deletes rows chosen by a parameter"),
    (re.compile(r"\bRAISERROR\b|\bTHROW\s+\d", I), 1, "raises business errors to the caller"),
]
_REPORT_BODY = [
    (re.compile(r"\bGROUP\s+BY\b", I), 1, "aggregates (GROUP BY)", "report"),
    (re.compile(r"\bOVER\s*\(", I), 1, "window functions", "report"),
    (re.compile(r"\bPIVOT\b", I), 1, "pivots data", "report"),
    (re.compile(r"\bINSERT\s+(?:INTO\s+)?[\w.\[\]`\"#]+\s*(?:\([^)]*\)\s*)?(?:WITH\b[^;]*?)?SELECT\b", I), 1,
     "set-based INSERT ... SELECT", "etl"),
    (re.compile(r"\bMERGE\s+(?:INTO\s+)?[\w.\[\]`\"]+(?:\s+(?:AS\s+)?\w+)?\s+USING\b", I), 1, "MERGE", "etl"),
    (re.compile(r"\bTRUNCATE\s+TABLE\b", I), 1, "truncates and reloads tables", "etl"),
]
_PARAMS = re.compile(r"\bCREATE\s+(?:OR\s+(?:ALTER|REPLACE)\s+)?PROC(?:EDURE)?\s+[^\s(]+\s*(?:\(\s*\w|[@:]\w)", I)
_TOKEN = re.compile(r"[\w$#@]+")
_JOIN = re.compile(r"\bJOIN\b", I)
_AGG = re.compile(r"\b(?:SUM|AVG|COUNT|MIN|MAX)\s*\(", I)

# Table-level evidence
_FRAMEWORK_TABLES = re.compile(r"^(?:__EFMigrationsHistory|__MigrationHistory|sysdiagrams|flyway_schema_history|"
                               r"databasechangelog(?:lock)?|schema_migrations|AspNet\w+|HangFire\.\w+|DatabaseLog)$", I)
_LOG_TABLE = re.compile(r"(?:^|_)(?:exception|error|audit)_?logs?$|ExceptionLogs?$|ErrorLogs?$|_error$", I)
_WAREHOUSE_TABLE = re.compile(r"^(?:fact|dim|agg|stg|staging|ods|edw|dw)_|^(?:Fact|Dim)[A-Z]|_(?:fact|dim|snapshot)$", I)
_AUDIT_COLS = re.compile(r"\b(?:Created|Modified|Updated)(?:_?By|_?On|_?Date|_?At)\b|\bIsDeleted\b|\bis_deleted\b", I)
_ROWVERSION = re.compile(r"\bROWVERSION\b|\[?\w+\]?\s+TIMESTAMP\s+NOT\s+NULL", I)
_TEMPORAL = re.compile(r"\bSYSTEM_VERSIONING\s*=\s*ON\b", I)
_DEPLOY_SCRIPT = re.compile(r"(?:^|[./\\])Script\.(?:Pre|Post)Deployment", I)


def _words(name: str) -> set[str]:
    name = name.split(".")[-1].strip("[]`\"")
    return {w.lower() for w in re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])", name.replace("-", "_"))}


def _object(text: str, rel: Path) -> tuple[str, str]:
    """(type, name) of the main object a file creates; type 'script' when it creates none."""
    m = _CREATE.search(mask(text))
    if not m:
        return ("security" if _SECURITY.search(mask(text)) else "script"), rel.stem
    kind = m.group(1).upper().split()[0]
    kind = {"PROC": "procedure", "PROCEDURE": "procedure", "UNIQUE": "index", "CLUSTERED": "index",
            "NONCLUSTERED": "index", "COLUMNSTORE": "index", "FULLTEXT": "index", "PARTITION": "index", "MACRO": "procedure",
            "PACKAGE": "procedure", "USER": "security", "ROLE": "security", "LOGIN": "security"}.get(kind, kind.lower())
    name = re.sub(r"[\[\]`\"]", "", m.group(2)) or rel.stem
    return kind, name


def _routine(kind: str, name: str, code: str) -> dict[str, Any]:
    """Score a procedure / function / view."""
    words = _words(name)
    reasons: list[str] = []
    report = etl = app = 0
    if words & REPORT_WORDS:
        report += 4
        reasons.append(f"name suggests reporting ({', '.join(sorted(words & REPORT_WORDS))})")
    if words & ETL_WORDS:
        etl += 4
        reasons.append(f"name suggests ETL / data movement ({', '.join(sorted(words & ETL_WORDS))})")
    if words & APP_WORDS:
        app += 4
        reasons.append(f"name suggests application logic ({', '.join(sorted(words & APP_WORDS))})")
    for pat, weight, why in _APP_BODY:
        if pat.search(code):
            app += weight
            reasons.append(why)
    for pat, weight, why, group in _REPORT_BODY:
        if pat.search(code):
            if group == "etl":
                etl += weight
            else:
                report += weight
            reasons.append(why)
    joins, aggs = len(_JOIN.findall(code)), len(_AGG.findall(code))
    if joins >= 3:
        report += 1
        reasons.append(f"joins {joins} tables")
    if aggs >= 2:
        report += 1
        reasons.append(f"{aggs} aggregate calculations")
    if kind == "view":
        report += 2
        reasons.append("views are queries - they move with the data")
    if (kind == "procedure" and _PARAMS.search(code) and not _WRITES.search(code) and joins <= 2 and aggs == 0
            and not report and not etl):
        app += 2
        reasons.append("simple lookup by parameters (typical of an application screen)")

    score = report + etl - app
    if score >= 2:
        category, purpose = "migrate", ("ETL / data movement" if etl > report else "reporting / analytics")
    elif score <= -2:
        category, purpose = "keep", "application logic"
    else:
        category, purpose = "review", "unclear"
    return {"category": category, "purpose": purpose, "reasons": reasons or ["no clear signals in the name or code"]}


def classify(cfg: ProjectConfig) -> dict[str, Any]:
    objects: list[dict[str, Any]] = []
    codes: dict[str, set[str]] = {}  # routine -> the words in its code (to find callers fast)
    app_ev: list[str] = []
    wh_ev: list[str] = []
    tables = audited = 0

    for rel in source_files(cfg):
        text = read_source(cfg.input_dir / rel)
        code = mask(text)
        kind, name = _object(text, rel)
        short = name.split(".")[-1]
        obj: dict[str, Any] = {"file": rel.as_posix(), "name": name, "type": kind}

        if _DEPLOY_SCRIPT.search(rel.as_posix()):
            obj.update(category="skip", purpose="deployment script",
                       reasons=["SSDT pre/post-deployment script - its lookup data arrives with the table copy"])
        elif cfg.source.key in ETL_SOURCES:
            obj.update(category="migrate", purpose="ETL / data movement", reasons=["ETL job: runs as a Databricks notebook or job"])
        elif kind in ("procedure", "function", "view"):
            obj.update(_routine(kind, name, code))
            codes[short.lower()] = set(_TOKEN.findall(code.lower()))
        elif kind == "table":
            tables += 1
            if _FRAMEWORK_TABLES.match(short):
                obj.update(category="skip", purpose="framework table",
                           reasons=["application framework bookkeeping (e.g. Entity Framework migrations) - no analytic value"])
                app_ev.append(f"application framework table {short}")
            elif _LOG_TABLE.search(short):
                obj.update(category="data", purpose="log table",
                           reasons=["application log - copy it only if you want to analyse it"])
            else:
                obj.update(category="data", purpose="table", reasons=["holds data: create it on Databricks and copy the data"])
                if _WAREHOUSE_TABLE.search(short):
                    wh_ev.append(f"warehouse-style table {short}")
            if _AUDIT_COLS.search(code):
                audited += 1
            if cfg.source.key in ("mssql", "synapse") and _ROWVERSION.search(code):
                app_ev.append(f"{short} has a rowversion column (optimistic locking by an application)")
            if _TEMPORAL.search(code):
                app_ev.append(f"{short} is a system-versioned (temporal) table")
        elif kind == "trigger":
            obj.update(category="keep", purpose="application logic",
                       reasons=["triggers enforce application behaviour on every write; Delta tables have no triggers"])
        elif kind == "type":
            obj.update(category="keep", purpose="application logic",
                       reasons=["user-defined (table) type - used to pass data from the application"])
        elif kind in ("security", "schema"):
            obj.update(category="skip", purpose="security / setup",
                       reasons=["logins, users, roles and schemas are set up with Unity Catalog permissions instead"])
        elif kind == "index":
            obj.update(category="skip", purpose="index",
                       reasons=["Delta uses data skipping and liquid clustering instead of indexes and partition functions"])
        elif kind in ("sequence", "synonym"):
            obj.update(category="review", purpose=kind, reasons=[f"{kind}: usually replaced by IDENTITY columns or views"])
        elif _WRITES.search(code):
            obj.update(category="data", purpose="seed data", reasons=["data script - the data arrives with the table copy"])
        else:
            obj.update(category="review", purpose="script", reasons=["script that creates no object"])
        objects.append(obj)

    # Helper functions (and unclear routines) follow the objects that call them.
    by_name = {o["name"].split(".")[-1].lower(): o for o in objects if o["type"] in ("procedure", "function", "view")}
    for o in objects:
        if o["category"] != "review" or o["type"] not in ("function", "procedure", "view"):
            continue
        short = o["name"].split(".")[-1].lower()
        callers = [n for n, words in codes.items() if n != short and short in words and by_name[n]["category"] != "review"]
        users = {by_name[n]["category"] for n in callers}
        if "migrate" in users:
            o.update(category="migrate", purpose="helper for reporting / ETL")
            o["reasons"].append("used by objects that move: " + ", ".join(by_name[n]["name"] for n in callers
                                                                          if by_name[n]["category"] == "migrate")[:200])
        elif users == {"keep"}:
            o.update(category="keep", purpose="helper for application logic")
            o["reasons"].append("used only by application logic: " + ", ".join(by_name[n]["name"] for n in callers)[:200])

    counts = {k: sum(o["category"] == k for o in objects) for k in CATEGORY_LABELS}
    keep, move = counts["keep"], counts["migrate"]
    if tables and audited / tables >= 0.3:
        app_ev.append(f"{audited} of {tables} tables have audit columns (CreatedBy, ModifiedOn, IsDeleted ...)")
    if keep >= 3:
        app_ev.append(f"{keep} routines are application logic (CRUD, lookups for screens, paging, login, e-mail)")
    if move >= 3 and keep == 0:
        wh_ev.append(f"all {move} classified routines are reporting / ETL")

    source = cfg.source.analyzer_tech.removeprefix("MS ")
    if cfg.source.key in ETL_SOURCES:
        verdict = "warehouse"
    elif app_ev and not wh_ev:
        verdict = "application"
    elif wh_ev and not app_ev:
        verdict = "warehouse"
    elif app_ev and wh_ev:
        verdict = "mixed"
    else:
        verdict = "application" if keep > move else ("warehouse" if keep == 0 else "mixed")

    if verdict == "application":
        headline = "Not a data warehouse: this is an application's database. WishBridge does not migrate it."
        advice = [
            f"Keep the database (and the application using it) on {source}.",
            "If the client's warehouse needs this data, add the database as a new source of the warehouse: "
            "copy its tables with Lakehouse Federation, Lakeflow Connect or another CDC tool.",
            (f"{move} reporting object(s) marked 'Move to Databricks' could be rebuilt on that copied data."
             if move else "No reporting or ETL logic was found here."),
        ]
    elif verdict == "warehouse":
        headline = "Good fit: this is data-warehouse / ETL code. Migrate it."
        advice = ["Migrate the code and the data to Databricks.",
                  "Point reports and dashboards at Databricks once reconciliation passes."]
    else:
        headline = "Mixed: warehouse / reporting code and application code in the same database."
        advice = [f"Migrate the warehouse part: the {move} object(s) marked 'Move to Databricks' and the tables they use "
                  "(set scope: recommended to deploy only these).",
                  f"Leave the {keep} application object(s) marked 'Keep on source' on {source}."]
    if counts["review"]:
        advice.append(f"Decide the {counts['review']} object(s) marked 'Decide'.")

    return {"verdict": verdict, "headline": headline, "advice": advice, "app_evidence": app_ev[:12],
            "warehouse_evidence": wh_ev[:12], "counts": counts, "objects": objects}


def run_fit(cfg: ProjectConfig) -> dict[str, Any]:
    if not cfg.input_dir.exists():
        raise FileNotFoundError(f"No input files in {cfg.input_dir}")
    result = classify(cfg)
    save_step(cfg, "fit", result)
    return result


def excluded_files(cfg: ProjectConfig, fit: dict[str, Any] | None) -> dict[str, str]:
    """Files deploy leaves out when the project's scope is 'recommended': file -> reason."""
    if cfg.scope != "recommended" or not fit:
        return {}
    return {o["file"]: CATEGORY_LABELS[o["category"]] for o in fit["objects"] if o["category"] in ("keep", "skip")}
