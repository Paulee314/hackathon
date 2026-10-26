"""Chase credit card activity CSV (Card, Transaction Date, Post Date, ...). Purchases are negative."""
from __future__ import annotations

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence


def _activity(path, rows):
    out = []
    for n, r in rows:
        amount = money(r["Amount"])
        if amount == 0:
            continue
        card = r.get("Card", "").strip()
        out.append(Transaction(
            date=iso_date(r["Transaction Date"]), amount=amount,
            source="chase_card", account=f"chase_ink_{card}" if card else "chase_ink",
            kind="card", description=r["Description"], counterparty=r["Description"],
            source_category=" / ".join(x for x in (r.get("Category", ""), r.get("Type", "")) if x),
            evidence=evidence(path, n),
        ))
    return out


ACTIVITY = Parser("chase_card", frozenset({"Card", "Transaction Date", "Post Date", "Amount"}), _activity)
