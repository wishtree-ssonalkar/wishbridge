"""Step 2 - Convert: LakeBridge transpile -> WishBridge rules -> (optional) AI suggestions."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from . import lakebridge
from .config import ProjectConfig
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
            fixed, findings = apply_rules(text, cfg.schema_map, cfg.source.key)
            added: set[str] = set()
            for msg in tnotes.get(rel.name, []):
                # Most transpiler warnings are also written into the code as FIXME comments (possibly with guidance added).
                if msg in added or any(msg in f.message for f in findings):
                    continue
                added.add(msg)
                findings.append(Finding("transpile-error", ERROR, 1, msg))
            source_file = cfg.input_dir / rel
            if src.exists() and source_file.is_file() and source_file.suffix.lower() == ".sql":
                dropped = dropped_statement_check(source_file.read_text(encoding="utf-8-sig", errors="replace"), text, findings)
                if dropped:
                    findings.append(dropped)
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
