"""The close agent: tie every account out, match money moving between accounts, list what it can't explain.

    python3 -m agents.close samples/october            # checks, exceptions, and the score against the answer key

Every decision about a number is made in code, so each finding comes with the rows that prove it:
  - platform payouts matched to bank deposits (Uber, Turo), within a few days, to the penny
  - Lyft has no payout export, so each Lyft deposit is matched to the week of rides it pays for
  - card payments in checking matched to the payments the cards received
  - identical charges on the same day, and money in that no platform or transfer explains
  - checking tied to its statement: opening balance plus the month's activity equals the closing balance
Muse Spark explains findings on demand in the app; agents/categorize.py maps spending to Schedule C lines.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import defaultdict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from ingest.load import load_folder
from ingest.parsers import read_csv
from ingest.schema import Transaction, iso_date, money

ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal("0")
PAYOUT_DAYS = 4          # an ACH payout normally lands within a few days
LATE_AFTER_DAYS = 3      # older than this at month end with no deposit: flag it
CARD_PAYMENT_WORDS = ("CREDIT CRD", "EPAYMENT", "AUTOPAY", "AUTOMATIC PAYMENT")


def _day(t: Transaction) -> date:
    return date.fromisoformat(t.date)


def _desc(t: Transaction) -> str:
    return f"{t.description} {t.counterparty}".upper()


def _m(d) -> str:
    d = Decimal(str(d))
    return ("-" if d < 0 else "") + "${:,.2f}".format(abs(d))


def _short(d: date) -> str:
    return d.strftime("%b ") + str(d.day)


def _check(cid: str, title: str, ok: bool, detail: str, rows: Optional[List] = None) -> Dict:
    return {"id": cid, "title": title, "ok": ok, "detail": detail, "rows": rows or []}


def _title(text: str) -> str:
    """'TESLA SUPERCHARGER US' -> 'Tesla Supercharger US' (short words like US and TX stay capitals)."""
    return " ".join(w if len(w) <= 2 else w.capitalize() for w in re.sub(r"\s+", " ", text).strip().split(" "))


CHECK_OF = {"payout_missing_deposit": "{p}-payouts", "deposit_without_payout": "{p}-deposits", "deposit_short": "lyft-weeks",
            "possible_duplicate": "duplicates", "unidentified_income": "deposits-explained"}


def _exception(etype, when: date, amount, title, body, rows, txn_ids, suggestion, choices, accounts, platform="") -> Dict:
    return {"id": f"close:{etype}:{when.isoformat()}:{Decimal(str(amount)).copy_abs()}", "type": etype,
            "check": CHECK_OF[etype].format(p=platform),
            "date": when.isoformat(), "amount": str(Decimal(str(amount))), "at_stake": str(Decimal(str(amount)).copy_abs()),
            "title": title, "body": body,
            "rows": rows, "txn_ids": txn_ids, "suggestion": suggestion, "choices": choices, "accounts": accounts}


# ---- payouts and deposits --------------------------------------------------------------
def match_platform(txns: List[Transaction], platform: str, month_end: date):
    """Pair each payout in the platform's export with one bank deposit of the same amount."""
    payouts = sorted((t for t in txns if t.source == platform and t.kind == "payout"), key=_day)
    deposits = [t for t in txns if t.kind == "bank" and t.amount > 0 and platform.upper() in _desc(t)]
    used, pairs, missing = set(), [], []
    for p in payouts:
        best = None
        for d in deposits:
            gap = (_day(d) - _day(p)).days
            if d.txn_id not in used and d.amount == -p.amount and 0 <= gap <= PAYOUT_DAYS:
                if best is None or gap < best[0]:
                    best = (gap, d)
        if best:
            used.add(best[1].txn_id)
            pairs.append((p, best[1]))
        else:
            missing.append(p)
    orphans = [d for d in deposits if d.txn_id not in used]
    return pairs, missing, orphans


def lyft_weeks(txns: List[Transaction]):
    """Lyft's export has rides but no payouts. Each weekly deposit pays the Monday-to-Sunday week before it."""
    weeks = defaultdict(lambda: ZERO)
    for t in txns:
        if t.source == "lyft" and t.kind == "earning":
            d = _day(t)
            weeks[d - timedelta(days=d.weekday())] += t.amount
    deposits = sorted((t for t in txns if t.kind == "bank" and t.amount > 0 and "LYFT" in _desc(t)), key=_day)
    out = []
    for monday in sorted(weeks):
        sunday = monday + timedelta(days=6)
        dep = next((d for d in deposits if sunday < _day(d) <= sunday + timedelta(days=PAYOUT_DAYS)), None)
        out.append({"monday": monday, "sunday": sunday, "rides": weeks[monday], "deposit": dep})
    return out


# ---- the statement -------------------------------------------------------------------------
def checking_statement(month_dir: Path) -> Optional[Dict]:
    """Opening and closing balance from the bank export's running Balance column."""
    for path in sorted(Path(month_dir).rglob("*.csv")):
        header, rows = read_csv(path)
        if {"Posting Date", "Balance", "Amount"} <= set(header) and "Details" in header:
            lines = [(iso_date(r["Posting Date"]), money(r["Amount"]), money(r["Balance"]), n) for n, r in rows if r.get("Balance")]
            if not lines:
                continue
            # Chase lists newest first; within a day, the row lower in the file happened earlier.
            first = max(lines, key=lambda x: (-int(x[0].replace("-", "")), x[3]))
            last = min(lines, key=lambda x: (-int(x[0].replace("-", "")), x[3]))
            return {"file": path.name, "opening": first[2] - first[1], "closing": last[2],
                    "account": "chase_" + re.search(r"(\d{4})", path.name).group(1)}
    return None


# ---- the agent ------------------------------------------------------------------------------
def run(txns: List[Transaction], month_dir: Path) -> Dict:
    month_end = max(_day(t) for t in txns)
    checks, exceptions = [], []

    # Uber and Turo: every payout reached the bank, every deposit came from a payout.
    for platform, name in (("uber", "Uber"), ("turo", "Turo")):
        pairs, missing, orphans = match_platform(txns, platform, month_end)
        late = [p for p in missing if (month_end - _day(p)).days > LATE_AFTER_DAYS]
        checks.append(_check(f"{platform}-payouts", f"Every {name} payout reached the bank", not late,
                             f"{len(pairs)} of {len(pairs) + len(missing)} payouts matched a deposit to the penny.",
                             [[f"{_short(_day(p))} payout", _m(-p.amount), _short(_day(d))] for p, d in pairs]))
        for p in late:
            days = (month_end - _day(p)).days
            exceptions.append(_exception(
                "payout_missing_deposit", _day(p), -p.amount,
                f"{name} sent {_m(-p.amount)} on {_short(_day(p))}, and it never reached the bank",
                f"{name}'s payout list shows it, but no deposit of that amount arrived in the {days} days since. "
                f"Payouts usually land within {PAYOUT_DAYS} days.",
                [[f"{name} payout, {_short(_day(p))}", _m(-p.amount)], ["Deposits that match", "None"]],
                [p.txn_id], {"choice": "follow_up", "label": f"Ask {name} where it went"},
                [{"choice": "follow_up", "label": f"Ask {name} where it went"},
                 {"choice": "in_transit", "label": "It's on the way, check again next week"}],
                [f"{platform}_balance", "chase_1234"], platform))
        checks.append(_check(f"{platform}-deposits", f"Every {name} deposit came from a payout", not orphans,
                             "Every deposit matched a payout." if not orphans else
                             f"{len(orphans)} deposit{'s' if len(orphans) != 1 else ''} with no payout behind it."))
        for d in orphans:
            exceptions.append(_exception(
                "deposit_without_payout", _day(d), d.amount,
                f"A {_m(d.amount)} {name} deposit with no payout behind it",
                f"It arrived {_short(_day(d))}, but nothing in {name}'s export adds up to it. It may be a reimbursement "
                f"or a claim paid outside trip earnings, so it isn't booked as trip income yet.",
                [[f"Deposit, {_short(_day(d))}", _m(d.amount)], [f"{name} payouts of that amount", "None"]],
                [d.txn_id], {"choice": "income", "label": f"Book it as other {name} income"},
                [{"choice": "income", "label": f"Book it as other {name} income"},
                 {"choice": "hold", "label": f"Leave it out until {name} explains it"}],
                ["chase_1234"], platform))

    # Lyft: deposits against the week of rides they pay for.
    weeks = lyft_weeks(txns)
    bad = []
    for w in weeks:
        dep = w["deposit"]
        if dep is None:
            due = w["sunday"] + timedelta(days=PAYOUT_DAYS)
            if due <= month_end:
                bad.append(w)
            continue
        if dep.amount != w["rides"]:
            bad.append(w)
            short = w["rides"] - dep.amount
            exceptions.append(_exception(
                "deposit_short", _day(dep), dep.amount,
                f"Lyft paid you {_m(short)} {'less' if short > 0 else 'more'} than your rides",
                f"Rides from {_short(w['monday'])} to {_short(w['sunday'])} add up to {_m(w['rides'])}. "
                f"The deposit on {_short(_day(dep))} was {_m(dep.amount)}, and nothing in the ride list explains the gap.",
                [[f"Rides, {_short(w['monday'])}–{w['sunday'].day}", _m(w["rides"])],
                 [f"Deposit, {_short(_day(dep))}", _m(dep.amount)], ["Short by" if short > 0 else "Over by", _m(abs(short))]],
                [dep.txn_id], {"choice": "fee", "label": "Book it as a Lyft fee"},
                [{"choice": "fee", "label": "Book it as a Lyft fee"}, {"choice": "follow_up", "label": "Ask Lyft about it"}],
                ["lyft_balance", "chase_1234"]))
            exceptions[-1]["at_stake"] = str(abs(short))
    pending = [w for w in weeks if w["deposit"] is None and w["sunday"] + timedelta(days=PAYOUT_DAYS) > month_end]
    checks.append(_check("lyft-weeks", "Each Lyft deposit equals its week of rides", not bad,
                         f"{sum(1 for w in weeks if w['deposit'] is not None and w['deposit'].amount == w['rides'])} of "
                         f"{sum(1 for w in weeks if w['deposit'] is not None)} deposits match to the penny."
                         + (f" Rides since {_short(pending[0]['monday'])} ({_m(pending[0]['rides'])}) pay out after the month ends." if pending else ""),
                         [[f"Rides {_short(w['monday'])}–{w['sunday'].day}", _m(w["rides"]),
                           _m(w["deposit"].amount) if w["deposit"] else "after month end"] for w in weeks]))

    # Card payments: what left checking arrived on a card.
    bank_pays = [t for t in txns if t.kind == "bank" and t.amount < 0 and any(w in _desc(t) for w in CARD_PAYMENT_WORDS)]
    card_pays = [t for t in txns if t.kind == "card" and t.amount > 0]
    unmatched = []
    for b in bank_pays:
        if not any(c.amount == -b.amount and abs((_day(c) - _day(b)).days) <= 5 for c in card_pays):
            unmatched.append(b)
    checks.append(_check("card-payments", "Card payments from checking reached the cards", not unmatched,
                         f"{len(bank_pays) - len(unmatched)} of {len(bank_pays)} payments matched."))

    # The same charge twice on one day.
    groups = defaultdict(list)
    for t in txns:
        if t.kind in ("card", "bank") and t.amount < 0:
            groups[(t.account, t.date, t.amount, re.sub(r"\s+", " ", t.description.strip().upper()))].append(t)
    dupes = [g for g in groups.values() if len(g) > 1]
    checks.append(_check("duplicates", "No charge appears twice on the same day", not dupes,
                         "None found." if not dupes else f"{len(dupes)} charge{'s' if len(dupes) != 1 else ''} posted twice."))
    for g in dupes:
        t = g[0]
        merchant = _title(t.description)
        exceptions.append(_exception(
            "possible_duplicate", _day(t), t.amount,
            f"{merchant} charged you {_m(-t.amount)} twice on {_short(_day(t))}",
            "Two identical charges on the same day. Charging sessions rarely cost exactly the same, so this looks like "
            "one session billed twice. Until you say, the copy isn't counted.",
            [[f"Charge {i + 1}, {_short(_day(x))}", _m(-x.amount)] for i, x in enumerate(g)],
            [x.txn_id for x in g[1:]], {"choice": "duplicate", "label": "It's a duplicate: leave the copy out and dispute it"},
            [{"choice": "duplicate", "label": "It's a duplicate: leave the copy out and dispute it"},
             {"choice": "both", "label": "Two real sessions: keep both"}],
            [t.account]))

    # Money in that no platform, transfer or refund explains.
    explained = set()
    for platform in ("uber", "turo"):
        pairs, _, orphans = match_platform(txns, platform, month_end)
        explained.update(d.txn_id for _, d in pairs)
        explained.update(d.txn_id for d in orphans)  # already its own exception
    explained.update(w["deposit"].txn_id for w in weeks if w["deposit"])
    strangers = [t for t in txns if t.kind == "bank" and t.amount > 0 and t.txn_id not in explained]
    checks.append(_check("deposits-explained", "Every deposit is explained", not strangers,
                         "All money in is accounted for." if not strangers else
                         f"{len(strangers)} deposit{'s' if len(strangers) != 1 else ''} with no platform or transfer behind it."))
    for t in strangers:
        who = _title(re.sub(r"\s+[0-9A-F]{6,}$", "", t.description.split("from")[-1].strip()) if "from" in t.description else t.description)
        exceptions.append(_exception(
            "unidentified_income", _day(t), t.amount,
            f"{_m(t.amount)} from {who}: what was it for?",
            f"It came in on {_short(_day(t))}, outside Uber, Lyft and Turo. If a rider or renter paid you directly it's "
            f"business income; if it was a friend paying you back, it stays out of the books.",
            [[f"Deposit, {_short(_day(t))}", _m(t.amount)], ["From", who]],
            [t.txn_id], None,
            [{"choice": "business", "label": "Business income"}, {"choice": "personal", "label": "Personal, not business"}],
            [t.account]))

    # Checking ties to its statement.
    st = checking_statement(month_dir)
    if st:
        activity = sum((t.amount for t in txns if t.account == st["account"]), ZERO)
        ok = st["opening"] + activity == st["closing"]
        checks.append(_check("checking-ties", "Checking ties to the bank statement", ok,
                             f"Opening {_m(st['opening'])} plus the month's activity {_m(activity)} "
                             f"{'equals' if ok else 'does not equal'} the closing balance {_m(st['closing'])}.",
                             [["Opening balance", _m(st["opening"])], ["Month's activity", _m(activity)], ["Closing balance", _m(st["closing"])]]))

    # What the platforms still hold at month end (information, not a problem).
    held = {}
    for platform, account in (("uber", "uber_balance"), ("turo", "turo_balance")):
        held[platform] = sum((t.amount for t in txns if t.account == account), ZERO)
    return {"month_end": month_end.isoformat(), "checks": checks, "exceptions": exceptions,
            "platform_balances": {k: str(v) for k, v in held.items()}}


# ---- scoring against the answer key ------------------------------------------------------------
def score(found: List[Dict], expected_path: Path) -> Dict:
    """found: dicts with date and amount (agent exceptions plus receipt flags). Matched on date and amount.
    The key's owner questions (things only the owner can answer, like whether a meal was on an overnight work
    trip) count as asked, not as false alarms."""
    data = json.loads(Path(expected_path).read_text())
    key, questions = data["exceptions"], data.get("owner_questions", [])
    def same(f, e):
        return (abs((date.fromisoformat(f["date"]) - date.fromisoformat(e["date"])).days) <= 1
                and Decimal(str(f["amount"])).copy_abs() == Decimal(e["amount"]).copy_abs())
    caught = [e["id"] for e in key if any(same(f, e) for f in found)]
    missed = [e["id"] for e in key if e["id"] not in caught]
    asked = [q["id"] for q in questions if any(same(f, q) for f in found)]
    false_alarms = [f.get("title", f["date"]) for f in found if not any(same(f, e) for e in key + questions)]
    return {"caught": caught, "missed": missed, "false_alarms": false_alarms, "of": len(key), "asked": asked}


def main():
    ap = argparse.ArgumentParser(description="Close the month: checks, exceptions and the score.")
    ap.add_argument("month", nargs="?", default="samples/october")
    args = ap.parse_args()
    month = Path(args.month)
    txns, _, _ = load_folder(month)
    out = run(txns, month)
    for c in out["checks"]:
        print(f"  {'pass' if c['ok'] else 'FAIL'}  {c['title']}: {c['detail']}")
    print()
    for e in out["exceptions"]:
        print(f"  {e['date']}  {e['type']:<24} {e['title']}")
    found = [{"date": e["date"], "amount": e["amount"], "title": e["title"]} for e in out["exceptions"]]
    # the receipt readings: your own run of agents.receipts, or the recorded one (as the app does), so a fresh
    # clone with no key scores the same; the checks on them are redone in code
    receipts = next((p for p in (ROOT / "data" / "ledger" / "receipts.json",
                                 ROOT / "tests" / "fixtures" / "receipts_extracted.json") if p.exists()), None)
    if receipts is not None:
        from agents.receipts import assess
        for r in json.loads(receipts.read_text()):
            a = r if "flags" in r and "match" in r else assess(r["extracted"], txns)
            if a.get("flags") and a.get("match"):
                found.append({"date": a["match"]["date"], "amount": a["match"]["amount"], "title": f"receipt: {r['file']}"})
    key = month / "expected_exceptions.json"
    if key.exists():
        s = score(found, key)
        print(f"\nAnswer key: caught {len(s['caught'])} of {s['of']} {s['caught']}; missed {s['missed']}; "
              f"false alarms {len(s['false_alarms'])} {s['false_alarms']}; owner questions asked {s['asked']}")


if __name__ == "__main__":
    sys.exit(main())
