"""What the driver did in the app: review decisions, cash logged by voice or text, cached explanations.

Kept in data/app/state.json (git-ignored). POST /api/reset clears it, which is handy between video takes.
"""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Callable, Dict

ROOT = Path(__file__).resolve().parents[1]
PATH = Path(os.environ.get("APP_STATE", ROOT / "data" / "app" / "state.json"))
_lock = threading.Lock()


def empty() -> Dict:
    return {"v": 0, "decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}, "pile": None}


def load() -> Dict:
    if not PATH.exists():
        return empty()
    try:
        data = json.loads(PATH.read_text())
    except (OSError, ValueError):
        return empty()
    return {**empty(), **data}


def update(change: Callable[[Dict], None]) -> Dict:
    with _lock:
        state = load()
        change(state)
        state["v"] = state.get("v", 0) + 1
        PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, indent=1))
        tmp.replace(PATH)
        return state


def reset() -> Dict:
    def change(s):
        v = s.get("v", 0)  # keep counting up so cached views know the state changed
        s.clear()
        s.update(empty())
        s["v"] = v
    return update(change)
