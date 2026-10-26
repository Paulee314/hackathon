"""Uber Fleet Hub exports: trips, payouts, other earnings."""
from __future__ import annotations

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence


def _trips(path, rows):
    out = []
    for n, r in rows:
        amount = money(r["your_earnings"])
        if amount == 0:
            continue
        status = r.get("status", "")
        desc = f"Uber {r.get('product', '')} trip".strip()
        if status and status != "COMPLETED":
            desc += f" ({status.lower().replace('_', ' ')})"
        out.append(Transaction(
            date=iso_date(r["trip_datetime_local"]),
            amount=amount,
            source="uber", account="uber_balance", kind="earning",
            description=desc, counterparty="Uber", business_line="rideshare",
            tips=money(r.get("tip", "")),
            ref=r["trip_id"], evidence=evidence(path, n),
        ))
    return out


def _payouts(path, rows):
    out = []
    for n, r in rows:
        transferred = money(r["transferred"])
        fee = money(r.get("instant_pay_fee", ""))
        day = iso_date(r["time"])
        ref = r["time"]
        out.append(Transaction(
            date=day, amount=-transferred,
            source="uber", account="uber_balance", kind="payout",
            description="Uber payout to bank", counterparty="Uber",
            ref=ref, evidence=evidence(path, n),
        ))
        if fee:
            out.append(Transaction(
                date=day, amount=-fee,
                source="uber", account="uber_balance", kind="fee",
                description="Uber instant pay fee", counterparty="Uber",
                business_line="rideshare", fees=fee,
                ref=f"{ref}|fee", evidence=evidence(path, n),
            ))
    return out


def _other(path, rows):
    out = []
    for n, r in rows:
        amount = money(r["earnings"])
        if amount == 0:
            continue
        desc = r["description"]
        line = "contract_work" if "KNOWLEDGE_WORK" in desc else "rideshare"
        out.append(Transaction(
            date=iso_date(r["time"]), amount=amount,
            source="uber", account="uber_balance", kind="earning",
            description=desc, counterparty="Uber", business_line=line,
            ref=r.get("trip_uuid") or r["time"], evidence=evidence(path, n),
        ))
    return out


TRIPS = Parser("uber_trips", frozenset({"trip_id", "trip_datetime_local", "your_earnings"}), _trips)
PAYOUTS = Parser("uber_payouts", frozenset({"time", "transferred", "instant_pay_fee"}), _payouts)
OTHER = Parser("uber_other_earnings", frozenset({"time", "description", "earnings", "trip_uuid"}), _other)
