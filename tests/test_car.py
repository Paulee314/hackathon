"""Synthetic car data: drives, tire pressure and ride offers hang together.

    python3 -m unittest discover tests
"""
from __future__ import annotations

import json
import shutil
import sys
import tempfile
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "synth"))

from generate_october import generate  # noqa: E402
from car.telemetry import load_drives, load_offers, load_profile, load_tires  # noqa: E402


class CarData(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.month = cls.tmp / "october"
        cls.manifest = generate(cls.month)
        cls.drives = load_drives(cls.month)
        cls.tires = load_tires(cls.month)
        cls.offers = load_offers(cls.month)
        cls.truth = json.loads((cls.month / "car" / "car_truth.json").read_text())

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tmp)

    def test_miles_and_energy_match_generator(self):
        miles = sum(Decimal(str(d.miles)) for d in self.drives)
        kwh = sum(Decimal(str(d.kwh)) for d in self.drives)
        self.assertEqual(miles.quantize(Decimal("0.01")), Decimal(self.manifest["car_miles"]))
        self.assertEqual(kwh.quantize(Decimal("0.01")), Decimal(self.manifest["car_kwh"]))

    def test_drives_never_overlap_and_odometer_runs_on(self):
        for a, b in zip(self.drives, self.drives[1:]):
            self.assertLessEqual(a.end, b.start)
            self.assertAlmostEqual(a.end_odometer, b.start_odometer, places=1)

    def test_every_ride_has_an_accepted_offer_and_a_pickup_drive(self):
        accepted = {o.trip_id for o in self.offers if o.accepted}
        self.assertEqual(accepted, set(self.truth["rides"]))
        ids = {d.id for d in self.drives}
        for entry in self.truth["rides"].values():
            self.assertIn(str(entry["pickup_drive"]), ids)

    def test_some_offers_were_declined(self):
        declined = [o for o in self.offers if not o.accepted]
        self.assertGreater(len(declined), 20)
        self.assertTrue(all(o.trip_id == "" for o in declined))

    def test_rear_left_leak_is_planted(self):
        by_day = {t.at.date(): t for t in self.tires}
        self.assertLess(by_day[date(2026, 10, 24)].rear_left, 37.0)
        self.assertEqual(by_day[date(2026, 10, 24)].lowest[0], "rear left")
        self.assertGreater(by_day[date(2026, 10, 26)].rear_left, 40.0)
        self.assertGreater(by_day[date(2026, 10, 5)].rear_left, 41.0)

    def test_profile_present(self):
        profile = load_profile(self.month)
        self.assertEqual(profile["placard_tire_psi"], 42)


if __name__ == "__main__":
    unittest.main()
