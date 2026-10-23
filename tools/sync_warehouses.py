"""Keep the warehouse copies in step - without taking away their independence.

Each warehouse (src/wishbridge/warehouses/<warehouse>/) is a complete copy and may work differently. ssis/ is the
REFERENCE: a fix meant for every warehouse is made there first and copied to the others with this tool.

A file a warehouse changes on purpose is listed in that warehouse's CUSTOMIZED.txt (one line: path - why). Such a
file is never overwritten and never reported. A file the reference itself customizes (listed in ssis/CUSTOMIZED.txt)
is never copied to the others. Every other file is SHARED: it must match the reference, apart from the package name
(wishbridge.warehouses.ssis -> wishbridge.warehouses.<warehouse>).

    python tools/sync_warehouses.py --check            # list what drifted (tests/test_warehouse_drift.py does this)
    python tools/sync_warehouses.py --dry-run          # show what a sync would change
    python tools/sync_warehouses.py                    # copy every shared file from ssis/ to the other warehouses
    python tools/sync_warehouses.py data.py ui/steps/data.py   # copy only these files
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

WAREHOUSES = Path(__file__).resolve().parents[1] / "src" / "wishbridge" / "warehouses"
REFERENCE = "ssis"
SKIP_PARTS = {"__pycache__"}
LIST = "CUSTOMIZED.txt"


def warehouses() -> list[str]:
    return sorted(p.name for p in WAREHOUSES.iterdir() if p.is_dir() and (p / "ai.py").is_file())


def customized(warehouse: str) -> dict[str, str]:
    """path (with /) -> why, from <warehouse>/CUSTOMIZED.txt."""
    path = WAREHOUSES / warehouse / LIST
    out: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                name, _, why = line.partition(" - ")
                out[name.strip().replace("\\", "/")] = why.strip()
    return out


def _files(warehouse: str) -> dict[str, Path]:
    root = WAREHOUSES / warehouse
    return {p.relative_to(root).as_posix(): p for p in sorted(root.rglob("*"))
            if p.is_file() and not SKIP_PARTS.intersection(p.parts) and p.name != LIST}


def expected(rel: str, warehouse: str) -> str:
    """The reference file as it should read in `warehouse`."""
    text = (WAREHOUSES / REFERENCE / rel).read_text(encoding="utf-8")
    return text.replace(f"wishbridge.warehouses.{REFERENCE}", f"wishbridge.warehouses.{warehouse}")


def drift(warehouse: str) -> list[str]:
    """Shared files of `warehouse` that differ from the reference (or are missing)."""
    skip = set(customized(REFERENCE)) | set(customized(warehouse))
    have = _files(warehouse)
    out = []
    for rel in _files(REFERENCE):
        if rel in skip:
            continue
        if rel not in have:
            out.append(f"{rel} (missing)")
        elif have[rel].read_text(encoding="utf-8").replace("\r\n", "\n") != expected(rel, warehouse).replace("\r\n", "\n"):
            out.append(rel)
    return out


def sync(only: list[str] | None = None, dry_run: bool = False) -> list[str]:
    """Copy shared files from the reference to every other warehouse. Returns 'warehouse/path' changed."""
    ref_custom = set(customized(REFERENCE))
    changed = []
    for w in warehouses():
        if w == REFERENCE:
            continue
        skip = ref_custom | set(customized(w))
        for rel in _files(REFERENCE):
            if rel in skip or (only and rel not in only):
                continue
            target = WAREHOUSES / w / rel
            new = expected(rel, w)
            if target.is_file() and target.read_text(encoding="utf-8").replace("\r\n", "\n") == new.replace("\r\n", "\n"):
                continue
            changed.append(f"{w}/{rel}")
            if not dry_run:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(new, encoding="utf-8")
    return changed


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("files", nargs="*", help="only these files (paths inside a warehouse folder, e.g. ui/steps/data.py)")
    ap.add_argument("--check", action="store_true", help="only list drifted files; exit 1 if any")
    ap.add_argument("--dry-run", action="store_true", help="show what a sync would change, change nothing")
    a = ap.parse_args()
    only = [f.replace("\\", "/") for f in a.files] or None
    if a.check:
        bad = {w: d for w in warehouses() if w != REFERENCE and (d := drift(w))}
        for w, d in bad.items():
            print(f"{w}: {', '.join(d)}")
        if bad:
            print("\nCopy a fix from ssis/ with: python tools/sync_warehouses.py <file>  - or, when the difference is "
                  "on purpose, add the file to that warehouse's CUSTOMIZED.txt with the reason.")
        else:
            print("All warehouses match the reference, apart from their customized files.")
        return 1 if bad else 0
    changed = sync(only, a.dry_run)
    print(("Would change" if a.dry_run else "Changed") + f" {len(changed)} file(s)" + (":" if changed else "."))
    for c in changed:
        print("  " + c)
    return 0


if __name__ == "__main__":
    sys.exit(main())
