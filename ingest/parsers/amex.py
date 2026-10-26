"""American Express activity CSV. Amex reports charges as positive, so the sign is flipped."""
from __future__ import annotations

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence


def _activity(path, rows):
    out = []
    for n, r in rows:
        amount = -money(r["Amount"])
        if amount == 0:
            continue
        desc = " ".join((r.get("Description") or "").split())
        out.append(Transaction(
            date=iso_date(r["Date"]), amount=amount,
            source="amex", account="amex_platinum", kind="card",
            description=desc, counterparty=desc,
            source_category=r.get("Category", ""),
            ref=(r.get("Reference") or "").strip("'"), evidence=evidence(path, n),
        ))
    return out


ACTIVITY = Parser("amex", frozenset({"Date", "Description", "Amount", "Appears On Your Statement As"}), _activity)
