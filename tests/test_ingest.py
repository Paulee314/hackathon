"""End-to-end checks: generate the synthetic month, load it, compare to the generator's own totals.

    python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from collections import defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "synth"))

from generate_october import generate  # noqa: E402
from ingest.load import load_folder  # noqa: E402
from ingest.schema import iso_date, money  # noqa: E402


class SyntheticMonth(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.manifest = generate(cls.tmp / "october")
        cls.txns, cls.report, cls.skipped = load_folder(cls.tmp / "october")

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_every_file_recognized(self):
        self.assertEqual(self.skipped, [])
        self.assertEqual(len(self.report), len(self.manifest["files"]))

    def test_account_totals_match_generator(self):
        net = defaultdict(Decimal)
        for t in self.txns:
            net[t.account] += t.amount
        expected = {k: Decimal(v) for k, v in self.manifest["account_net"].items()}
        self.assertEqual(dict(net), expected)

    def test_checking_ties_to_closing_balance(self):
        checking = sum(t.amount for t in self.txns if t.source == "chase_checking")
        opening = Decimal(self.manifest["checking_opening_balance"])
        self.assertEqual(opening + checking, Decimal(self.manifest["checking_closing_balance"]))

    def test_all_october(self):
        self.assertTrue(all("2026-10-01" <= t.date <= "2026-10-31" for t in self.txns))

    def test_ids_unique(self):
        self.assertEqual(len({t.txn_id for t in self.txns}), len(self.txns))

    def test_reloading_overlapping_exports_adds_nothing(self):
        twice = self.tmp / "twice"
        shutil.copytree(self.tmp / "october", twice / "a")
        shutil.copytree(self.tmp / "october", twice / "b")
        txns, _, _ = load_folder(twice)
        self.assertEqual(len(txns), len(self.txns))

    def test_identical_rows_in_one_file_both_kept(self):
        dupes = [t for t in self.txns if t.description == "TESLA SUPERCHARGER US" and t.amount == Decimal("-17.36")]
        self.assertEqual(len(dupes), 2)

    def test_knowledge_work_is_not_rideshare(self):
        for t in self.txns:
            if "KNOWLEDGE_WORK" in t.description:
                self.assertEqual(t.business_line, "contract_work")

    def test_answer_key_written(self):
        key = json.loads((self.tmp / "october" / "expected_exceptions.json").read_text())
        ids = {e["id"] for e in key["exceptions"]}
        self.assertTrue({"E1", "E2", "E3", "E4", "E5", "E6"} <= ids)


class Helpers(unittest.TestCase):
    def test_money(self):
        self.assertEqual(money("$1,234.56"), Decimal("1234.56"))
        self.assertEqual(money("-$2.41"), Decimal("-2.41"))
        self.assertEqual(money("(5.00)"), Decimal("-5.00"))
        self.assertEqual(money(""), Decimal("0"))

    def test_dates(self):
        self.assertEqual(iso_date("08/20/2026"), "2026-08-20")
        self.assertEqual(iso_date("2026-10-14 18:47:31"), "2026-10-14")
        self.assertEqual(iso_date("2026-10-14 18:03"), "2026-10-14")


if __name__ == "__main__":
    unittest.main()
