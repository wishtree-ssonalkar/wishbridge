"""Results of each pipeline step, persisted to <output>/state.json so steps can run independently."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from .config import ProjectConfig


def load_state(cfg: ProjectConfig) -> dict[str, Any]:
    p = cfg.output_dir / "state.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def save_step(cfg: ProjectConfig, step: str, result: dict[str, Any]) -> None:
    state = load_state(cfg)
    result = {**result, "completed_at": datetime.now(timezone.utc).isoformat(timespec="seconds")}
    state[step] = result
    p = cfg.out("state.json")
    p.write_text(json.dumps(state, indent=2, default=str), encoding="utf-8")
