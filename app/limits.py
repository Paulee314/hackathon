"""Keeps the hosted demo affordable and up for everyone.

Two guards run before any request that calls a Meta model:
- a daily dollar cap across all visitors, summed from the usage log (agents/meta.py writes what
  each call cost), so a busy day can't use up the event's credits;
- request limits per visitor and per network address over a sliding window, so one tab (or a
  script) can't crowd everyone else out.

APP_DAILY_BUDGET (dollars, default 5) sets the cap. Standard library only.
"""
from __future__ import annotations

import json
import os
import threading
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Callable, Deque, Dict

from agents.meta import USAGE_LOG, cost_of

DAILY_BUDGET = float(os.environ.get("APP_DAILY_BUDGET", "5"))


class Budget:
    """Today's Meta spend, read incrementally from the usage log (only new lines on each check)."""

    def __init__(self, log: Path = USAGE_LOG, daily: float = DAILY_BUDGET,
                 today: Callable[[], str] = lambda: time.strftime("%Y-%m-%d")):
        self.log, self.daily, self.today = Path(log), daily, today
        self._day, self._offset, self._spent = None, 0, 0.0
        self._lock = threading.Lock()

    def spent_today(self) -> float:
        with self._lock:
            day = self.today()
            if day != self._day:  # a new day (or the first check): count from the top again
                self._day, self._offset, self._spent = day, 0, 0.0
            if not self.log.exists():
                return 0.0
            with open(self.log, "rb") as f:
                f.seek(self._offset)
                chunk = f.read()
            complete = chunk[:chunk.rfind(b"\n") + 1]  # leave a half-written last line for next time
            self._offset += len(complete)
            for line in complete.splitlines():
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if str(row.get("at", "")).startswith(day):
                    self._spent += row["cost"] if isinstance(row.get("cost"), (int, float)) else cost_of(row)
            return self._spent

    def left(self) -> float:
        return max(0.0, self.daily - self.spent_today())


class RateLimit:
    """At most `limit` hits per key in any `window` seconds."""

    def __init__(self, limit: int, window: float, clock: Callable[[], float] = time.time):
        self.limit, self.window, self.clock = limit, window, clock
        self._hits: Dict[str, Deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = self.clock()
        with self._lock:
            hits = self._hits[key]
            while hits and now - hits[0] >= self.window:
                hits.popleft()
            if len(hits) >= self.limit:
                return False
            hits.append(now)
            if len(self._hits) > 5000:  # forget keys that have gone quiet
                for k in [k for k, h in self._hits.items() if not h or now - h[-1] >= self.window]:
                    del self._hits[k]
            return True

    def retry_after(self, key: str) -> int:
        """Seconds until the oldest hit in the window expires."""
        with self._lock:
            hits = self._hits.get(key)
            if not hits:
                return 0
            return max(1, int(self.window - (self.clock() - hits[0])) + 1)
