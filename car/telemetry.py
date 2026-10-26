"""Read car telemetry (Tessie drives, tire pressure) and ride offers from a month folder.

Files are found by their columns, like the ledger exports. Nothing here is a ledger row:
these feed true profit per ride, the tire alert and the ride-rule replay.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from ingest.parsers import read_csv

DRIVES = frozenset({"starting_odometer", "ending_odometer", "energy_used_kwh"})
TIRES = frozenset({"front_left_psi", "rear_left_psi"})
OFFERS = frozenset({"offer_id", "pickup_miles", "decision"})


@dataclass
class Drive:
    id: str
    start: datetime
    end: datetime
    miles: float
    kwh: float
    start_odometer: float
    end_odometer: float
    frm: str
    to: str


@dataclass
class TireReading:
    at: datetime
    front_left: float
    front_right: float
    rear_left: float
    rear_right: float

    @property
    def average(self) -> float:
        return (self.front_left + self.front_right + self.rear_left + self.rear_right) / 4

    @property
    def lowest(self):
        name, psi = min(
            (("front left", self.front_left), ("front right", self.front_right),
             ("rear left", self.rear_left), ("rear right", self.rear_right)),
            key=lambda p: p[1])
        return name, psi


@dataclass
class Offer:
    id: str
    platform: str
    at: datetime
    pickup_miles: float
    pickup_minutes: float
    trip_miles: float
    trip_minutes: float
    fare: float
    accepted: bool
    trip_id: str


def _find(folder, signature) -> List[Path]:
    out = []
    for p in sorted(Path(folder).rglob("*.csv")):
        header, _ = read_csv(p)
        if signature <= set(header):
            out.append(p)
    return out


def _ts(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%d %H:%M:%S")


def load_drives(folder) -> List[Drive]:
    drives = []
    for path in _find(folder, DRIVES):
        _, rows = read_csv(path)
        for _, r in rows:
            drives.append(Drive(
                id=r["id"], start=_ts(r["start_local"]), end=_ts(r["end_local"]),
                miles=float(r["miles"] or 0), kwh=float(r["energy_used_kwh"] or 0),
                start_odometer=float(r["starting_odometer"]), end_odometer=float(r["ending_odometer"]),
                frm=r.get("starting_location", ""), to=r.get("ending_location", "")))
    return sorted(drives, key=lambda d: d.start)


def load_tires(folder) -> List[TireReading]:
    readings = []
    for path in _find(folder, TIRES):
        _, rows = read_csv(path)
        for _, r in rows:
            readings.append(TireReading(
                at=_ts(r["timestamp"]), front_left=float(r["front_left_psi"]),
                front_right=float(r["front_right_psi"]), rear_left=float(r["rear_left_psi"]),
                rear_right=float(r["rear_right_psi"])))
    return sorted(readings, key=lambda t: t.at)


def load_offers(folder) -> List[Offer]:
    offers = []
    for path in _find(folder, OFFERS):
        _, rows = read_csv(path)
        for _, r in rows:
            offers.append(Offer(
                id=r["offer_id"], platform=r["platform"], at=_ts(r["offered_at"]),
                pickup_miles=float(r["pickup_miles"]), pickup_minutes=float(r["pickup_minutes"]),
                trip_miles=float(r["trip_miles"]), trip_minutes=float(r["trip_minutes"]),
                fare=float(r["offered_fare"]), accepted=r["decision"] == "accepted", trip_id=r["trip_id"]))
    return sorted(offers, key=lambda o: o.at)


def load_profile(folder) -> Optional[Dict]:
    for p in sorted(Path(folder).rglob("vehicle_profile.json")):
        return json.loads(p.read_text())
    return None
