"""The close agent, true profit per ride, and the categorizer's merchant grouping. Offline."""
from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import close  # noqa: E402
from agents.categorize import merchant_key, spending  # noqa: E402
from car.profit import link, ride_profits, summary  # noqa: E402
from car.telemetry import load_drives, load_offers, load_profile  # noqa: E402
from ingest.load import load_folder  # noqa: E402

MONTH = ROOT / "samples" / "october"


class CloseAgent(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.txns, _, _ = load_folder(MONTH)
        cls.out = close.run(cls.txns, MONTH)
        cls.checks = {c["id"]: c for c in cls.out["checks"]}

    def test_catches_every_planted_problem_from_the_ledger(self):
        found = [{"date": e["date"], "amount": e["amount"], "title": e["title"]} for e in self.out["exceptions"]]
        receipts = json.loads((ROOT / "tests" / "fixtures" / "receipts_extracted.json").read_text())
        grocery = next(r for r in receipts if "bayou-fresh" in r["file"])
        found.append({"date": grocery["extracted"]["date"], "amount": grocery["extracted"]["total"], "title": "groceries"})
        s = close.score(found, MONTH / "expected_exceptions.json")
        self.assertEqual(sorted(s["caught"]), ["E1", "E2", "E3", "E4", "E5", "E6"])
        self.assertEqual(s["false_alarms"], [])

    def test_exception_types(self):
        types = sorted(e["type"] for e in self.out["exceptions"])
        self.assertEqual(types, ["deposit_short", "deposit_without_payout", "payout_missing_deposit",
                                 "possible_duplicate", "unidentified_income"])

    def test_checking_ties_to_the_statement(self):
        c = self.checks["checking-ties"]
        self.assertTrue(c["ok"], c["detail"])
        manifest = json.loads((MONTH / "manifest.json").read_text())
        self.assertIn("$" + "{:,}".format(Decimal(manifest["checking_closing_balance"])), c["detail"])

    def test_checks_that_should_pass(self):
        for cid in ("uber-deposits", "turo-payouts", "card-payments", "checking-ties"):
            self.assertTrue(self.checks[cid]["ok"], cid)
        for cid in ("uber-payouts", "turo-deposits", "lyft-weeks", "duplicates", "deposits-explained"):
            self.assertFalse(self.checks[cid]["ok"], cid)

    def test_lyft_short_is_five_dollars(self):
        short = next(e for e in self.out["exceptions"] if e["type"] == "deposit_short")
        self.assertEqual(Decimal(short["at_stake"]), Decimal("5.00"))

    def test_duplicate_holds_only_the_copy(self):
        dup = next(e for e in self.out["exceptions"] if e["type"] == "possible_duplicate")
        self.assertEqual(len(dup["txn_ids"]), 1)


class TrueProfit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.offers, cls.drives = load_offers(MONTH), load_drives(MONTH)
        cls.truth = json.loads((MONTH / "car" / "car_truth.json").read_text())["rides"]

    def test_every_ride_is_tied_to_the_right_drives(self):
        links = link(self.offers, self.drives)
        accepted = [o for o in self.offers if o.accepted]
        self.assertEqual(len(links), len(accepted))
        for o in accepted:
            pickup, trip = links[o.id]
            t = self.truth[o.trip_id]
            self.assertEqual(pickup.id, str(t["pickup_drive"]))
            self.assertEqual(trip.id if trip else None, str(t["trip_drive"]) if t["trip_drive"] else None)

    def test_profit_adds_up(self):
        rides = ride_profits(MONTH)
        for r in rides:
            self.assertAlmostEqual(r["fare"] - r["energy"] - r["depreciation"] - r["tires"], r["true_profit"], delta=0.02)
        s = summary(rides, load_profile(MONTH))
        self.assertEqual(s["all"]["rides"], len(rides))
        self.assertLess(s["all"]["true_profit"], s["all"]["fare"])
        self.assertTrue(all(not r["cancelled"] for r in s["best"] + s["worst"]))


class Categorize(unittest.TestCase):
    def test_merchant_keys_group_the_same_merchant(self):
        txns, _, _ = load_folder(MONTH)
        groups = spending(txns)
        self.assertEqual(groups["TESLA SUPERCHARGER"]["count"], 12)
        self.assertIn("OPENAI", groups)
        self.assertIn("BAYOU FRESH MARKET", groups)

    def test_recorded_categories_cover_the_month(self):
        cats = json.loads((ROOT / "tests" / "fixtures" / "categories.json").read_text())
        txns, _, _ = load_folder(MONTH)
        for key in spending(txns):
            self.assertIn(key, cats, key)
        self.assertEqual(cats["BAYOU FRESH MARKET"]["treatment"], "personal")
        self.assertEqual(cats["TESLA FINANCE"]["treatment"], "loan_payment")
        self.assertEqual(cats["TESLA SUPERCHARGER"]["treatment"], "vehicle")


if __name__ == "__main__":
    unittest.main()
