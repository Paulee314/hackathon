"""Turo host earnings export: trip earnings and payouts to the bank.

Trip rows name the guest ("Sarah's trip"); the guest's name is dropped here.
"""
from __future__ import annotations

import re

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence

_ACCOUNT_DIGITS = re.compile(r"(\d{4})\)?\s*$")


def _earnings(path, rows):
    out = []
    for n, r in rows:
        kind_text = r.get("Type", "")
        day = iso_date(r["Date"])
        if kind_text.startswith("Payment"):
            amount = money(r.get("Payment", ""))
            if amount == 0:
                continue
            m = _ACCOUNT_DIGITS.search(kind_text)
            to = f" to account ending {m.group(1)}" if m else ""
            failed = " (failed)" if r.get("Failed payment") else ""
            out.append(Transaction(
                date=day, amount=-amount,
                source="turo", account="turo_balance", kind="payout",
                description=f"Turo payout{to}{failed}", counterparty="Turo",
                ref=f"payout|{day}|{amount}", evidence=evidence(path, n),
            ))
        else:
            amount = money(r.get("Earnings", ""))
            if amount == 0:
                continue
            vehicle = r.get("Vehicle") or "vehicle"
            label = "Turo trip adjustment" if amount < 0 else "Turo trip earnings"
            out.append(Transaction(
                date=day, amount=amount,
                source="turo", account="turo_balance", kind="earning",
                description=f"{label} · {vehicle}", counterparty="Turo",
                business_line="turo",
                ref=r.get("Reservation URL", "") if r.get("Reservation URL", "") not in ("", "N/A") else "",
                evidence=evidence(path, n),
            ))
    return out


EARNINGS = Parser("turo_earnings", frozenset({"Type", "Reservation URL", "Date", "Earnings", "Payment"}), _earnings)
