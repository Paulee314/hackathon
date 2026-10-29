"""Read receipt photos with Muse Spark and attach each to the card charge it belongs to.

    python3 -m agents.receipts samples/october

For every image: extract merchant, date, lines, tax and total; check the arithmetic
(lines add up to the subtotal, subtotal plus tax equals the total); find the card charge
with the same amount within three days; and say whether the purchase looks like business
or personal spending, with a one-sentence reason. Results go to data/ledger/receipts.json.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional

from agents.meta import chat_json, image_part
from ingest.load import load_folder

ROOT = Path(__file__).resolve().parents[1]
IMAGE_TYPES = {".jpg", ".jpeg", ".png", ".webp"}

PROMPT = """You are reading a photo of a receipt for the books of a small business that drives for
Uber and Lyft and rents cars on Turo. Read only what is printed. Return only JSON, no prose:
{"merchant": string, "date": "YYYY-MM-DD", "items": [{"description": string, "amount": number}],
 "subtotal": number or null, "tax": number or null, "total": number, "card_last4": string or null,
 "kind": "business" | "personal" | "mixed" | "unclear",
 "reason": one short sentence explaining "kind" for this kind of business}"""


def _d(x) -> Optional[Decimal]:
    return None if x is None else Decimal(str(x)).quantize(Decimal("0.01"))


def _words(text: str) -> set:
    return {w for w in re.findall(r"[a-z]{3,}", text.lower())}


def check_arithmetic(r: Dict) -> Dict:
    items = sum((_d(i["amount"]) for i in r.get("items", [])), Decimal("0"))
    sub, tax, total = _d(r.get("subtotal")), _d(r.get("tax")), _d(r.get("total"))
    return {
        "items_sum": str(items),
        "items_match_subtotal": sub is not None and abs(items - sub) <= Decimal("0.02"),
        "subtotal_plus_tax_matches_total": None not in (sub, tax, total) and abs(sub + tax - total) <= Decimal("0.02"),
    }


def find_charge(r: Dict, txns) -> Optional[Dict]:
    total = _d(r["total"])
    try:
        when = date.fromisoformat(r["date"])
    except (TypeError, ValueError):
        when = None
    best = None
    for t in txns:
        if t.kind != "card" or t.amount != -total:
            continue
        gap = abs((date.fromisoformat(t.date) - when).days) if when else 99
        if gap > 3:
            continue
        overlap = len(_words(t.description) & _words(r.get("merchant", "")))
        score = (overlap, -gap)
        if best is None or score > best[0]:
            best = (score, t)
    if best is None:
        return None
    t = best[1]
    return {"txn_id": t.txn_id, "account": t.account, "date": t.date, "description": t.description,
            "amount": str(t.amount), "merchant_words_in_common": best[0][0], "days_apart": -best[0][1]}


# A prepared meal (not groceries): the tax question for a meal is whether work kept the driver away from home
# overnight, which no receipt can answer, so the app asks. Words are matched on the merchant and the item lines.
MEAL_MERCHANT_WORDS = {"burger", "pizza", "taco", "taqueria", "grill", "cafe", "diner", "kitchen", "restaurant",
                       "bbq", "sushi", "deli", "wings", "pho", "noodle", "bistro", "cantina", "donut", "bakery"}
MEAL_ITEM_WORDS = {"combo", "fries", "entree", "sandwich", "burrito", "latte", "meal", "platter"}


def is_meal(extracted: Dict) -> bool:
    merchant = _words(extracted.get("merchant") or "")
    items = set().union(*[_words(i.get("description") or "") for i in extracted.get("items") or []] or [set()])
    return bool(merchant & MEAL_MERCHANT_WORDS or items & MEAL_ITEM_WORDS)


def assess(extracted: Dict, txns) -> Dict:
    """Checks done in code on what the model read: arithmetic, the matching charge, and flags."""
    checks = check_arithmetic(extracted)
    match = find_charge(extracted, txns)
    flags = []
    if not checks["items_match_subtotal"] or not checks["subtotal_plus_tax_matches_total"]:
        flags.append("receipt arithmetic does not add up")
    if match is None:
        flags.append("no card charge with this amount within 3 days")
    if extracted.get("kind") in ("personal", "mixed"):
        flags.append(f"looks {extracted['kind']}: {extracted.get('reason', '')}")
    return {"checks": checks, "match": match, "flags": flags}


def read_receipts(month: Path) -> List[Dict]:
    txns, _, _ = load_folder(month)
    images = sorted(p for p in month.rglob("*") if p.suffix.lower() in IMAGE_TYPES and "receipt" in str(p.parent).lower())
    results = []
    for img in images:
        extracted = chat_json([{"role": "user", "content": [{"type": "text", "text": PROMPT}, image_part(img)]}])
        a = assess(extracted, txns)
        checks, match = a["checks"], a["match"]
        results.append({"file": str(img.relative_to(month)), "extracted": extracted, **a})
        print(f"  {img.name:<34} total {extracted.get('total')!s:>8}  "
              f"{'matched ' + match['date'] if match else 'NO MATCH':<20} {extracted.get('kind', '')}")
    return results


def main():
    ap = argparse.ArgumentParser(description="Read receipts and attach them to card charges.")
    ap.add_argument("month", nargs="?", default="samples/october")
    ap.add_argument("--out", default=str(ROOT / "data" / "ledger" / "receipts.json"))
    args = ap.parse_args()
    results = read_receipts(Path(args.month))
    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(results, indent=2) + "\n")
    matched = sum(1 for r in results if r["match"])
    print(f"\n{matched} of {len(results)} receipts matched to a card charge; "
          f"{sum(1 for r in results if r['flags'])} flagged. Wrote {args.out}")


if __name__ == "__main__":
    sys.exit(main())
