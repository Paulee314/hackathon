"""Load every CSV in a folder into one normalized transaction table.

    python3 -m ingest.load samples/october
    python3 -m ingest.load samples/october --from 2026-10-01 --to 2026-10-31 --out data/ledger/transactions.csv

Files are recognized by their columns, not their names. Overlapping exports
(the same transaction in two files) are loaded once. A bank feed (ingest/bank_feed.py) stands
in for the exports of the accounts it covers.
"""
from __future__ import annotations

import argparse
import csv
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path
from typing import List, Optional

from .parsers import detect, is_telemetry, read_csv
from .schema import COLUMNS, Transaction


def load_folder(folder, start: Optional[str] = None, end: Optional[str] = None,
                feed: Optional[List[Transaction]] = None):
    """feed: rows from a bank connection (ingest.bank_feed.to_transactions). An export whose accounts
    the feed covers is left out, so a month is never counted twice."""
    files = sorted(p for p in Path(folder).rglob("*") if p.is_file() and p.suffix.lower() == ".csv")
    covered = {t.account for t in feed or []}
    kept, report, skipped, batches = {}, [], [], []
    for path in files:
        header, rows = read_csv(path)
        if is_telemetry(header):
            continue  # car data and ride offers: see the car package
        parser = detect(header)
        if parser is None:
            skipped.append(path)
            continue
        txns = parser.parse(path, rows)
        if covered and txns and {t.account for t in txns} <= covered:
            report.append((path, parser.name + " (the bank feed instead)", len(txns), 0))
            continue
        batches.append((path, parser.name, txns))
    if feed:
        batches.append((Path("bank feed"), "bank_feed", list(feed)))
    for path, name, txns in batches:
        seen_in_file, added = Counter(), 0
        for t in txns:
            k = t.key()
            seen_in_file[k] += 1
            t.assign_id(seen_in_file[k])
            if (start and t.date < start) or (end and t.date > end) or t.txn_id in kept:
                continue
            kept[t.txn_id] = t
            added += 1
        report.append((path, name, len(txns), added))
    ordered = sorted(kept.values(), key=lambda t: (t.date, t.account, t.txn_id))
    return ordered, report, skipped


def write_csv(txns: List[Transaction], out: Path) -> None:
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(COLUMNS)
        for t in txns:
            w.writerow(["" if getattr(t, c) is None else getattr(t, c) for c in COLUMNS])


def summarize(txns: List[Transaction]) -> str:
    by_account = defaultdict(lambda: [0, Decimal("0"), Decimal("0")])
    for t in txns:
        row = by_account[t.account]
        row[0] += 1
        if t.amount > 0:
            row[1] += t.amount
        else:
            row[2] += t.amount
    lines = [f"{'account':<18}{'rows':>6}{'money in':>13}{'money out':>13}{'net':>13}"]
    for account in sorted(by_account):
        n, inflow, outflow = by_account[account]
        lines.append(f"{account:<18}{n:>6}{inflow:>13,.2f}{outflow:>13,.2f}{inflow + outflow:>13,.2f}")
    kinds = Counter(t.kind for t in txns)
    lines.append("")
    lines.append("kinds: " + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items())))
    if txns:
        lines.append(f"dates: {txns[0].date} to {txns[-1].date}")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Normalize exports into one transaction table.")
    ap.add_argument("folder")
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    ap.add_argument("--out", default="data/ledger/transactions.csv")
    args = ap.parse_args()

    txns, report, skipped = load_folder(args.folder, args.start, args.end)
    for path, name, parsed, added in report:
        print(f"  {name:<20} {path.name:<40} {parsed:>5} parsed {added:>5} new")
    for path in skipped:
        print(f"  {'(not recognized)':<20} {path.name}")
    print()
    print(summarize(txns))
    write_csv(txns, Path(args.out))
    print(f"\nwrote {len(txns)} transactions to {args.out}")


if __name__ == "__main__":
    main()
