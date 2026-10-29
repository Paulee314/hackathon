"""The Plaid Sandbox bank feed: what the test bank is loaded with, the pull itself (against a stand-in
for Plaid), and a recorded pull from a live Sandbox run turned into the books' rows.
Offline and standard library only."""
from __future__ import annotations

import io
import json
import os
import re
import sys
import tempfile
import unittest
import urllib.error
from datetime import date
from decimal import Decimal
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents import close  # noqa: E402
from app.books import Books  # noqa: E402
from ingest import bank_feed  # noqa: E402
from ingest.load import load_folder  # noqa: E402

MONTH = ROOT / "samples" / "october"
TODAY = date(2026, 10, 31)
FEED = json.loads((ROOT / "tests" / "fixtures" / "plaid_feed.json").read_text())   # a live Sandbox pull, Oct 29


def state(bank=None):
    return {"decisions": {}, "entries": [], "amount_overrides": {}, "explanations": {}, "bank": bank}


class WhatTheTestBankHolds(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.accounts = {a["books_account"]: a for a in bank_feed.statements(MONTH)}

    def test_three_accounts_with_every_row(self):
        self.assertEqual(sorted(self.accounts), ["amex_platinum", "chase_1234", "chase_ink_9001"])
        self.assertEqual([len(self.accounts[k]["transactions"]) for k in sorted(self.accounts)], [6, 33, 19])

    def test_checking_balances_are_the_statement_s(self):
        manifest = json.loads((MONTH / "manifest.json").read_text())
        chk = self.accounts["chase_1234"]
        self.assertEqual(str(chk["opening"]), manifest["checking_opening_balance"])
        self.assertEqual(str(chk["closing"]), manifest["checking_closing_balance"])

    def test_plaid_counts_money_leaving_as_positive(self):
        def amount(account, desc, day):
            return next(t["amount"] for t in self.accounts[account]["transactions"]
                        if desc in t["description"] and t["date_posted"] == day)
        self.assertEqual(amount("chase_1234", "MONTHLY SERVICE FEE", "2026-10-31"), Decimal("15.00"))
        self.assertEqual(amount("chase_1234", "TESLA FINANCE", "2026-10-03"), Decimal("612.40"))
        self.assertEqual(amount("chase_1234", "Zelle payment", "2026-10-17"), Decimal("-150.00"))
        self.assertEqual(amount("amex_platinum", "LONE STAR BURGER", "2026-10-11"), Decimal("14.32"))
        self.assertEqual(amount("amex_platinum", "AUTOPAY", "2026-10-22"), Decimal("-318.90"))
        self.assertEqual(amount("chase_ink_9001", "AUTOMATIC PAYMENT", "2026-10-21"), Decimal("-412.66"))

    def test_card_rows_keep_the_day_of_the_purchase(self):
        row = next(t for t in self.accounts["chase_ink_9001"]["transactions"] if t["amount"] == Decimal("6.46"))
        self.assertEqual((row["date_transacted"], row["date_posted"]), ("2026-10-04", "2026-10-05"))

    def test_fits_one_sandbox_custom_user(self):
        config = bank_feed.custom_user(list(self.accounts.values()))
        self.assertLess(len(json.dumps(config)), bank_feed.MAX_CONFIG)
        self.assertLessEqual(sum(len(a["transactions"]) for a in config["override_accounts"]), bank_feed.MAX_ROWS)
        for a in config["override_accounts"]:
            self.assertRegex(a["meta"]["mask"], r"^[A-Za-z0-9]{2,4}$")
        balances = {a["meta"]["mask"]: a.get("starting_balance") for a in config["override_accounts"]}
        self.assertEqual(balances, {"1234": 5896.74, "9001": None, "1005": None})


class FakePlaid:
    """Stands in for Plaid: answers the five calls a pull makes, from the recorded pull."""

    def __init__(self, loading_rounds=1, fail_on=None):
        self.calls, self.loading, self.fail_on, self.syncs = [], loading_rounds, fail_on, 0

    def __call__(self, path, payload):
        self.calls.append((path, payload))
        if path == self.fail_on:
            raise bank_feed.BankFeedError(f"Plaid {path} said INTERNAL_SERVER_ERROR: try again")
        if path == "/sandbox/public_token/create":
            return {"public_token": "public-sandbox-test"}
        if path == "/item/public_token/exchange":
            return {"access_token": "access-sandbox-test"}
        if path == "/transactions/sync":
            self.syncs += 1
            if self.syncs <= self.loading:
                return {"added": [], "modified": [], "removed": [], "has_more": False, "next_cursor": "",
                        "transactions_update_status": "NOT_READY"}
            rows = [{**t, "personal_finance_category": {"detailed": t["category"]}} for t in FEED["transactions"]]
            half = len(rows) // 2
            first = self.syncs == self.loading + 1
            return {"added": rows[:half] if first else rows[half:], "modified": [], "removed": [],
                    "has_more": first, "next_cursor": f"c{self.syncs}",
                    "transactions_update_status": "HISTORICAL_UPDATE_COMPLETE"}
        if path == "/accounts/get":
            return {"accounts": [{"account_id": a["account_id"], "name": a["name"], "mask": a["mask"], "type": a["type"],
                                  "subtype": a["subtype"], "balances": {"current": a["balance"]}} for a in FEED["accounts"]]}
        if path == "/item/remove":
            return {"removed": True}
        raise AssertionError(path)


class ThePull(unittest.TestCase):
    def test_waits_for_the_bank_reads_every_page_then_signs_out(self):
        fake = FakePlaid(loading_rounds=2)
        feed = bank_feed.pull(MONTH, post=fake, sleep=lambda s: None)
        paths = [p for p, _ in fake.calls]
        self.assertEqual(paths, ["/sandbox/public_token/create", "/item/public_token/exchange"] + ["/transactions/sync"] * 4
                         + ["/accounts/get", "/item/remove"])
        self.assertEqual(len(feed["transactions"]), len(FEED["transactions"]))
        self.assertEqual(fake.calls[5][1]["cursor"], "c3")   # the next page starts where the last one ended
        self.assertEqual(bank_feed.compare(MONTH, feed)["only_in_feed"], [])

    def test_signs_in_as_the_custom_user_at_a_sandbox_bank(self):
        fake = FakePlaid()
        bank_feed.pull(MONTH, post=fake, sleep=lambda s: None)
        create = fake.calls[0][1]
        self.assertEqual(create["institution_id"], "ins_109508")
        self.assertEqual(create["options"]["override_username"], "user_custom")
        config = json.loads(create["options"]["override_password"])
        self.assertEqual(len(config["override_accounts"]), 3)

    def test_keeps_no_token(self):
        feed = bank_feed.pull(MONTH, post=FakePlaid(), sleep=lambda s: None)
        text = json.dumps(feed)
        self.assertNotIn("access-sandbox-test", text)
        self.assertNotIn("public-sandbox-test", text)

    def test_signs_out_even_when_a_read_fails(self):
        fake = FakePlaid(fail_on="/transactions/sync")
        with self.assertRaises(bank_feed.BankFeedError):
            bank_feed.pull(MONTH, post=fake, sleep=lambda s: None)
        self.assertEqual(fake.calls[-1][0], "/item/remove")

    def test_gives_up_when_the_bank_never_finishes_loading(self):
        fake = FakePlaid(loading_rounds=10 ** 6)
        with self.assertRaises(bank_feed.BankFeedError):
            bank_feed.pull(MONTH, wait=0, post=fake, sleep=lambda s: None)
        self.assertEqual(fake.calls[-1][0], "/item/remove")


class Requests(unittest.TestCase):
    def test_sandbox_only(self):
        self.assertEqual(bank_feed.SANDBOX, "https://sandbox.plaid.com")

    def test_an_error_names_plaid_s_reason_and_never_the_keys(self):
        body = json.dumps({"error_code": "INVALID_API_KEYS", "error_message": "invalid client_id or secret provided"})
        seen = {}

        def refuse(req, timeout):
            seen["url"], seen["body"] = req.full_url, req.data
            raise urllib.error.HTTPError(req.full_url, 400, "Bad Request", {}, io.BytesIO(body.encode()))
        keys = {"PLAID_CLIENT_ID": "test-client-id-123", "PLAID_SECRET": "test-secret-456"}
        with mock.patch.dict(os.environ, keys), mock.patch.object(bank_feed.urllib.request, "urlopen", refuse):
            with self.assertRaises(bank_feed.BankFeedError) as e:
                bank_feed._post("/accounts/get", {"access_token": "x"})
        self.assertTrue(seen["url"].startswith("https://sandbox.plaid.com/"))
        self.assertIn("INVALID_API_KEYS", str(e.exception))
        self.assertNotIn("test-secret-456", str(e.exception))
        self.assertNotIn("test-client-id-123", str(e.exception))

    def test_no_keys_no_request(self):
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(bank_feed, "ROOT", Path(tmp)), \
                mock.patch.dict(os.environ, {"PLAID_CLIENT_ID": "", "PLAID_SECRET": ""}), \
                mock.patch.object(bank_feed.urllib.request, "urlopen", side_effect=AssertionError("no request")):
            self.assertFalse(bank_feed.configured())
            with self.assertRaises(bank_feed.BankFeedError):
                bank_feed._post("/accounts/get", {})


class TheRecordedPull(unittest.TestCase):
    """What a live Sandbox pull gave back, turned into the books' rows."""

    @classmethod
    def setUpClass(cls):
        cls.compared = bank_feed.compare(MONTH, FEED)
        cls.csv, _, _ = load_folder(MONTH)
        cls.fed, _, _ = load_folder(MONTH, feed=bank_feed.to_transactions(FEED))

    def test_every_row_is_the_csv_row(self):
        c = self.compared
        self.assertEqual(c["only_in_csv"], [])
        self.assertEqual(c["only_in_feed"], [])
        self.assertEqual(c["feed"] + len(c["not_posted_yet"]), c["csv"])
        self.assertTrue(all(r[1] > FEED["pulled_at"][:10] for r in c["not_posted_yet"]))

    def test_checking_balance_ties_to_the_statement(self):
        self.assertEqual([(b["account"], b["ok"]) for b in self.compared["balances"]], [("chase_1234", True)])

    def test_the_feed_stands_in_for_the_bank_and_card_exports(self):
        banks = {"chase_1234", "chase_ink_9001", "amex_platinum"}
        fed_bank = [t for t in self.fed if t.account in banks]
        self.assertTrue(fed_bank)
        self.assertTrue(all(t.evidence.startswith("Plaid Sandbox") for t in fed_bank))
        others = lambda txns: sorted(t.txn_id for t in txns if t.account not in banks)   # noqa: E731
        self.assertEqual(others(self.fed), others(self.csv))   # rides, payouts and Turo untouched

    def test_checking_and_ink_rows_keep_their_ids(self):
        # trace numbers ride along in the checking description; the Ink card has none. Plaid doesn't carry
        # Amex's own reference number, so Amex rows get an id from date, amount and description instead.
        for account in ("chase_1234", "chase_ink_9001"):
            ids = {t.txn_id for t in self.csv if t.account == account}
            self.assertTrue({t.txn_id for t in self.fed if t.account == account} <= ids, account)

    def test_plaid_s_categories_ride_along_as_hints(self):
        burger = next(t for t in self.fed if "LONE STAR BURGER" in t.description)
        self.assertEqual(burger.source_category, "FOOD_AND_DRINK_FAST_FOOD")

    def test_the_close_agent_scores_the_same(self):
        out = close.run(self.fed, MONTH)
        found = [{"date": e["date"], "amount": e["amount"], "title": e["title"]} for e in out["exceptions"]]
        receipts = json.loads((ROOT / "tests" / "fixtures" / "receipts_extracted.json").read_text())
        grocery = next(r for r in receipts if "bayou-fresh" in r["file"])
        found.append({"date": grocery["extracted"]["date"], "amount": grocery["extracted"]["total"], "title": "groceries"})
        s = close.score(found, MONTH / "expected_exceptions.json")
        self.assertEqual(sorted(s["caught"]), ["E1", "E2", "E3", "E4", "E5", "E6"])
        self.assertEqual(s["false_alarms"], [])


class TheApp(unittest.TestCase):
    def test_settings_says_where_the_rows_come_from(self):
        self.assertEqual(Books(MONTH, TODAY, state()).bank_view(), {"connected": False})
        v = Books(MONTH, TODAY, state(FEED)).bank_view()
        self.assertTrue(v["connected"])
        self.assertEqual(sorted((a["label"], a["mask"]) for a in v["accounts"]),
                         [("Amex card", "1005"), ("Chase Ink card", "9001"), ("Chase checking", "1234")])
        self.assertEqual(v["balance"], {"plaid": "$5,896.74", "statement": "$5,896.74", "ok": True})
        self.assertEqual(v["rows"] + v["later"], 58)

    def test_the_review_queue_is_the_same(self):
        ids = lambda b: sorted(i["id"] for i in b.review_items())   # noqa: E731
        self.assertEqual(ids(Books(MONTH, TODAY, state(FEED))), ids(Books(MONTH, TODAY, state())))


if __name__ == "__main__":
    unittest.main()
