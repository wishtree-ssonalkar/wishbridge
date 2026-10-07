"""Step 2 - Convert: LakeBridge transpile -> WishBridge rules -> (optional) AI suggestions."""

from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Any

from . import lakebridge
from .config import ProjectConfig
from .rules import ERROR, WARNING, Finding, apply_rules
from .state import save_step

_TRANSPILE_NOTE = re.compile(r"path='([^']+)', message='(.*)'\)\s*$")


def _transpile_notes(error_log: Path) -> dict[str, list[str]]:
    notes: dict[str, list[str]] = {}
    if error_log.exists():
        for line in error_log.read_text(encoding="utf-8", errors="replace").splitlines():
            m = _TRANSPILE_NOTE.search(line)
            if m and "severity=INFO" not in line:
                notes.setdefault(Path(m.group(1)).name, []).append(m.group(2))
    return notes


def file_status(findings: list[Finding]) -> str:
    open_ = [f for f in findings if not f.fixed]
    if any(f.severity == ERROR for f in open_):
        return "needs-fix"
    if any(f.severity == WARNING for f in open_):
        return "review"
    return "ready"


def run_convert(cfg: ProjectConfig, use_ai: bool | None = None) -> dict[str, Any]:
    use_ai = cfg.ai_enabled if use_ai is None else use_ai
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
    for src in sorted(p for p in raw_dir.rglob("*") if p.is_file()):
        rel = src.relative_to(raw_dir)
        text = src.read_text(encoding="utf-8", errors="replace")
        fixed, findings = apply_rules(text, cfg.schema_map, cfg.source.key)
        seen = {f.message for f in findings}
        for msg in tnotes.get(rel.name, []):
            if msg not in seen:  # most transpiler warnings are also written into the code as FIXME comments
                findings.append(Finding("transpile-error", ERROR, 1, msg))
        dest = final_dir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(fixed, encoding="utf-8")

        entry: dict[str, Any] = {
            "file": rel.as_posix(),
            "input": str(cfg.input_dir / rel),
            "converted": str(src),
            "final": str(dest),
            "status": file_status(findings),
            "fixed": sum(1 for f in findings if f.fixed),
            "findings": [f.to_dict() for f in findings],
        }

        if use_ai and entry["status"] != "ready":
            from .ai import suggest_fix

            original_path = cfg.input_dir / rel
            original = original_path.read_text(encoding="utf-8", errors="replace") if original_path.exists() else ""
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
        "open_errors": sum(1 for f in files for x in f["findings"] if not x["fixed"] and x["severity"] == ERROR),
        "open_warnings": sum(1 for f in files for x in f["findings"] if not x["fixed"] and x["severity"] == WARNING),
    }
    result = {"summary": summary, "files": files, "transpiler": cfg.transpiler, "final_dir": str(final_dir)}
    save_step(cfg, "convert", result)
    return result
