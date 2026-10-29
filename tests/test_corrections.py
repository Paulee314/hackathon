"""Meals and corrections (Oct 29): the meal card asks with the tax rule instead of guessing; an overnight work meal
books half on Schedule C line 24b; answers become rules, here and in the next categorizer run. Offline."""
from __future__ import annotations

import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import agents.categorize as categorize  # noqa: E402
from agents import close  # noqa: E402
from app.books import MEALS_KEPT_OUT, Books  # noqa: E402
from ingest.load import load_folder  # noqa: E402

MONTH = ROOT / "samples" / "october"
MEAL = "receipt:2026-10-11_lone-star-burger"
GROCERIES = "receipt:2026-10-18_bayou-fresh"


def books(**decisions):
    return Books(MONTH, date(2026, 10, 31), {"decisions": {k: {"choice": v} for k, v in decisions.items()}})


class Meal(unittest.TestCase):
    def test_card_asks_without_suggesting(self):
        it = {i["id"]: i for i in books().review_items()}[MEAL]
        self.assertIsNone(it["suggestion"])
        self.assertEqual({c["choice"] for c in it["choices"]}, {"personal", "travel_meal"})
        self.assertIn("274(n)", it["basis"])
        self.assertIn("1.262-1(b)(5)", it["basis"])

    def test_overnight_work_meal_is_half_on_line_24b(self):
        local, away = books(**{MEAL: "personal"}), books(**{MEAL: "travel_meal"})
        self.assertEqual(away.schedule_c()["lines"]["24b Meals"], Decimal("7.16"))
        self.assertNotIn("24b Meals", local.schedule_c()["lines"])
        self.assertEqual(away.totals()["other"][MEALS_KEPT_OUT], Decimal("7.16"))
        self.assertEqual(local.totals()["profit"] - away.totals()["profit"], Decimal("7.16"))

    def test_scored_as_an_owner_question_not_a_false_alarm(self):
        s = close.score([{"date": "2026-10-11", "amount": "-14.32", "title": "receipt: burger"}],
                        MONTH / "expected_exceptions.json")
        self.assertEqual((s["asked"], s["false_alarms"]), (["Q1"], []))


class Rules(unittest.TestCase):
    def test_answers_become_rules(self):
        rules = books(**{MEAL: "personal", GROCERIES: "business"}).merchant_rules()
        self.assertEqual(rules["LONE STAR BURGER"]["says"], "personal")
        self.assertEqual(rules["LONE STAR BURGER"]["name"], "Lone Star Burger")
        self.assertFalse(rules["LONE STAR BURGER"]["corrected_the_model"])     # the categorizer said personal too
        self.assertTrue(rules["BAYOU FRESH MARKET"]["corrected_the_model"])    # it said personal; you said business

    def test_an_overnight_meal_is_not_a_rule(self):
        self.assertEqual(books(**{MEAL: "travel_meal"}).merchant_rules(), {})

    def test_a_rule_beats_the_categorizer_for_every_charge_there(self):
        b = books(**{GROCERIES: "business"})
        t = next(t for t in b.txns if "BAYOU FRESH" in t.description)
        self.assertEqual(b.treatment(t), "expense")
        self.assertEqual(books().treatment(t), "personal")

    def test_next_categorizer_run_applies_them(self):
        sent = []

        def fake(messages, model=None):
            sent.append(messages[0]["content"])
            listing = messages[0]["content"].split("Merchants:\n", 1)[1]
            names = [line[2:].split(" | ")[0] for line in listing.splitlines() if line.startswith("- ")]
            return [{"merchant": n, "treatment": "personal", "schedule_c_line": None, "business_line": "both",
                     "reason": "test"} for n in names]
        real, categorize.chat_json = categorize.chat_json, fake
        try:
            txns, _, _ = load_folder(MONTH)
            cats = categorize.categorize(txns, rules={"LONE STAR BURGER": {"says": "personal"},
                                                     "BAYOU FRESH MARKET": {"says": "business"}})
        finally:
            categorize.chat_json = real
        self.assertNotIn("LONE STAR BURGER", sent[0])           # answered already: never sent to the model
        self.assertIn("BAYOU FRESH MARKET", sent[0])
        self.assertIn("the owner says: a business cost", sent[0])
        self.assertEqual((cats["LONE STAR BURGER"]["treatment"], cats["LONE STAR BURGER"]["rule"]), ("personal", True))
        self.assertEqual(cats["BAYOU FRESH MARKET"]["treatment"], "expense")   # the owner's answer wins
        self.assertEqual(cats["BAYOU FRESH MARKET"]["schedule_c_line"], "27a Other expenses")


if __name__ == "__main__":
    unittest.main()
