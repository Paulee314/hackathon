"""What each visitor did in the app: review decisions, cash logged by voice or text, cached
explanations, the receipt photo they snapped, and the Plaid Sandbox pull if they connected one.

Every browser gets its own copy of the demo month, so several judges can use the hosted app at
once without seeing each other's answers. A visitor's state lives in
data/app/visitors/<id>/state.json (git-ignored), with their receipt photos beside it in pile/.
Nothing is written until a visitor changes something, and visitors idle for two days are removed.
APP_DATA moves the whole folder (the hosted app keeps it on a Modal Volume).
"""
from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import threading
import time
from pathlib import Path
from typing import Callable, Dict, Optional

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get("APP_DATA", ROOT / "data" / "app"))
VISITORS = DATA / "visitors"
KEEP_DAYS = float(os.environ.get("APP_KEEP_DAYS", "2"))
MAX_VISITORS = int(os.environ.get("APP_MAX_VISITORS", "500"))
_ID = re.compile(r"^[0-9a-f]{24}$")

_locks: Dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def new_id() -> str:
    return secrets.token_hex(12)


def valid_id(value: Optional[str]) -> bool:
    """Only ids this module made: 24 hex characters, so a cookie can never name another folder."""
    return bool(value) and bool(_ID.match(value))


def empty() -> Dict:
    return {"v": 0, "decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}, "pile": None,
            "bank": None}


def _lock_for(path: Path) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(str(path), threading.Lock())


class Store:
    """One visitor's state file and receipt photos."""

    def __init__(self, folder: Path):
        self.folder = Path(folder)
        self.path = self.folder / "state.json"
        self.piles = self.folder / "pile"

    def load(self) -> Dict:
        if not self.path.exists():
            return empty()
        try:
            data = json.loads(self.path.read_text())
        except (OSError, ValueError):
            return empty()
        return {**empty(), **data}

    def update(self, change: Callable[[Dict], None]) -> Dict:
        with _lock_for(self.path):
            state = self.load()
            change(state)
            state["v"] = state.get("v", 0) + 1
            state.setdefault("born", new_id())  # tells a fresh visitor apart from one swept and back
            self.folder.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(state, indent=1))
            tmp.replace(self.path)
            return state

    def reset(self) -> Dict:
        def change(s):
            keep = {"v": s.get("v", 0), "born": s.get("born")}  # keep counting so cached views know it changed
            s.clear()
            s.update(empty())
            s.update({k: v for k, v in keep.items() if v is not None})
        state = self.update(change)
        shutil.rmtree(self.piles, ignore_errors=True)
        return state


def for_visitor(visitor_id: str, base: Path = VISITORS) -> Store:
    if not valid_id(visitor_id):
        raise ValueError("not a visitor id")
    return Store(Path(base) / visitor_id)


def _last_used(folder: Path) -> float:
    state = folder / "state.json"
    return (state if state.exists() else folder).stat().st_mtime


def sweep(base: Path = VISITORS, keep_days: float = KEEP_DAYS, max_visitors: int = MAX_VISITORS,
          now: Optional[float] = None) -> int:
    """Remove visitors idle for more than keep_days, then the least recently used beyond
    max_visitors. Returns how many were removed."""
    base = Path(base)
    if not base.is_dir():
        return 0
    now = time.time() if now is None else now
    folders = []
    for f in base.iterdir():
        if f.is_dir() and valid_id(f.name):
            try:
                folders.append((_last_used(f), f))
            except OSError:
                continue
    folders.sort(key=lambda x: x[0], reverse=True)  # most recent first
    removed = 0
    for i, (used, folder) in enumerate(folders):
        if now - used > keep_days * 86400 or i >= max_visitors:
            shutil.rmtree(folder, ignore_errors=True)
            removed += 1
    return removed
