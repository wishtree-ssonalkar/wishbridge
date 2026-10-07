"""Post-transpile rules.

LakeBridge's transpilers convert most code, but leave FIXME markers and the
occasional construct that won't run on Databricks. Rules here either *fix*
the text (only rewrites that are safe and mechanical) or *flag* it for a
human (anything that changes semantics). Strings and comments are never
touched by fixes, and detectors only look at code unless marked `raw`.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable

from .sqltext import comments, line_of, mask, split_statements

ERROR, WARNING, INFO = "error", "warning", "info"

TSQL = frozenset({"mssql", "synapse"})
SNOWFLAKE = frozenset({"snowflake"})
ORACLE = frozenset({"oracle"})
TERADATA = frozenset({"teradata"})
NETEZZA = frozenset({"netezza"})
ALL = None  # rule applies to every source dialect

I, M, S = re.IGNORECASE, re.MULTILINE, re.DOTALL


@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    message: str
    fixed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class Detector:
    rule: str
    severity: str
    scope: frozenset | None
    pattern: re.Pattern
    message: str
    raw: bool = False  # match against raw text (strings visible); the match must still start in code


def _applies(scope: frozenset | None, dialect: str | None) -> bool:
    return scope is None or dialect is None or dialect in scope


def _sub_code(sql: str, pattern: re.Pattern, repl: str | Callable[[re.Match], str],
              keep_idents: bool = False) -> tuple[str, list[int]]:
    """Substitute matches that lie entirely in code (not in strings/comments). Returns new text and match offsets."""
    m = mask(sql, keep_idents)
    hits = [h for h in pattern.finditer(m) if sql[h.start():h.end()] == h.group(0)]
    offsets = [h.start() for h in hits]
    for h in reversed(hits):
        new = repl(h) if callable(repl) else h.expand(repl)
        sql = sql[:h.start()] + new + sql[h.end():]
    return sql, offsets


def _sub_raw_anchored(sql: str, pattern: re.Pattern, repl: Callable[[re.Match], str], anchor: str) -> tuple[str, list[int]]:
    """Substitute matches on raw text (they may span comments) whose first `len(anchor)` chars are code."""
    m = mask(sql)
    out, last, offsets = [], 0, []
    for hit in pattern.finditer(sql):
        if m[hit.start():hit.start() + len(anchor)].upper() != anchor:
            continue
        out.append(sql[last:hit.start()])
        out.append(repl(hit))
        offsets.append(hit.start())
        last = hit.end()
    out.append(sql[last:])
    return "".join(out), offsets


# ---------------------------------------------------------------- fixers

def _fix_schema_map(sql: str, schema_map: dict[str, str]) -> tuple[str, list[Finding]]:
    findings = []
    for src, tgt in schema_map.items():
        name = re.escape(src)
        pat = re.compile(rf"(?<![\w.`])(?:\[{name}\]|`{name}`|{name})\.(?=[\w\[`])", I)
        sql, hits = _sub_code(sql, pat, lambda _m, t=tgt: f"{t}.", keep_idents=True)
        if hits:
            findings.append(Finding("schema-map", INFO, line_of(sql, hits[0]), f"Mapped schema '{src}' -> '{tgt}' ({len(hits)}x)", True))
    return sql, findings


_SIMPLE_FIXES: list[tuple[str, frozenset | None, re.Pattern, str, str]] = [
    ("bracket-identifier", TSQL, re.compile(r"\[([A-Za-z_#@][^\]\n]*)\]"), r"`\1`",
     "Converted [bracketed] identifier to `backticks`"),
    ("nolock-hint", TSQL, re.compile(r"\s*\bWITH\s*\(\s*NOLOCK\s*\)|\s*\(\s*NOLOCK\s*\)", I), "",
     "Removed NOLOCK hint (Delta uses snapshot isolation)"),
    ("getdate", TSQL, re.compile(r"\b(?:GETDATE|SYSDATETIME)\s*\(\s*\)", I),
     "CURRENT_TIMESTAMP()", "Normalised current-time function to CURRENT_TIMESTAMP()"),
    ("getutcdate", TSQL, re.compile(r"\b(?:GETUTCDATE|SYSUTCDATETIME)\s*\(\s*\)", I),
     "to_utc_timestamp(CURRENT_TIMESTAMP(), current_timezone())", "Converted UTC current-time function"),
    ("set-nocount", TSQL, re.compile(r"^[ \t]*SET\s+NOCOUNT\s+(?:ON|OFF)\s*;?[ \t]*$", I | M), "",
     "Removed SET NOCOUNT (no row-count messages in Databricks)"),
    ("go-separator", TSQL, re.compile(r"^[ \t]*GO[ \t]*$", I | M), "",
     "Removed GO batch separator"),
    ("count-big", TSQL, re.compile(r"\bCOUNT_BIG\s*\(", I), "COUNT(",
     "Replaced COUNT_BIG with COUNT (Databricks COUNT already returns BIGINT)"),
    ("snowflake-max-varchar", SNOWFLAKE, re.compile(r"\bVARCHAR\s*\(\s*16777216\s*\)", I), "STRING",
     "Replaced VARCHAR(16777216) (Snowflake's default max length) with STRING"),
    ("missing-semicolon", ALL, re.compile(r"\)(?=[ \t]*CREATE\s+(?:OR\s+REPLACE\s+)?(?:PROCEDURE|TABLE|VIEW|FUNCTION)\b)", I), ");\n",
     "Inserted a missing ';' between two statements that ran together"),
]

# `AS` <newline> -- comment <newline> alias   ->   AS alias, -- comment
_DANGLING_COMMENT = re.compile(r"\bAS[ \t]*\r?\n[ \t]*(--[^\n]*)\r?\n[ \t]*(\w+)([^\n-]*)", I)

# `CREATE [OR REPLACE] /* <unconverted text> */;` - an empty shell that cannot run
_EMPTY_CREATE = re.compile(r"\bCREATE(?:\s+OR\s+REPLACE)?\s*/\*(.*?)\*/\s*;", I | S)


def _fix_dangling_comment(sql: str) -> tuple[str, list[Finding]]:
    new, hits = _sub_raw_anchored(
        sql, _DANGLING_COMMENT, lambda m: f"AS {m.group(2)}{m.group(3).rstrip()} {m.group(1)}", "AS")
    return new, [Finding("dangling-comment", INFO, line_of(sql, h), "Moved comment that split a column alias", True) for h in hits]


# Column definitions: Databricks rejects an explicit `NULL` (nullable is the default); keep NOT NULL / DEFAULT NULL / IS NULL.
_CREATE_TABLE = re.compile(r"^\s*CREATE\s+(?:OR\s+REPLACE\s+)?(?:TEMP(?:ORARY)?\s+)?TABLE\b", I)
_EXPLICIT_NULL = re.compile(r"(?<=[\w)])(?<!\bNOT)(?<!\bDEFAULT)(?<!\bIS)\s+NULL\b(?=\s*[,)])", I)


def _fix_explicit_null(sql: str) -> tuple[str, list[Finding]]:
    findings, out, pos = [], [], 0
    for stmt in split_statements(sql):
        start = sql.find(stmt, pos)
        if start < 0:
            continue
        out.append(sql[pos:start])
        new = stmt
        if _CREATE_TABLE.match(mask(stmt)):
            new, hits = _sub_code(stmt, _EXPLICIT_NULL, "")
            if hits:
                findings.append(Finding("explicit-null", INFO, line_of(sql, start + hits[0]),
                                        f"Removed explicit NULL from {len(hits)} column definition(s) (Databricks rejects it)", True))
        out.append(new)
        pos = start + len(stmt)
    out.append(sql[pos:])
    return "".join(out), findings


def _fix_empty_create(sql: str) -> tuple[str, list[Finding]]:
    def repl(m: re.Match) -> str:
        body = " ".join(m.group(1).split())
        return f"-- [WishBridge] not converted - rewrite manually: CREATE {body}"

    new, hits = _sub_raw_anchored(sql, _EMPTY_CREATE, repl, "CREATE")
    return new, [Finding("empty-create", INFO, line_of(sql, h),
                         "Commented out an unconverted CREATE statement that would fail to run", True) for h in hits]


# --------------------------------------------------------------- detectors

_DETECTORS: list[Detector] = [
    # T-SQL
    Detector("system-variable", ERROR, TSQL, re.compile(r"@@\w+(?:\s*\(\s*\))?"),
             "T-SQL system variable {m} is not supported. For row counts, read num_affected_rows from the statement result."),
    Detector("local-variable", WARNING, TSQL, re.compile(r"(?<![@\w])@[A-Za-z_]\w*"),
             "Local variable {m} is not valid Databricks SQL. Use DECLARE VARIABLE / procedure parameters."),
    Detector("temp-table", ERROR, TSQL, re.compile(r"(?<![\w#`])##?[A-Za-z_]\w*"),
             "Temp table {m}: use a TEMPORARY VIEW or a scratch Delta table."),
    Detector("temp-table-name", WARNING, TSQL, re.compile(r"`##?[A-Za-z_]\w*`"),
             "Temp table converted as {m}: confirm temporary tables are enabled on your warehouse, or use a TEMPORARY VIEW."),
    Detector("top-clause", ERROR, TSQL | TERADATA, re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s*\(?\s*\d+", I),
             "Unconverted TOP clause: use LIMIT."),
    Detector("output-clause", ERROR, TSQL, re.compile(r"\bOUTPUT\s+(?:INSERTED|DELETED)\.", I),
             "OUTPUT INSERTED/DELETED is not supported: query the table or use Change Data Feed."),
    Detector("raiserror", WARNING, TSQL, re.compile(r"\bRAISERROR\b|\bTHROW\s+\d", I),
             "Error raising: use SIGNAL SQLSTATE or raise_error()."),
    Detector("identity-insert", WARNING, TSQL, re.compile(r"\bIDENTITY_INSERT\b", I),
             "IDENTITY_INSERT is not needed: GENERATED BY DEFAULT AS IDENTITY accepts explicit values."),
    Detector("convert-leftover", WARNING, TSQL, re.compile(r"\b(?:TRY_)?CONVERT\s*\(\s*\w+", I),
             "Unconverted CONVERT(): use CAST / date_format."),
    # Oracle
    Detector("rownum", ERROR, ORACLE, re.compile(r"\bROWNUM\b", I),
             "ROWNUM is not supported: use LIMIT n, or ROW_NUMBER() OVER (...)."),
    Detector("connect-by", ERROR, ORACLE, re.compile(r"\bCONNECT\s+BY\b|\bSTART\s+WITH\b(?!\s*\d)", I),
             "Hierarchical query (START WITH / CONNECT BY): rewrite as a recursive CTE (WITH RECURSIVE)."),
    Detector("outer-join-plus", ERROR, ORACLE, re.compile(r"\(\s*\+\s*\)"),
             "Oracle (+) outer join: rewrite as LEFT/RIGHT OUTER JOIN ... ON."),
    Detector("sequence", ERROR, ORACLE, re.compile(r"\bCREATE\s+SEQUENCE\b|\.\s*(?:NEXTVAL|CURRVAL)\b", I),
             "Sequences are not supported: use an IDENTITY column (GENERATED ALWAYS AS IDENTITY)."),
    Detector("plsql-package", ERROR, ORACLE, re.compile(r"\b(?:DBMS|UTL)_\w+\.\w+", I),
             "Oracle package call {m}: remove it or replace with Databricks equivalents (e.g. SELECT for output)."),
    Detector("plsql-cursor-attr", ERROR, ORACLE, re.compile(r"\bSQL%(?:ROWCOUNT|FOUND|NOTFOUND|ISOPEN)\b", I),
             "{m} is not supported: read num_affected_rows from the statement result."),
    Detector("oracle-date-mask", WARNING, ORACLE,
             # function names in any case; mask tokens case-sensitive (Oracle masks are upper-case, Java's are not)
             re.compile(r"\b(?i:TO_CHAR|TO_DATE|TO_TIMESTAMP|DATE_FORMAT)\s*\([^;]*?'[^']*(?:YYYY|HH24|\bMI\b|\bRR\b|\bMON\b)[^']*'"),
             "Oracle date format mask in {m}...: Databricks uses Java patterns (yyyy-MM-dd HH:mm:ss).", raw=True),
    # Snowflake
    Detector("snowflake-stage", ERROR, SNOWFLAKE, re.compile(r"(?<![\w@'])@~?[A-Za-z_][\w./]*"),
             "Snowflake stage {m}: load from a Unity Catalog volume path (/Volumes/...) instead."),
    Detector("snowflake-flatten", ERROR, SNOWFLAKE, re.compile(r"\bFLATTEN\s*\(", I),
             "FLATTEN(): use explode() / variant_explode() with LATERAL VIEW or a lateral join."),
    Detector("snowflake-tz-timestamp", WARNING, SNOWFLAKE, re.compile(r"\bTIMESTAMP_(?:LTZ|TZ)\b", I),
             "{m}: Databricks TIMESTAMP is session-time-zone based; confirm time-zone behaviour."),
    # Teradata
    Detector("bteq-command", ERROR, TERADATA, re.compile(r"^[ \t]*\.(?:LOGON|LOGOFF|QUIT|IF|GOTO|LABEL|EXPORT|IMPORT|SET|RUN|OS)\b", I | M),
             "BTEQ command {m}: move control flow into a Databricks Job or notebook."),
    Detector("teradata-table-kind", WARNING, TERADATA, re.compile(r"\b(?:VOLATILE|MULTISET|GLOBAL\s+TEMPORARY)\s+TABLE\b", I),
             "{m}: use a regular Delta table or a TEMPORARY VIEW."),
    Detector("primary-index", WARNING, TERADATA, re.compile(r"\b(?:UNIQUE\s+)?PRIMARY\s+INDEX\b", I),
             "PRIMARY INDEX has no meaning on Delta: remove it; consider liquid clustering (CLUSTER BY)."),
    Detector("collect-stats", WARNING, TERADATA, re.compile(r"\bCOLLECT\s+STAT(?:ISTIC)?S?\b", I),
             "COLLECT STATISTICS: use ANALYZE TABLE ... COMPUTE STATISTICS, or rely on predictive optimisation."),
    Detector("sel-abbrev", ERROR, TERADATA, re.compile(r"^[ \t]*SEL\b", I | M),
             "Unconverted SEL abbreviation: use SELECT."),
    # Netezza
    Detector("netezza-groom", ERROR, NETEZZA, re.compile(r"\bGROOM\s+TABLE\b", I),
             "GROOM TABLE: run OPTIMIZE on the Delta table instead (or rely on predictive optimisation)."),
    Detector("netezza-stats", WARNING, NETEZZA, re.compile(r"\bGENERATE\s+(?:EXPRESS\s+)?STATISTICS\b", I),
             "GENERATE STATISTICS: use ANALYZE TABLE ... COMPUTE STATISTICS."),
    Detector("netezza-age", ERROR, NETEZZA, re.compile(r"\bAGE\s*\(", I),
             "AGE() does not exist in Databricks: use datediff() or months_between()."),
    # All sources
    Detector("partition-expression", ERROR, ALL, re.compile(r"\bPARTITIONED\s+BY\s*\([^()]*\(", I),
             "Delta tables partition by columns only: add a generated column (e.g. order_day DATE GENERATED ALWAYS AS "
             "(CAST(order_date AS DATE))) and partition by it, or use CLUSTER BY instead."),
    Detector("date-minus-number", WARNING, ALL,
             re.compile(r"\b(?:CURRENT_TIMESTAMP|CURRENT_DATE|NOW)\s*(?:\(\s*\))?\s*-\s*\d+\b(?!\s*(?:DAYS?|HOURS?|MINUTES?|SECONDS?|MONTHS?|YEARS?)\b)", I),
             "Date arithmetic with a bare number ({m}): use INTERVAL n DAYS or date_sub() in Databricks."),
    Detector("merge-into-alias", ERROR, ALL,
             re.compile(r"\bMERGE\s+INTO\s+`?(\w+)`?\s+USING\b(?:(?!\bMERGE\b).){0,4000}?\bAS\s+`?\1`?(?!\w)", I | S),
             "MERGE target {m1} is a table alias, not a table: an UPDATE ... FROM ... JOIN was mis-converted. "
             "Rewrite as MERGE INTO <table> AS {m1} USING (<join>) ON <key> WHEN MATCHED THEN UPDATE ..."),
    Detector("raise-error-placeholder", ERROR, ALL,
             re.compile(r"\bRAISE_ERROR\s*\(\s*'[^']*\bwould\b", I),
             "The transpiler inserted a RAISE_ERROR placeholder: this statement fails at runtime until it is rewritten.", raw=True),
    Detector("cursor", WARNING, ALL, re.compile(r"\bDECLARE\s+\w+\s+CURSOR\b|\bFETCH\s+NEXT\b", I),
             "Cursor logic: rewrite as a set-based query."),
    Detector("dynamic-sql", WARNING, ALL, re.compile(r"\bEXEC(?:UTE)?\s*\(|\bsp_executesql\b|\bEXECUTE\s+IMMEDIATE\s+'", I),
             "Dynamic SQL: review it and use EXECUTE IMMEDIATE."),
    Detector("transaction", INFO, ALL, re.compile(r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bROLLBACK\b", I),
             "Explicit transaction: each Databricks statement is atomic; review multi-statement transaction semantics."),
]

_COMMENT_MARKERS = re.compile(r"\b(FIXME|TODO|UNSUPPORTED|NOT\s+SUPPORTED|CANNOT\s+BE\s+TRANSLATED)\b[:\s-]*(.*)", I)

# Transpiler notes that are informational, not work items.
BENIGN_NOTES = [
    re.compile(r"returns datetime with approximately 3\.33 ms resolution", I),
    re.compile(r"Databricks SQL does not return row count messages", I),
    # NOLOCK allowed dirty reads; Delta always reads a committed snapshot, which is strictly safer.
    re.compile(r"table hint .* NOLOCK\s*$", I),
]

# Transpiler notes that mean code was NOT converted - these block deployment.
BLOCKING_NOTES = re.compile(
    r"cannot (?:currently )?(?:be )?(?:convert|translat)|no equivalent|Unparsed input|parse error|ErrorNode", I)

# Databricks guidance appended to transpiler notes about untranslatable features.
GUIDANCE: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\bUNLOAD\b", I), "write the query result with INSERT OVERWRITE DIRECTORY, or from a notebook to a volume"),
    (re.compile(r"\bVACUUM\b", I), "run OPTIMIZE on the Delta table (Delta's VACUUM only removes old files)"),
    (re.compile(r"\bSTREAM\b", I), "use Delta Change Data Feed (delta.enableChangeDataFeed) and table_changes()"),
    (re.compile(r"\bTASK\b", I), "schedule the statement as a Databricks Job (Lakeflow Jobs)"),
    (re.compile(r"\bCOPY INTO\b|\bstage\b", I), "load with COPY INTO from a Unity Catalog volume, or Auto Loader"),
    (re.compile(r"SEQUENCE|NEXTVAL|CURRVAL", I), "use an IDENTITY column (GENERATED ALWAYS AS IDENTITY)"),
    (re.compile(r"START WITH|CONNECT BY", I), "rewrite as a recursive CTE (WITH RECURSIVE)"),
    (re.compile(r"\bPRINT\b", I), "drop it, or SELECT the value if it is needed"),
    (re.compile(r"\(\+\)", I), "rewrite the (+) join as LEFT/RIGHT OUTER JOIN"),
]


def is_benign(message: str) -> bool:
    return any(p.search(message) for p in BENIGN_NOTES)


def note_finding(note: str, line: int) -> Finding:
    note = note or "Transpiler marked this line for review"
    if is_benign(note):
        return Finding("transpiler-note", INFO, line, note)
    for pat, advice in GUIDANCE:
        if pat.search(note):
            note = f"{note} -> Databricks: {advice}"
            break
    return Finding("transpiler-note", ERROR if BLOCKING_NOTES.search(note) else WARNING, line, note)


_UNCONVERTED = re.compile(r"\A\s*--\s*internal error\b", I)


def structural_checks(sql: str) -> list[Finding]:
    """Problems with the shape of the converted file rather than any one construct."""
    findings = []
    if _UNCONVERTED.match(sql):
        findings.append(Finding("unconverted-file", ERROR, 1,
                                "The converter failed on this file and copied it unchanged: it is still source-dialect "
                                "SQL. Rewrite it by hand in overrides/."))
    pos = 0
    for stmt in split_statements(sql):
        start = sql.find(stmt, pos)
        pos = start + len(stmt) if start >= 0 else pos
        code = mask(stmt)
        if code.count("(") != code.count(")"):
            findings.append(Finding("unbalanced-parentheses", ERROR, line_of(sql, max(start, 0)),
                                    "Unbalanced parentheses: the converter left broken syntax in this statement."))
    return findings


def count_statements(sql: str) -> int:
    """Statements at any depth (so wrapping statements in BEGIN ... END does not hide or invent a drop):
    one per ';' in code, plus a final statement without one. Comments and strings are ignored."""
    code = mask(sql)
    tail = code.rsplit(";", 1)[-1]
    return code.count(";") + (1 if tail.strip() else 0)


def dropped_statement_check(source_sql: str, converted_sql: str, findings: list[Finding]) -> Finding | None:
    """Warn when the converted file has fewer statements than the source and the gap is not explained by notes."""
    notes = sum(1 for f in findings if f.rule in ("transpiler-note", "empty-create"))
    missing = count_statements(source_sql) - count_statements(converted_sql) - notes
    if missing > 0:
        return Finding("dropped-statements", WARNING, 1,
                       f"{missing} statement(s) from the source file are missing from the converted file without any "
                       "note - check that nothing was silently dropped.")
    return None


def _dedupe(findings: list[Finding]) -> list[Finding]:
    """Report a repeated issue once, listing the other lines."""
    first: dict[tuple[str, str], Finding] = {}
    extra: dict[tuple[str, str], list[int]] = {}
    for f in sorted(findings, key=lambda x: x.line):
        key = (f.rule, f.message)
        if key in first:
            extra.setdefault(key, []).append(f.line)
        else:
            first[key] = f
    for key, lines in extra.items():
        first[key].message += f" (also line{'s' if len(lines) > 1 else ''} {', '.join(map(str, lines))})"
    return list(first.values())


def detect(sql: str, dialect: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    code = mask(sql, keep_idents=True)
    for d in _DETECTORS:
        if not _applies(d.scope, dialect):
            continue
        for hit in d.pattern.finditer(sql if d.raw else code):
            if d.raw and code[hit.start()] != sql[hit.start()]:
                continue  # match starts inside a string or comment
            m1 = hit.group(1) if d.pattern.groups else ""
            text = hit.group(0).strip()
            if d.raw:
                text = text.split("(")[0]
            findings.append(Finding(d.rule, d.severity, line_of(sql, hit.start()), d.message.format(m=text, m1=m1)))
    for off, text in comments(sql):
        hit = _COMMENT_MARKERS.search(text)
        if hit:
            note = (hit.group(2) or "").strip().rstrip("*/").strip()
            findings.append(note_finding(note, line_of(sql, off)))
    findings += structural_checks(sql)
    # A local-variable hit inside a system-variable hit (@@X) is the same issue.
    sys_lines = {f.line for f in findings if f.rule == "system-variable"}
    findings = [f for f in findings if not (f.rule == "local-variable" and f.line in sys_lines)]
    return _dedupe(findings)


def apply_rules(sql: str, schema_map: dict[str, str] | None = None, dialect: str | None = None) -> tuple[str, list[Finding]]:
    findings: list[Finding] = []
    sql, f = _fix_schema_map(sql, schema_map or {})
    findings += f
    for rule, scope, pat, repl, msg in _SIMPLE_FIXES:
        if not _applies(scope, dialect):
            continue
        before = sql
        sql, hits = _sub_code(sql, pat, repl)
        if hits and sql != before:
            findings.append(Finding(rule, INFO, line_of(before, hits[0]), f"{msg} ({len(hits)}x)", True))
    for fixer in (_fix_dangling_comment, _fix_empty_create, _fix_explicit_null):
        sql, f = fixer(sql)
        findings += f
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    findings += detect(sql, dialect)
    return sql, sorted(findings, key=lambda x: (x.line, x.rule))
