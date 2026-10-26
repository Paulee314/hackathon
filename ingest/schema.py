"""One normalized transaction record for every source.

Sign convention: `amount` is signed from the point of view of `account`.
  + money arriving in that account, - money leaving it.
So an Uber trip is +31.29 in `uber_balance`, the Uber payout that moves it to the
bank is -170.95 in `uber_balance`, and the matching deposit is +170.95 in the
checking account. The reconciler pairs a payout with a deposit of the opposite sign.

Accounts: platform balances (`uber_balance`, `lyft_balance`, `turo_balance`) act as
clearing accounts; bank and card accounts are named from the export, e.g.
`chase_1234`, `chase_ink_9001`, `amex_platinum`.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, fields
from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Optional

PLATFORM_ACCOUNTS = {"uber_balance", "lyft_balance", "turo_balance"}
KINDS = {"earning", "payout", "fee", "bank", "card", "cash"}  # cash: spoken notes, account cash_on_hand
BUSINESS_LINES = {"rideshare", "turo", "contract_work", ""}  # "" = decided later


@dataclass
class Transaction:
    date: str                       # YYYY-MM-DD, the day the money moved
    amount: Decimal                 # signed, from the account's point of view
    source: str                     # uber | lyft | turo | chase_checking | chase_card | amex | voice
    account: str                    # where the money sits
    kind: str                       # one of KINDS
    description: str
    counterparty: str = ""
    business_line: str = ""
    tips: Optional[Decimal] = None  # platform earnings only
    fees: Optional[Decimal] = None  # platform fees deducted, when the source reports them
    source_category: str = ""       # the bank or card's own category label, as a hint
    category: str = ""              # Schedule C line, filled by the categorizer (day 4)
    ref: str = ""                   # the source's own id: trip, reservation, trace number
    evidence: str = ""              # file name and row number it came from
    txn_id: str = ""                # stable id, set by the loader

    def key(self) -> str:
        """Identity used for de-duplication across overlapping exports."""
        if self.ref:
            return f"{self.source}|{self.account}|{self.kind}|{self.ref}|{self.amount}"
        desc = re.sub(r"\s+", " ", self.description.strip().lower())
        return f"{self.source}|{self.account}|{self.kind}|{self.date}|{self.amount}|{desc}"

    def assign_id(self, occurrence: int) -> None:
        raw = f"{self.key()}|{occurrence}"
        self.txn_id = hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


COLUMNS = [f.name for f in fields(Transaction)]


def money(text: str) -> Decimal:
    """'$1,234.56', '-$2.41', '(5.00)', '' -> Decimal."""
    t = (text or "").strip().replace(",", "").replace("$", "")
    if not t:
        return Decimal("0")
    negative = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        value = Decimal(t)
    except InvalidOperation:
        raise ValueError(f"not a money amount: {text!r}")
    return -value if negative else value


def iso_date(text: str) -> str:
    """'08/20/2026', '2026-08-20', '2026-08-20 14:34:05' -> '2026-08-20'."""
    t = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    raise ValueError(f"unrecognized date: {text!r}")
