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


def photo_books(pile_state=None) -> Books:
    state = {"decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}, "pile": pile_state}
    return Books(MONTH, TODAY, state, receipts_from_photo=True)


def pile() -> dict:
    """A receipt photo as the app stores it once SAM 3.1 found the slips and Muse Spark read them
    (the recorded readings of the five receipts, in the photo's reading order)."""
    corners = [(420, 260, 860, 980), (1290, 300, 1710, 880), (2140, 270, 2580, 990),
               (840, 1240, 1280, 2040), (1730, 1300, 2170, 2000)]
    boxes = [{"x1": a, "y1": b, "x2": c, "y2": d, "w": 3000, "h": 2250, "confidence": 0.9} for a, b, c, d in corners]
    recorded = json.loads((ROOT / "tests" / "fixtures" / "receipts_extracted.json").read_text())
    names = ["2026-10-09_bayou-shine", "2026-10-11_lone-star", "2026-10-12_detailpro-supply",
             "2026-10-18_bayou-fresh", "2026-10-24_bayou-shine"]
    return {"id": "t", "photo": "pile/t/photo.jpg", "width": 3000, "height": 2250, "boxes": boxes,
            "receipts": [{"file": f"pile/t/{n}.jpg", "box": b, "extracted": r["extracted"]}
                         for n, b, r in zip(names, boxes, recorded)],
            "seconds": {"find": 3.4, "read": 7.0}}


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
        # the burger, the groceries, the duplicate charge's copy, the Turo deposit and the Zelle
        self.assertEqual(len(held), 5)
        self.assertEqual(self.t["in_review"], Decimal("14.32") + Decimal("126.43") + Decimal("17.36") + Decimal("64.20") + Decimal("150.00"))

    def test_schedule_c_lines_add_up_to_what_was_spent(self):
        sc = self.b.schedule_c()
        self.assertEqual(sc["total_expenses"], self.t["spent"])
        self.assertEqual(sc["gross_receipts"], self.t["earned"])

    def test_loans_transfers_and_unexplained_money_stay_out_of_profit(self):
        self.assertEqual(classify(txn("TESLA FINANCE LLC · AUTO PMT", "-612.40"))[0], "loan")
        self.assertEqual(classify(txn("Online Transfer to CHK ...5678", "-1500"))[0], "owner")
        self.assertEqual(classify(txn("Zelle payment from TAYLOR MORGAN", "150"))[0], "unexplained")
        self.assertEqual(classify(txn("UBER USA 6787 · EDI PAYMNT", "490.91"))[0], "transfer")
        self.assertEqual(classify(txn("AUTOMATIC PAYMENT - THANK", "412.66", kind="card", source="chase_card"))[0], "transfer")
        self.assertEqual(classify(txn("TESLA SUPERCHARGER US", "-17.36", kind="card", source="chase_card")), ("expense", "Charging"))


class Review(unittest.TestCase):
    def test_queue_puts_the_most_money_first(self):
        items = books().review_items()
        self.assertEqual(len(items), 8)
        self.assertEqual(items[0]["type"], "payout_missing_deposit")
        amounts = [Decimal(i["amount"]) for i in items]
        self.assertEqual(amounts, sorted(amounts, reverse=True))
        self.assertEqual(sum(amounts), Decimal("714.89"))

    def test_agent_answers_change_the_books(self):
        items = {i.get("type") or i["id"]: i for i in books().review_items()}
        base = books().totals()
        def with_choice(kind, choice):
            return books({"decisions": {items[kind]["id"]: {"choice": choice}}}).totals()
        self.assertEqual(with_choice("deposit_short", "fee")["spent"] - base["spent"], Decimal("5.00"))
        self.assertEqual(with_choice("possible_duplicate", "both")["spent"] - base["spent"], Decimal("17.36"))
        self.assertEqual(with_choice("possible_duplicate", "duplicate")["spent"], base["spent"])
        self.assertEqual(with_choice("deposit_without_payout", "income")["earned"] - base["earned"], Decimal("64.20"))
        self.assertEqual(with_choice("unidentified_income", "business")["earned"] - base["earned"], Decimal("150.00"))
        self.assertEqual(with_choice("unidentified_income", "personal")["earned"], base["earned"])

    def test_checks_turn_answered(self):
        b = books()
        needs = [c for c in b.checks() if c["status"] == "needs you"]
        self.assertEqual(len(needs), 5)
        decisions = {i["id"]: {"choice": (i["suggestion"] or i["choices"][0])["choice"]} for i in b.review_items()}
        statuses = {c["status"] for c in books({"decisions": decisions}).checks()}
        self.assertEqual(statuses, {"passes", "answered"})

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
        decisions = {i["id"]: {"choice": (i["suggestion"] or i["choices"][0])["choice"]} for i in books().review_items()}
        home = books({"decisions": decisions}).home()
        self.assertEqual(home["review"]["count"], 0)
        self.assertEqual(home["headline"], "October is closed.")


class Screens(unittest.TestCase):
    def test_home_bubbles(self):
        home = books().home()
        self.assertEqual([b["id"] for b in home["bubbles"]], ["profit", "review", "turo", "rides", "tire"])
        self.assertEqual(home["review"]["count"], 8)
        self.assertTrue(home["bubbles"][3]["value"].endswith("/hr"))

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

    def test_photo_mode_starts_without_receipts(self):
        b = photo_books()
        home = b.home()
        self.assertEqual([x["id"] for x in home["bubbles"]], ["profit", "review", "turo", "rides", "receipts", "tire"])
        bubble = home["bubbles"][4]
        self.assertEqual((bubble["value"], bubble["label"], bubble["icon"]), ("", "Snap receipts", "camera"))
        self.assertEqual(home["review"]["count"], 6)
        self.assertFalse(any(i["id"].startswith("receipt:") for i in b.review_items()))
        self.assertIsNone(b.pile_view())

    def test_photo_mode_after_the_pile_is_read(self):
        b = photo_books(pile())
        home = b.home()
        bubble = next(x for x in home["bubbles"] if x["id"] == "receipts")
        self.assertEqual((bubble["value"], bubble["label"]), ("5", "receipts read"))
        self.assertEqual(home["review"]["count"], 8)
        receipts = sorted(i["id"] for i in b.review_items() if i["id"].startswith("receipt:"))
        self.assertEqual(receipts, ["receipt:2026-10-11_lone-star", "receipt:2026-10-18_bayou-fresh"])
        self.assertEqual(b.totals()["in_review"] - photo_books().totals()["in_review"], Decimal("14.32") + Decimal("126.43"))
        view = b.pile_view()
        self.assertEqual((view["found"], view["matched"], view["flagged"], view["read"]), (5, 5, 2, True))
        self.assertEqual(view["items"][0]["box"]["left"], round(100.0 * 420 / 3000, 2))
        self.assertEqual(view["items"][3]["flag"], "Looks personal")

    def test_photo_mode_while_sam_has_found_but_spark_has_not_read(self):
        found = pile()
        found["receipts"] = None
        view = photo_books(found).pile_view()
        self.assertEqual((view["found"], view["read"], view["flagged"]), (5, False, 0))
        self.assertEqual(photo_books(found).home()["review"]["count"], 6)

    def test_details_and_context(self):
        b = books()
        for which in ("profit", "turo", "rides", "tire"):
            self.assertIsNotNone(b.detail(which), which)
        self.assertIsNone(b.detail("nope"))
        ctx = json.dumps(b.context(), default=str)
        self.assertLess(len(ctx), 12000)


if __name__ == "__main__":
    unittest.main()
