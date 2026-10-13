"""SQL text utilities: masking of strings/comments and statement splitting that understands BEGIN...END blocks."""

from __future__ import annotations

import re

_WORD = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_SCRIPT_BLOCK_ENDS = {"IF", "WHILE", "LOOP", "FOR", "REPEAT"}


def _scan(sql: str):
    """Yield (start, end, kind) segments where kind is 'code', 'string', 'ident' or 'comment'."""
    i, n, start = 0, len(sql), 0
    while i < n:
        ch = sql[i]
        nxt = sql[i + 1] if i + 1 < n else ""
        kind = None
        if ch == "-" and nxt == "-":
            j = sql.find("\n", i)
            j = n if j == -1 else j
            kind = "comment"
        elif ch == "/" and nxt == "*":
            j = sql.find("*/", i + 2)
            j = n if j == -1 else j + 2
            kind = "comment"
        elif ch in ("'", '"', "`"):
            j = i + 1
            while j < n:
                if sql[j] == "\\" and ch != "`":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:  # doubled quote escape
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            kind = "ident" if ch == "`" else "string"
        if kind:
            if start < i:
                yield start, i, "code"
            yield i, j, kind
            i = start = j
        else:
            i += 1
    if start < n:
        yield start, n, "code"


def mask(sql: str, keep_idents: bool = False) -> str:
    """Same length as `sql`, with strings, comments and (unless keep_idents) `quoted` identifiers blanked out."""
    keep = {"code", "ident"} if keep_idents else {"code"}
    out = []
    for s, e, kind in _scan(sql):
        seg = sql[s:e]
        out.append(seg if kind in keep else re.sub(r"[^\n]", " ", seg))
    return "".join(out)


def comments(sql: str):
    """Yield (offset, text) for each comment."""
    for s, e, kind in _scan(sql):
        if kind == "comment":
            yield s, sql[s:e]


def line_of(sql: str, offset: int) -> int:
    return sql.count("\n", 0, offset) + 1


def split_statements(sql: str) -> list[str]:
    """Split on top-level `;`, keeping CREATE PROCEDURE / BEGIN...END bodies together."""
    m = mask(sql)
    words = [(w.start(), w.group().upper()) for w in _WORD.finditer(m)]
    depth = case_depth = 0
    cuts: list[int] = []
    wi = 0
    for pos, ch in enumerate(m):
        while wi < len(words) and words[wi][0] <= pos:
            wpos, word = words[wi]
            if wpos == pos:
                if word == "BEGIN":
                    depth += 1
                elif word == "CASE":
                    case_depth += 1
                elif word == "END":
                    follow = ""
                    if wi + 1 < len(words) and not m[wpos + 3:words[wi + 1][0]].strip():
                        follow = words[wi + 1][1]
                    if follow in _SCRIPT_BLOCK_ENDS:
                        pass
                    elif case_depth > 0:
                        case_depth -= 1
                    else:
                        depth = max(0, depth - 1)
            wi += 1
        if ch == ";" and depth == 0 and case_depth == 0:
            cuts.append(pos)

    stmts, prev = [], 0
    for c in cuts + [len(sql)]:
        chunk = sql[prev:c].strip()
        prev = c + 1
        if chunk and mask(chunk).strip():
            stmts.append(chunk)
    return stmts


_LEADING = re.compile(r"^\s*([A-Za-z]+)(?:\s+(?:OR\s+REPLACE\s+|TEMPORARY\s+|TEMP\s+)*([A-Za-z]+))?", re.IGNORECASE)


def statement_kind(stmt: str) -> str:
    """Rough classification: 'ddl', 'query', 'dml' or 'other'."""
    m = _LEADING.match(mask(stmt))
    if not m:
        return "other"
    first = m.group(1).upper()
    if first in ("CREATE", "ALTER", "DROP", "COMMENT"):
        return "ddl"
    if first in ("SELECT", "WITH", "VALUES", "SHOW", "DESCRIBE"):
        return "query"
    if first in ("INSERT", "UPDATE", "DELETE", "MERGE", "TRUNCATE", "COPY"):
        return "dml"
    return "other"
