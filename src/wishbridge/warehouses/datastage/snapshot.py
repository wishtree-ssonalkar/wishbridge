"""Take the client's code once, with a receipt.

Clients rarely let a consultant back onto their systems again and again. So WishBridge copies the whole code
folder into the project (build and tool folders excluded) during the first visit and records a receipt:
where it came from, when, how many files and a SHA-256 fingerprint of every file. All later work - analysis,
conversion, fixes, review - runs on that copy, and the receipt proves which version of the code the
migration was built from (and shows if a file in the copy was changed afterwards).
"""

from __future__ import annotations

import hashlib
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

RECEIPT = "code_received.json"
MAX_FILE_BYTES = 200 * 1024 * 1024  # larger files (database backups, data extracts) are not code


def _files(folder: Path) -> list[Path]:
    from .discover import SKIP_DIRS

    out, stack = [], [folder]
    while stack:
        d = stack.pop()
        for e in sorted(d.iterdir()):
            if e.is_dir():
                if e.name not in SKIP_DIRS and not e.name.startswith("."):
                    stack.append(e)
            elif e.is_file():
                out.append(e)
    return out


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def take_copy(code_dir: str | Path, dest: str | Path) -> dict[str, Any]:
    """Copy code_dir into dest (which must be empty or new) and return the receipt (also written next to dest)."""
    code_dir, dest = Path(code_dir).expanduser().resolve(), Path(dest).resolve()
    if not code_dir.is_dir():
        raise ValueError(f"Folder not found: {code_dir}")
    if dest == code_dir or code_dir in dest.parents:
        raise ValueError("The copy must go outside the client's folder")
    if dest.exists() and any(dest.iterdir()):
        raise ValueError(f"{dest} is not empty - the code was already copied")
    hashes: dict[str, str] = {}
    skipped: list[str] = []
    total = 0
    for f in _files(code_dir):
        rel = f.relative_to(code_dir).as_posix()
        size = f.stat().st_size
        if size > MAX_FILE_BYTES:
            skipped.append(f"{rel} ({size // (1024 * 1024)} MB)")
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(f, target)
        hashes[rel] = _sha256(target)
        total += size
    if not hashes:
        raise ValueError(f"No files to copy in {code_dir}")
    receipt = {
        "copied_from": str(code_dir),
        "copied_at": datetime.now().isoformat(timespec="seconds"),
        "files": len(hashes),
        "bytes": total,
        "fingerprint": hashlib.sha256("".join(f"{k}:{v}\n" for k, v in sorted(hashes.items())).encode()).hexdigest(),
        "skipped_large_files": skipped,
        "sha256": hashes,
    }
    (dest.parent / RECEIPT).write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    return receipt


def receipt(project_root: str | Path) -> dict[str, Any] | None:
    p = Path(project_root) / RECEIPT
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def check_copy(project_root: str | Path, input_dir: str | Path) -> dict[str, list[str]]:
    """Files of the copy that changed, disappeared or appeared since it was taken (hand fixes belong in overrides/)."""
    r = receipt(project_root)
    if not r:
        return {}
    input_dir = Path(input_dir)
    now = {f.relative_to(input_dir).as_posix(): f for f in _files(input_dir)} if input_dir.is_dir() else {}
    return {
        "changed": sorted(k for k, h in r["sha256"].items() if k in now and _sha256(now[k]) != h),
        "missing": sorted(k for k in r["sha256"] if k not in now),
        "added": sorted(k for k in now if k not in r["sha256"]),
    }


def copy_project_code(project_file: str | Path) -> dict[str, Any]:
    """For a project that reads the client's folder in place: copy that folder into the project and switch to it."""
    from .config import load_config

    project_file = Path(project_file).resolve()
    cfg = load_config(project_file)
    root = project_file.parent
    if root in cfg.input_dir.parents or cfg.input_dir == root:
        raise ValueError(f"The code is already inside the project ({cfg.input_dir})")
    dest = root / "input"
    rec = take_copy(cfg.input_dir, dest)
    raw = yaml.safe_load(project_file.read_text(encoding="utf-8-sig")) or {}
    raw["input"] = "input"
    header = (f"# WishBridge project. Code copied from {rec['copied_from']} on {rec['copied_at']} "
              f"({rec['files']} files, receipt in {RECEIPT}).\n")
    project_file.write_text(header + yaml.safe_dump(raw, sort_keys=False, allow_unicode=True), encoding="utf-8")
    return rec
