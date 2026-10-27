"""The month's reports: the P&L, the Schedule C draft and the mileage log tie to the books and to
each other, before and after the driver's answers and the receipt photo.

Offline and standard library only.
"""
from __future__ import annotations

import sys
import unittest
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import reports as R  # noqa: E402
from tests.test_app import books, photo_books, pile  # noqa: E402


def answered():
    b = books()
    return books({"decisions": {i["id"]: {"choice": (i["suggestion"] or i["choices"][0])["choice"]} for i in b.review_items()}})


class ProfitAndLoss(unittest.TestCase):
    def check_ties(self, b):
        p, t = R.profit_and_loss(b), b.totals()
        self.assertEqual(sum((v["net"] for v in p["by_line"].values()), Decimal("0")), t["profit"])
        self.assertEqual(sum((v["income"] for v in p["by_line"].values()), Decimal("0")), t["earned"])
        self.assertEqual(sum((v["expense"] for v in p["by_line"].values()), Decimal("0")), t["spent"])
        return p

    def test_ties_to_the_books_however_the_month_stands(self):
        for b in (books(), answered(), photo_books(), photo_books(pile())):
            self.check_ties(b)

    def test_income_lands_on_its_business_line(self):
        p = self.check_ties(books())
        self.assertEqual(p["income"]["Turo"]["turo"], books().turo()["total"])
        self.assertEqual(p["income"]["Uber"]["turo"], Decimal("0"))
        self.assertEqual(p["by_line"]["shared"]["income"], Decimal("0"))

    def test_spending_the_categorizer_calls_personal_stays_out(self):
        p = self.check_ties(photo_books())                      # before the receipt photo
        self.assertEqual(p["kept_out"]["Personal, kept out"], Decimal("14.32") + Decimal("126.43"))
        self.assertEqual(photo_books().totals()["profit"], photo_books(pile()).totals()["profit"])

    def test_a_receipt_answered_business_counts_after_all(self):
        b = books()
        key = next(i["id"] for i in b.review_items() if i["id"].startswith("receipt:2026-10-18"))
        business = books({"decisions": {key: {"choice": "business"}}})
        self.assertEqual(business.totals()["spent"] - b.totals()["spent"], Decimal("126.43"))
        self.assertEqual(business.schedule_c()["total_expenses"], business.totals()["spent"])


class ScheduleC(unittest.TestCase):
    def test_net_profit_is_the_profit(self):
        for b in (books(), answered(), photo_books(), photo_books(pile())):
            s, t = R.schedule_c_draft(b), b.totals()
            self.assertEqual(s["line_1"], t["earned"])
            self.assertEqual(s["line_28"], t["spent"])
            self.assertEqual(s["line_31"], t["profit"])
            self.assertEqual(sum(s["part_ii"].values(), Decimal("0")), s["line_28"])

    def test_vehicle_lines_come_from_the_mileage_log(self):
        b = books()
        s, m = R.schedule_c_draft(b), R.mileage_log(b)
        self.assertEqual(s["line_44a"], m["business"])
        self.assertEqual(s["standard_rate_value"], (m["business"] * Decimal("0.725")).quantize(Decimal("0.01")))
        self.assertLessEqual(s["vehicle_costs"], s["part_ii"]["9 Car and truck expenses"])


class Mileage(unittest.TestCase):
    def test_every_drive_is_logged_once_with_its_purpose(self):
        b = books()
        m = R.mileage_log(b)
        self.assertEqual(len(m["rows"]), len(b.drives))
        self.assertEqual(sum(m["miles"].values(), Decimal("0")), m["total"])
        self.assertAlmostEqual(float(m["total"]), sum(d.miles for d in b.drives), places=1)
        self.assertEqual(m["business"], m["miles"]["rider"] + m["miles"]["pickup"] + m["miles"]["between"])
        rides = sum(1 for r in m["rows"] if r["kind"] == "rider")
        self.assertEqual(rides, sum(1 for r in b.ride_profit()["rides"] if not r["cancelled"]))
        odo = [(r["start_odometer"], r["end_odometer"]) for r in m["rows"]]
        self.assertTrue(all(a <= z for a, z in odo))
        self.assertTrue(all(odo[i][1] <= odo[i + 1][0] + 0.01 for i in range(len(odo) - 1)))


class Shown(unittest.TestCase):
    def test_each_report_shows_and_downloads(self):
        b = books()
        for name in R.REPORTS:
            v = R.view(b, name)
            self.assertEqual(v["id"], name)
            self.assertFalse(v["status"]["final"])
            filename, rows = R.csv_rows(b, name)
            self.assertTrue(filename.endswith("2026-10.csv"))
            self.assertGreater(len(rows), 2)
            self.assertTrue(R.to_csv(rows).startswith(",".join(rows[0])))
        self.assertIsNone(R.view(b, "nope"))
        self.assertIsNone(R.csv_rows(b, "nope"))
        self.assertTrue(R.view(answered(), "pnl")["status"]["final"])

    def test_downloads_keep_cents(self):
        b = books()
        _, rows = R.csv_rows(b, "pnl")
        net = next(r for r in rows if r[1] == "Net profit")
        self.assertEqual(net[-1], f"{b.totals()['profit']:.2f}")
        _, log = R.csv_rows(b, "mileage")
        self.assertEqual(len(log), len(b.drives) + 1)


if __name__ == "__main__":
    unittest.main()
