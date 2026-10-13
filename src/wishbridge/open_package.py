"""Open a WishBridge zip as a project (main screen: "Open the assessment zip", `wishbridge open-package`).

The zip is made by a warehouse's package.py; this file only turns it back into a project folder, so the
main screen needs no pipeline code.
"""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

MANIFEST = "wishbridge_package.json"
BASELINE = "baseline/"  # the code before conversion (a neutral name: the zip is shared with the client)


def open_package(zip_path: str | Path, parent: str | Path, name: str | None = None) -> Path:
    """Recreate the project from a package in parent/name and point its results at the new location."""
    zip_path = Path(zip_path)
    with zipfile.ZipFile(zip_path) as z:
        names = z.namelist()
        if MANIFEST not in names or "project/project.yml" not in names:
            raise ValueError(f"{zip_path.name} is not a WishBridge package (made with `wishbridge package`)")
        man = json.loads(z.read(MANIFEST))
        dest = Path(parent).expanduser().resolve() / (name or man["name"])
        if dest.exists() and any(dest.iterdir()):
            raise ValueError(f"{dest} already exists - choose another name or folder")
        places = {"project/": dest, BASELINE: dest / "input", "original_code/": dest / "input",  # older packages
                  "converted_code/": dest / "output" / "final", "database_code/": dest / "database_code"}
        for n in names:
            prefix = next((k for k in places if n.startswith(k)), None)
            if prefix is None or n.endswith("/"):
                continue
            target = (places[prefix] / n[len(prefix):]).resolve()
            if dest not in target.parents:  # never write outside the new project
                raise ValueError(f"Unsafe path in package: {n}")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(z.read(n))
    # Results store absolute paths: move them from the old project to the new one.
    state = dest / "output" / "state.json"
    if state.exists():
        import re

        moves: dict[str, str] = {}
        for old, new in ((man["input_dir"], dest / "input"), (man["output_dir"], dest / "output"),
                         (man.get("overrides_dir") or "", dest / "overrides"), (man["project_root"], dest)):
            if old:  # paths appear with backslashes (Windows) and with forward slashes (LakeBridge's analyzer)
                moves[json.dumps(str(old))[1:-1]] = json.dumps(str(new))[1:-1]
                moves[str(old).replace("\\", "/")] = str(new).replace("\\", "/")
        # One pass, longest paths first, so a new path is never rewritten again.
        pattern = re.compile("|".join(re.escape(k) for k in sorted(moves, key=len, reverse=True)))
        text = pattern.sub(lambda m: moves[m.group(0)], state.read_text(encoding="utf-8"))
        state.write_text(text, encoding="utf-8")
    from .config import load_config

    load_config(dest / "project.yml")  # fail now if anything is off
    # Remember where it came from: the app then offers "Fix code" for this project.
    (dest / "opened_from_package.json").write_text(json.dumps({**man, "package": str(zip_path.resolve())}, indent=2),
                                                   encoding="utf-8")
    return dest
