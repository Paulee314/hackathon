"""Receipts: the synthetic receipts tie to their card charges, and the matcher finds them.

Runs offline. tests/fixtures/receipts_extracted.json is a recorded Muse Spark reading of the
five receipt photos, so the checks below exercise real model output without calling the API.
"""
from __future__ import annotations

import json
import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "synth"))

from agents.receipts import check_arithmetic, find_charge  # noqa: E402
from ingest.load import load_folder  # noqa: E402
from receipts import RECEIPTS, total  # noqa: E402

MONTH = ROOT / "samples" / "october"
FIXTURE = ROOT / "tests" / "fixtures" / "receipts_extracted.json"


class Receipts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.txns, _, _ = load_folder(MONTH)
        cls.recorded = json.loads(FIXTURE.read_text())

    def test_every_receipt_has_a_card_charge(self):
        for r in RECEIPTS:
            m, d, y = r["date"].split("/")
            charges = [t for t in self.txns if t.kind == "card" and t.date == f"{y}-{m}-{d}" and t.amount == -total(r)]
            self.assertEqual(len(charges), 1, r["file"])

    def test_images_exist(self):
        for r in RECEIPTS:
            self.assertTrue((MONTH / "receipts" / r["file"]).exists(), r["file"])

    def test_recorded_reading_matches_what_was_printed(self):
        by_file = {Path(x["file"]).name: x["extracted"] for x in self.recorded}
        for r in RECEIPTS:
            got = by_file[r["file"]]
            self.assertEqual(Decimal(str(got["total"])), total(r), r["file"])
            self.assertEqual(len(got["items"]), len(r["items"]), r["file"])
            self.assertEqual(got["card_last4"], r["card"][-4:], r["file"])

    def test_recorded_reading_ties_and_matches(self):
        for x in self.recorded:
            e = x["extracted"]
            checks = check_arithmetic(e)
            self.assertTrue(checks["items_match_subtotal"], x["file"])
            self.assertTrue(checks["subtotal_plus_tax_matches_total"], x["file"])
            match = find_charge(e, self.txns)
            self.assertIsNotNone(match, x["file"])
            self.assertEqual(match["date"], e["date"], x["file"])

    def test_groceries_flagged_personal(self):
        grocery = next(x for x in self.recorded if "bayou-fresh" in x["file"])
        self.assertEqual(grocery["extracted"]["kind"], "personal")

    def test_arithmetic_catches_a_bad_receipt(self):
        bad = {"items": [{"description": "a", "amount": 10}], "subtotal": 12, "tax": 1, "total": 14}
        checks = check_arithmetic(bad)
        self.assertFalse(checks["items_match_subtotal"])
        self.assertFalse(checks["subtotal_plus_tax_matches_total"])

    def test_no_match_for_wrong_amount_or_far_date(self):
        self.assertIsNone(find_charge({"merchant": "BAYOU SHINE", "date": "2026-10-09", "total": 18.01}, self.txns))
        self.assertIsNone(find_charge({"merchant": "BAYOU SHINE", "date": "2026-10-20", "total": 18.00}, self.txns))


if __name__ == "__main__":
    unittest.main()
