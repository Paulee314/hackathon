"""The hosted demo's guards: each visitor's state is separate, idle visitors are swept, spend is
counted from the usage log and capped per day, and request limits slide over a window.

Offline and standard library only.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.meta import cost_of  # noqa: E402
from app import state as st  # noqa: E402
from app.limits import Budget, RateLimit  # noqa: E402


class Visitors(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_ids_cannot_name_another_folder(self):
        self.assertTrue(st.valid_id(st.new_id()))
        for bad in (None, "", "../../etc", "ABCDEF0123456789abcdef01", "a" * 23, "a" * 25, "pile"):
            self.assertFalse(st.valid_id(bad), bad)
            if bad:
                with self.assertRaises(ValueError):
                    st.for_visitor(bad, self.base)

    def test_each_visitor_has_their_own_month(self):
        a, b = st.for_visitor(st.new_id(), self.base), st.for_visitor(st.new_id(), self.base)
        self.assertEqual(a.load(), st.empty())
        a.update(lambda s: s["decisions"].__setitem__("x", {"choice": "personal"}))
        self.assertEqual(a.load()["decisions"], {"x": {"choice": "personal"}})
        self.assertEqual(b.load()["decisions"], {})
        self.assertFalse(b.folder.exists())  # nothing written for a visitor who only looked

    def test_reset_clears_one_visitor_and_their_photos(self):
        a, b = st.for_visitor(st.new_id(), self.base), st.for_visitor(st.new_id(), self.base)
        for s in (a, b):
            s.update(lambda x: x["entries"].append({"id": "e1"}))
            (s.piles / "1").mkdir(parents=True)
            (s.piles / "1" / "photo.jpg").write_bytes(b"jpeg")
        before = a.load()
        after = a.reset()
        self.assertEqual(after["entries"], [])
        self.assertGreater(after["v"], before["v"])         # cached views see the change
        self.assertEqual(after["born"], before["born"])
        self.assertFalse(a.piles.exists())
        self.assertEqual(len(b.load()["entries"]), 1)
        self.assertTrue((b.piles / "1" / "photo.jpg").exists())

    def test_sweep_removes_idle_visitors_then_the_oldest(self):
        now = time.time()
        stores = [st.for_visitor(st.new_id(), self.base) for _ in range(4)]
        ages_days = [0.1, 1.0, 3.0, 0.5]
        for s, age in zip(stores, ages_days):
            s.update(lambda x: None)
            os.utime(s.path, (now - age * 86400, now - age * 86400))
        (self.base / "not-a-visitor").mkdir()
        self.assertEqual(st.sweep(self.base, keep_days=2, max_visitors=500, now=now), 1)
        self.assertFalse(stores[2].folder.exists())
        self.assertEqual(st.sweep(self.base, keep_days=2, max_visitors=2, now=now), 1)
        self.assertFalse(stores[1].folder.exists())             # least recently used of the three left
        self.assertTrue(stores[0].folder.exists() and stores[3].folder.exists())
        self.assertTrue((self.base / "not-a-visitor").exists())
        self.assertEqual(st.sweep(self.base / "missing"), 0)


class Spend(unittest.TestCase):
    def test_cost_of_each_kind_of_call(self):
        chat = {"model": "muse-spark-1.3", "usage": {"prompt_tokens": 1000, "completion_tokens": 2000,
                                                     "prompt_tokens_details": {"cached_tokens": 200}}}
        self.assertAlmostEqual(cost_of(chat), (800 * 1.25 + 200 * 0.15 + 2000 * 4.25) / 1e6)
        self.assertAlmostEqual(cost_of({"model": "sam-3.1", "images": 1}), 0.0025)
        self.assertAlmostEqual(cost_of({"model": "muse-image-1.0", "images": 2}), 0.02)
        self.assertAlmostEqual(cost_of({"model": "muse-voice-transcribe-1.0", "audio_ms": 3_600_000}), 0.18)

    def test_budget_counts_today_only_and_reads_new_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "usage.jsonl"
            day = {"d": "2026-11-02"}
            budget = Budget(log, daily=1.0, today=lambda: day["d"])
            self.assertEqual(budget.spent_today(), 0.0)
            rows = [{"at": "2026-11-01T23:59:00", "cost": 5.0},
                    {"at": "2026-11-02T09:00:00", "cost": 0.25},
                    {"at": "2026-11-02T09:01:00", "model": "sam-3.1", "images": 1}]   # older row: no cost field
            log.write_text("".join(json.dumps(r) + "\n" for r in rows))
            self.assertAlmostEqual(budget.spent_today(), 0.2525)
            with open(log, "a") as f:
                f.write(json.dumps({"at": "2026-11-02T10:00:00", "cost": 0.75}) + "\n")
                f.write('{"at": "2026-11-02T10:00:01", "co')                          # still being written
            self.assertAlmostEqual(budget.spent_today(), 1.0025)
            self.assertEqual(budget.left(), 0.0)
            with open(log, "a") as f:
                f.write('st": 0.5}\n')
            self.assertAlmostEqual(budget.spent_today(), 1.5025)
            day["d"] = "2026-11-03"
            self.assertEqual(budget.spent_today(), 0.0)
            self.assertEqual(budget.left(), 1.0)


class Limits(unittest.TestCase):
    def test_sliding_window(self):
        clock = {"t": 1000.0}
        limit = RateLimit(3, 60, clock=lambda: clock["t"])
        self.assertEqual([limit.allow("a") for _ in range(4)], [True, True, True, False])
        self.assertTrue(limit.allow("b"))                     # other visitors are unaffected
        self.assertEqual(limit.retry_after("a"), 61)
        clock["t"] += 60
        self.assertTrue(limit.allow("a"))
        self.assertEqual(limit.retry_after("nobody"), 0)


if __name__ == "__main__":
    unittest.main()
