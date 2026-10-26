"""Chase checking activity CSV (Details, Posting Date, Description, Amount, Type, Balance)."""
from __future__ import annotations

import re

from ..schema import Transaction, iso_date, money
from .base import Parser, evidence

_FILE_DIGITS = re.compile(r"Chase(\d{4})", re.I)
_ORIG = re.compile(r"ORIG CO NAME:(.*?)\s{2,}|ORIG CO NAME:(.*?)\s+ORIG ID", re.I)
_DESCR = re.compile(r"CO ENTRY DESCR:(.*?)SEC:", re.I)
_TRACE = re.compile(r"TRACE#:(\d+)", re.I)


def _clean(desc: str):
    """ACH descriptions are long fixed-width blobs; keep the company and entry description."""
    m = _ORIG.search(desc)
    if not m:
        return re.sub(r"\s+", " ", desc).strip(), ""
    company = (m.group(1) or m.group(2) or "").strip()
    d = _DESCR.search(desc)
    entry = re.sub(r"\s+", " ", d.group(1)).strip() if d else ""
    return (f"{company} · {entry}" if entry else company), company


def _activity(path, rows):
    m = _FILE_DIGITS.search(path.name)
    account = f"chase_{m.group(1)}" if m else "chase_checking"
    out = []
    for n, r in rows:
        amount = money(r["Amount"])
        if amount == 0:
            continue
        desc, company = _clean(r["Description"])
        trace = _TRACE.search(r["Description"])
        ref = trace.group(1) if trace else (r.get("Check or Slip #") or "")
        out.append(Transaction(
            date=iso_date(r["Posting Date"]), amount=amount,
            source="chase_checking", account=account, kind="bank",
            description=desc, counterparty=company,
            source_category=r.get("Type", ""),
            ref=ref, evidence=evidence(path, n),
        ))
    return out


ACTIVITY = Parser("chase_checking", frozenset({"Details", "Posting Date", "Description", "Amount", "Balance"}), _activity)
