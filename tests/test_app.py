"""The phone app's numbers: totals, the review queue and what each decision does, the tire story.

Offline and standard library only (the web server itself needs FastAPI; these test what it serves).
"""
from __future__ import annotations

import json
import sys
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.books import Books, classify  # noqa: E402
from ingest.schema import Transaction  # noqa: E402

MONTH = ROOT / "samples" / "october"
TODAY = date(2026, 10, 31)


def books(state=None) -> Books:
    base = {"decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}}
    base.update(state or {})
    return Books(MONTH, TODAY, base)


def txn(desc, amount, kind="bank", source="chase_checking"):
    return Transaction(date="2026-10-10", amount=Decimal(amount), source=source, account="chase_1234", kind=kind, description=desc)


class Totals(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.b = books()
        cls.t = cls.b.totals()

    def test_earned_is_every_platform_earning(self):
        expected = sum((t.amount for t in self.b.txns if t.kind == "earning"), Decimal("0"))
        self.assertEqual(self.t["earned"], expected)
        self.assertEqual(self.t["profit"], self.t["earned"] - self.t["spent"])

    def test_items_waiting_on_review_are_not_counted_yet(self):
        held = {h for i in self.b.pending() for h in i["holds"]}
        self.assertEqual(len(held), 2)  # the burger and the groceries
        self.assertEqual(self.t["in_review"], Decimal("140.75"))

    def test_loans_transfers_and_unexplained_money_stay_out_of_profit(self):
        self.assertEqual(classify(txn("TESLA FINANCE LLC · AUTO PMT", "-612.40"))[0], "loan")
        self.assertEqual(classify(txn("Online Transfer to CHK ...5678", "-1500"))[0], "owner")
        self.assertEqual(classify(txn("Zelle payment from TAYLOR MORGAN", "150"))[0], "unexplained")
        self.assertEqual(classify(txn("UBER USA 6787 · EDI PAYMNT", "490.91"))[0], "transfer")
        self.assertEqual(classify(txn("AUTOMATIC PAYMENT - THANK", "412.66", kind="card", source="chase_card"))[0], "transfer")
        self.assertEqual(classify(txn("TESLA SUPERCHARGER US", "-17.36", kind="card", source="chase_card")), ("expense", "Charging"))


class Review(unittest.TestCase):
    def test_queue(self):
        ids = [i["id"] for i in books().review_items()]
        self.assertEqual(ids, ["receipt:2026-10-11_lone-star-burger", "receipt:2026-10-18_bayou-fresh", "voice:2026-10-21_water"])

    def test_personal_keeps_the_charge_out_and_business_counts_it(self):
        key = "receipt:2026-10-18_bayou-fresh"
        base = books().totals()
        personal = books({"decisions": {key: {"choice": "personal"}}}).totals()
        business = books({"decisions": {key: {"choice": "business"}}}).totals()
        self.assertEqual(personal["spent"], base["spent"])
        self.assertEqual(personal["other"]["Personal, kept out"], Decimal("126.43"))
        self.assertEqual(business["spent"], base["spent"] + Decimal("126.43"))

    def test_changing_a_spoken_amount(self):
        key = "voice:2026-10-21_water"
        base = books().totals()["spent"]
        changed = books({"decisions": {key: {"choice": "amount"}}, "amount_overrides": {key: 10.49}}).totals()["spent"]
        self.assertEqual(changed - base, Decimal("0.50"))

    def test_closing_the_month(self):
        decisions = {i["id"]: {"choice": i["suggestion"]["choice"]} for i in books().review_items()}
        home = books({"decisions": decisions}).home()
        self.assertEqual(home["review"]["count"], 0)
        self.assertEqual(home["headline"], "October is closed.")


class Screens(unittest.TestCase):
    def test_home_bubbles(self):
        home = books().home()
        self.assertEqual([b["id"] for b in home["bubbles"]], ["profit", "review", "turo", "rides", "tire"])
        self.assertEqual(home["review"]["count"], 3)

    def test_cash_logged_in_the_app_counts(self):
        entry = {"id": "e1", "parsed": {"date": "2026-10-31", "amount": 22.0, "payee": "Car wash", "description": "Car wash",
                                        "business_line": "turo", "schedule_c_line": "9 Car and truck expenses",
                                        "assumption": None, "question": None}}
        base = books().totals()["spent"]
        self.assertEqual(books({"entries": [entry]}).totals()["spent"] - base, Decimal("22.00"))
        undone = dict(entry, undone=True)
        self.assertEqual(books({"entries": [undone]}).totals()["spent"], base)

    def test_tire_story(self):
        t = books().tire()
        self.assertEqual((t["tire"], t["until"], t["refilled"]), ("rear-left", "2026-10-25", "2026-10-26"))
        self.assertFalse(t["ongoing"])
        self.assertLess(t["extra_cost"], 1.0)

    def test_details_and_context(self):
        b = books()
        for which in ("profit", "turo", "rides", "tire"):
            self.assertIsNotNone(b.detail(which), which)
        self.assertIsNone(b.detail("nope"))
        ctx = json.dumps(b.context(), default=str)
        self.assertLess(len(ctx), 12000)


if __name__ == "__main__":
    unittest.main()
