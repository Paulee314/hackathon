"""Generate one synthetic October 2026 for a fictional gig operator.

Bayou City Mobility LLC (fictional), owned by Jordan Rivera (fictional), drives Uber
and Lyft in a Tesla Model Y and rents two more Model Ys on Turo.
Bank: Chase checking ...1234. Cards: Chase Ink ...9001 and Amex Platinum.

Files use the same column layouts as real exports. Every platform payout lands in
checking EXCEPT the planted problems in expected_exceptions.json, the answer key
used to score the close agent. Same seed, same files.

    python3 synth/generate_october.py --out samples/october
"""
from __future__ import annotations

import argparse
import csv
import json
import random
import uuid
from collections import defaultdict
from datetime import date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path

SEED = 2026
START, END = date(2026, 10, 1), date(2026, 10, 31)
OWNER_ACH = "RIVERA JORDAN"
CHECKING, INK = "1234", "9001"
OPENING_BALANCE = Decimal("3842.17")

PLACES = [
    "George Bush Intercontinental Airport (IAH), Houston, TX 77032",
    "William P. Hobby Airport (HOU), Houston, TX 77061",
    "Texas Medical Center, Houston, TX 77030",
    "The Galleria, Houston, TX 77056",
    "Rice Village, Houston, TX 77005",
    "Minute Maid Park, Houston, TX 77002",
    "Montrose, Houston, TX 77006",
    "The Heights, Houston, TX 77008",
    "Downtown, Houston, TX 77002",
    "NRG Park, Houston, TX 77054",
]
GUESTS = ["Avery", "Blake", "Casey", "Devon", "Emerson", "Finley", "Harper", "Jules", "Kendall", "Logan"]


def d2(x) -> Decimal:
    return Decimal(str(x)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def days(a: date, b: date):
    while a <= b:
        yield a
        a += timedelta(days=1)


class Month:
    def __init__(self, seed: int = SEED):
        self.rng = random.Random(seed)
        self.uber_trips, self.uber_other, self.uber_payouts = [], [], []
        self.lyft_trips, self.turo_rows = [], []
        self.checking, self.ink, self.amex = [], [], []
        self.exceptions = []
        self.expected = defaultdict(Decimal)   # account -> net, in the loader's sign convention

    # ---------- helpers ----------
    def uid(self) -> str:
        return str(uuid.UUID(int=self.rng.getrandbits(128), version=4))

    def hexid(self, n=24) -> str:
        return "".join(self.rng.choice("0123456789abcdef") for _ in range(n))

    def digits(self, n) -> str:
        return "".join(self.rng.choice("0123456789") for _ in range(n))

    def ach(self, company: str, entry: str, day: date) -> str:
        return (f"ORIG CO NAME:{company:<22} ORIG ID:{self.digits(10)} DESC DATE:{day:%y%m%d} "
                f"CO ENTRY DESCR:{entry:<10}SEC:CCD    TRACE#:{self.digits(15)} EED:{day:%y%m%d}   "
                f"IND ID:{self.digits(8)}           IND NAME:{OWNER_ACH} TRN: {self.digits(10)}TC")

    def bank(self, day: date, amount: Decimal, desc: str, kind: str, details: str = None):
        details = details or ("CREDIT" if amount > 0 else "DEBIT")
        self.checking.append([day, details, desc, d2(amount), kind])
        self.expected[f"chase_{CHECKING}"] += d2(amount)

    def card(self, day: date, desc: str, category: str, amount: Decimal, kind="Sale"):
        self.ink.append([day, desc, category, kind, d2(amount)])
        self.expected[f"chase_ink_{INK}"] += d2(amount)

    def amex_charge(self, day: date, desc: str, category: str, charge: Decimal):
        """charge > 0 is a purchase (Amex shows it positive); < 0 is a payment or credit."""
        self.amex.append([day, desc, category, d2(charge)])
        self.expected["amex_platinum"] -= d2(charge)

    # ---------- rideshare ----------
    def rideshare(self):
        rng = self.rng
        all_days = list(days(START, END))
        uber_days = sorted(rng.sample(all_days, 19))
        lyft_days = sorted(rng.sample(all_days, 12))
        events = []  # (datetime, amount) into the Uber balance

        for day in uber_days:
            for _ in range(rng.randint(3, 7)):
                t = datetime.combine(day, time(rng.randint(6, 21), rng.randint(0, 59)))
                pickup, dropoff = rng.sample(PLACES, 2)
                if rng.random() < 0.06:
                    fee = d2(rng.uniform(3.5, 5.5))
                    status, fare, tip, earn, dist, dur = "RIDER_CANCELLED", d2(0), d2(0), fee, d2(0), d2(rng.uniform(4, 9))
                else:
                    airport = "Airport" in pickup or "Airport" in dropoff
                    fare = d2(rng.uniform(32, 58) if airport else rng.uniform(7, 28))
                    tip = d2(rng.uniform(2, 12)) if rng.random() < 0.4 else d2(0)
                    earn = fare + tip
                    dist = d2(rng.uniform(18, 30) if airport else rng.uniform(2, 14))
                    dur = d2(float(dist) * 2.2 + rng.uniform(3, 10))
                    status, fee = "COMPLETED", d2(0)
                self.uber_trips.append({
                    "trip_id": self.uid(), "source": "fleet_hub", "trip_datetime_local": t.strftime("%Y-%m-%d %H:%M"),
                    "product": rng.choices(["UberX", "Comfort", "UberXL"], [80, 15, 5])[0], "type": "TRIP",
                    "status": status, "your_earnings": earn, "tip": tip, "fare_ex_tip": fare, "surge": "0.0",
                    "duration_min": dur, "distance_mi": dist, "pickup_address": pickup, "dropoff_address": dropoff,
                    "pickup_lat": "", "pickup_lng": "", "dropoff_lat": "", "dropoff_lng": "",
                    "request_time": (t - timedelta(minutes=rng.randint(3, 12))).strftime("%Y-%m-%d %H:%M:%S"),
                    "toll_refund": "0.0", "cancel_fee": fee, "license_plate": "DEMO123", "payment_found": "True",
                })
                events.append((t, earn))
                self.expected["uber_balance"] += earn

        for when, desc, amt in [
            (datetime(2026, 10, 11, 7, 0, 12), "You received an earnings adjustment so the amount you keep is more consistent week over week.", d2(8.02)),
            (datetime(2026, 10, 19, 3, 15, 40), "Quest bonus: complete 40 trips", d2(60)),
        ]:
            self.uber_other.append({"time": when.strftime("%Y-%m-%d %H:%M:%S"), "description": desc, "earnings": amt, "trip_uuid": ""})
            events.append((when, amt))
            self.expected["uber_balance"] += amt

        # Payouts: weekly every Monday 02:00, plus two instant cash-outs.
        cashouts = [(datetime(2026, 10, d, 2, 0, 16), False) for d in (5, 12, 19, 26)]
        cashouts += [(datetime(2026, 10, 9, 20, 31, 5), True), (datetime(2026, 10, 22, 21, 14, 48), True)]
        balance, timeline = d2(0), sorted(events + [(t, ("cashout", inst)) for t, inst in cashouts], key=lambda e: e[0])
        for t, item in timeline:
            if isinstance(item, tuple):
                instant = item[1]
                sent = balance
                fee = d2(1.75) if instant else d2(0)
                self.uber_payouts.append({"time": t.strftime("%Y-%m-%d %H:%M:%S"), "transferred": sent, "instant_pay_fee": fee})
                self.expected["uber_balance"] -= sent + fee
                balance = -fee
                if instant:
                    self.bank(t.date(), sent, "UBER TECHNOLOGIES INSTANT PAY", "MISC_CREDIT")
                elif t.day == 26:
                    self.exceptions.append({
                        "id": "E1", "type": "payout_missing_deposit", "date": "2026-10-26", "amount": str(sent),
                        "accounts": ["uber_balance", f"chase_{CHECKING}"],
                        "truth": "Uber weekly payout sent Oct 26 never posted to checking in October. Should be flagged as in transit or missing, not matched to anything.",
                    })
                else:
                    post = t.date() + timedelta(days=1)
                    self.bank(post, sent, self.ach("UBER USA 6787", "EDI PAYMNT", post), "ACH_CREDIT")
            else:
                balance += item

        # Lyft rides; weekly ACH every Tuesday for the prior Monday-Sunday.
        weekly = defaultdict(Decimal)
        for day in lyft_days:
            for _ in range(rng.randint(1, 4)):
                t = datetime.combine(day, time(rng.randint(6, 22), rng.randint(0, 59)))
                if rng.random() < 0.05:
                    product, typ, ride, tip, adj = "Passenger canceled", "ride_cancel", d2(5), d2(0), d2(0)
                    mins, miles = d2(rng.uniform(3, 8)), d2(0)
                else:
                    product = rng.choices(["Lyft", "Extra Comfort"], [85, 15])[0]
                    typ = "ride_lyft" if product == "Lyft" else "ride_lux"
                    ride = d2(rng.uniform(8, 42))
                    tip = d2(rng.uniform(1, 10)) if rng.random() < 0.35 else d2(0)
                    adj = d2(rng.uniform(1, 4)) if rng.random() < 0.05 else d2(0)
                    miles = d2(rng.uniform(2, 26))
                    mins = d2(float(miles) * 2.1 + rng.uniform(3, 9))
                total = ride + tip + adj
                self.lyft_trips.append({
                    "ride_id": self.hexid(), "accepted_local": t.strftime("%Y-%m-%d %H:%M"), "date": day.isoformat(),
                    "product": product, "type": typ, "ride_earnings": ride, "tip": tip, "other_adjustments": adj,
                    "total": total, "minutes": mins, "miles": miles, "rides_in_route": 1, "pay_status": 2,
                })
                weekly[day - timedelta(days=day.weekday())] += total
                self.expected["lyft_balance"] += total
        for monday, total in sorted(weekly.items()):
            pay_day = monday + timedelta(days=8)  # following Tuesday
            if pay_day > END:
                continue
            deposit = total
            if pay_day == date(2026, 10, 20):
                deposit = total - d2(5)
                self.exceptions.append({
                    "id": "E2", "type": "deposit_short", "date": pay_day.isoformat(), "amount": str(deposit),
                    "expected_amount": str(total), "accounts": ["lyft_balance", f"chase_{CHECKING}"],
                    "truth": "Lyft deposit is $5.00 less than the week's ride totals. Nothing in the ride export explains it; flag for review rather than force-match.",
                })
            # Lyft exports no payout file, so the payout never appears on the Lyft side;
            # the reconciler has to infer it from the weekly ride totals.
            self.bank(pay_day, deposit, self.ach("LYFT INC", "PAYMENTS", pay_day), "ACH_CREDIT")

    # ---------- Turo ----------
    def turo(self):
        rng = self.rng
        for vehicle_id in ("V-1001", "V-1002"):
            day = START + timedelta(days=rng.randint(0, 2))
            while True:
                length = rng.randint(2, 5)
                end = day + timedelta(days=length)
                if end > END:
                    break
                guest = rng.choice(GUESTS)
                label = f"{guest}’s trip\nWith Tesla Model Y 2025"
                url = f"https://turo.com/us/en/reservation/{self.digits(8)}"
                earn = d2(rng.uniform(78, 112) * length)
                rows = [(label, url, earn, d2(0))]
                if rng.random() < 0.3:
                    rows.append((label, url, -d2(rng.uniform(2, 9)), d2(0)))
                for row in rows:
                    self.turo_rows.append(["earning", row[0], row[1], "Tesla Model Y 2025", vehicle_id, end, row[2]])
                    self.expected["turo_balance"] += row[2]
                net = sum(r[2] for r in rows)
                paid = end + timedelta(days=3)
                if paid <= END:
                    self.turo_rows.append(["payment", f"Payment(*******{CHECKING})", "N/A", "", "", paid, net])
                    self.expected["turo_balance"] -= net
                    post = paid + timedelta(days=1)
                    if post <= END:
                        self.bank(post, net, self.ach("TURO INC", "PAYOUT", post), "ACH_CREDIT")
                    else:
                        self.exceptions.append({
                            "id": f"T-{paid.isoformat()}", "type": "in_transit", "date": paid.isoformat(), "amount": str(net),
                            "accounts": ["turo_balance", f"chase_{CHECKING}"],
                            "truth": "Turo payout sent at month end; deposit posts in November. Normal timing, not an error.",
                        })
                day = end + timedelta(days=rng.randint(0, 3))

        extra = date(2026, 10, 23)
        self.bank(extra, d2(64.20), self.ach("TURO INC", "PAYOUT", extra), "ACH_CREDIT")
        self.exceptions.append({
            "id": "E5", "type": "deposit_without_payout", "date": extra.isoformat(), "amount": "64.20",
            "accounts": [f"chase_{CHECKING}"],
            "truth": "Turo deposit with no payout row in the Turo earnings export (e.g. a claim reimbursement paid outside trip earnings). Should be flagged and asked about, not booked as trip revenue silently.",
        })

    # ---------- bank, cards ----------
    def expenses(self):
        rng = self.rng
        b = self.bank
        b(date(2026, 10, 3), d2(-612.40), self.ach("TESLA FINANCE LLC", "AUTO PMT", date(2026, 10, 3)), "ACH_DEBIT")
        b(date(2026, 10, 8), d2(-598.10), self.ach("TESLA FINANCE LLC", "AUTO PMT", date(2026, 10, 8)), "ACH_DEBIT")
        b(date(2026, 10, 5), d2(-286.40), self.ach("PROGRESSIVE INS", "INS PREM", date(2026, 10, 5)), "ACH_DEBIT")
        b(date(2026, 10, 9), d2(-40.00), "HCTRA EZ TAG REBILL 281-875-3279 TX card 4417", "DEBIT_CARD")
        b(date(2026, 10, 15), d2(-1500), "Online Transfer to CHK ...5678 transaction#: 18273645", "ACCT_XFER")
        b(date(2026, 10, 28), d2(-1200), "Online Transfer to CHK ...5678 transaction#: 18291102", "ACCT_XFER")
        b(date(2026, 10, 31), d2(-15), "MONTHLY SERVICE FEE", "FEE_TRANSACTION")

        # Card payments: a debit in checking and a credit on the card, same amount.
        b(date(2026, 10, 20), d2(-412.66), self.ach("CHASE CREDIT CRD", "AUTOPAYBUS", date(2026, 10, 20)), "ACH_DEBIT")
        self.card(date(2026, 10, 20), "AUTOMATIC PAYMENT - THANK", "", d2(412.66), kind="Payment")
        b(date(2026, 10, 22), d2(-318.90), self.ach("AMEX EPAYMENT", "ACH PMT", date(2026, 10, 22)), "ACH_DEBIT")
        self.amex_charge(date(2026, 10, 22), "AUTOPAY PAYMENT - THANK YOU", "", d2(-318.90))

        # Off-platform money: a Zelle from a person.
        z = date(2026, 10, 17)
        b(z, d2(150), "Zelle payment from TAYLOR MORGAN 1A2B3C4D5E", "QUICKPAY_CREDIT")
        self.exceptions.append({
            "id": "E6", "type": "unidentified_income", "date": z.isoformat(), "amount": "150.00",
            "accounts": [f"chase_{CHECKING}"],
            "truth": "Zelle from an individual with no platform record (possibly an off-platform Turo extension). Income, but the owner must say what it is.",
        })

        # Chase Ink: charging, washes, phone, supplies.
        for day in sorted(rng.sample(list(days(START, END)), 10)):
            self.card(day, "TESLA SUPERCHARGER US", "Automotive", -d2(rng.uniform(6, 24)))
        dup = date(2026, 10, 16)
        self.card(dup, "TESLA SUPERCHARGER US", "Automotive", d2(-17.36))
        self.card(dup, "TESLA SUPERCHARGER US", "Automotive", d2(-17.36))
        self.exceptions.append({
            "id": "E3", "type": "possible_duplicate", "date": dup.isoformat(), "amount": "-17.36",
            "accounts": [f"chase_ink_{INK}"],
            "truth": "Two identical Supercharger charges the same day. Could be two sessions; flag for the owner rather than drop or double count silently.",
        })
        self.card(date(2026, 10, 9), "BAYOU SHINE CAR WASH", "Automotive", d2(-18.00))
        self.card(date(2026, 10, 24), "BAYOU SHINE CAR WASH", "Automotive", d2(-45.47))
        self.card(date(2026, 10, 14), "T-MOBILE*AUTO PAY", "Bills & Utilities", d2(-85.00))
        self.card(date(2026, 10, 6), "PARKHOUSTON", "Travel", d2(-6.00))
        self.card(date(2026, 10, 12), "AMAZON MKTPL*ZT4K91", "Shopping", d2(-38.62))
        groceries = date(2026, 10, 18)
        self.card(groceries, "H-E-B #482", "Groceries", d2(-126.43))
        self.exceptions.append({
            "id": "E4", "type": "likely_personal", "date": groceries.isoformat(), "amount": "-126.43",
            "accounts": [f"chase_ink_{INK}"],
            "truth": "Grocery purchase on the business card. Likely personal: should be flagged as owner draw, not deducted.",
        })

        # Amex: software, insurance, a meal.
        for day, desc, cat, amt in [
            (date(2026, 10, 2), "OPENAI *CHATGPT SUBSCR", "Business Services-Internet Services", 20.00),
            (date(2026, 10, 3), "HISCOX INSURANCE", "Business Services-Insurance Services", 45.00),
            (date(2026, 10, 7), "APPLE.COM/BILL", "Merchandise & Supplies-Internet Purchase", 2.99),
            (date(2026, 10, 11), "WHATABURGER 1047", "Restaurant-Restaurant", 14.32),
            (date(2026, 10, 21), "GOOGLE *WORKSPACE BAYOU", "Business Services-Internet Services", 7.20),
        ]:
            self.amex_charge(day, desc, cat, d2(amt))

    # ---------- write ----------
    def write(self, out: Path):
        out.mkdir(parents=True, exist_ok=True)
        files = {}

        def dump(name, header, rows, quote=csv.QUOTE_MINIMAL, trailing=False):
            with open(out / name, "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f, quoting=quote)
                w.writerow(header)
                for r in rows:
                    w.writerow(list(r) + ([""] if trailing else []))
            files[name] = len(rows)

        trip_cols = list(self.uber_trips[0].keys())
        dump("uber_trips_fleet_hub.csv", trip_cols, [[t[c] for c in trip_cols] for t in sorted(self.uber_trips, key=lambda t: t["trip_datetime_local"])])
        dump("uber_payouts.csv", ["time", "transferred", "instant_pay_fee"], [[p["time"], p["transferred"], p["instant_pay_fee"]] for p in self.uber_payouts])
        dump("uber_other_earnings.csv", ["time", "description", "earnings", "trip_uuid"], [[o["time"], o["description"], o["earnings"], o["trip_uuid"]] for o in self.uber_other])
        lyft_cols = list(self.lyft_trips[0].keys())
        dump("lyft_trips.csv", lyft_cols, [[t[c] for c in lyft_cols] for t in sorted(self.lyft_trips, key=lambda t: t["accepted_local"], reverse=True)])

        def usd(x: Decimal) -> str:
            return f"-${-x:.2f}" if x < 0 else f"${x:.2f}"
        turo = []
        for kind, label, url, vehicle, vid, day, amt in sorted(self.turo_rows, key=lambda r: r[5], reverse=True):
            if kind == "payment":
                turo.append([label, url, "", "", day.isoformat(), "", usd(amt), ""])
            else:
                turo.append([label, url, vehicle, vid, day.isoformat(), usd(amt), "", ""])
        dump("turo_earnings_2026-10.csv", ["Type", "Reservation URL", "Vehicle", "Vehicle ID", "Date", "Earnings", "Payment", "Failed payment"], turo, quote=csv.QUOTE_ALL)

        bal, chk = OPENING_BALANCE, []
        for day, details, desc, amt, kind in sorted(self.checking, key=lambda r: r[0]):
            bal += amt
            chk.append([details, day.strftime("%m/%d/%Y"), desc, f"{amt:.2f}", kind, f"{bal:.2f}", ""])
        self.closing_balance = bal
        dump(f"Chase{CHECKING}_Activity_20261031.csv", ["Details", "Posting Date", "Description", "Amount", "Type", "Balance", "Check or Slip #"], list(reversed(chk)), trailing=True)

        ink = [[INK, d.strftime("%m/%d/%Y"), (d + timedelta(days=1)).strftime("%m/%d/%Y"), desc, cat, kind, f"{amt:.2f}", ""]
               for d, desc, cat, kind, amt in sorted(self.ink, key=lambda r: r[0], reverse=True)]
        dump(f"Chase{INK}_Activity_20261031.csv", ["Card", "Transaction Date", "Post Date", "Description", "Category", "Type", "Amount", "Memo"], ink)

        amex = [[d.strftime("%m/%d/%Y"), "", desc, "JORDAN RIVERA", "-41005", f"{amt:.2f}", desc, desc, "", "", "", "",
                 f"'{self.digits(18)}'", cat] for d, desc, cat, amt in sorted(self.amex, key=lambda r: r[0], reverse=True)]
        dump("amex_activity_2026-10.csv", ["Date", "Receipt", "Description", "Card Member", "Account #", "Amount", "Extended Details",
                                          "Appears On Your Statement As", "Address", "City/State", "Zip Code", "Country", "Reference", "Category"], amex)

        key = {
            "company": "Bayou City Mobility LLC (fictional)",
            "period": [START.isoformat(), END.isoformat()],
            "exceptions": sorted(self.exceptions, key=lambda e: e["id"]),
        }
        (out / "expected_exceptions.json").write_text(json.dumps(key, indent=2) + "\n")
        manifest = {
            "seed": SEED,
            "files": files,
            "account_net": {k: str(v) for k, v in sorted(self.expected.items())},
            "checking_opening_balance": str(OPENING_BALANCE),
            "checking_closing_balance": str(self.closing_balance),
        }
        (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
        return manifest


def generate(out: Path, seed: int = SEED) -> dict:
    m = Month(seed)
    m.rideshare()
    m.turo()
    m.expenses()
    return m.write(out)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default="samples/october")
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args()
    manifest = generate(Path(args.out), args.seed)
    print(json.dumps(manifest, indent=2))
