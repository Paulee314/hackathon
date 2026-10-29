"""A bank feed through Plaid, in Sandbox only, in place of the bank and card CSV exports.

    .venv/bin/python -m ingest.bank_feed samples/october        # pull through Plaid, then compare with the CSVs
    .venv/bin/python -m ingest.bank_feed samples/october --record tests/fixtures/plaid_feed.json
    .venv/bin/python -m agents.close samples/october --feed tests/fixtures/plaid_feed.json

A real driver would sign in to their bank through Plaid Link, and the books would read their
accounts from it. This demo uses Plaid's Sandbox, which has no real banks and no real money. Its
custom test user holds whatever accounts and transactions it is given, so:

  statements()       reads the month's synthetic bank and card exports (Chase checking, the Chase Ink
                     card, the Amex card): what the test bank will hold
  pull()             loads the test bank with them, signs in through the Sandbox API, reads every
                     transaction back with /transactions/sync (the call a live connection uses), reads
                     the balances, then removes the test connection
  to_transactions()  turns Plaid's rows into the books' rows with the same cleanup the CSV parsers do
  compare()          checks the two routes give the same month, row for row

Safety: the address is fixed to sandbox.plaid.com, so production keys and real accounts can't be
used from here (Plaid rejects a production secret there). Keys come from PLAID_CLIENT_ID and
PLAID_SECRET (.env locally, a Modal Secret when hosted) and are never printed, logged or stored.
The access token lives in memory for one pull, and the test connection is removed when it's done.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Callable, Dict, List, Optional

from .parsers import detect, read_csv
from .parsers.chase_checking import _FILE_DIGITS, _TRACE, _clean
from .schema import Transaction, iso_date, money

ROOT = Path(__file__).resolve().parents[1]
SANDBOX = "https://sandbox.plaid.com"                 # Sandbox only: never production
INSTITUTION = ("ins_109508", "First Platypus Bank")   # a Plaid Sandbox test bank that takes custom users
PLAID_VERSION = "2020-09-14"
FORMATS = ("chase_checking", "chase_card", "amex")    # the exports the feed stands in for
CENT = Decimal("0.01")
SYNC_WAIT = 60       # seconds to wait for Plaid to finish loading the test bank's history
MAX_ROWS = 250       # Plaid's limit for one custom user
MAX_CONFIG = 55_000  # bytes, ditto
KEEP = ("transaction_id", "account_id", "amount", "date", "authorized_date", "name", "original_description",
        "merchant_name", "pending")


class BankFeedError(RuntimeError):
    pass


# ---- keys and requests -----------------------------------------------------------------------
def _env(name: str) -> str:
    value = os.environ.get(name, "").strip()
    env = ROOT / ".env"
    if not value and env.exists():
        for line in env.read_text().splitlines():
            key, sep, rest = line.partition("=")
            if sep and key.strip() == name:
                value = rest.strip().strip('"').strip("'")
    return value


def configured() -> bool:
    """True when Plaid Sandbox keys are set (the values are never shown)."""
    return bool(_env("PLAID_CLIENT_ID") and _env("PLAID_SECRET"))


def _post(path: str, payload: Dict, timeout: float = 30) -> Dict:
    keys = {"client_id": _env("PLAID_CLIENT_ID"), "secret": _env("PLAID_SECRET")}
    if not all(keys.values()):
        raise BankFeedError("Plaid Sandbox keys aren't set (PLAID_CLIENT_ID and PLAID_SECRET).")
    body = json.dumps({**keys, **payload}).encode("utf-8")
    req = urllib.request.Request(SANDBOX + path, data=body, method="POST",
                                 headers={"Content-Type": "application/json", "Plaid-Version": PLAID_VERSION,
                                          "User-Agent": "ryde-off/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())
            why = f"{err.get('error_code')}: {err.get('error_message')}"
        except ValueError:
            why = f"HTTP {e.code}"
        raise BankFeedError(f"Plaid {path} said {why}") from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise BankFeedError(f"Couldn't reach Plaid's Sandbox: {getattr(e, 'reason', e)}") from None


# ---- what the test bank holds -----------------------------------------------------------------
def statements(month_dir) -> List[Dict]:
    """The month's bank and card exports, one account each, with every row in Plaid's terms
    (Plaid's amount is positive for money leaving the account)."""
    out = []
    for path in sorted(Path(month_dir).rglob("*.csv")):
        header, numbered = read_csv(path)
        parser = detect(header)
        if parser is None or parser.name not in FORMATS:
            continue
        rows = [r for _, r in numbered if money(r.get("Amount")) != 0]
        if not rows:
            continue
        if parser.name == "chase_checking":
            m = _FILE_DIGITS.search(path.name)
            mask = m.group(1) if m else "0000"
            # Chase lists newest first: the top row's balance is where the month ended
            closing = money(rows[0]["Balance"])
            opening = closing - sum((money(r["Amount"]) for r in rows), Decimal("0"))
            txns = [{"date_transacted": iso_date(r["Posting Date"]), "date_posted": iso_date(r["Posting Date"]),
                     "amount": -money(r["Amount"]), "description": r["Description"]} for r in rows]
            out.append({"format": parser.name, "books_account": f"chase_{mask}", "type": "depository",
                        "subtype": "checking", "name": "Business checking", "mask": mask,
                        "opening": opening, "closing": closing, "transactions": txns})
        elif parser.name == "chase_card":
            card = rows[0].get("Card", "").strip() or "0000"
            txns = [{"date_transacted": iso_date(r["Transaction Date"]),
                     "date_posted": iso_date(r.get("Post Date") or r["Transaction Date"]),
                     "amount": -money(r["Amount"]), "description": r["Description"]} for r in rows]
            out.append({"format": parser.name, "books_account": f"chase_ink_{card}", "type": "credit",
                        "subtype": "credit card", "name": "Ink Business card", "mask": card[-4:],
                        "transactions": txns})
        else:
            digits = "".join(ch for ch in rows[0].get("Account #", "") if ch.isdigit())
            txns = [{"date_transacted": iso_date(r["Date"]), "date_posted": iso_date(r["Date"]),
                     "amount": money(r["Amount"]), "description": r["Description"]} for r in rows]
            out.append({"format": parser.name, "books_account": "amex_platinum", "type": "credit",
                        "subtype": "credit card", "name": "Platinum card", "mask": digits[-4:] or "0000",
                        "transactions": txns})
    return out


def custom_user(accounts: List[Dict]) -> Dict:
    """Plaid's custom Sandbox user holding these accounts and rows."""
    config = {"version": 2, "override_accounts": [{
        "type": a["type"], "subtype": a["subtype"], "currency": "USD",
        # Plaid reports this as the account's current balance (it doesn't replay the rows onto it), so the
        # checking account gets the balance the statement ended on
        **({"starting_balance": float(a["closing"])} if "closing" in a else {}),
        "meta": {"name": a["name"], "mask": a["mask"]},
        "transactions": [{**t, "amount": float(t["amount"]), "currency": "USD"} for t in a["transactions"]],
    } for a in accounts]}
    rows = sum(len(a["transactions"]) for a in accounts)
    size = len(json.dumps(config))
    if rows > MAX_ROWS or size > MAX_CONFIG or len(accounts) > 10:
        raise BankFeedError(f"Too much for one Sandbox test user ({rows} rows, {size:,} bytes, {len(accounts)} accounts).")
    return config


# ---- the pull --------------------------------------------------------------------------------
def pull(month_dir, wait: float = SYNC_WAIT, post: Callable[[str, Dict], Dict] = None,
         sleep: Callable[[float], None] = time.sleep) -> Dict:
    """Load the test bank with the month's statements, sign in, read every row and balance back, sign out."""
    post = post or _post
    accounts = statements(month_dir)
    if not accounts:
        raise BankFeedError("This month has no bank or card exports to load into the test bank.")
    config = custom_user(accounts)
    started = time.time()
    public = post("/sandbox/public_token/create", {
        "institution_id": INSTITUTION[0], "initial_products": ["transactions"],
        "options": {"override_username": "user_custom", "override_password": json.dumps(config)}})["public_token"]
    access = post("/item/public_token/exchange", {"public_token": public})["access_token"]
    try:
        seen: Dict[str, Dict] = {}
        cursor, deadline, status = "", time.time() + wait, None
        while True:
            page = post("/transactions/sync", {"access_token": access, "cursor": cursor, "count": 500,
                                               "options": {"include_original_description": True}})
            for t in page.get("added", []) + page.get("modified", []):
                seen[t["transaction_id"]] = t
            for t in page.get("removed", []):
                seen.pop(t.get("transaction_id"), None)
            cursor = page.get("next_cursor", cursor)
            status = page.get("transactions_update_status")
            if page.get("has_more"):
                continue
            if status == "HISTORICAL_UPDATE_COMPLETE" or (status is None and seen):
                break
            if time.time() > deadline:
                raise BankFeedError(f"Plaid's test bank was still loading after {wait:.0f} seconds ({status}). Try again.")
            sleep(1.5)
        balances = post("/accounts/get", {"access_token": access})["accounts"]
    finally:
        try:
            post("/item/remove", {"access_token": access})
        except BankFeedError:
            pass   # a Sandbox test connection; nothing real is left behind
    by_mask = {(a["type"], a["mask"]): a for a in accounts}
    out_accounts = []
    for b in balances:
        mine = by_mask.get((b.get("type"), b.get("mask")))
        row = {"account_id": b["account_id"], "name": b.get("name"), "mask": b.get("mask"), "type": b.get("type"),
               "subtype": b.get("subtype"), "balance": (b.get("balances") or {}).get("current")}
        if mine:
            row.update({"format": mine["format"], "books_account": mine["books_account"]})
            if "opening" in mine:
                row["statement"] = {"opening": str(mine["opening"]), "closing": str(mine["closing"])}
        out_accounts.append(row)
    rows = []
    for t in sorted(seen.values(), key=lambda t: (t.get("date") or "", t["transaction_id"])):
        row = {k: t.get(k) for k in KEEP}
        pfc = t.get("personal_finance_category") or {}
        row["category"] = pfc.get("detailed") or pfc.get("primary") or ""
        rows.append(row)
    return {"source": "plaid_sandbox", "institution": INSTITUTION[1], "institution_id": INSTITUTION[0],
            "pulled_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "seconds": round(time.time() - started, 1), "status": status,
            "accounts": out_accounts, "transactions": rows}


# ---- into the books --------------------------------------------------------------------------
def to_transactions(feed: Dict) -> List[Transaction]:
    """Plaid's rows as the books' rows: the same descriptions, payees and trace numbers the CSV
    parsers produce, signed from the account's point of view (+ money in). The evidence line says
    which Plaid transaction each row is."""
    accounts = {a["account_id"]: a for a in feed.get("accounts", []) if a.get("format") in FORMATS}
    out = []
    for p in feed.get("transactions", []):
        a = accounts.get(p.get("account_id"))
        if a is None:
            continue
        amount = -Decimal(str(p["amount"])).quantize(CENT)
        if amount == 0:
            continue
        raw = p.get("original_description") or p.get("name") or ""
        when = p.get("authorized_date") or p["date"]
        evidence = f"Plaid Sandbox, {a.get('name')} ending {a.get('mask')}: {p['transaction_id']}"
        category = p.get("category") or ""
        if a["format"] == "chase_checking":
            desc, company = _clean(raw)
            trace = _TRACE.search(raw)
            out.append(Transaction(date=p["date"], amount=amount, source="chase_checking", account=a["books_account"],
                                   kind="bank", description=desc, counterparty=company, source_category=category,
                                   ref=trace.group(1) if trace else "", evidence=evidence))
        elif a["format"] == "chase_card":
            out.append(Transaction(date=when, amount=amount, source="chase_card", account=a["books_account"],
                                   kind="card", description=raw, counterparty=raw, source_category=category,
                                   evidence=evidence))
        else:
            desc = " ".join(raw.split())
            out.append(Transaction(date=when, amount=amount, source="amex", account=a["books_account"], kind="card",
                                   description=desc, counterparty=desc, source_category=category, evidence=evidence))
    return out


def compare(month_dir, feed: Dict) -> Dict:
    """The feed against the CSV exports it stands in for: the same rows, dates, amounts and descriptions?"""
    from .load import load_folder
    csv_rows, _, _ = load_folder(month_dir)
    accounts = {a["books_account"] for a in feed.get("accounts", []) if a.get("format") in FORMATS}

    def sig(t):
        return (t.account, t.date, str(t.amount), t.description, t.counterparty)
    a = Counter(sig(t) for t in csv_rows if t.account in accounts)
    b = Counter(sig(t) for t in to_transactions(feed))
    only_csv = sorted((a - b).elements())
    # a row dated after the day of the pull isn't in the bank yet (Plaid's Sandbox leaves out future dates)
    pulled = (feed.get("pulled_at") or "9999-12-31")[:10]
    balances = []
    for acct in feed.get("accounts", []):
        if acct.get("statement") and acct.get("balance") is not None:
            plaid, closing = Decimal(str(acct["balance"])).quantize(CENT), Decimal(acct["statement"]["closing"])
            balances.append({"account": acct["books_account"], "plaid": plaid, "statement": closing,
                             "ok": plaid == closing})
    return {"csv": sum(a.values()), "feed": sum(b.values()),
            "only_in_csv": [r for r in only_csv if r[1] <= pulled],
            "not_posted_yet": [r for r in only_csv if r[1] > pulled],
            "only_in_feed": sorted((b - a).elements()), "balances": balances}


def summary(feed: Dict) -> List[str]:
    lines = [f"{feed['institution']} (Plaid Sandbox), pulled {feed['pulled_at']} in {feed['seconds']}s, status {feed['status']}"]
    counts = Counter(t["account_id"] for t in feed["transactions"])
    pending = Counter(t["account_id"] for t in feed["transactions"] if t.get("pending"))
    for a in feed["accounts"]:
        extra = f", statement closing {a['statement']['closing']}" if a.get("statement") else ""
        lines.append(f"  {a['name']} ending {a['mask']} -> {a.get('books_account', '(not used)')}: "
                     f"{counts[a['account_id']]} rows ({pending[a['account_id']]} pending), balance {a['balance']}{extra}")
    return lines


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Pull the month's bank and cards through Plaid's Sandbox and compare with the CSVs.")
    ap.add_argument("month", nargs="?", default="samples/october")
    ap.add_argument("--record", help="save the pull (no keys or tokens are in it) to this JSON file")
    ap.add_argument("--feed", help="compare a saved pull instead of pulling again")
    args = ap.parse_args(argv)
    month = Path(args.month)
    if args.feed:
        feed = json.loads(Path(args.feed).read_text())
    else:
        try:
            feed = pull(month)
        except BankFeedError as e:
            print(f"error: {e}", file=sys.stderr)
            return 1
    print("\n".join(summary(feed)))
    c = compare(month, feed)
    print(f"\nCSV exports {c['csv']} rows, Plaid feed {c['feed']} rows; {len(c['only_in_csv'])} only in the CSVs, "
          f"{len(c['only_in_feed'])} only in the feed, {len(c['not_posted_yet'])} dated after the pull")
    for label, rows in (("only in the CSVs", c["only_in_csv"]), ("only in the feed", c["only_in_feed"]),
                        ("dated after the pull", c["not_posted_yet"])):
        for r in rows[:20]:
            print(f"  {label}: {r}")
    for b in c["balances"]:
        print(f"  {b['account']}: Plaid balance {b['plaid']}, statement closing {b['statement']}: "
              f"{'ties' if b['ok'] else 'DOES NOT TIE'}")
    if args.record:
        Path(args.record).parent.mkdir(parents=True, exist_ok=True)
        Path(args.record).write_text(json.dumps(feed, indent=1) + "\n")
        print(f"\nsaved the pull to {args.record}")
    return 0 if not (c["only_in_csv"] or c["only_in_feed"]) and all(b["ok"] for b in c["balances"]) else 2


if __name__ == "__main__":
    sys.exit(main())
