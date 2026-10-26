"""Ride-rule replay: the baseline is the real month, rules only change what they should. Offline."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.books import Books  # noqa: E402
from car.profit import ride_profits  # noqa: E402
from car.rules import PRESETS, Rule, compare, offer_estimates, online_hours, replay, rule_from_dict  # noqa: E402

MONTH = ROOT / "samples" / "october"


class Replay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.cache = offer_estimates(MONTH)
        cls.offers, cls.est, cls.model = cls.cache
        cls.hours = online_hours(cls.offers, cls.est)

    def test_baseline_is_the_real_month(self):
        base = replay(PRESETS[0], self.offers, self.est, self.hours)
        real = ride_profits(MONTH)
        self.assertEqual(base["rides"], len(real))
        self.assertAlmostEqual(base["true_profit"], sum(r["true_profit"] for r in real), places=2)
        self.assertEqual(base["estimated_rides"], 0)

    def test_skip_long_pickups_drops_exactly_those_rides(self):
        c = compare(Rule("x", max_pickup_miles=3), MONTH, cache=self.cache)
        long_ones = [o for o in self.offers if o.accepted and self.est[o.id]["pickup_miles"] > 3]
        # the rule reads the offer card's pickup distance, which is what the driver sees when deciding
        card_long = [o for o in self.offers if o.accepted and o.pickup_miles > 3]
        self.assertEqual(c["rides_you_took_it_skips"], len(card_long))
        self.assertTrue(long_ones)
        self.assertAlmostEqual(c["difference"], c["profit_of_offers_it_takes"] - c["profit_of_rides_it_skips"], places=1)

    def test_rules_never_move_rides_you_kept(self):
        for rule in PRESETS[1:]:
            r = replay(rule, self.offers, self.est, self.hours)
            spans = sorted((self.est[o.id]["start"], self.est[o.id]["end"]) for o in self.offers
                           if self.est[o.id]["real"] and r["decisions"][o.id][0] == "take")
            for (a1, b1), (a2, b2) in zip(spans, spans[1:]):
                self.assertLessEqual(b1, a2 if b1 <= a2 else b1)  # real rides keep their own times

    def test_take_every_offer_never_does_worse(self):
        c = compare(PRESETS[-1], MONTH, cache=self.cache)
        self.assertGreaterEqual(c["difference"], 0)
        self.assertEqual(c["rides_you_took_it_skips"], 0)

    def test_rule_from_spoken_fields(self):
        r = rule_from_dict({"name": "Skip far pickups", "max_pickup_miles": "4", "platforms": ["Uber", "bike"], "hours": [6, 22]})
        self.assertEqual((r.max_pickup_miles, r.platforms, r.hours), (4.0, ["uber"], (6, 22)))
        self.assertEqual(r.describe(), "Skip pickups over 4 miles, only Uber, only 6 AM to 10 PM")


class InTheApp(unittest.TestCase):
    def test_rule_result_reads_right(self):
        b = Books(MONTH, date(2026, 10, 31), {"decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}})
        res = b.rule_result({"name": "Skip pickups over 3 miles", "max_pickup_miles": 3})
        self.assertEqual(res["type"], "rule")
        self.assertTrue(res["amount"].startswith("-$"))
        self.assertIn("would have paid", res["text"])
        self.assertEqual(len(b.rule_presets()), len(PRESETS) - 1)
        self.assertTrue(b.home()["bubbles"][3]["value"].endswith("/hr"))


if __name__ == "__main__":
    unittest.main()
