"""Parser registry. Each parser module exposes NAME, detect(header) and parse(path, rows)."""
from __future__ import annotations

import csv
from pathlib import Path
from typing import Dict, List, Tuple

from . import amex, chase_card, chase_checking, lyft, turo, uber

PARSERS = [
    uber.TRIPS, uber.PAYOUTS, uber.OTHER,
    lyft.TRIPS,
    turo.EARNINGS,
    chase_checking.ACTIVITY, chase_card.ACTIVITY,
    amex.ACTIVITY,
]


def read_csv(path: Path) -> Tuple[List[str], List[Tuple[int, Dict[str, str]]]]:
    """Header plus (row number, row) pairs. Row numbers match a spreadsheet view (header = 1)."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        header = [h.strip() for h in (reader.fieldnames or [])]
        rows = []
        for i, row in enumerate(reader, start=2):
            clean = {(k or "").strip(): (v or "").strip() if isinstance(v, str) else "" for k, v in row.items()}
            rows.append((i, clean))
    return header, rows


def detect(header: List[str]):
    cols = set(header)
    for p in PARSERS:
        if p.detect(cols):
            return p
    return None
