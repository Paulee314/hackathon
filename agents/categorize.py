"""Map every kind of spending to a Schedule C line, once per merchant, with Muse Spark Open: Meta's
open-weights model, served by Baseten (needs BASETEN_API_KEY in .env; --model muse-spark-1.3 uses Meta's API).
Open weights mean a firm could run this step on its own hardware and keep client data in-house. On the demo
month it matches Muse Spark 1.3 on all 22 merchants (scripts/compare_categorizers.py).

    python3 -m agents.categorize samples/october [--rules data/ledger/rules.json]

Corrections become rules: the owner's answers in the app (download them from /api/rules) are applied first. A
merchant the owner called personal is set in code and never sent to the model; one the owner called business is
sent with that said, and the model only picks its line.

Merchants are grouped first (store numbers and reference codes stripped), so one call covers the month and
the same merchant is categorized the same way every time. Results go to data/ledger/categories.json; the app
and the reports read them. The model only labels; totals are always added up in code.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections import OrderedDict
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from agents.meta import OPEN_MODEL, chat_json
from ingest.load import load_folder
from ingest.schema import Transaction

ROOT = Path(__file__).resolve().parents[1]
LINES = ["1 Gross receipts", "9 Car and truck expenses", "10 Commissions and fees", "15 Insurance (other than health)",
         "16b Interest, other", "18 Office expense", "22 Supplies", "24b Meals", "25 Utilities", "27a Other expenses"]
TREATMENTS = ["expense", "vehicle", "personal", "transfer", "loan_payment", "owner_draw"]

PROMPT = """You are the bookkeeper for a one-person LLC that drives for Uber and Lyft in one Tesla and rents Teslas
on Turo. Below is every kind of money that left its accounts this month, grouped by merchant.

For each merchant, decide how it belongs on the owner's Schedule C (US, sole owner). Use exactly one treatment:
- "expense": deductible business cost whatever vehicle method is used (e.g. parking, tolls, phone, software, platform fees)
- "vehicle": a vehicle running cost (charging, car insurance, car washes, repairs). Deductible only under the
  actual-expense method; under the standard mileage rate it is covered by the rate instead
- "personal": not a business cost (household groceries, personal meals while driving locally)
- "transfer": money moving between the owner's own accounts, or paying off a card
- "loan_payment": a car loan payment (principal is not deductible; only the interest is, from the loan statement)
- "owner_draw": money moved out to the owner personally
schedule_c_line must be one of {lines}, or null for personal, transfer, loan_payment and owner_draw.
A merchant marked "the owner says: a business cost" is one: choose "expense" or "vehicle" and its line.

Return only a JSON array, one object per merchant, in the same order:
[{{"merchant": exactly as given, "treatment": one of the treatments, "schedule_c_line": string or null,
  "business_line": "rideshare" | "turo" | "both", "reason": one short sentence}}]

Merchants:
{merchants}"""


def merchant_key(t: Transaction) -> str:
    payee = (t.counterparty or "").strip()
    usable = payee and payee.upper() not in ("UNKNOWN", "N/A", "NONE", "CASH")
    d = (payee if t.kind == "cash" and usable else t.description).upper()
    d = d.split("·")[0]
    d = re.sub(r"\*.*$", "", d)                     # "OPENAI *CHATGPT SUBSCR" -> "OPENAI"
    d = re.sub(r"#\s*\d+|\b\d{3,}\b.*$", "", d)     # store numbers, phone numbers, trailing ids
    d = re.sub(r"\s+(US|TX|LLC|INC|CO)\b\.?$", "", d.strip())
    return re.sub(r"\s+", " ", d).strip(" -,") or t.description.upper()


def spending(txns: List[Transaction]) -> "OrderedDict[str, Dict]":
    groups: "OrderedDict[str, Dict]" = OrderedDict()
    for t in sorted(txns, key=lambda x: x.date):
        if t.amount >= 0 or t.kind in ("earning", "payout"):
            continue
        g = groups.setdefault(merchant_key(t), {"count": 0, "total": Decimal("0"), "example": t.description,
                                                "account": t.account, "bank_category": t.source_category, "kind": t.kind})
        g["count"] += 1
        g["total"] += -t.amount
    return groups


def categorize(txns: List[Transaction], model: str = OPEN_MODEL, rules: Optional[Dict[str, Dict]] = None) -> Dict[str, Dict]:
    """rules: the owner's answers, merchant -> {"says": "personal" | "business"} (Books.merchant_rules)."""
    groups = spending(txns)
    rules = rules or {}
    says = {k: (rules.get(k) or {}).get("says") for k in groups}
    result = {}
    for k, g in groups.items():
        if says[k] == "personal":   # the owner already answered: no model call needed
            result[k] = {"treatment": "personal", "schedule_c_line": None, "business_line": "both",
                         "reason": "You said personal on an earlier review card.", "rule": True,
                         "count": g["count"], "total": str(g["total"])}
    ask = OrderedDict((k, g) for k, g in groups.items() if k not in result)
    if ask:
        listing = "\n".join(f"- {k} | {g['count']}x, {g['total']:.2f} total | e.g. \"{g['example']}\" | {g['kind']} on {g['account']}"
                            + (f" | bank says {g['bank_category']}" if g["bank_category"] else "")
                            + (" | the owner says: a business cost" if says[k] == "business" else "") for k, g in ask.items())
        out = chat_json([{"role": "user", "content": PROMPT.format(lines=", ".join(f'"{l}"' for l in LINES), merchants=listing)}], model=model)
        for row in out if isinstance(out, list) else []:
            key = str(row.get("merchant", "")).upper().strip()
            if key in ask and row.get("treatment") in TREATMENTS:
                treatment, line = row["treatment"], row.get("schedule_c_line")
                line = line if line in LINES else None
                if says[key] == "business" and treatment not in ("expense", "vehicle"):
                    treatment, line = "expense", line or "27a Other expenses"   # the owner's answer wins
                result[key] = {"treatment": treatment, "schedule_c_line": line,
                               "business_line": row.get("business_line") or "both", "reason": row.get("reason", ""),
                               "count": groups[key]["count"], "total": str(groups[key]["total"])}
                if says[key] == "business":
                    result[key]["rule"] = True
    return OrderedDict((k, result[k]) for k in groups if k in result)


def main():
    ap = argparse.ArgumentParser(description="Map spending to Schedule C lines.")
    ap.add_argument("month", nargs="?", default="samples/october")
    ap.add_argument("--model", default=OPEN_MODEL)
    ap.add_argument("--out", default=str(ROOT / "data" / "ledger" / "categories.json"))
    ap.add_argument("--rules", default=str(ROOT / "data" / "ledger" / "rules.json"),
                    help="the owner's answers from the app (/api/rules); used if the file exists")
    args = ap.parse_args()
    rules = {}
    if Path(args.rules).exists():
        data = json.loads(Path(args.rules).read_text())
        rules = data.get("rules", data)
        print(f"  applying {len(rules)} rule(s) from your answers: {', '.join(rules)}")
    txns, _, _ = load_folder(Path(args.month))
    from app.books import Books  # cash entries from voice notes count too
    from datetime import date
    txns = txns + Books(Path(args.month), date(2026, 10, 31), {}).cash_txns()
    cats = categorize(txns, args.model, rules)
    missing = [k for k in spending(txns) if k not in cats]
    for k, c in cats.items():
        print(f"  {k:<28} {c['treatment']:<13} {c['schedule_c_line'] or '':<34} {c['reason']}")
    if missing:
        print(f"\n  not categorized: {missing}")
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(cats, indent=2) + "\n")
    print(f"\n{len(cats)} merchants categorized with {args.model}. Wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
