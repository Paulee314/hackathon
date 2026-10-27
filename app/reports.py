"""The month's reports: profit and loss by business line, a Schedule C draft, and a mileage log.

Everything here is arithmetic on the books (app/books.py) after the driver's answers; no model is
called. Each report comes back in the shape the app shows (sections of rows) and as CSV rows for a
download. While review items are open the reports say so and leave those amounts out, exactly as
the profit on the home screen does.
"""
from __future__ import annotations

import csv
import io
from collections import OrderedDict
from datetime import datetime
from decimal import Decimal
from typing import Dict, List, Optional, Tuple

from agents.categorize import merchant_key
from app.books import Books, money, money0
from car import rules as ride_rules
from car.profit import link

ZERO = Decimal("0")
IRS_RATE = Decimal("0.725")            # 2026 standard mileage rate, dollars per business mile
LINES = ("rideshare", "turo", "shared")
LINE_NAMES = {"rideshare": "Rideshare", "turo": "Turo", "shared": "Shared"}
INCOME_LINE = {"uber": "rideshare", "lyft": "rideshare", "turo": "turo"}
REPORTS = OrderedDict([("pnl", "Profit and loss"), ("schedule_c", "Schedule C draft"), ("mileage", "Mileage log")])


def _status(b: Books) -> Dict:
    pending = b.pending()
    held = b.totals()["in_review"]
    if pending:
        return {"final": False, "text": f"Draft: {len(pending)} item{'s' if len(pending) != 1 else ''} still need you, "
                                         f"and {money(held)} waits on them."}
    return {"final": True, "text": "Every item is reviewed, so these are the month's numbers."}


def _business_line(b: Books, role: str, t) -> str:
    if t is None:  # an amount an answer added; today only Lyft's shortfall booked as a platform fee
        return "rideshare"
    if role == "income":
        return INCOME_LINE.get(t.source, "shared")
    cat = b.categories.get(merchant_key(t)) or {}
    line = cat.get("business_line") or t.business_line or "both"
    return line if line in ("rideshare", "turo") else "shared"


# ---- profit and loss ----------------------------------------------------------------------------
def profit_and_loss(b: Books) -> Dict:
    """Income and spending by business line. Costs that serve both (phone, software, insurance, bank
    fees) stay in their own column rather than being split by a guess."""
    income: Dict[str, Dict[str, Decimal]] = OrderedDict()
    expense: Dict[str, Dict[str, Decimal]] = OrderedDict()
    kept_out: Dict[str, Decimal] = OrderedDict()
    for role, group, amount, t in b.booked():
        if role in ("income", "expense"):
            table = income if role == "income" else expense
            row = table.setdefault(group, {k: ZERO for k in LINES})
            row[_business_line(b, role, t)] += amount
        elif role == "other":
            kept_out[group] = kept_out.get(group, ZERO) + amount
    def total(table, line):
        return sum((r[line] for r in table.values()), ZERO)
    def ordered(table):
        return OrderedDict(sorted(table.items(), key=lambda kv: -sum(kv[1].values(), ZERO)))
    income, expense = ordered(income), ordered(expense)
    by_line = {k: {"income": total(income, k), "expense": total(expense, k)} for k in LINES}
    for k in LINES:
        by_line[k]["net"] = by_line[k]["income"] - by_line[k]["expense"]
    t = b.totals()
    return {"income": income, "expense": expense, "by_line": by_line, "kept_out": kept_out,
            "held": t["in_review"], "earned": t["earned"], "spent": t["spent"], "profit": t["profit"]}


# ---- mileage log --------------------------------------------------------------------------------
def mileage_log(b: Books) -> Dict:
    """Every drive of the rideshare car with its odometer readings and what it was for.

    With a rider and on the way to a pickup come from matching each ride to its drives. Between
    rides means the car moved while the driver was on the apps (offers and rides less than 30
    minutes apart). Drives with the apps off that start or end at home are commuting; the rest are
    personal. Business miles = with a rider + to a pickup + between rides."""
    drives = sorted(b.drives, key=lambda d: d.start)
    offers, est, _ = b._offer_cache()
    stretches = ride_rules.online_stretches(offers, est)
    legs: Dict[str, str] = {}
    for pickup, trip in link(b.offers, b.drives).values():
        legs[pickup.id] = "pickup"
        if trip is not None:
            legs[trip.id] = "rider"

    def online(d) -> bool:
        return any(a <= d.start <= z for a, z in stretches)

    rows = []
    for d in drives:
        kind = legs.get(d.id) or ("between" if online(d) else
                                  "commute" if "home" in (d.frm.lower(), d.to.lower()) else "personal")
        rows.append({"date": d.start.date().isoformat(), "start": d.start, "end": d.end, "from": d.frm, "to": d.to,
                     "start_odometer": d.start_odometer, "end_odometer": d.end_odometer,
                     "miles": Decimal(str(round(d.miles, 2))), "kind": kind})
    miles = {k: sum((r["miles"] for r in rows if r["kind"] == k), ZERO)
             for k in ("rider", "pickup", "between", "commute", "personal")}
    business = miles["rider"] + miles["pickup"] + miles["between"]
    days: Dict[str, Dict] = OrderedDict()
    for r in rows:
        day = days.setdefault(r["date"], {"first": r["start_odometer"], "last": r["end_odometer"], "business": ZERO,
                                          "other": ZERO, "rides": 0})
        day["last"] = r["end_odometer"]
        if r["kind"] in ("rider", "pickup", "between"):
            day["business"] += r["miles"]
        else:
            day["other"] += r["miles"]
        day["rides"] += r["kind"] == "rider"
    return {"rows": rows, "days": days, "miles": miles, "business": business,
            "total": sum((r["miles"] for r in rows), ZERO),
            "first_odometer": rows[0]["start_odometer"] if rows else None,
            "last_odometer": rows[-1]["end_odometer"] if rows else None,
            "standard_rate_value": (business * IRS_RATE).quantize(Decimal("0.01"))}


# ---- Schedule C draft ---------------------------------------------------------------------------
def schedule_c_draft(b: Books) -> Optional[Dict]:
    sc = b.schedule_c()
    if sc is None:
        return None
    log = mileage_log(b)
    gross = sc["gross_receipts"]
    total = sc["total_expenses"]
    return {"line_1": gross, "line_7": gross, "part_ii": sc["lines"], "line_28": total, "line_29": gross - total,
            "line_31": gross - total, "vehicle_costs": sc["vehicle_costs"],
            "line_44a": log["business"], "line_44b": log["miles"]["commute"], "line_44c": log["miles"]["personal"],
            "standard_rate_value": log["standard_rate_value"]}


# ---- shown in the app ---------------------------------------------------------------------------
def view(b: Books, name: str) -> Optional[Dict]:
    month = b.first_day.strftime("%B %Y")
    status = _status(b)
    if name == "pnl":
        p = profit_and_loss(b)
        cols = ["", "Rideshare", "Turo", "Shared", "Total"]
        def cell(d):  # whole dollars on screen so four columns fit; the download keeps cents
            return "–" if d == 0 else money0(d)
        def row(label, parts, total=None):
            return [label] + [cell(parts[k]) for k in LINES] + [cell(total if total is not None else sum(parts.values(), ZERO))]
        sections = [
            {"title": "Income", "columns": cols,
             "rows": [row(g, v) for g, v in p["income"].items()]
                     + [row("Total income", {k: p["by_line"][k]["income"] for k in LINES}, p["earned"])]},
            {"title": "Spending", "columns": cols,
             "rows": [row(g, v) for g, v in p["expense"].items()]
                     + [row("Total spending", {k: p["by_line"][k]["expense"] for k in LINES}, p["spent"])]},
            {"title": "Profit", "columns": cols,
             "rows": [row("Net profit", {k: p["by_line"][k]["net"] for k in LINES}, p["profit"])]},
        ]
        out_rows = [[k, money(v)] for k, v in p["kept_out"].items()]
        if p["held"]:
            out_rows.insert(0, ["Waiting on your review", money(p["held"])])
        if out_rows:
            sections.append({"title": "Not counted in profit", "total": "", "rows": out_rows})
        return {"id": "pnl", "title": "Profit and loss", "eyebrow": f"Bayou City Mobility LLC · {month}",
                "value": money(p["profit"]), "sub": "Net profit, all business lines", "status": status,
                "sections": sections,
                "note": "Shared costs (phone, software, business insurance, bank fees) serve both businesses, so "
                        "they aren't split by a guess. Loan payments, transfers and personal spending are listed "
                        "but kept out of profit."}
    if name == "schedule_c":
        s = schedule_c_draft(b)
        if s is None:
            return None
        part_ii = [[line, money(v)] for line, v in s["part_ii"].items()]
        return {"id": "schedule_c", "title": "Schedule C draft", "eyebrow": f"Bayou City Mobility LLC · {month}",
                "value": money(s["line_31"]), "sub": "Line 31, net profit, actual-expense method", "status": status,
                "sections": [
                    {"title": "Part I, Income", "total": "", "rows": [
                        ["1 Gross receipts", money(s["line_1"])], ["7 Gross income", money(s["line_7"])]]},
                    {"title": "Part II, Expenses", "total": money(s["line_28"]), "rows": part_ii + [
                        ["28 Total expenses", money(s["line_28"])]]},
                    {"title": "Net", "total": "", "rows": [
                        ["29 Tentative profit", money(s["line_29"])], ["31 Net profit", money(s["line_31"])]]},
                    {"title": "Part IV, Your vehicle (the rideshare car)", "total": "", "rows": [
                        ["44a Business miles", f"{s['line_44a']:,.1f}"], ["44b Commuting miles", f"{s['line_44b']:,.1f}"],
                        ["44c Other miles", f"{s['line_44c']:,.1f}"], ["47a Evidence to support the deduction", "Yes"],
                        ["47b Written evidence", "Yes, the mileage log"]]},
                    {"title": "The other way to deduct the car", "total": "", "rows": [
                        ["Charging, car insurance and washes, in line 9", money(s["vehicle_costs"])],
                        [f"Standard rate, {s['line_44a']:,.1f} business miles × 72.5¢", money(s["standard_rate_value"])]]},
                ],
                "note": "One month of the year's Schedule C. Line 1 is what Uber, Lyft and Turo paid out; if a 1099-K "
                        "shows gross fares, add the platforms' fees to line 10 so the two match. Rideshare and Turo "
                        "are drafted together; if they're separate businesses, each gets its own Schedule C. The "
                        "standard rate replaces the rideshare car's charging, insurance share, washes and "
                        "depreciation, and is chosen per car; depreciation isn't in these books yet."}
    if name == "mileage":
        m = mileage_log(b)
        mi = m["miles"]
        days = [[datetime.fromisoformat(d).strftime("%a %b %-d"),
                 f"{v['business']:,.1f} business" + (f", {v['other']:,.1f} other" if v["other"] else ""),
                 f"{v['first']:,.0f} → {v['last']:,.0f}"] for d, v in m["days"].items()]
        return {"id": "mileage", "title": "Mileage log", "eyebrow": f"The rideshare car · {month}",
                "value": f"{m['business']:,.1f} business miles", "status": status,
                "sub": f"Odometer {m['first_odometer']:,.0f} → {m['last_odometer']:,.0f}, {len(m['rows'])} drives, "
                       f"{m['total']:,.1f} miles in all",
                "sections": [
                    {"title": "Business miles", "total": f"{m['business']:,.1f}", "rows": [
                        ["With a rider", f"{mi['rider']:,.1f}"], ["On the way to a pickup", f"{mi['pickup']:,.1f}"],
                        ["Between rides, on the apps", f"{mi['between']:,.1f}"]]},
                    {"title": "Not business", "total": f"{mi['commute'] + mi['personal']:,.1f}", "rows": [
                        ["To and from home, apps off", f"{mi['commute']:,.1f}"], ["Personal", f"{mi['personal']:,.1f}"]]},
                    {"title": "At the standard rate", "total": "", "rows": [
                        [f"{m['business']:,.1f} miles × 72.5¢", money(m["standard_rate_value"])]]},
                    {"title": "Day by day", "columns": ["Day", "Miles", "Odometer"], "rows": days},
                ],
                "note": "From the car's own drive log (Tessie): every drive has its start and end odometer, places "
                        "and times. The download has each drive on its own line, the way the IRS asks for a log."}
    return None


# ---- downloads ----------------------------------------------------------------------------------
KIND_NAMES = {"rider": "Rideshare trip, rider on board", "pickup": "Rideshare, driving to a pickup",
              "between": "Rideshare, on the apps between rides", "commute": "To or from home, apps off",
              "personal": "Personal"}


def csv_rows(b: Books, name: str) -> Optional[Tuple[str, List[List]]]:
    month = b.first_day.strftime("%Y-%m")
    if name == "pnl":
        p = profit_and_loss(b)
        rows = [["Section", "Line", "Rideshare", "Turo", "Shared", "Total"]]
        for section, table in (("Income", p["income"]), ("Spending", p["expense"])):
            for g, v in table.items():
                rows.append([section, g] + [f"{v[k]:.2f}" for k in LINES] + [f"{sum(v.values(), ZERO):.2f}"])
        rows.append(["Profit", "Net profit"] + [f"{p['by_line'][k]['net']:.2f}" for k in LINES] + [f"{p['profit']:.2f}"])
        for g, v in p["kept_out"].items():
            rows.append(["Not counted in profit", g, "", "", "", f"{v:.2f}"])
        if p["held"]:
            rows.append(["Not counted in profit", "Waiting on review", "", "", "", f"{p['held']:.2f}"])
        return f"profit-and-loss-{month}.csv", rows
    if name == "schedule_c":
        s = schedule_c_draft(b)
        if s is None:
            return None
        rows = [["Line", "Amount"], ["1 Gross receipts", f"{s['line_1']:.2f}"], ["7 Gross income", f"{s['line_7']:.2f}"]]
        rows += [[line, f"{v:.2f}"] for line, v in s["part_ii"].items()]
        rows += [["28 Total expenses", f"{s['line_28']:.2f}"], ["29 Tentative profit", f"{s['line_29']:.2f}"],
                 ["31 Net profit", f"{s['line_31']:.2f}"], ["44a Business miles", f"{s['line_44a']:.1f}"],
                 ["44b Commuting miles", f"{s['line_44b']:.1f}"], ["44c Other miles", f"{s['line_44c']:.1f}"]]
        return f"schedule-c-draft-{month}.csv", rows
    if name == "mileage":
        m = mileage_log(b)
        rows = [["Date", "Start", "End", "From", "To", "Start odometer", "End odometer", "Miles", "Purpose", "Business"]]
        for r in m["rows"]:
            rows.append([r["date"], r["start"].strftime("%H:%M"), r["end"].strftime("%H:%M"), r["from"], r["to"],
                         f"{r['start_odometer']:.1f}", f"{r['end_odometer']:.1f}", f"{r['miles']:.2f}",
                         KIND_NAMES[r["kind"]], "Yes" if r["kind"] in ("rider", "pickup", "between") else "No"])
        return f"mileage-log-{month}.csv", rows
    return None


def to_csv(rows: List[List]) -> str:
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(rows)
    return buf.getvalue()
