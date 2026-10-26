"""Lyft per-ride earnings export."""
from __future__ import annotations

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence


def _trips(path, rows):
    out = []
    for n, r in rows:
        amount = money(r["total"])
        if amount == 0:
            continue
        product = r.get("product", "")
        kind_label = "cancellation" if "cancel" in r.get("type", "") else "ride"
        desc = f"Lyft {product} {kind_label}".replace("  ", " ").strip()
        out.append(Transaction(
            date=iso_date(r["date"]), amount=amount,
            source="lyft", account="lyft_balance", kind="earning",
            description=desc, counterparty="Lyft", business_line="rideshare",
            tips=money(r.get("tip", "")),
            ref=r["ride_id"], evidence=evidence(path, n),
        ))
    return out


TRIPS = Parser("lyft_trips", frozenset({"ride_id", "date", "total", "pay_status"}), _trips)
