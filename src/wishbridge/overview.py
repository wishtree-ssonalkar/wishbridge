"""Code Overview: a readable description of the client's code base, written from the code itself.

For the team that has to understand a system it did not build. From the source files (and, when present,
the analyzer workbook, the DBA inventory and the conversion results) it describes:

  * what the code base is - a few plain sentences;
  * its structure - folders, and objects per schema and type;
  * every table - columns, primary key, which routines read or write it, rows (from the inventory);
  * every procedure, function and view - size, complexity, tables read and written, notable features,
    fit-check recommendation and conversion status;
  * how data flows - which routines and ETL jobs load which tables;
  * what needs attention - cursors, dynamic SQL, temp tables, transactions, cross-database and linked-server
    references, triggers;
  * old and new files - every source file next to its converted file and status.

The result is output/code_overview.html (self-contained) and an "overview" summary in state.json.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from datetime import datetime
from html import escape
from pathlib import Path
from typing import Any

from . import __version__
from .config import ETL_SOURCES, ProjectConfig
from .sqltext import mask
from .staging import read_source, source_files
from .state import load_state, save_step

I = re.IGNORECASE
_NAME = r"(?:\[[^\]]+\]|`[^`]+`|\"[^\"]+\"|[\w$#@]+)"
_QUALIFIED = rf"{_NAME}(?:\s*\.\s*{_NAME}){{0,3}}"
_CREATE_TABLE = re.compile(rf"\bCREATE\s+(?:OR\s+REPLACE\s+)?(?:(?:GLOBAL\s+)?TEMP(?:ORARY)?\s+|MULTISET\s+|SET\s+|VOLATILE\s+)*TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?({_QUALIFIED})\s*\(", I)
_READ = re.compile(rf"\b(?:FROM|JOIN|USING)\s+({_QUALIFIED})", I)
_WRITE = re.compile(rf"\b(?:INSERT\s+(?:INTO\s+)?|UPDATE\s+|MERGE\s+(?:INTO\s+)?|TRUNCATE\s+TABLE\s+|DELETE\s+(?:FROM\s+)?)({_QUALIFIED})", I)
FEATURES = {
    "cursor": (re.compile(r"\bDECLARE\s+\w+\s+(?:\w+\s+)*CURSOR\b|\bCURSOR\s+FOR\b", I), "Cursors (row-by-row loops)"),
    "dynamic-sql": (re.compile(r"\bsp_executesql\b|\bEXEC(?:UTE)?\s*\(\s*@|\bEXECUTE\s+IMMEDIATE\b", I), "Dynamic SQL"),
    "temp-table": (re.compile(r"(?<![\w#])#{1,2}[A-Za-z_]\w*|\bVOLATILE\s+TABLE\b|\bGLOBAL\s+TEMPORARY\b", I), "Temporary tables"),
    "transaction": (re.compile(r"\bBEGIN\s+TRAN(?:SACTION)?\b|\bCOMMIT\b|\bROLLBACK\b", I), "Explicit transactions"),
    "linked-server": (re.compile(r"\bOPENQUERY\s*\(|\bOPENROWSET\s*\(|\bOPENDATASOURCE\s*\(", I), "Linked servers / external queries"),
    "error-handling": (re.compile(r"\bBEGIN\s+TRY\b|\bEXCEPTION\s+WHEN\b|\bRAISERROR\b|\bTHROW\b", I), "Error handling"),
    "while-loop": (re.compile(r"\bWHILE\b[^;]*?\bBEGIN\b|\bLOOP\b", I), "Loops"),
}
LABELS = {k: label for k, (_, label) in FEATURES.items()} | {"cross-database": "Cross-database or linked-server references"}


def _clean(name: str) -> str:
    return ".".join(p.strip().strip("[]`\"") for p in re.split(r"\s*\.\s*", name.strip()))


def _short(name: str) -> str:
    return _clean(name).split(".")[-1].lower()


def _balanced(text: str, start: int) -> str:
    """Text between the parenthesis at start-1 and its partner."""
    depth, i = 1, start
    while i < len(text) and depth:
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        i += 1
    return text[start:i - 1]


def _split_top(body: str) -> list[str]:
    parts, depth, cur = [], 0, []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def parse_tables(text: str) -> list[dict[str, Any]]:
    """Tables created in a SQL text with their columns and primary key."""
    code = mask(text, keep_idents=True)
    out = []
    for m in _CREATE_TABLE.finditer(code):
        body = _balanced(code, m.end())  # comments and string defaults blanked out
        cols, pk = [], []
        for part in _split_top(body):
            words = part.split()
            head = words[0].upper().strip("[]`\"")
            if head in ("CONSTRAINT", "PRIMARY", "FOREIGN", "UNIQUE", "INDEX", "KEY", "CHECK", "PERIOD"):
                k = re.search(r"PRIMARY\s+KEY\s*(?:CLUSTERED|NONCLUSTERED)?\s*\(([^)]*)\)", part, I)
                if k:
                    pk += [_clean(c.split()[0]) for c in k.group(1).split(",") if c.strip()]
                continue
            if len(words) < 2:
                continue
            name = words[0].strip("[]`\"")
            ctype = re.match(r"\s*(?:\[[^\]]+\]|`[^`]+`|\"[^\"]+\"|\S+)\s+(\[?[\w.]+\]?(?:\s*\([^)]*\))?|[^\s,]+)", part)
            col_type = ctype.group(1).strip("[]") if ctype else words[1]
            if re.search(r"\bPRIMARY\s+KEY\b", part, I):
                pk.append(name)
            cols.append({"name": name, "type": col_type,
                         "nullable": not re.search(r"\bNOT\s+NULL\b", part, I),
                         "identity": bool(re.search(r"\bIDENTITY\b|\bGENERATED\b.*\bAS\s+IDENTITY\b|\bAUTOINCREMENT\b", part, I))})
        out.append({"name": _clean(m.group(1)), "columns": cols, "primary_key": pk})
    return out


def _schema_of(name: str) -> str:
    parts = _clean(name).split(".")
    return parts[-2] if len(parts) >= 2 else "(default)"


def build_overview(cfg: ProjectConfig) -> Path:
    from .fit import CATEGORY_LABELS, classify

    state = load_state(cfg)
    files = source_files(cfg)
    texts = {rel.as_posix(): read_source(cfg.input_dir / rel) for rel in files}
    etl = cfg.source.key in ETL_SOURCES
    fit = state.get("fit") or classify(cfg)
    objects = {o["file"]: o for o in fit["objects"]}
    conv = {f["file"]: f for f in (state.get("convert") or {}).get("files", [])}
    analyzed = {Path(p.get("source_file", "")).name.lower(): p for p in (state.get("analyze") or {}).get("programs", [])}
    inv = {(_short(t["table"])): t for t in (state.get("inventory") or {}).get("tables", [])}

    # Tables from the DDL (or the inventory when the code has none)
    tables: dict[str, dict[str, Any]] = {}
    for rel, text in texts.items():
        for t in parse_tables(text):
            if t["name"].startswith("#"):
                continue
            tables.setdefault(_short(t["name"]), {**t, "file": rel, "readers": set(), "writers": set()})
    for key, t in inv.items():
        tables.setdefault(key, {"name": f"{t['schema']}.{t['table']}", "file": "", "primary_key": [],
                                "columns": [{"name": c["name"], "type": c["type"], "nullable": c["nullable"], "identity": False}
                                            for c in t["columns"]], "readers": set(), "writers": set()})

    # Routines: what they read and write, and notable features
    routines = []
    feature_users: dict[str, list[str]] = defaultdict(list)
    for rel, text in texts.items():
        o = objects.get(rel, {})
        if etl or o.get("type") not in ("procedure", "function", "view", "trigger", "script"):
            continue
        code = mask(text)
        writes = {_short(m.group(1)) for m in _WRITE.finditer(code)} & set(tables)
        reads = {_short(m.group(1)) for m in _READ.finditer(code)} & set(tables)
        feats = [k for k, (pat, _) in FEATURES.items() if pat.search(code)]
        cross = sorted({_clean(m.group(0)) for m in re.finditer(rf"(?<![\w.\[]){_NAME}\.{_NAME}\.{_NAME}(?:\.{_NAME})?", code)
                        if len(_clean(m.group(0)).split(".")) == 4 or not _clean(m.group(0)).lower().startswith(("dbo.", "sys."))})
        cross = [c for c in cross if not re.match(r"^\w+\.\w+\.\w+$", c) or c.split(".")[0].lower() not in
                 {s.lower() for s in (_schema_of(t["name"]) for t in tables.values())}][:10]
        if cross:
            feats.append("cross-database")
        name = o.get("name") or Path(rel).stem
        for k in feats:
            feature_users[k].append(name)
        for t in reads:
            tables[t]["readers"].add(name)
        for t in writes:
            tables[t]["writers"].add(name)
        reads = reads - writes
        reads, writes = {tables[k]["name"] for k in reads}, {tables[k]["name"] for k in writes}
        prog = analyzed.get(Path(rel).name.lower(), {})
        routines.append({"file": rel, "name": name, "type": o.get("type", "script"),
                         "lines": text.count("\n") + 1, "complexity": prog.get("complexity", ""),
                         "reads": sorted(reads - writes), "writes": sorted(writes), "features": feats, "cross": cross,
                         "fit": CATEGORY_LABELS.get(o.get("category", ""), ""), "status": conv.get(rel, {}).get("status", "")})

    # Structure
    folders = Counter(str(Path(rel).parent).replace("\\", "/") if str(Path(rel).parent) != "." else "(top)" for rel in texts)
    per_schema: dict[str, Counter] = defaultdict(Counter)
    for rel, o in objects.items():
        if o["type"] in ("table", "procedure", "function", "view", "trigger"):
            per_schema[_schema_of(o["name"]) if "." in o["name"] else "(default)"][o["type"]] += 1
    types = Counter(o["type"] for o in objects.values())

    # Old and new files
    pairs = []
    for rel in sorted(set(texts) | set(conv)):
        c = conv.get(rel, {})
        final = Path(c["final"]).relative_to(cfg.output_dir / "final").as_posix() if c.get("final") and \
            (cfg.output_dir / "final") in Path(c["final"]).parents else ""
        pairs.append({"old": rel if rel in texts else "", "new": final, "status": c.get("status", "not converted yet"),
                      "open": sum(1 for x in c.get("findings", []) if not x["fixed"] and x["severity"] != "info"),
                      "converter": c.get("converter", "")})

    a, cs = state.get("analyze") or {}, (state.get("convert") or {}).get("summary")
    n_tables = len([t for t in tables.values() if t["file"]]) or len(tables)
    summary = {
        "files": len(texts), "folders": len(folders), "tables": n_tables, "procedures": types.get("procedure", 0),
        "functions": types.get("function", 0), "views": types.get("view", 0), "triggers": types.get("trigger", 0),
        "schemas": sorted(per_schema), "lines": sum(t.count("\n") + 1 for t in texts.values()),
        "estimate_hours": a.get("estimated_hours_baseline"), "verdict": fit.get("verdict"), "headline": fit.get("headline"),
        "features": {k: len(v) for k, v in feature_users.items()},
    }
    out = cfg.out("code_overview.html")
    out.write_text(_html(cfg, summary, folders, per_schema, tables, routines, feature_users, pairs, cs, etl, a, inv),
                   encoding="utf-8")
    save_step(cfg, "overview", {**summary, "file": str(out)})
    return out


def _story(cfg: ProjectConfig, s: dict[str, Any], cs: dict | None, etl: bool) -> str:
    product = cfg.source.label.replace(" data warehouse", "")
    parts = [f"<b>{escape(cfg.name)}</b> is a {escape(product)} code base of {s['files']} files "
             f"({s['lines']:,} lines) in {s['folders']} folder(s)."]
    if etl:
        parts.append("It holds ETL jobs that load a data warehouse; each job is described below with the tables it reads and writes.")
    else:
        bits = [f"{s[k]} {label}" for k, label in (("tables", "tables"), ("procedures", "stored procedures"),
                                                     ("views", "views"), ("functions", "functions"), ("triggers", "triggers")) if s[k]]
        if bits:
            parts.append("It defines " + ", ".join(bits) + (f" in the schema(s) {', '.join(s['schemas'])}." if s["schemas"] else "."))
    if s.get("headline"):
        parts.append(f"Fit check: {escape(s['headline'])}")
    if s.get("estimate_hours"):
        parts.append(f"Rewriting it by hand would take about {s['estimate_hours']} hours (analyzer estimate).")
    if cs:
        parts.append(f"Conversion: {cs['ready']} file(s) ready, {cs['review']} to review, {cs['needs_fix']} needing a fix.")
    notable = [f"{n} use {LABELS[k].lower()}" for k, n in sorted(s["features"].items(), key=lambda kv: -kv[1])[:3] if n]
    if notable:
        parts.append("Worth knowing: " + "; ".join(notable) + ".")
    return " ".join(parts)


def _html(cfg, s, folders, per_schema, tables, routines, feature_users, pairs, cs, etl, a, inv) -> str:
    from .report import CSS, _card, _pill, _table

    parts = [f"<p>{_story(cfg, s, cs, etl)}</p>"]
    cards = [_card(s["files"], "code files"), _card(f"{s['lines']:,}", "lines of code")]
    if not etl:
        cards += [_card(s["tables"], "tables"), _card(s["procedures"], "stored procedures"), _card(s["views"], "views"),
                  _card(s["functions"], "functions")]
    if s.get("estimate_hours"):
        cards.append(_card(f"{s['estimate_hours']} h", "manual rewrite estimate"))
    parts.append(f'<div class="grid">{"".join(cards)}</div>')

    parts.append("<h2>Structure</h2>")
    parts.append(_table(["Folder", "Files"], [[f"<code>{escape(f)}</code>", str(n)] for f, n in sorted(folders.items())]))
    if per_schema:
        kinds = ["table", "view", "procedure", "function", "trigger"]
        parts.append("<p></p>" + _table(["Schema"] + [k.title() + "s" for k in kinds],
                                         [[f"<code>{escape(sc)}</code>"] + [str(c.get(k, 0) or "—") for k in kinds]
                                          for sc, c in sorted(per_schema.items())]))

    if etl and a.get("programs"):
        parts.append("<h2>ETL jobs</h2>")
        parts.append(_table(["Job", "Type", "Steps", "Complexity"], [
            [f"<code>{escape(p['name'])}</code>", escape(p.get("category", "")), str(p.get("statements", "")),
             escape(p.get("complexity", ""))] for p in a["programs"]]))

    if tables:
        parts.append("<h2>Tables</h2>")
        rows = []
        for key, t in sorted(tables.items(), key=lambda kv: kv[1]["name"].lower()):
            cols = ", ".join(f"{escape(c['name'])} <span class='muted'>{escape(c['type'])}</span>" for c in t["columns"][:40])
            more = f" … +{len(t['columns']) - 40}" if len(t["columns"]) > 40 else ""
            rv = inv.get(key, {}).get("rows")
            rows.append([f"<code>{escape(t['name'])}</code>", str(len(t["columns"])), escape(", ".join(t["primary_key"])) or "—",
                         escape(", ".join(sorted(t["writers"]))) or "—", escape(", ".join(sorted(t["readers"]))[:300]) or "—",
                         f"{int(rv):,}" if rv is not None else "—", cols + more])
        parts.append(_table(["Table", "Cols", "Primary key", "Loaded by", "Read by", "Rows", "Columns"], rows))

    if routines:
        parts.append("<h2>Procedures, functions and views</h2>")
        parts.append(_table(["Object", "Type", "Lines", "Complexity", "Reads", "Writes", "Notable", "Fit", "Conversion"], [
            [f"<code>{escape(r['name'])}</code>", escape(r["type"]), str(r["lines"]), escape(r["complexity"]) or "—",
             escape(", ".join(r["reads"])) or "—", escape(", ".join(r["writes"])) or "—",
             escape(", ".join(LABELS[k] for k in r["features"])) or "—", escape(r["fit"]) or "—",
             _pill(r["status"]) if r["status"] else "—"]
            for r in sorted(routines, key=lambda r: (r["type"], r["name"].lower()))]))

        loads = [(t["name"], sorted(t["writers"])) for t in tables.values() if t["writers"]]
        if loads:
            parts.append("<h2>How data flows</h2><p class='muted'>Which routines load each table.</p>")
            parts.append(_table(["Table", "Loaded by"], [[f"<code>{escape(n)}</code>", escape(", ".join(w))]
                                                         for n, w in sorted(loads, key=lambda x: x[0].lower())]))

    if feature_users:
        parts.append("<h2>What needs attention</h2><p class='muted'>Patterns that usually need a person when moving to Databricks.</p>")
        parts.append(_table(["Pattern", "Objects", "Where"], [
            [escape(LABELS[k]), str(len(v)), escape(", ".join(sorted(set(v)))[:600])]
            for k, v in sorted(feature_users.items(), key=lambda kv: -len(kv[1]))]))

    parts.append("<h2>Old and new files</h2><p class='muted'>Every source file next to its converted Databricks file. "
                 "In the package: <code>original_code/</code> and <code>converted_code/</code>.</p>")
    parts.append(_table(["Original file", "Converted file", "Status", "Open items", "Converter"], [
        [f"<code>{escape(p['old'])}</code>" if p["old"] else "—", f"<code>{escape(p['new'])}</code>" if p["new"] else "—",
         _pill(p["status"]), str(p["open"]) if p["open"] else "—", escape(p["converter"]) or "—"] for p in pairs]))

    generated = datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Code Overview — {escape(cfg.name)}</title><style>{CSS}</style></head><body><main>
<h1>Code overview: {escape(cfg.name)}</h1>
<p class="sub">{escape(cfg.source.label.replace(" data warehouse", ""))} · generated {generated} from the code itself · Wishtree WishBridge {__version__}</p>
{''.join(parts)}
<footer>Read automatically from the source code{', the analyzer workbook' if a else ''}{' and the database inventory' if inv else ''}.
Table usage is found by name matching, so treat it as a guide and confirm in the code. Prepared with Wishtree WishBridge.</footer>
</main></body></html>"""
