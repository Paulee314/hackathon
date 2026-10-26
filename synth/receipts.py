"""Generate receipt photos for some of the synthetic month's card charges, with Muse Image.

Merchants are fictional, and every receipt's lines add up to the card charge it belongs to.
Run once; the images are committed under samples/october/receipts/.

    python3 synth/receipts.py
"""
from __future__ import annotations

import json
import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.meta import generate_image  # noqa: E402

OUT = ROOT / "samples" / "october" / "receipts"

RECEIPTS = [
    {"file": "2026-10-09_bayou-shine.jpg", "merchant": "BAYOU SHINE CAR WASH", "city": "Houston TX", "date": "10/09/2026",
     "items": [("Exterior wash", "16.63")], "tax": "1.37", "card": "VISA ****9001",
     "scene": "held in a hand inside a car, slightly crumpled thermal paper"},
    {"file": "2026-10-24_bayou-shine.jpg", "merchant": "BAYOU SHINE CAR WASH", "city": "Houston TX", "date": "10/24/2026",
     "items": [("Exterior wash", "18.00"), ("Interior detail", "24.00")], "tax": "3.47", "card": "VISA ****9001",
     "scene": "lying on a car seat, top-down photo"},
    {"file": "2026-10-12_detailpro.jpg", "merchant": "DETAILPRO SUPPLY CO", "city": "Houston TX", "date": "10/12/2026",
     "items": [("Microfiber towels 12pk", "15.99"), ("Interior cleaner spray", "9.99"), ("Seat back protector", "9.70")],
     "tax": "2.94", "card": "VISA ****9001", "scene": "on a store counter, photographed at a slight angle"},
    {"file": "2026-10-18_bayou-fresh.jpg", "merchant": "BAYOU FRESH MARKET #482", "city": "Houston TX", "date": "10/18/2026",
     "items": [("Ribeye steaks 2pk", "32.94"), ("Salmon fillet", "16.92"), ("Chicken thighs", "11.84"), ("Coffee beans", "9.98"),
               ("Eggs 18ct", "5.47"), ("Whole milk", "4.28"), ("Strawberries", "3.98"), ("Avocados 4ct", "5.00"),
               ("Bread", "3.48"), ("Bananas", "3.38"), ("Paper towels T", "12.97"), ("Laundry detergent T", "13.97")],
     "tax": "2.22", "card": "VISA ****9001", "scene": "long grocery receipt on a kitchen counter, top-down photo"},
    {"file": "2026-10-11_lone-star-burger.jpg", "merchant": "LONE STAR BURGER 1047", "city": "Houston TX", "date": "10/11/2026",
     "items": [("Burger combo", "11.49"), ("Large drink upgrade", "1.74")], "tax": "1.09", "card": "AMEX ****1005",
     "scene": "on a car dashboard next to a phone mount, photographed at night"},
]


def total(r) -> Decimal:
    return sum(Decimal(p) for _, p in r["items"]) + Decimal(r["tax"])


def prompt(r) -> str:
    lines = [f"'{r['merchant']}'", f"'{r['city']}'", f"'Date {r['date']}'"]
    lines += [f"'{name}  {price}'" for name, price in r["items"]]
    sub = sum(Decimal(p) for _, p in r["items"])
    lines += [f"'Subtotal {sub:.2f}'", f"'Tax {r['tax']}'", f"'TOTAL {total(r):.2f}'", f"'{r['card']}'"]
    return ("Realistic photo of a printed paper receipt, " + r["scene"] + ". "
            "The receipt text is sharp and fully legible, printed in this order: " + ", ".join(lines) + ". "
            "No other text, no logos.")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    key = []
    for r in RECEIPTS:
        path = OUT / r["file"]
        if not path.exists():
            path.write_bytes(generate_image(prompt(r)))
            print(f"generated {path.name}")
        key.append({"file": r["file"], "merchant": r["merchant"], "date": r["date"], "total": f"{total(r):.2f}",
                    "card": r["card"]})
    (OUT / "receipts_truth.json").write_text(json.dumps(key, indent=2) + "\n")


if __name__ == "__main__":
    main()
