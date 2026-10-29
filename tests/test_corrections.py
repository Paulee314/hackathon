"""Receipt answers (Oct 29): a meal card asks business or personal with no suggestion; a business meal books half on
Schedule C line 24b; nothing is learned on its own, and "Every time at ..." sets a merchant, here and in the next
categorizer run. Offline."""
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
    """decisions: item id -> choice, or (choice, True) to also tick "Every time at ..."."""
    state = {"decisions": {}}
    for k, v in decisions.items():
        choice, every = v if isinstance(v, tuple) else (v, False)
        state["decisions"][k] = {"choice": choice, **({"remember": True} if every else {})}
    return Books(MONTH, date(2026, 10, 31), state)


class Meal(unittest.TestCase):
    def test_card_asks_business_or_personal_without_suggesting(self):
        it = {i["id"]: i for i in books().review_items()}[MEAL]
        self.assertIsNone(it["suggestion"])
        self.assertEqual([c["choice"] for c in it["choices"]], ["business", "personal"])
        self.assertTrue(it["meal"])
        self.assertEqual(it["remember"], {"merchant": "LONE STAR BURGER", "name": "Lone Star Burger"})

    def test_business_meal_is_half_on_line_24b(self):
        personal, business = books(**{MEAL: "personal"}), books(**{MEAL: "business"})
        self.assertEqual(business.schedule_c()["lines"]["24b Meals"], Decimal("7.16"))
        self.assertNotIn("24b Meals", personal.schedule_c()["lines"])
        self.assertEqual(business.totals()["other"][MEALS_KEPT_OUT], Decimal("7.16"))
        self.assertEqual(personal.totals()["profit"] - business.totals()["profit"], Decimal("7.16"))

    def test_any_line_24b_meal_counts_half(self):
        b = Books(MONTH, date(2026, 10, 31), {}, receipts_from_photo=True)   # receipt not snapped: nothing held
        b.categories = dict(b.categories, **{"LONE STAR BURGER": {"treatment": "expense", "schedule_c_line": "24b Meals"}})
        self.assertEqual(b.schedule_c()["lines"]["24b Meals"], Decimal("7.16"))

    def test_scored_as_an_owner_question_not_a_false_alarm(self):
        s = close.score([{"date": "2026-10-11", "amount": "-14.32", "title": "receipt: burger"}],
                        MONTH / "expected_exceptions.json")
        self.assertEqual((s["asked"], s["false_alarms"]), (["Q1"], []))


class EveryTime(unittest.TestCase):
    def test_nothing_is_learned_without_every_time(self):
        self.assertEqual(books(**{MEAL: "personal", GROCERIES: "business"}).merchant_rules(), {})

    def test_every_time_sets_the_merchant(self):
        rules = books(**{MEAL: ("personal", True), GROCERIES: ("business", True)}).merchant_rules()
        self.assertEqual(rules["LONE STAR BURGER"]["says"], "personal")
        self.assertFalse(rules["LONE STAR BURGER"]["corrected_the_model"])     # the categorizer said personal too
        self.assertEqual(rules["BAYOU FRESH MARKET"]["name"], "Bayou Fresh Market")
        self.assertTrue(rules["BAYOU FRESH MARKET"]["corrected_the_model"])    # it said personal; you said business

    def test_a_set_merchant_beats_the_categorizer_for_every_charge_there(self):
        t = next(t for t in books().txns if "BAYOU FRESH" in t.description)
        self.assertEqual(books(**{GROCERIES: ("business", True)}).treatment(t), "expense")
        self.assertEqual(books(**{GROCERIES: "business"}).treatment(t), "personal")   # one charge only
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
        self.assertNotIn("LONE STAR BURGER", sent[0])           # set already: never sent to the model
        self.assertIn("BAYOU FRESH MARKET", sent[0])
        self.assertIn("the owner says: a business cost", sent[0])
        self.assertEqual((cats["LONE STAR BURGER"]["treatment"], cats["LONE STAR BURGER"]["rule"]), ("personal", True))
        self.assertEqual(cats["BAYOU FRESH MARKET"]["treatment"], "expense")   # the owner's answer wins
        self.assertEqual(cats["BAYOU FRESH MARKET"]["schedule_c_line"], "27a Other expenses")


if __name__ == "__main__":
    unittest.main()
