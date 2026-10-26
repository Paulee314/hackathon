"""Everything the phone app shows, computed in code from the month's files.

Totals, the review queue, the tire story, ride stats and the facts Muse Spark may use when it
answers a question all come from here, so every number on screen can be traced to a row.
Expense groups use plain merchant rules for now; the categorizer replaces them with
Schedule C lines.
"""
from __future__ import annotations

import json
from collections import OrderedDict
from datetime import date, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from typing import Dict, List, Optional, Tuple

from agents.receipts import assess
from agents.voice import to_transaction
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

    def __init__(self, month_dir: Path, today: date, state: Dict):
        self.month_dir = Path(month_dir)
        self.today = today
        self.state = state
        self.txns, _, _ = load_folder(self.month_dir)
        self.first_day = date.fromisoformat(min(t.date for t in self.txns))
        self.last_day = date.fromisoformat(max(t.date for t in self.txns))
        self.receipts = self._receipts()
        self.voice = self._voice()
        self.drives = load_drives(self.month_dir)
        self.tires = load_tires(self.month_dir)
        self.offers = load_offers(self.month_dir)
        self.profile = load_profile(self.month_dir) or {}

    # ---- inputs -------------------------------------------------------------------------
    def _receipts(self) -> List[Dict]:
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
        items = []
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
        return items

    def pending(self) -> List[Dict]:
        return [i for i in self.review_items() if not i["decision"]]

    def _held_and_personal(self):
        held, personal = set(), set()
        for it in self.review_items():
            choice = (it["decision"] or {}).get("choice")
            if choice is None:
                held.update(it["holds"])
            elif choice == "personal":
                personal.update(it["holds"])
        return held, personal

    # ---- totals -------------------------------------------------------------------------
    def totals(self) -> Dict:
        income, expense, other = OrderedDict(), OrderedDict(), OrderedDict()
        held, personal = self._held_and_personal()
        in_review = ZERO
        for t in self.all_txns():
            role, group = classify(t)
            if t.txn_id in held:
                in_review += abs(t.amount)
                continue
            if t.txn_id in personal:
                other["Personal, kept out"] = other.get("Personal, kept out", ZERO) + abs(t.amount)
                continue
            if role == "income":
                income[group] = income.get(group, ZERO) + t.amount
            elif role == "expense":
                expense[group] = expense.get(group, ZERO) - t.amount
            elif role in ("loan", "owner", "unexplained"):
                other[group] = other.get(group, ZERO) + abs(t.amount)
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
            {"id": "rides", "value": str(rides["rides"]), "label": "rides", "tone": "white", "size": "m"},
        ]
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
            closed = not self.pending()
            return {"id": "profit", "title": f"{self.first_day.strftime('%B')} profit" if closed else "Profit so far",
                    "value": money(t["profit"]), "sections": sections,
                    "note": "Car loan payments aren't expenses. The interest part is, once the loan statement is in."}
        if which == "turo":
            tu = self.turo()
            return {"id": "turo", "title": "Turo this month", "value": money(tu["total"]),
                    "sub": f"{tu['trips']} trips, after {money(-tu['adjustments'])} in adjustments",
                    "sections": [{"title": "Trips", "total": "",
                                  "rows": [[f"{short_date(x['date'])}, {x['car']}", money(x["amount"])] for x in tu["list"]]}]}
        if which == "rides":
            r = self.rides()
            return {"id": "rides", "title": "Rides this month", "value": str(r["rides"]),
                    "sub": f"{r['uber_rides']} Uber, {r['lyft_rides']} Lyft, {r['declined_offers']} offers declined",
                    "sections": [
                        {"title": "Money", "total": "", "rows": [["Uber", money(r["uber_earned"])], ["Lyft", money(r["lyft_earned"])],
                                                                 ["Tips, included above", money(r["tips"])]]},
                        {"title": "Driving", "total": "", "rows": [["Miles driven", f"{r['miles_driven']:,}"],
                                                                   ["Miles to pickups, unpaid", f"{r['pickup_miles']:,}"],
                                                                   ["Pickups over 3 miles", str(r["pickups_over_3_miles"])],
                                                                   ["Hours on rides", str(r["hours_on_rides"])]]},
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
            "tire": None if not tr else {**{k: v for k, v in tr.items() if k != "series"},
                                          "also": "A low tire wears faster and grips less in the rain; there is no dollar figure for wear yet."},
            "cash_logged": [{"date": c.date, "amount": m(-c.amount), "what": c.description, "line": c.business_line}
                            for c in self.cash_txns()],
        }
