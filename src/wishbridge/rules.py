"""Post-transpile rules.

LakeBridge's transpilers convert most code, but leave FIXME markers and the
occasional construct that won't run on Databricks. Rules here either *fix*
the text (only rewrites that are safe and mechanical) or *flag* it for a
human (anything that changes semantics). Strings and comments are never
touched by fixes or detectors.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass
from typing import Callable

from .sqltext import comments, line_of, mask

ERROR, WARNING, INFO = "error", "warning", "info"

TSQL = frozenset({"mssql", "synapse"})
ALL = None  # rule applies to every source dialect


@dataclass
class Finding:
    rule: str
    severity: str
    line: int
    message: str
    fixed: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


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


# ---------------------------------------------------------------- fixers

def _fix_schema_map(sql: str, schema_map: dict[str, str]) -> tuple[str, list[Finding]]:
    findings = []
    for src, tgt in schema_map.items():
        name = re.escape(src)
        pat = re.compile(rf"(?<![\w.`])(?:\[{name}\]|`{name}`|{name})\.(?=[\w\[`])", re.IGNORECASE)
        sql, hits = _sub_code(sql, pat, lambda _m, t=tgt: f"{t}.", keep_idents=True)
        if hits:
            findings.append(Finding("schema-map", INFO, line_of(sql, hits[0]), f"Mapped schema '{src}' -> '{tgt}' ({len(hits)}x)", True))
    return sql, findings


_SIMPLE_FIXES: list[tuple[str, frozenset | None, re.Pattern, str, str]] = [
    ("bracket-identifier", TSQL, re.compile(r"\[([A-Za-z_#@][^\]\n]*)\]"), r"`\1`",
     "Converted [bracketed] identifier to `backticks`"),
    ("nolock-hint", TSQL, re.compile(r"\s*\bWITH\s*\(\s*NOLOCK\s*\)|\s*\(\s*NOLOCK\s*\)", re.IGNORECASE), "",
     "Removed NOLOCK hint (Delta uses snapshot isolation)"),
    ("getdate", TSQL, re.compile(r"\b(?:GETDATE|SYSDATETIME)\s*\(\s*\)", re.IGNORECASE),
     "CURRENT_TIMESTAMP()", "Normalised current-time function to CURRENT_TIMESTAMP()"),
    ("getutcdate", TSQL, re.compile(r"\b(?:GETUTCDATE|SYSUTCDATETIME)\s*\(\s*\)", re.IGNORECASE),
     "to_utc_timestamp(CURRENT_TIMESTAMP(), current_timezone())", "Converted UTC current-time function"),
    ("set-nocount", TSQL, re.compile(r"^[ \t]*SET\s+NOCOUNT\s+(?:ON|OFF)\s*;?[ \t]*$", re.IGNORECASE | re.MULTILINE), "",
     "Removed SET NOCOUNT (no row-count messages in Databricks)"),
    ("go-separator", TSQL, re.compile(r"^[ \t]*GO[ \t]*$", re.IGNORECASE | re.MULTILINE), "",
     "Removed GO batch separator"),
]

# `AS` <newline> -- comment <newline> alias   ->   AS alias, -- comment
_DANGLING_COMMENT = re.compile(r"\bAS[ \t]*\r?\n[ \t]*(--[^\n]*)\r?\n[ \t]*(\w+)([^\n-]*)", re.IGNORECASE)


def _fix_dangling_comment(sql: str) -> tuple[str, list[Finding]]:
    findings = []

    def repl(m: re.Match) -> str:
        findings.append(Finding("dangling-comment", INFO, line_of(sql, m.start()), "Moved comment that split a column alias", True))
        return f"AS {m.group(2)}{m.group(3).rstrip()} {m.group(1)}"

    # The match intentionally spans a comment, so it runs on raw text, guarded by the mask check on the `AS`.
    m = mask(sql)
    out, last = [], 0
    for hit in _DANGLING_COMMENT.finditer(sql):
        if m[hit.start():hit.start() + 2].upper() != "AS":
            continue
        out.append(sql[last:hit.start()])
        out.append(repl(hit))
        last = hit.end()
    out.append(sql[last:])
    return "".join(out), findings


# --------------------------------------------------------------- detectors

_DETECTORS: list[tuple[str, str, frozenset | None, re.Pattern, str]] = [
    ("system-variable", ERROR, TSQL, re.compile(r"@@\w+(?:\s*\(\s*\))?"),
     "T-SQL system variable {m} is not supported. For row counts, read num_affected_rows from the statement result."),
    ("local-variable", WARNING, TSQL, re.compile(r"(?<![@\w])@[A-Za-z_]\w*"),
     "Local variable {m} is not valid Databricks SQL. Use DECLARE VARIABLE / procedure parameters."),
    ("temp-table", ERROR, TSQL, re.compile(r"(?<![\w#`])##?[A-Za-z_]\w*"),
     "Temp table {m}: use a TEMPORARY VIEW or a scratch Delta table."),
    ("temp-table-name", WARNING, TSQL, re.compile(r"`##?[A-Za-z_]\w*`"),
     "Temp table converted as {m}: confirm temporary tables are enabled on your warehouse, or use a TEMPORARY VIEW."),
    ("merge-into-alias", ERROR, ALL,
     re.compile(r"\bMERGE\s+INTO\s+`?(\w+)`?\s+USING\b(?:(?!\bMERGE\b).){0,4000}?\bAS\s+`?\1`?(?!\w)", re.IGNORECASE | re.DOTALL),
     "MERGE target {m1} is a table alias, not a table: an UPDATE ... FROM ... JOIN was mis-converted. "
     "Rewrite as MERGE INTO <table> AS {m1} USING (<join>) ON <key> WHEN MATCHED THEN UPDATE ..."),
    ("top-clause", ERROR, TSQL | {"teradata"}, re.compile(r"\bSELECT\s+(?:DISTINCT\s+)?TOP\s*\(?\s*\d+", re.IGNORECASE),
     "Unconverted TOP clause: use LIMIT."),
    ("output-clause", ERROR, TSQL, re.compile(r"\bOUTPUT\s+(?:INSERTED|DELETED)\.", re.IGNORECASE),
     "OUTPUT INSERTED/DELETED is not supported: query the table or use Change Data Feed."),
    ("cursor", WARNING, ALL, re.compile(r"\bDECLARE\s+\w+\s+CURSOR\b|\bFETCH\s+NEXT\b", re.IGNORECASE),
     "Cursor logic: rewrite as a set-based query."),
    ("dynamic-sql", WARNING, ALL, re.compile(r"\bEXEC(?:UTE)?\s*\(|\bsp_executesql\b", re.IGNORECASE),
     "Dynamic SQL: use EXECUTE IMMEDIATE."),
    ("raiserror", WARNING, TSQL, re.compile(r"\bRAISERROR\b|\bTHROW\s+\d", re.IGNORECASE),
     "Error raising: use SIGNAL SQLSTATE or raise_error()."),
    ("transaction", INFO, ALL, re.compile(r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bROLLBACK\b", re.IGNORECASE),
     "Explicit transaction: each Databricks statement is atomic; review multi-statement transaction semantics."),
    ("identity-insert", WARNING, TSQL, re.compile(r"\bIDENTITY_INSERT\b", re.IGNORECASE),
     "IDENTITY_INSERT is not needed: GENERATED BY DEFAULT AS IDENTITY accepts explicit values."),
    ("convert-leftover", WARNING, TSQL, re.compile(r"\b(?:TRY_)?CONVERT\s*\(\s*\w+", re.IGNORECASE),
     "Unconverted CONVERT(): use CAST / date_format."),
]

_COMMENT_MARKERS = re.compile(r"\b(FIXME|TODO|UNSUPPORTED|NOT\s+SUPPORTED|CANNOT\s+BE\s+TRANSLATED)\b[:\s-]*(.*)", re.IGNORECASE)


def _applies(scope: frozenset | None, dialect: str | None) -> bool:
    return scope is None or dialect is None or dialect in scope


# Transpiler notes that are informational, not work items.
BENIGN_NOTES = [
    re.compile(r"returns datetime with approximately 3\.33 ms resolution", re.IGNORECASE),
    re.compile(r"Databricks SQL does not return row count messages", re.IGNORECASE),
    # NOLOCK allowed dirty reads; Delta always reads a committed snapshot, which is strictly safer.
    re.compile(r"table hint .* NOLOCK\s*$", re.IGNORECASE),
]


def is_benign(message: str) -> bool:
    return any(p.search(message) for p in BENIGN_NOTES)


def detect(sql: str, dialect: str | None = None) -> list[Finding]:
    findings: list[Finding] = []
    m = mask(sql, keep_idents=True)
    for rule, sev, scope, pat, msg in _DETECTORS:
        if not _applies(scope, dialect):
            continue
        for hit in pat.finditer(m):
            m1 = hit.group(1) if pat.groups else ""
            findings.append(Finding(rule, sev, line_of(sql, hit.start()), msg.format(m=hit.group(0).strip(), m1=m1)))
    for off, text in comments(sql):
        hit = _COMMENT_MARKERS.search(text)
        if hit:
            note = (hit.group(2) or hit.group(1)).strip().rstrip("*/").strip()
            findings.append(Finding("transpiler-note", INFO if is_benign(note) else WARNING, line_of(sql, off), note))
    # A local-variable hit inside a system-variable hit (@@X) is the same issue.
    sys_lines = {f.line for f in findings if f.rule == "system-variable"}
    findings = [f for f in findings if not (f.rule == "local-variable" and f.line in sys_lines)]
    # Report a repeated issue once, listing the other lines.
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
    sql, f = _fix_dangling_comment(sql)
    findings += f
    sql = re.sub(r"\n{3,}", "\n\n", sql)
    findings += detect(sql, dialect)
    return sql, sorted(findings, key=lambda x: (x.line, x.rule))
