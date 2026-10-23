"""The Databricks App runs Python 3.11: no f-string may reuse its own quote inside {...} (allowed from 3.12 only)."""

import io
import tokenize
from pathlib import Path

SRC = Path(__file__).parents[1] / "src" / "wishbridge"


def _quote(tok: str) -> str:
    s = tok.lstrip("rRbBuUfF")
    return s[:3] if s[:3] in ('"""', "'''") else s[:1]


def problems(path: Path) -> list[str]:
    out = []
    stack: list[str] = []  # quotes of the f-strings we are inside
    for t in tokenize.generate_tokens(io.StringIO(path.read_text(encoding="utf-8")).readline):
        name = tokenize.tok_name[t.type]
        if name == "FSTRING_START":
            q = _quote(t.string)
            if q in stack:
                out.append(f"{path.relative_to(SRC.parent)}:{t.start[0]}")
            stack.append(q)
        elif name == "FSTRING_END":
            stack.pop()
        elif name == "STRING" and stack and _quote(t.string) in stack:
            out.append(f"{path.relative_to(SRC.parent)}:{t.start[0]}")
    return out


def test_no_python_312_only_fstrings():
    found = [p for f in sorted(SRC.rglob("*.py")) for p in problems(f)]
    assert not found, "f-strings that Python 3.11 rejects: " + ", ".join(found)
