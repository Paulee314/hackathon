"""Replay a ride-acceptance rule against the month's offers: what would October have paid?

    python3 -m car.rules samples/october

A rule is a few thresholds a driver can say in one sentence: skip pickups over N miles, only fares of at
least $X, at least $Y a mile, at least $Z an hour after car costs, only some platforms or hours.

A rule sits on top of the driver's own judgment. The replay first drops the rides the driver took that the
rule would have skipped; the rides it keeps stay exactly where they were, with their real fare, tip, time
and costs. Then, in the time that frees up, it takes offers the driver passed on that fit the rule and fit
in the gap (platforms queue a ride near the end of the current one, so the car can leave when it's free).
Those use the numbers on the offer card, the car's average cost per mile and the month's average tip rate.
With the baseline rule (what the driver did), the replay is exactly the real month.

Time on the road is counted from the real month in stretches: a gap of more than 30 minutes between rides
and offers counts as off. Every rule gets the same stretches, so rules are compared by true profit per
hour on the road.

Advice only. Nothing here accepts or declines a ride; the platforms' terms leave that to the driver.
"""
from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from car.profit import costs_per, ride_profits
from car.telemetry import Offer, load_drives, load_offers, load_profile

QUEUE = timedelta(minutes=20)   # a passed-on offer can be taken if it would start within this long
OFF_AFTER = timedelta(minutes=30)  # a longer gap between rides and offers counts as time off


@dataclass
class Rule:
    name: str
    max_pickup_miles: Optional[float] = None
    min_fare: Optional[float] = None
    min_fare_per_mile: Optional[float] = None      # fare / (pickup + trip miles)
    min_profit_per_hour: Optional[float] = None    # estimated true profit per hour, from the offer card
    platforms: Optional[List[str]] = None          # e.g. ["uber"]
    hours: Optional[Tuple[int, int]] = None        # take offers only from this hour up to (not including) that one
    as_driven: bool = False                        # the baseline: what the driver actually did

    def check(self, o: Offer, est: Dict) -> Tuple[bool, str]:
        if self.as_driven:
            return o.accepted, "you took it" if o.accepted else "you passed"
        if self.platforms and o.platform not in self.platforms:
            return False, f"not {' or '.join(p.title() for p in self.platforms)}"
        if self.hours and not (self.hours[0] <= o.at.hour < self.hours[1]):
            return False, "outside your hours"
        if self.max_pickup_miles is not None and o.pickup_miles > self.max_pickup_miles:
            return False, f"pickup {o.pickup_miles:.1f} mi"
        if self.min_fare is not None and o.fare < self.min_fare:
            return False, f"fare ${o.fare:.2f}"
        if self.min_fare_per_mile is not None and est["fare_per_mile"] < self.min_fare_per_mile:
            return False, f"${est['fare_per_mile']:.2f} a mile"
        if self.min_profit_per_hour is not None and est["card_per_hour"] < self.min_profit_per_hour:
            return False, f"${est['card_per_hour']:.0f}/hr after costs"
        return True, "fits"

    def describe(self) -> str:
        if self.as_driven:
            return "What you did"
        parts = []
        if self.max_pickup_miles is not None:
            parts.append(f"skip pickups over {self.max_pickup_miles:g} miles")
        if self.min_fare is not None:
            parts.append(f"only fares of ${self.min_fare:g} or more")
        if self.min_fare_per_mile is not None:
            parts.append(f"at least ${self.min_fare_per_mile:.2f} a mile")
        if self.min_profit_per_hour is not None:
            parts.append(f"at least ${self.min_profit_per_hour:g} an hour after car costs")
        if self.platforms:
            parts.append("only " + " and ".join(p.title() for p in self.platforms))
        if self.hours:
            parts.append(f"only {_clock(self.hours[0])} to {_clock(self.hours[1])}")
        text = ", ".join(parts) or "take every offer"
        return text[:1].upper() + text[1:]


def _clock(hour: int) -> str:
    hour %= 24
    return "midnight" if hour == 0 else "noon" if hour == 12 else f"{hour % 12} {'AM' if hour < 12 else 'PM'}"


PRESETS = [
    Rule("What you did", as_driven=True),
    Rule("Skip pickups over 3 miles", max_pickup_miles=3),
    Rule("Skip pickups over 2 miles", max_pickup_miles=2),
    Rule("At least $1 a mile", min_fare_per_mile=1.0),
    Rule("At least $20 an hour after car costs", min_profit_per_hour=20),
    Rule("Take every offer"),
]


def offer_estimates(month_dir: Path, txns=None) -> Tuple[List[Offer], Dict[str, Dict], Dict]:
    """Every offer with what it would earn, from the real ride if it was driven, else from its card."""
    month_dir = Path(month_dir)
    offers, drives = load_offers(month_dir), load_drives(month_dir)
    profile = load_profile(month_dir) or {}
    c = costs_per(profile)
    actual = {r["offer"]: r for r in ride_profits(month_dir, txns)}
    kwh_per_mile = sum(d.kwh for d in drives) / max(1e-9, sum(d.miles for d in drives))
    per_mile = kwh_per_mile * c["kwh"] + c["depreciation_mile"] + c["tire_mile"]
    driven = [r for r in actual.values() if not r["cancelled"]]
    card_fares = {o.id: o.fare for o in offers}
    fare_sum = sum(card_fares[r["offer"]] for r in driven)
    tip_rate = (sum(r["fare"] for r in driven) - fare_sum) / fare_sum if fare_sum else 0.0
    est = {}
    for o in offers:
        miles = o.pickup_miles + o.trip_miles
        minutes = o.pickup_minutes + o.trip_minutes
        card_profit = o.fare - miles * per_mile
        e = {"fare_per_mile": o.fare / miles if miles else 0.0,
             "card_per_hour": card_profit / (minutes / 60) if minutes else 0.0}
        if o.id in actual:
            r = actual[o.id]
            e.update(real=True, fare=r["fare"], profit=r["true_profit"], minutes=r["minutes"],
                     pickup_miles=r["pickup_miles"], miles=r["miles"],
                     start=datetime.fromisoformat(r["start"]), end=datetime.fromisoformat(r["end"]))
        else:
            fare = o.fare * (1 + tip_rate)
            e.update(real=False, fare=round(fare, 2), profit=round(fare - miles * per_mile, 2), minutes=round(minutes, 1),
                     pickup_miles=o.pickup_miles, miles=round(miles, 2))
        est[o.id] = e
    model = {"cost_per_mile": round(per_mile, 4), "tip_rate": round(tip_rate, 4), "kwh_per_mile": round(kwh_per_mile, 4)}
    return offers, est, model


def online_hours(offers: List[Offer], est: Dict[str, Dict]) -> float:
    """Hours on the road in the real month: offers and rides merged into stretches, a gap over 30 minutes is off."""
    spans = []
    for o in offers:
        e = est[o.id]
        spans.append((o.at, e["end"]) if e.get("end") else (o.at, o.at))
    spans.sort()
    total, cur_start, cur_end = timedelta(0), None, None
    for a, b in spans:
        if cur_end is not None and a - cur_end <= OFF_AFTER:
            cur_end = max(cur_end, b)
            continue
        if cur_end is not None:
            total += cur_end - cur_start
        cur_start, cur_end = a, b
    if cur_end is not None:
        total += cur_end - cur_start
    return total.total_seconds() / 3600


def replay(rule: Rule, offers: List[Offer], est: Dict[str, Dict], hours: float) -> Dict:
    ordered = sorted(offers, key=lambda o: o.at)
    blocks: List[Tuple[datetime, datetime]] = []
    taken, decisions = [], {}
    # 1. The rides you drove that the rule keeps stay where they were.
    for o in ordered:
        e = est[o.id]
        if not e["real"]:
            continue
        ok, why = rule.check(o, e)
        decisions[o.id] = ("take" if ok else "skip", why)
        if ok:
            blocks.append((e["start"], e["end"]))
            taken.append(o)
    # 2. Offers you passed on that fit the rule, if the car was free for them.
    for o in ordered:
        e = est[o.id]
        if e["real"]:
            continue
        ok, why = rule.check(o, e)
        if not ok:
            decisions[o.id] = ("skip", why)
            continue
        start, moved = o.at, True
        while moved:  # wait out any ride the car is on when the offer arrives
            moved = False
            for s, en in blocks:
                if s <= start < en:
                    start, moved = en, True
        end = start + timedelta(minutes=e["minutes"])
        if start - o.at > QUEUE or any(s < end and start < en for s, en in blocks):
            decisions[o.id] = ("busy", "on another ride")
            continue
        blocks.append((start, end))
        taken.append(o)
        decisions[o.id] = ("take", why)
    profit = sum(est[o.id]["profit"] for o in taken)
    fare = sum(est[o.id]["fare"] for o in taken)
    return {"rule": rule.name, "describe": rule.describe(), "rides": len(taken),
            "fare": round(fare, 2), "true_profit": round(profit, 2),
            "per_online_hour": round(profit / hours, 2) if hours else None,
            "pickup_miles": round(sum(est[o.id]["pickup_miles"] for o in taken), 1),
            "estimated_rides": sum(1 for o in taken if not est[o.id]["real"]),
            "decisions": decisions}


def compare(rule: Rule, month_dir: Path, txns=None, cache=None) -> Dict:
    """The rule against what the driver actually did, over the same online hours."""
    offers, est, model = cache or offer_estimates(month_dir, txns)
    hours = online_hours(offers, est)
    base = replay(PRESETS[0], offers, est, hours)
    mine = replay(rule, offers, est, hours)
    skipped_real = [o for o in offers if base["decisions"][o.id][0] == "take" and mine["decisions"][o.id][0] != "take"]
    added = [o for o in offers if base["decisions"][o.id][0] != "take" and mine["decisions"][o.id][0] == "take"]
    return {"rule": asdict(rule), "describe": rule.describe(), "online_hours": round(hours, 1), "model": model,
            "as_driven": {k: v for k, v in base.items() if k != "decisions"},
            "with_rule": {k: v for k, v in mine.items() if k != "decisions"},
            "difference": round(mine["true_profit"] - base["true_profit"], 2),
            "rides_you_took_it_skips": len(skipped_real),
            "profit_of_rides_it_skips": round(sum(est[o.id]["profit"] for o in skipped_real), 2),
            "offers_you_passed_it_takes": len(added),
            "profit_of_offers_it_takes": round(sum(est[o.id]["profit"] for o in added), 2),
            "hours_it_frees": round(sum(est[o.id]["minutes"] for o in skipped_real) / 60, 1),
            "pickup_miles_it_saves": round(sum(est[o.id]["pickup_miles"] for o in skipped_real), 1),
            "skipped_rides_per_hour": round(sum(est[o.id]["profit"] for o in skipped_real)
                                            / (sum(est[o.id]["minutes"] for o in skipped_real) / 60), 2) if skipped_real else None}


def rule_from_dict(d: Dict) -> Rule:
    def num(k):
        v = d.get(k)
        try:
            return None if v in (None, "", "null") else float(v)
        except (TypeError, ValueError):
            return None
    hours = d.get("hours")
    hours = (int(hours[0]), int(hours[1])) if isinstance(hours, (list, tuple)) and len(hours) == 2 else None
    platforms = [p.lower() for p in d.get("platforms") or [] if str(p).lower() in ("uber", "lyft")] or None
    r = Rule(name=str(d.get("name") or "Your rule")[:80], max_pickup_miles=num("max_pickup_miles"), min_fare=num("min_fare"),
             min_fare_per_mile=num("min_fare_per_mile"), min_profit_per_hour=num("min_profit_per_hour"),
             platforms=platforms, hours=hours)
    return r


def export(month: Path) -> Dict:
    """Every offer, the numbers behind it, and each preset rule's decision: the data for the offers sheet."""
    offers, est, model = offer_estimates(month)
    hours = online_hours(offers, est)
    profile = load_profile(month) or {}
    runs = {r.name: replay(r, offers, est, hours) for r in PRESETS}
    rows = []
    for o in sorted(offers, key=lambda o: o.at):
        e = est[o.id]
        rows.append({"id": o.id, "at": o.at.isoformat(sep=" "), "platform": o.platform, "pickup_miles": o.pickup_miles,
                     "pickup_minutes": o.pickup_minutes, "trip_miles": o.trip_miles, "trip_minutes": o.trip_minutes,
                     "fare": o.fare, "accepted": o.accepted,
                     "actual_fare": e["fare"] if e["real"] else None, "actual_profit": e["profit"] if e["real"] else None,
                     "decisions": {name: run["decisions"][o.id][0] for name, run in runs.items()}})
    drives = load_drives(month)
    return {"rows": rows, "presets": [{"name": r.name, "describe": r.describe()} for r in PRESETS],
            "totals": {name: {"rides": run["rides"], "true_profit": run["true_profit"]} for name, run in runs.items()},
            "hours_on_road": hours, "profile": profile,
            "kwh_per_mile": sum(d.kwh for d in drives) / sum(d.miles for d in drives), "model": model,
            "tip_rate_exact": _tip_rate(offers, est), "queue_minutes": QUEUE.seconds // 60, "off_after_minutes": OFF_AFTER.seconds // 60}


def _tip_rate(offers, est) -> float:
    real = [o for o in offers if est[o.id]["real"] and est[o.id]["miles"] > est[o.id]["pickup_miles"]]
    card = sum(o.fare for o in real)
    return (sum(est[o.id]["fare"] for o in real) - card) / card if card else 0.0


def main():
    ap = argparse.ArgumentParser(description="Replay ride rules against the month's offers.")
    ap.add_argument("month", nargs="?", default="samples/october")
    ap.add_argument("--export", help="write every offer and each rule's decision to this JSON file")
    args = ap.parse_args()
    month = Path(args.month)
    if args.export:
        import json
        Path(args.export).write_text(json.dumps(export(month), indent=1))
        print(f"wrote {args.export}")
        return
    cache = offer_estimates(month)
    offers, est, model = cache
    hours = online_hours(offers, est)
    print(f"  {len(offers)} offers, {sum(o.accepted for o in offers)} taken, {hours:.1f} online hours; "
          f"cost ${model['cost_per_mile']}/mi, tips +{model['tip_rate']:.1%}")
    for rule in PRESETS:
        r = replay(rule, offers, est, hours)
        print(f"  {rule.name:<38} {r['rides']:>3} rides  true profit ${r['true_profit']:>8,.2f}  "
              f"${r['per_online_hour']:>6}/online hr  ({r['estimated_rides']} estimated from offer cards)")


if __name__ == "__main__":
    sys.exit(main())
