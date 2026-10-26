"""What each ride really paid: the fare minus what the car spent to earn it.

    python3 -m car.profit samples/october

Each accepted offer is tied to the two drives that served it, found by time in the Tessie log: the drive to
the pickup, then the trip. The miles to the pickup are unpaid, so they're charged to the ride. Costs come from
the owner's vehicle profile: electricity (home and Supercharger, blended), depreciation per mile, and tire
wear per mile. Fares are the actual earnings in the platform export, tips included.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from car.telemetry import Drive, Offer, load_drives, load_offers, load_profile
from ingest.load import load_folder

PICKUP_STARTS_WITHIN = timedelta(hours=2)      # a ride accepted mid-trip starts after the current drop-off
TRIP_STARTS_WITHIN = timedelta(minutes=15)     # the trip starts soon after the pickup drive ends
IRS_RATE_2026 = 0.725                          # standard mileage rate, dollars per mile


def _close(miles: float, expected: float, floor: float, share: float) -> bool:
    return abs(miles - expected) <= max(floor, share * expected)


def link(offers: List[Offer], drives: List[Drive]) -> Dict[str, Tuple[Drive, Optional[Drive]]]:
    """offer id -> (pickup drive, trip drive or None if the ride was cancelled).

    Drives carry no trip ids, so each accepted offer is matched in time order to the first unused drive after it
    whose length fits the offer's pickup distance, then to the drive right after that fits the trip distance.
    Drivers often accept the next ride before dropping off the current one, so the pickup can start well after
    the offer."""
    used, out = set(), {}
    for o in sorted((o for o in offers if o.accepted), key=lambda o: o.at):
        lo, hi = o.at - timedelta(minutes=2), o.at + PICKUP_STARTS_WITHIN
        pickup = next((d for d in drives if d.id not in used and lo <= d.start <= hi
                       and _close(d.miles, o.pickup_miles, 0.3, 0.25)), None)
        if pickup is None:
            continue
        used.add(pickup.id)
        trip = next((d for d in drives if d.id not in used and pickup.end <= d.start <= pickup.end + TRIP_STARTS_WITHIN
                     and _close(d.miles, o.trip_miles, 0.5, 0.2)), None)
        if trip is not None:
            used.add(trip.id)
        out[o.id] = (pickup, trip)
    return out


def costs_per(profile: Dict) -> Dict[str, float]:
    share = profile.get("share_of_energy_from_supercharging", 0.25)
    return {
        "kwh": profile.get("home_electricity_per_kwh", 0.14) * (1 - share) + profile.get("supercharger_per_kwh", 0.42) * share,
        "depreciation_mile": profile.get("depreciation_per_mile", 0.20),
        "tire_mile": profile.get("tire_set_cost", 1100) / profile.get("tire_life_miles", 40000),
        "target_hour": profile.get("target_hourly_rate", 20.0),
    }


def ride_profits(month_dir: Path, txns=None) -> List[Dict]:
    month_dir = Path(month_dir)
    if txns is None:
        txns, _, _ = load_folder(month_dir)
    fares = defaultdict(lambda: Decimal("0"))
    for t in txns:
        if t.kind == "earning" and t.source in ("uber", "lyft") and t.ref:
            fares[t.ref] += t.amount
    offers, drives = load_offers(month_dir), load_drives(month_dir)
    c = costs_per(load_profile(month_dir) or {})
    rides = []
    links = link(offers, drives)
    for o in sorted((o for o in offers if o.accepted), key=lambda o: o.at):
        if o.id not in links:
            continue
        pickup, trip = links[o.id]
        legs = [d for d in (pickup, trip) if d is not None]
        miles = sum(d.miles for d in legs)
        kwh = sum(d.kwh for d in legs)
        # From heading to the pickup to dropping the rider off. Time before the pickup drive belongs to the
        # ride the car was still finishing when this one was accepted.
        minutes = (legs[-1].end - pickup.start).total_seconds() / 60
        fare = float(fares.get(o.trip_id, Decimal(str(o.fare))))
        energy = kwh * c["kwh"]
        depreciation = miles * c["depreciation_mile"]
        tires = miles * c["tire_mile"]
        profit = fare - energy - depreciation - tires
        hours = minutes / 60 if minutes > 0 else None
        rides.append({
            "offer": o.id, "trip": o.trip_id, "platform": o.platform, "at": o.at.isoformat(sep=" "),
            "cancelled": trip is None,
            "fare": round(fare, 2), "pickup_miles": round(pickup.miles, 2), "trip_miles": round(trip.miles, 2) if trip else 0.0,
            "miles": round(miles, 2), "kwh": round(kwh, 2), "minutes": round(minutes, 1),
            "energy": round(energy, 2), "depreciation": round(depreciation, 2), "tires": round(tires, 2),
            "true_profit": round(profit, 2), "per_hour": round(profit / hours, 2) if hours else None,
            "fare_per_hour": round(fare / hours, 2) if hours else None,
            "unpaid_share_of_miles": round(pickup.miles / miles, 3) if miles else 0.0,
            "irs_rate_covers": round(miles * IRS_RATE_2026, 2),
        })
    return rides


def summary(rides: List[Dict], profile: Optional[Dict] = None) -> Dict:
    if not rides:
        return {}
    c = costs_per(profile or {})
    def agg(rs):
        hours = sum(r["minutes"] for r in rs) / 60
        fare = sum(r["fare"] for r in rs)
        profit = sum(r["true_profit"] for r in rs)
        return {"rides": len(rs), "fare": round(fare, 2), "true_profit": round(profit, 2),
                "costs": round(fare - profit, 2), "hours": round(hours, 1),
                "per_hour": round(profit / hours, 2) if hours else None,
                "miles": round(sum(r["miles"] for r in rs), 1), "pickup_miles": round(sum(r["pickup_miles"] for r in rs), 1)}
    far = [r for r in rides if r["pickup_miles"] > 3]
    near = [r for r in rides if r["pickup_miles"] <= 3]
    by_platform = {p: agg([r for r in rides if r["platform"] == p]) for p in sorted({r["platform"] for r in rides})}
    ranked = sorted((r for r in rides if not r["cancelled"]), key=lambda r: r["per_hour"] or 0, reverse=True)
    cost_parts = {k: round(sum(r[k] for r in rides), 2) for k in ("energy", "depreciation", "tires")}
    return {"all": agg(rides), "by_platform": by_platform, "cost_parts": cost_parts,
            "cancelled": sum(1 for r in rides if r["cancelled"]),
            "pickup_over_3_miles": agg(far) if far else None, "pickup_3_miles_or_less": agg(near) if near else None,
            "below_target": sum(1 for r in rides if (r["per_hour"] or 0) < c["target_hour"]), "target_per_hour": c["target_hour"],
            "costs_per_mile": round(c["depreciation_mile"] + c["tire_mile"], 4), "electricity_per_kwh": round(c["kwh"], 3),
            "best": ranked[:3], "worst": ranked[-3:]}


def main():
    ap = argparse.ArgumentParser(description="True profit per ride.")
    ap.add_argument("month", nargs="?", default="samples/october")
    args = ap.parse_args()
    month = Path(args.month)
    rides = ride_profits(month)
    s = summary(rides, load_profile(month))
    a = s["all"]
    print(f"  {a['rides']} rides: fares ${a['fare']:,.2f}, car costs ${a['costs']:,.2f}, true profit ${a['true_profit']:,.2f}, "
          f"{a['hours']} hours, ${a['per_hour']}/hour")
    for name in ("pickup_over_3_miles", "pickup_3_miles_or_less"):
        g = s[name]
        if g:
            print(f"  {name:<24} {g['rides']:>3} rides, ${g['per_hour']}/hour true profit, {g['pickup_miles']} pickup miles")
    print(f"  below the ${s['target_per_hour']:.0f}/hour target: {s['below_target']} rides")
    for r in s["best"]:
        print(f"  best  {r['at']} {r['platform']:<5} fare ${r['fare']:>6} true ${r['true_profit']:>6} ${r['per_hour']}/h")
    for r in s["worst"]:
        print(f"  worst {r['at']} {r['platform']:<5} fare ${r['fare']:>6} true ${r['true_profit']:>6} ${r['per_hour']}/h pickup {r['pickup_miles']} mi")


if __name__ == "__main__":
    sys.exit(main())
