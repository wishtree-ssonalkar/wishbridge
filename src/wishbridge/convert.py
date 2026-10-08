"""Step 2 - Convert: LakeBridge transpile -> WishBridge rules -> (optional) AI suggestions."""

from __future__ import annotations

import re
import shutil
from dataclasses import replace
from pathlib import Path
from typing import Any

from . import lakebridge
from .config import ProjectConfig, fallback_converter
from .rules import ERROR, INFO, WARNING, Finding, apply_rules, detect, dropped_statement_check
from .state import save_step

# One TranspileError(...) record; messages can span several lines.
_TRANSPILE_NOTE = re.compile(
    r"TranspileError\(.*?kind=(\w+), severity=(\w+), path='([^']+)', message='(.*?)'\)\s*(?=TranspileError\(|\Z)", re.DOTALL)


def _transpile_notes(error_log: Path) -> dict[str, list[str]]:
    """File name -> non-INFO messages from LakeBridge's error log (parse errors summarised into one message)."""
    notes: dict[str, list[str]] = {}
    parse_errors: dict[str, list[str]] = {}
    if error_log.exists():
        for kind, severity, path, message in _TRANSPILE_NOTE.findall(error_log.read_text(encoding="utf-8", errors="replace")):
            if severity == "INFO":
                continue
            text = " ".join(message.split())
            target = parse_errors if kind == "PARSING" else notes
            target.setdefault(Path(path).name, []).append(text)
    for name, errs in parse_errors.items():
        examples = "; ".join(errs[:2]) + ("; ..." if len(errs) > 2 else "")
        notes.setdefault(name, []).insert(0, f"LakeBridge could not parse {len(errs)} part(s) of this file, which were "
                                             f"left as comments - rewrite them manually (e.g. {examples})")
    return notes


NOTEBOOK_NOTES = {
    "notebook": "Databricks notebook (PySpark) generated from the ETL job: `wishbridge deploy` uploads it and "
                "`wishbridge execute` runs it on Databricks.",
    "python": "Python helper module used by the converted notebooks; `wishbridge deploy` uploads it next to them.",
}


def file_kind(rel: Path, text: str) -> str:
    """sql, notebook (Databricks notebook source) or python (helper module)."""
    if rel.suffix.lower() == ".py":
        return "notebook" if text.lstrip().startswith("# Databricks notebook source") else "python"
    return "sql"


def file_status(findings: list[Finding]) -> str:
    open_ = [f for f in findings if not f.fixed]
    if any(f.severity == ERROR for f in open_):
        return "needs-fix"
    if any(f.severity == WARNING for f in open_):
        return "review"
    return "ready"


def check_sql(cfg: ProjectConfig, rel: Path, text: str, notes: list[str],
              source_file: Path | None) -> tuple[str, list[Finding]]:
    """Apply the rules to one converted SQL file and add the converter's own notes and the dropped-statement check."""
    fixed, findings = apply_rules(text, cfg.schema_map, cfg.source.key)
    added: set[str] = set()
    for msg in notes:
        # Most transpiler warnings are also written into the code as FIXME comments (possibly with guidance added).
        if msg in added or any(msg in f.message for f in findings):
            continue
        added.add(msg)
        findings.append(Finding("transpile-error", ERROR, 1, msg))
    if source_file is not None and source_file.is_file() and source_file.suffix.lower() == ".sql":
        dropped = dropped_statement_check(source_file.read_text(encoding="utf-8-sig", errors="replace"), text, findings)
        if dropped:
            findings.append(dropped)
    return fixed, findings


def _score(findings: list[Finding]) -> tuple[int, int]:
    """Lower is better: open errors first, then open warnings."""
    open_ = [f for f in findings if not f.fixed]
    return sum(f.severity == ERROR for f in open_), sum(f.severity == WARNING for f in open_)


def choose_best_converter(cfg: ProjectConfig, raw_dir: Path, tnotes: dict[str, list[str]]) -> dict[str, str]:
    """Automatic converter mode: for each SQL file the main converter left errors in, also try the other
    LakeBridge converter (when it supports this source) and keep whichever result has fewer open errors.

    Better results replace the file in raw_dir (and its notes in tnotes). Returns file -> converter used."""
    used = {p.relative_to(raw_dir).as_posix(): cfg.transpiler for p in raw_dir.rglob("*") if p.is_file()}
    alternative = fallback_converter(cfg)
    if not alternative:
        return used
    primary: dict[Path, tuple[int, int]] = {}
    for p in raw_dir.rglob("*"):
        rel = p.relative_to(raw_dir)
        text = p.read_text(encoding="utf-8-sig", errors="replace") if p.is_file() else ""
        source = cfg.input_dir / rel
        if p.is_file() and file_kind(rel, text) == "sql" and source.is_file():
            score = _score(check_sql(cfg, rel, text, tnotes.get(rel.name, []), source)[1])
            if score[0] > 0:
                primary[rel] = score
    if not primary:
        return used

    work = cfg.output_dir / "fallback"
    if work.exists():
        shutil.rmtree(work)
    for rel in primary:
        (work / "input" / rel).parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(cfg.input_dir / rel, work / "input" / rel)
    alt_cfg = replace(cfg, transpiler=alternative, input_dir=work / "input", target_technology="")
    try:
        lakebridge.transpile(alt_cfg, work / "converted", cfg.out("logs", "fallback_errors.log"))
    except lakebridge.LakeBridgeError:
        return used  # the fallback converter failed outright; keep the main converter's output
    alt_notes = _transpile_notes(cfg.output_dir / "logs" / "fallback_errors.log")
    for rel, score in primary.items():
        alt_file = work / "converted" / rel
        if not alt_file.is_file():
            continue
        alt_text = alt_file.read_text(encoding="utf-8-sig", errors="replace")
        alt_score = _score(check_sql(cfg, rel, alt_text, alt_notes.get(rel.name, []), cfg.input_dir / rel)[1])
        # Switch only for strictly fewer errors: the fallback is there to rescue files the main converter
        # failed on, not to trade warnings (a converter that writes fewer notes is not necessarily better).
        if alt_score[0] < score[0]:
            shutil.copy2(alt_file, raw_dir / rel)
            tnotes[rel.name] = alt_notes.get(rel.name, [])
            used[rel.as_posix()] = alternative
    return used


def run_convert(cfg: ProjectConfig, use_ai: bool | None = None) -> dict[str, Any]:
    use_ai = cfg.ai_enabled if use_ai is None else use_ai
    if use_ai:
        from .ai import preflight

        preflight()
    raw_dir = cfg.output_dir / "converted"
    final_dir = cfg.output_dir / "final"
    ai_dir = cfg.output_dir / "ai_suggestions"
    for d in (raw_dir, final_dir):
        if d.exists():
            shutil.rmtree(d)
    error_log = cfg.out("logs", "transpile_errors.log")
    if error_log.exists():
        error_log.unlink()

    lakebridge.transpile(cfg, raw_dir, error_log)
    tnotes = _transpile_notes(error_log)
    converters = choose_best_converter(cfg, raw_dir, tnotes)

    files: list[dict[str, Any]] = []
    converted = sorted(p.relative_to(raw_dir) for p in raw_dir.rglob("*") if p.is_file())
    # Hand-written files in overrides/ with no converted counterpart are added too (e.g. target tables for an ETL job).
    extra = sorted(p.relative_to(cfg.overrides_dir) for p in cfg.overrides_dir.rglob("*")
                   if p.is_file() and p.relative_to(cfg.overrides_dir) not in converted) if cfg.overrides_dir and cfg.overrides_dir.exists() else []
    for rel in sorted(converted + extra):
        src = raw_dir / rel
        text = src.read_text(encoding="utf-8-sig", errors="replace") if src.exists() else ""
        kind = file_kind(rel, text)
        if kind == "sql":
            fixed, findings = check_sql(cfg, rel, text, tnotes.get(rel.name, []), cfg.input_dir / rel if src.exists() else None)
        else:
            fixed, findings = text, [Finding("notebook", INFO, 1, NOTEBOOK_NOTES[kind])]

        # A hand-fixed file in overrides/ replaces the converted one; it is still checked, never rewritten.
        override = cfg.overrides_dir / rel if cfg.overrides_dir else None
        manual = override is not None and override.is_file()
        if manual:
            fixed = override.read_text(encoding="utf-8-sig", errors="replace")
            kind = file_kind(rel, fixed)
            findings = detect(fixed, cfg.source.key) if kind == "sql" else [Finding("notebook", INFO, 1, NOTEBOOK_NOTES[kind])]

        dest = final_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(fixed, encoding="utf-8")

        entry: dict[str, Any] = {
            "file": rel.as_posix(),
            "kind": kind,
            "converter": "manual" if not src.exists() else converters.get(rel.as_posix(), cfg.transpiler),
            "input": str(cfg.input_dir / rel),
            "converted": str(src) if src.exists() else "",
            "final": str(dest),
            "manual_override": manual,
            "status": "review" if kind == "notebook" else file_status(findings),
            "fixed": sum(1 for f in findings if f.fixed),
            "findings": [f.to_dict() for f in findings],
        }

        if use_ai and not manual and kind == "sql" and entry["status"] != "ready":
            from .ai import suggest_fix

            original_path = cfg.input_dir / rel
            original = original_path.read_text(encoding="utf-8-sig", errors="replace") if original_path.exists() else ""
            s = suggest_fix(cfg.source.analyzer_tech, original, fixed, findings, cfg.ai_model)
            entry["ai"] = {"status": s.status, "notes": s.notes}
            if s.sql:
                out = ai_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(s.sql, encoding="utf-8")
                out.with_suffix(out.suffix + ".notes.md").write_text(s.notes + "\n", encoding="utf-8")
                entry["ai"]["file"] = str(out)
        files.append(entry)

    summary = {
        "files": len(files),
        "ready": sum(f["status"] == "ready" for f in files),
        "review": sum(f["status"] == "review" for f in files),
        "needs_fix": sum(f["status"] == "needs-fix" for f in files),
        "auto_fixed": sum(f["fixed"] for f in files),
        "manual_overrides": sum(1 for f in files if f.get("manual_override")),
        "open_errors": sum(1 for f in files for x in f["findings"] if not x["fixed"] and x["severity"] == ERROR),
        "open_warnings": sum(1 for f in files for x in f["findings"] if not x["fixed"] and x["severity"] == WARNING),
    }
    result = {"summary": summary, "files": files, "transpiler": cfg.transpiler, "final_dir": str(final_dir)}
    save_step(cfg, "convert", result)
    return result
