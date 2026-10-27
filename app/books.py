"""Everything the phone app shows, computed in code from the month's files.

Totals, the review queue, the tire story, ride stats and the facts Muse Spark may use when it
answers a question all come from here, so every number on screen can be traced to a row.
Expense groups use plain merchant rules for now; the categorizer replaces them with
Schedule C lines.
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

from agents import close as close_agent
from agents.categorize import merchant_key
from agents.receipts import assess
from agents.voice import to_transaction
from car import rules as ride_rules
from car.profit import ride_profits, summary as ride_summary
from car.telemetry import load_drives, load_offers, load_profile, load_tires
from ingest.load import load_folder
from ingest.schema import Transaction

ROOT = Path(__file__).resolve().parents[1]
ZERO = Decimal("0")
CENT = Decimal("0.01")

PLATFORM = {"uber": "Uber", "lyft": "Lyft", "turo": "Turo"}
EXPENSE_RULES = [
    (("SUPERCHARGER", "CHARGING"), "Charging"),
    (("CAR WASH", "DETAILPRO", "WATER"), "Cleaning and supplies"),
    (("PARKHOUSTON", "PARKING", "HCTRA", "EZ TAG", "TOLL"), "Parking and tolls"),
    (("T-MOBILE", "VERIZON", "AT&T"), "Phone"),
    (("OPENAI", "GOOGLE", "APPLE.COM"), "Software"),
    (("HISCOX", "PROGRESSIVE", "INSURANCE"), "Insurance"),
    (("SERVICE FEE",), "Bank fees"),
]
CARD_PAYMENT_WORDS = ("CREDIT CRD", "EPAYMENT", "AUTOPAY", "AUTOMATIC PAYMENT")
OWNER_TRANSFER_WORDS = ("ONLINE TRANSFER",)
LOAN_WORDS = ("TESLA FINANCE",)
PLATFORM_WORDS = ("UBER", "LYFT", "TURO")
SECONDS_PER_ITEM = 40


def money(d) -> str:
    d = Decimal(str(d)).quantize(CENT)
    return ("-" if d < 0 else "") + "${:,.2f}".format(abs(d))


def money0(d) -> str:
    d = Decimal(str(d)).quantize(Decimal("1"))
    return ("-" if d < 0 else "") + "${:,}".format(abs(int(d)))


def short_date(iso: str) -> str:
    d = date.fromisoformat(iso)
    return d.strftime("%b ") + str(d.day)


def span(a: date, b: date) -> str:
    if a == b:
        return f"{a.strftime('%b')} {a.day}"
    if a.month == b.month:
        return f"{a.strftime('%b')} {a.day}–{b.day}"
    return f"{a.strftime('%b')} {a.day}–{b.strftime('%b')} {b.day}"


def account_name(account: str) -> str:
    if account.startswith("chase_ink"):
        return "Chase Ink card"
    if account.startswith("amex"):
        return "Amex card"
    if account.startswith("chase_"):
        return "Chase checking"
    if account == "cash_on_hand":
        return "Cash"
    return account.replace("_", " ").title()


def classify(t: Transaction) -> Tuple[str, str]:
    """(role, group). role is income, expense, transfer, loan, owner or unexplained."""
    desc = f"{t.description} {t.counterparty}".upper()
    if t.kind == "earning":
        return "income", "Contract work" if t.business_line == "contract_work" else PLATFORM.get(t.source, t.source)
    if t.kind == "payout":
        return "transfer", "Platform payouts"
    if t.kind == "fee":
        return "expense", "Platform fees"
    if t.amount > 0:
        if t.kind == "card":
            return "transfer", "Card payments"
        if any(w in desc for w in PLATFORM_WORDS):
            return "transfer", "Platform deposits"
        return "unexplained", "Money in, not yet explained"
    if any(w in desc for w in CARD_PAYMENT_WORDS):
        return "transfer", "Card payments"
    if any(w in desc for w in OWNER_TRANSFER_WORDS):
        return "owner", "Moved to your personal account"
    if any(w in desc for w in LOAN_WORDS):
        return "loan", "Car loan payments"
    for words, group in EXPENSE_RULES:
        if any(w in desc for w in words):
            return "expense", group
    return "expense", "Cash spending" if t.kind == "cash" else "Other"


def _quote(text: str) -> str:
    """A transcript tidied for quoting: capitalized, one closing period."""
    t = text.strip().rstrip(".,; ")
    return (t[:1].upper() + t[1:] + ".") if t else ""


def _first_existing(*paths: Path) -> Optional[Path]:
    for p in paths:
        if p.exists():
            return p
    return None


class Books:
    """One month, as the app sees it. Rebuilt whenever app state changes (it is cheap)."""

    def __init__(self, month_dir: Path, today: date, state: Dict, receipts_from_photo: bool = False):
        self.month_dir = Path(month_dir)
        self.today = today
        self.state = state
        # True in the app: receipts enter the books only when the driver snaps them (SAM 3.1 finds
        # each slip, Muse Spark reads it). False reads them from the batch run or the test fixture.
        self.receipts_from_photo = receipts_from_photo
        self.txns, _, _ = load_folder(self.month_dir)
        self.first_day = date.fromisoformat(min(t.date for t in self.txns))
        self.last_day = date.fromisoformat(max(t.date for t in self.txns))
        self.receipts = self._receipts()
        self.voice = self._voice()
        self.drives = load_drives(self.month_dir)
        self.tires = load_tires(self.month_dir)
        self.offers = load_offers(self.month_dir)
        self.profile = load_profile(self.month_dir) or {}
        self.close = close_agent.run(self.txns, self.month_dir)
        path = _first_existing(ROOT / "data" / "ledger" / "categories.json", ROOT / "tests" / "fixtures" / "categories.json")
        self.categories = json.loads(path.read_text()) if path else {}
        self._rides = None
        self._items = None
        self._offers = None

    # ---- inputs -------------------------------------------------------------------------
    def _receipts(self) -> List[Dict]:
        if self.receipts_from_photo:
            rows = (self.state.get("pile") or {}).get("receipts") or []
            return [{"file": r["file"], "extracted": r["extracted"], **assess(r["extracted"], self.txns)}
                    for r in rows if r.get("extracted")]
        path = _first_existing(ROOT / "data" / "ledger" / "receipts.json",
                               ROOT / "tests" / "fixtures" / "receipts_extracted.json")
        if path is None:
            return []
        return [{"file": r["file"], "extracted": r["extracted"], **assess(r["extracted"], self.txns)}
                for r in json.loads(path.read_text())]

    def _voice(self) -> List[Dict]:
        path = _first_existing(ROOT / "data" / "ledger" / "voice.json", ROOT / "tests" / "fixtures" / "voice_parsed.json")
        notes = json.loads(path.read_text()) if path else []
        overrides = self.state.get("amount_overrides", {})
        for n in notes:
            parsed = dict(n["parsed"])
            key = "voice:" + Path(n["file"]).stem
            if key in overrides:
                parsed["amount"] = overrides[key]
                parsed["assumption"] = None
            if (self.state.get("decisions", {}).get(key) or {}).get("choice") == "book":
                parsed["question"] = None
            n["parsed_effective"] = parsed
            n["txn"] = to_transaction(parsed, n["file"])
        return notes

    def app_entries(self) -> List[Dict]:
        return [e for e in self.state.get("entries", []) if not e.get("undone")]

    def cash_txns(self) -> List[Transaction]:
        out = [n["txn"] for n in self.voice if n["txn"] is not None]
        for e in self.app_entries():
            t = to_transaction(e["parsed"], e["id"] + ".wav")
            if t is not None:
                t.evidence = "app/" + e["id"]
                t.source = "voice" if e.get("spoken") else "typed"
                out.append(t)
        return out

    def all_txns(self) -> List[Transaction]:
        return self.txns + self.cash_txns()

    # ---- review queue -------------------------------------------------------------------
    def review_items(self) -> List[Dict]:
        if self._items is not None:
            return self._items
        items = []
        holding = {"possible_duplicate", "deposit_without_payout", "unidentified_income"}
        for e in sorted(self.close["exceptions"], key=lambda e: e["date"]):
            first = e["accounts"][0]
            where = PLATFORM.get(first.split("_")[0], account_name(first)) if first.endswith("_balance") else account_name(first)
            items.append({
                "id": e["id"], "source": "agent", "type": e["type"], "check": e["check"], "amount": e["at_stake"],
                "tag": f"{where}, {short_date(e['date'])}", "title": e["title"], "body": e["body"], "rows": e["rows"],
                "suggestion": e["suggestion"], "choices": e["choices"], "evidence": {},
                "found_by": "Found by the close agent's checks", "holds": e["txn_ids"] if e["type"] in holding else [],
            })
        for r in self.receipts:
            if not r["flags"]:
                continue
            e, m = r["extracted"], r["match"]
            stem = Path(r["file"]).stem
            merchant = (e.get("merchant") or "This purchase").title()
            amount = Decimal(str(e.get("total", 0))).quantize(CENT)
            lines = [i.get("description", "") for i in e.get("items", [])]
            listed = ", ".join(lines[:3]) + (f" and {len(lines) - 3} more" if len(lines) > 3 else "")
            if e.get("kind") in ("personal", "mixed"):
                where = account_name(m["account"]) if m else "card"
                items.append({
                    "id": "receipt:" + stem, "source": "receipt", "amount": str(amount),
                    "tag": f"{where}, {short_date(m['date'] if m else e['date'])}",
                    "title": f"{merchant} looks personal",
                    "body": f"{e.get('reason', '').rstrip('.')}. On the receipt: {listed}.",
                    "rows": [[f"Charge on the {where}", money(amount)], ["Receipt total", money(amount)]],
                    "suggestion": {"choice": "personal", "label": "Keep it out of the business books"},
                    "choices": [{"choice": "personal", "label": "It's personal"},
                                {"choice": "business", "label": "It's business"}],
                    "evidence": {"receipt": r["file"]}, "found_by": "Muse Spark read the receipt",
                    "holds": [m["txn_id"]] if m else [],
                })
            else:
                items.append({
                    "id": "receipt:" + stem, "source": "receipt", "amount": str(amount),
                    "tag": f"Receipt, {short_date(e['date'])}", "title": f"{merchant} receipt needs a look",
                    "body": "; ".join(r["flags"]).capitalize() + ".", "rows": [["Receipt total", money(amount)]],
                    "suggestion": {"choice": "business", "label": "Keep it as a business expense"},
                    "choices": [{"choice": "business", "label": "It's business"},
                                {"choice": "personal", "label": "It's personal"}],
                    "evidence": {"receipt": r["file"]}, "found_by": "Muse Spark read the receipt", "holds": [],
                })
        for n in self.voice:
            p, raw = n["parsed_effective"], n["parsed"]
            key = "voice:" + Path(n["file"]).stem
            if raw.get("assumption") and n["txn"] is not None:
                items.append({
                    "id": key, "source": "voice", "amount": str(Decimal(str(p["amount"])).quantize(CENT)),
                    "tag": f"Cash, {short_date(p['date'])}",
                    "title": f"Check the amount: {money(p['amount'])} for {(p.get('description') or 'cash').lower()}",
                    "body": f"You said: “{_quote(n['transcript'])}” {raw['assumption'].rstrip('.')}.",
                    "rows": [["Booked as", money(p["amount"])]],
                    "suggestion": {"choice": "keep", "label": f"Keep {money(p['amount'])}"},
                    "choices": [{"choice": "keep", "label": f"Keep {money(p['amount'])}"},
                                {"choice": "amount", "label": "Change the amount"}],
                    "evidence": {"voice": "voice/" + n["file"]},
                    "found_by": "Heard by Muse Voice Transcribe, booked by Muse Spark", "holds": [],
                })
            elif raw.get("question") and raw.get("business_line") in ("rideshare", "turo"):
                items.append({
                    "id": key, "source": "voice", "amount": str(raw.get("amount") or 0),
                    "tag": f"Cash, {short_date(p['date'])}", "title": raw["question"],
                    "body": f"You said: “{_quote(n['transcript'])}”", "rows": [],
                    "suggestion": {"choice": "book", "label": f"Book {money(raw.get('amount') or 0)}"},
                    "choices": [{"choice": "book", "label": f"Book {money(raw.get('amount') or 0)}"},
                                {"choice": "skip", "label": "Leave it out"}],
                    "evidence": {"voice": "voice/" + n["file"]},
                    "found_by": "Heard by Muse Voice Transcribe", "holds": [],
                })
        decisions = self.state.get("decisions", {})
        for it in items:
            it["decision"] = decisions.get(it["id"])
        items.sort(key=lambda i: -Decimal(i["amount"]))  # the most money first
        self._items = items
        return items

    def pending(self) -> List[Dict]:
        return [i for i in self.review_items() if not i["decision"]]

    def _effects(self):
        """What the driver's answers do to the books: rows held back while an item waits, rows whose treatment
        an answer changes, and amounts an answer adds (a Lyft shortfall booked as a fee)."""
        held, override, extras = set(), {}, []
        for it in self.review_items():
            choice = (it["decision"] or {}).get("choice")
            if choice is None:
                held.update(it["holds"])
                continue
            kind = it.get("type") or it["source"]
            for h in it["holds"]:
                if kind == "receipt" and choice == "personal":
                    override[h] = ("other", "Personal, kept out")
                elif kind == "possible_duplicate" and choice == "duplicate":
                    override[h] = ("other", "Duplicate charges, disputed")
                elif kind == "deposit_without_payout":
                    override[h] = ("income", "Other Turo income") if choice == "income" else ("other", "Money in, not yet explained")
                elif kind == "unidentified_income":
                    override[h] = ("income", "Other income") if choice == "business" else ("other", "Personal, kept out")
            if kind == "deposit_short":
                extras.append(("expense", "Platform fees", Decimal(it["amount"])) if choice == "fee"
                              else ("other", "Lyft still owes you", Decimal(it["amount"])))
        return held, override, extras

    def checks(self) -> List[Dict]:
        items = self.review_items()
        out = []
        for c in self.close["checks"]:
            related = [i for i in items if i.get("check") == c["id"]]
            status = "passes" if c["ok"] else ("answered" if related and all(i["decision"] for i in related) else "needs you")
            out.append({**c, "status": status})
        return out

    # ---- totals -------------------------------------------------------------------------
    def totals(self) -> Dict:
        income, expense, other = OrderedDict(), OrderedDict(), OrderedDict()
        held, override, extras = self._effects()
        in_review = ZERO
        for t in self.all_txns():
            role, group = classify(t)
            if t.txn_id in held:
                in_review += abs(t.amount)
                continue
            if t.txn_id in override:
                role, group = override[t.txn_id]
                if role == "other":
                    other[group] = other.get(group, ZERO) + abs(t.amount)
                    continue
            if role == "income":
                income[group] = income.get(group, ZERO) + t.amount
            elif role == "expense":
                expense[group] = expense.get(group, ZERO) - t.amount
            elif role in ("loan", "owner", "unexplained"):
                other[group] = other.get(group, ZERO) + abs(t.amount)
        for role, group, amount in extras:
            bucket = expense if role == "expense" else other
            bucket[group] = bucket.get(group, ZERO) + amount
        earned = sum(income.values(), ZERO)
        spent = sum(expense.values(), ZERO)
        expense = OrderedDict(sorted(expense.items(), key=lambda kv: -kv[1]))
        income = OrderedDict(sorted(income.items(), key=lambda kv: -kv[1]))
        return {"earned": earned, "spent": spent, "profit": earned - spent, "income": income,
                "expense": expense, "other": other, "in_review": in_review}

    # ---- rides, Turo, weeks -------------------------------------------------------------
    def rides(self) -> Dict:
        accepted = [o for o in self.offers if o.accepted]
        declined = [o for o in self.offers if not o.accepted]

        def per_hour(o):
            minutes = o.pickup_minutes + o.trip_minutes
            return o.fare / (minutes / 60) if minutes else 0.0

        blocks = OrderedDict([("Early morning, 5–9 AM", (5, 9)), ("Midday, 9 AM–3 PM", (9, 15)),
                              ("Afternoon, 3–7 PM", (15, 19)), ("Evening, 7–11 PM", (19, 23)),
                              ("Late night, 11 PM–5 AM", (23, 29))])
        by_block = OrderedDict()
        for name, (a, b) in blocks.items():
            rs = [o for o in accepted if a <= (o.at.hour if o.at.hour >= 5 else o.at.hour + 24) < b]
            if rs:
                fares = sum(o.fare for o in rs)
                mins = sum(o.pickup_minutes + o.trip_minutes for o in rs)
                by_block[name] = {"rides": len(rs), "per_hour": round(fares / (mins / 60), 2) if mins else 0}
        best = sorted(accepted, key=per_hour, reverse=True)[:5]
        earn = {k: sum((t.amount for t in self.txns if t.kind == "earning" and t.source == k), ZERO) for k in ("uber", "lyft")}
        tips = sum((t.tips or ZERO for t in self.txns if t.kind == "earning" and t.source in ("uber", "lyft")), ZERO)
        return {
            "rides": len(accepted), "uber_rides": sum(1 for o in accepted if o.platform == "uber"),
            "lyft_rides": sum(1 for o in accepted if o.platform == "lyft"), "declined_offers": len(declined),
            "uber_earned": earn["uber"], "lyft_earned": earn["lyft"], "tips": tips,
            "miles_driven": round(sum(d.miles for d in self.drives), 1),
            "pickup_miles": round(sum(o.pickup_miles for o in accepted), 1),
            "pickups_over_3_miles": sum(1 for o in accepted if o.pickup_miles > 3),
            "hours_on_rides": round(sum(o.pickup_minutes + o.trip_minutes for o in accepted) / 60, 1),
            "by_time_of_day": by_block,
            "best": [{"when": f"{o.at.strftime('%a %b')} {o.at.day}, {o.at.strftime('%I:%M %p').lstrip('0')}",
                      "platform": PLATFORM.get(o.platform, o.platform), "fare": round(o.fare, 2),
                      "minutes": round(o.pickup_minutes + o.trip_minutes),
                      "miles": round(o.pickup_miles + o.trip_miles, 1), "pickup_miles": o.pickup_miles,
                      "per_hour": round(per_hour(o), 2)} for o in best],
        }

    def turo(self) -> Dict:
        rows = [t for t in self.txns if t.source == "turo" and t.kind == "earning"]
        trips = OrderedDict()
        for t in rows:
            trip = trips.setdefault(t.ref or t.description,
                                    {"date": t.date, "car": t.description.split("·")[-1].strip(), "amount": ZERO})
            trip["amount"] += t.amount
        return {"total": sum((t.amount for t in rows), ZERO), "trips": len(trips),
                "adjustments": sum((t.amount for t in rows if t.amount < 0), ZERO), "list": list(trips.values())}

    def weeks(self) -> List[Dict]:
        start = self.first_day - timedelta(days=self.first_day.weekday())
        out = []
        while start <= self.last_day:
            a, b = max(start, self.first_day), min(start + timedelta(days=6), self.last_day)
            ws = [t for t in self.txns if t.kind == "earning" and a.isoformat() <= t.date <= b.isoformat()]
            out.append({"week": span(a, b), "start": a.isoformat(), "end": b.isoformat(),
                        "uber": sum((t.amount for t in ws if t.source == "uber"), ZERO),
                        "lyft": sum((t.amount for t in ws if t.source == "lyft"), ZERO),
                        "turo": sum((t.amount for t in ws if t.source == "turo"), ZERO),
                        "rides": sum(1 for o in self.offers if o.accepted and a <= o.at.date() <= b),
                        "is_this_week": a <= self.today <= start + timedelta(days=6)})
            start += timedelta(days=7)
        return out

    def ride_profit(self) -> Dict:
        if self._rides is None:
            rides = ride_profits(self.month_dir, self.txns)
            self._rides = {"rides": rides, "summary": ride_summary(rides, self.profile)}
        return self._rides

    def _offer_cache(self):
        if self._offers is None:
            self._offers = ride_rules.offer_estimates(self.month_dir, self.txns)
        return self._offers

    def hours_on_road(self) -> float:
        offers, est, _ = self._offer_cache()
        return ride_rules.online_hours(offers, est)

    def rule_result(self, rule: Dict) -> Dict:
        """Replay one rule and say what it would have done, in plain words. All numbers from code."""
        r = ride_rules.rule_from_dict(rule)
        c = ride_rules.compare(r, self.month_dir, cache=self._offer_cache())
        avg = self.ride_profit()["summary"]["all"]["per_hour"] or 0
        diff = Decimal(str(c["difference"]))
        n, k = c["rides_you_took_it_skips"], c["offers_you_passed_it_takes"]
        said = []
        if n:
            ph = c["skipped_rides_per_hour"] or 0
            versus = ("about your average" if abs(ph - avg) < 3 else
                      f"less than your average of {money(avg)}" if ph < avg else f"more than your average of {money(avg)}")
            said.append(f"It skips {n} ride{'s' if n != 1 else ''} you drove. They paid {money(ph)} an hour after car costs, {versus}.")
        else:
            said.append("It keeps every ride you drove.")
        if k:
            said.append(f"In the time it frees, {k} offer{'s' if k != 1 else ''} you passed on would have fit, "
                        f"worth about {money(c['profit_of_offers_it_takes'])}.")
        if diff:
            said.append(f"{self.first_day.strftime('%B')} would have paid {money(abs(diff))} {'more' if diff > 0 else 'less'} after car costs.")
        else:
            said.append("It wouldn't have changed what you made.")
        a, w = c["as_driven"], c["with_rule"]
        return {"type": "rule", "headline": c["describe"], "amount": ("+" if diff > 0 else "") + money(diff),
                "verdict": "Worth trying" if diff > 0 else "Not worth it" if diff < 0 else "No change",
                "text": " ".join(said),
                "rows": [["As you drove", money(a["true_profit"])], ["With the rule", money(w["true_profit"])],
                         ["Per hour on the road", f"{money(a['per_online_hour'])} → {money(w['per_online_hour'])}"],
                         ["Unpaid pickup miles", f"{a['pickup_miles']:,} → {w['pickup_miles']:,}"]],
                "note": "Offers you passed on are estimated from their offer cards. Advice only: the app never accepts "
                        "or declines a ride for you.",
                "rule": c["rule"]}

    def rule_presets(self) -> List[Dict]:
        out = []
        for r in ride_rules.PRESETS[1:]:
            c = ride_rules.compare(r, self.month_dir, cache=self._offer_cache())
            out.append({"rule": r.name, "difference": c["difference"], "per_online_hour": c["with_rule"]["per_online_hour"],
                        "rides_it_skips": c["rides_you_took_it_skips"], "offers_it_adds": c["offers_you_passed_it_takes"]})
        return out

    def schedule_c(self) -> Optional[Dict]:
        """Spending rolled up by Schedule C line, from Muse Spark's labels, actual-expense method."""
        if not self.categories:
            return None
        held, override, extras = self._effects()
        lines, vehicle = {}, ZERO
        for t in self.all_txns():
            if t.amount >= 0 or t.kind in ("earning", "payout") or t.txn_id in held:
                continue
            if t.txn_id in override and override[t.txn_id][0] != "expense":
                continue
            cat = self.categories.get(merchant_key(t))
            if cat:
                if cat["treatment"] not in ("expense", "vehicle"):
                    continue
                line, is_vehicle = cat["schedule_c_line"] or "27a Other expenses", cat["treatment"] == "vehicle"
            elif t.kind == "cash" and t.category:
                line, is_vehicle = t.category, False
            else:
                continue
            lines[line] = lines.get(line, ZERO) - t.amount
            if is_vehicle:
                vehicle -= t.amount
        for role, group, amount in extras:
            if role == "expense":
                lines["10 Commissions and fees"] = lines.get("10 Commissions and fees", ZERO) + amount

        def order(line):
            m = re.match(r"(\d+)([a-z]?)", line)
            return (int(m.group(1)), m.group(2)) if m else (99, line)
        ordered = OrderedDict(sorted(lines.items(), key=lambda kv: order(kv[0])))
        miles = sum(d.miles for d in self.drives)
        return {"gross_receipts": self.totals()["earned"], "lines": ordered, "total_expenses": sum(ordered.values(), ZERO),
                "vehicle_costs": vehicle, "rideshare_car_miles": round(miles, 1),
                "standard_mileage_amount": Decimal(str(round(miles * 0.725, 2)))}

    # ---- tire ---------------------------------------------------------------------------
    def tire(self) -> Optional[Dict]:
        if not self.tires:
            return None
        names = ["front-left", "front-right", "rear-left", "rear-right"]

        def psis(r):
            return [r.front_left, r.front_right, r.rear_left, r.rear_right]

        def gap(r, idx):
            p = psis(r)
            return median([p[j] for j in range(4) if j != idx]) - p[idx]

        worst = None
        for idx, name in enumerate(names):
            run = []
            for r in self.tires + [None]:
                if r is not None and gap(r, idx) >= 2.0:
                    run.append(r)
                    continue
                if run and (worst is None or len(run) > len(worst[2])):
                    worst = (idx, name, run)
                run = []
        if worst is None:
            return {"low": False}
        idx, name, run = worst
        first, last = run[0].at.date(), run[-1].at.date()
        start = first  # the leak begins before the 2 psi threshold trips: walk back while it was already sinking
        for r in reversed([r for r in self.tires if r.at.date() < first]):
            if gap(r, idx) >= 0.5:
                start = r.at.date()
            else:
                break
        ongoing = last >= self.today - timedelta(days=1)
        share = self.profile.get("share_of_energy_from_supercharging", 0.25)
        price = (self.profile.get("home_electricity_per_kwh", 0.14) * (1 - share)
                 + self.profile.get("supercharger_per_kwh", 0.42) * share)
        gaps = {r.at.date(): gap(r, idx) for r in self.tires}
        extra_kwh = 0.0
        for d in self.drives:
            g = gaps.get(d.start.date(), 0.0)
            if start <= d.start.date() <= last and g > 0:
                extra_kwh += d.kwh - d.kwh / (1 + 0.002 * g / 4)  # one low tire moves the average by a quarter
        return {"low": True, "tire": name, "since": start.isoformat(), "until": last.isoformat(), "ongoing": ongoing,
                "lowest_psi": min(psis(r)[idx] for r in run), "placard_psi": self.profile.get("placard_tire_psi", 42),
                "refilled": None if ongoing else (last + timedelta(days=1)).isoformat(),
                "extra_kwh": round(extra_kwh, 2), "extra_cost": round(extra_kwh * price, 2),
                "series": [{"date": r.at.date().isoformat(), "psi": psis(r)[idx],
                            "others": round(median([psis(r)[j] for j in range(4) if j != idx]), 1)} for r in self.tires]}

    # ---- what the screens and the model see --------------------------------------------
    def home(self) -> Dict:
        t, turo, rides, tire = self.totals(), self.turo(), self.rides(), self.tire()
        pending = self.pending()
        pending_total = sum((Decimal(i["amount"]) for i in pending), ZERO)
        n = len(pending)
        minutes = max(1, round(n * SECONDS_PER_ITEM / 60)) if n else 0
        month = self.first_day.strftime("%B")
        bubbles = [
            {"id": "profit", "value": money0(t["profit"]), "label": "profit so far" if n else f"{month} profit",
             "tone": "blush", "size": "xl"},
            {"id": "review", "value": str(n), "label": "to review" if n else "all reviewed",
             "tone": "bubblegum" if n else "sky", "size": "l", "note": money(pending_total) if n else ""},
            {"id": "turo", "value": money0(turo["total"]), "label": f"Turo, {turo['trips']} trips", "tone": "sky", "size": "m"},
            {"id": "rides", "value": money0(self.ride_profit()["summary"]["all"]["true_profit"] / self.hours_on_road()) + "/hr",
             "label": "after car costs", "tone": "white", "size": "m"},
        ]
        if self.receipts_from_photo:
            got = len(self.receipts)
            bubbles.append({"id": "receipts", "value": str(got) if got else "",
                            "label": "receipts read" if got else "Snap receipts",
                            "tone": "sky" if got else "white", "size": "s", "icon": None if got else "camera"})
        if tire and tire.get("low"):
            bubbles.append({"id": "tire", "value": "", "label": "Tire is low" if tire["ongoing"] else "Tire ran low",
                            "tone": "mist", "size": "s", "icon": "tire"})
        if n:
            headline = f"{month} is almost closed."
            sub = f"{n} thing{'s' if n != 1 else ''} need{'' if n != 1 else 's'} you, about {minutes} minute{'s' if minutes != 1 else ''}."
        else:
            headline, sub = f"{month} is closed.", "Everything is reviewed."
        return {"month": month, "today": self.today.isoformat(), "headline": headline, "sub": sub,
                "hint": "Tap a bubble, or hold the cloud and talk.", "bubbles": bubbles,
                "suggestions": ["How did this week go?", "Which rides paid best?", "What did I spend on charging?"],
                "review": {"count": n, "total": money(pending_total)}}

    def pile_view(self) -> Optional[Dict]:
        """The receipt photo as the app shows it: SAM's boxes (as % of the photo) and, once Muse
        Spark has read them, what each receipt says and what the code checks found."""
        pile = self.state.get("pile")
        if not pile:
            return None
        w, h = pile["width"], pile["height"]
        read = pile.get("receipts") is not None
        readings = pile.get("receipts") or []
        items = []
        for i, b in enumerate(pile["boxes"]):
            item = {"title": f"Receipt {i + 1}", "box": {
                "left": round(100.0 * b["x1"] / w, 2), "top": round(100.0 * b["y1"] / h, 2),
                "width": round(100.0 * (b["x2"] - b["x1"] + 1) / w, 2),
                "height": round(100.0 * (b["y2"] - b["y1"] + 1) / h, 2), "confidence": b["confidence"]}}
            r = readings[i] if read and i < len(readings) else None
            if r and r.get("extracted"):
                e = r["extracted"]
                a = assess(e, self.txns)
                m = a["match"]
                try:
                    when = short_date(e.get("date") or "")
                except ValueError:
                    when = ""
                flag = None
                if e.get("kind") in ("personal", "mixed"):
                    flag = f"Looks {e['kind']}"
                elif any("arithmetic" in f for f in a["flags"]):
                    flag = "Doesn't add up"
                item.update(title=(e.get("merchant") or "Receipt").title(), date=when,
                            amount=money(Decimal(str(e.get("total") or 0))), flag=flag,
                            status=(f"Matched to the {account_name(m['account'])}, {short_date(m['date'])}" if m
                                    else "No card charge for this amount"),
                            image="/evidence/" + r["file"])
            elif r is not None:
                item.update(status="Couldn't read this one", flag="Unreadable")
            items.append(item)
        return {"photo": "/evidence/" + pile["photo"], "width": w, "height": h, "read": read, "items": items,
                "found": len(items), "matched": sum(1 for i in items if i.get("status", "").startswith("Matched")),
                "flagged": sum(1 for i in items if i.get("flag")), "seconds": pile.get("seconds") or {}}

    def detail(self, which: str) -> Optional[Dict]:
        if which == "profit":
            t = self.totals()
            sections = [
                {"title": "Earned", "total": money(t["earned"]), "rows": [[k, money(v)] for k, v in t["income"].items()]},
                {"title": "Spent", "total": money(t["spent"]), "rows": [[k, money(v)] for k, v in t["expense"].items()]},
            ]
            not_counted = [[k, money(v)] for k, v in t["other"].items()]
            if t["in_review"]:
                not_counted.insert(0, ["Waiting on your review", money(t["in_review"])])
            if not_counted:
                sections.append({"title": "Not counted in profit", "total": "", "rows": not_counted})
            label = {"passes": "Passes", "answered": "Answered", "needs you": "Needs you"}
            checks = self.checks()
            sections.append({"title": "Tie-out checks", "total": f"{sum(1 for c in checks if c['status'] != 'needs you')} of {len(checks)} done",
                             "rows": [[c["title"], label[c["status"]]] for c in checks]})
            sc = self.schedule_c()
            if sc:
                sections.append({"title": "Schedule C lines, actual-expense method", "total": money(sc["total_expenses"]),
                                 "rows": [["1 Gross receipts", money(sc["gross_receipts"])]] + [[k, money(v)] for k, v in sc["lines"].items()]})
            closed = not self.pending()
            note = "Car loan payments aren't expenses. The interest part is, once the loan statement is in."
            if sc:
                note += (f" Line 9 includes {money(sc['vehicle_costs'])} of charging, car insurance and washes that count only "
                         f"under the actual-expense method. At 72.5¢ a mile, the rideshare car's {sc['rideshare_car_miles']:,} miles "
                         f"come to {money(sc['standard_mileage_amount'])} under the standard rate.")
            return {"id": "profit", "title": f"{self.first_day.strftime('%B')} profit" if closed else "Profit so far",
                    "value": money(t["profit"]), "sections": sections, "note": note}
        if which == "turo":
            tu = self.turo()
            return {"id": "turo", "title": "Turo this month", "value": money(tu["total"]),
                    "sub": f"{tu['trips']} trips, after {money(-tu['adjustments'])} in adjustments",
                    "sections": [{"title": "Trips", "total": "",
                                  "rows": [[f"{short_date(x['date'])}, {x['car']}", money(x["amount"])] for x in tu["list"]]}]}
        if which == "rides":
            r = self.rides()
            s = self.ride_profit()["summary"]
            a, cp = s["all"], s["cost_parts"]
            far, near = s["pickup_over_3_miles"], s["pickup_3_miles_or_less"]
            road = self.hours_on_road()
            presets = self.rule_presets()
            return {"id": "rides", "title": "What your rides really paid", "value": f"{money(a['true_profit'] / road)} an hour",
                    "sub": f"After car costs, over {road:.1f} hours on the road ({a['hours']} with a rider), "
                           f"{a['rides']} rides: {r['uber_rides']} Uber, {r['lyft_rides']} Lyft",
                    "rules": [{"label": p["rule"], "value": ("+" if p["difference"] > 0 else "") + money(p["difference"])} for p in presets],
                    "sections": [
                        {"title": "After car costs", "total": money(a["true_profit"]), "rows": [
                            ["Fares and tips", money(a["fare"])], ["Electricity", money(-cp["energy"])],
                            ["Wear on the car", money(-cp["depreciation"])], ["Tire wear", money(-cp["tires"])]]},
                        {"title": "Is a long pickup worth it?", "total": "", "rows": [
                            [f"Pickups over 3 miles, {far['rides']} rides" if far else "Pickups over 3 miles", f"{money(far['per_hour'])}/hr" if far else "None"],
                            [f"3 miles or less, {near['rides']} rides" if near else "3 miles or less", f"{money(near['per_hour'])}/hr" if near else "None"],
                            [f"Rides under your {money0(s['target_per_hour'])}/hr goal", str(s["below_target"])]]},
                        {"title": "Money", "total": "", "rows": [["Uber", money(r["uber_earned"])], ["Lyft", money(r["lyft_earned"])],
                                                                 ["Tips, included above", money(r["tips"])]]},
                        {"title": "Driving", "total": "", "rows": [["Miles driven", f"{r['miles_driven']:,}"],
                                                                   ["Miles to pickups, unpaid", f"{r['pickup_miles']:,}"],
                                                                   ["Pickups over 3 miles", str(r["pickups_over_3_miles"])],
                                                                   ["Hours on the road", f"{road:.1f}"],
                                                                   ["Hours with a rider or heading to one", f"{a['hours']:.1f}"]]},
                        {"title": "Fares per hour, by time of day", "total": "",
                         "rows": [[k, money(v["per_hour"])] for k, v in r["by_time_of_day"].items()]},
                    ]}
        if which == "tire":
            tr = self.tire()
            if not tr or not tr.get("low"):
                return {"id": "tire", "title": "Tires look fine", "value": "", "sections": []}
            when = span(date.fromisoformat(tr["since"]), date.fromisoformat(tr["until"]))
            body = (f"Your {tr['tire']} tire ran low {when}, down to {tr['lowest_psi']} psi. "
                    f"The door sticker says {tr['placard_psi']} psi.")
            body += " It's still low." if tr["ongoing"] else f" It was filled {short_date(tr['refilled'])}."
            return {"id": "tire", "title": "Tire pressure", "value": f"{tr['lowest_psi']} psi", "body": body,
                    "series": tr["series"], "placard": tr["placard_psi"],
                    "sections": [{"title": "What it cost", "total": "", "rows": [
                        ["Extra charging, estimated", money(tr["extra_cost"])],
                        ["Tire wear", "Faster on a low tire"], ["Grip in the rain", "Lower"]]}],
                    "note": "The charging estimate uses 0.2% more energy per psi, a figure measured on gas cars. "
                            "Your own drives will show the real number over time."}
        return None

    def _ride_context(self) -> Dict:
        s = self.ride_profit()["summary"]
        keep = ("at", "platform", "fare", "pickup_miles", "miles", "minutes", "true_profit", "per_hour")
        return {**{k: v for k, v in s.items() if k not in ("best", "worst")},
                "best_by_true_profit_per_hour": [{k: r[k] for k in keep} for r in s["best"]],
                "worst_by_true_profit_per_hour": [{k: r[k] for k in keep} for r in s["worst"]],
                "how": "fare and tips minus electricity, wear on the car and tire wear, with miles to the pickup charged to the ride"}

    def context(self) -> Dict:
        """The facts Muse Spark may use to answer a question. Small on purpose."""
        t, tu, r, tr = self.totals(), self.turo(), self.rides(), self.tire()

        def m(v):
            return float(Decimal(str(v)).quantize(CENT))

        rides = {k: (m(v) if isinstance(v, Decimal) else v) for k, v in r.items()}
        return {
            "business": "Bayou City Mobility LLC (fictional): Uber and Lyft driving in one Tesla Model Y, plus Teslas rented on Turo",
            "today": self.today.isoformat(), "month": self.first_day.strftime("%B %Y"),
            "profit_so_far": m(t["profit"]), "earned": {k: m(v) for k, v in t["income"].items()},
            "spent_by_group": {k: m(v) for k, v in t["expense"].items()},
            "not_counted_in_profit": {k: m(v) for k, v in t["other"].items()},
            "waiting_on_review": [{"what": i["title"], "amount": i["amount"]} for i in self.pending()],
            "weeks": [{**w, "uber": m(w["uber"]), "lyft": m(w["lyft"]), "turo": m(w["turo"])} for w in self.weeks()],
            "turo": {"trips": tu["trips"], "earned": m(tu["total"]), "adjustments": m(tu["adjustments"])},
            "rides": rides,
            "true_profit_per_ride": self._ride_context(),
            "hours_on_the_road": round(self.hours_on_road(), 1),
            "ride_rules_replayed_on_this_month": self.rule_presets(),
            "checks": [{"check": c["title"], "status": c["status"], "detail": c["detail"]} for c in self.checks()],
            "schedule_c_actual_expense_method": None if not self.schedule_c() else {
                k: (m(v) if isinstance(v, Decimal) else ({kk: m(vv) for kk, vv in v.items()} if isinstance(v, dict) else v))
                for k, v in self.schedule_c().items()},
            "tire": None if not tr else {**{k: v for k, v in tr.items() if k != "series"},
                                          "also": "A low tire wears faster and grips less in the rain; there is no dollar figure for wear yet."},
            "cash_logged": [{"date": c.date, "amount": m(-c.amount), "what": c.description, "line": c.business_line}
                            for c in self.cash_txns()],
        }
