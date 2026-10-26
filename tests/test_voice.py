"""Voice notes: the audio is in the format Muse Voice Transcribe takes, and notes become the
right cash transactions.

Runs offline. tests/fixtures/voice_parsed.json is a recorded run of the real pipeline
(transcript from Muse Voice Transcribe, parse from Muse Spark).
"""
from __future__ import annotations

import json
import sys
import unittest
import wave
from datetime import date
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from agents.voice import recorded_on, to_transaction  # noqa: E402
from ingest.schema import KINDS  # noqa: E402

VOICE = ROOT / "samples" / "october" / "voice"
FIXTURE = ROOT / "tests" / "fixtures" / "voice_parsed.json"


class Voice(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.truth = {t["file"]: t for t in json.loads((VOICE / "voice_truth.json").read_text())}
        cls.recorded = {r["file"]: r for r in json.loads(FIXTURE.read_text())}

    def test_audio_format(self):
        for name in self.truth:
            with wave.open(str(VOICE / name)) as w:
                self.assertEqual((w.getnchannels(), w.getsampwidth(), w.getframerate()), (1, 2, 16000), name)
                self.assertGreater(w.getnframes() / w.getframerate(), 1.0, name)

    def test_recorded_date_from_file_name(self):
        self.assertEqual(recorded_on(Path("2026-10-25_carwash-tip.wav")), date(2026, 10, 25))

    def test_business_notes_parse_to_the_answer_key(self):
        for name, t in self.truth.items():
            if t["business_line"] == "personal":
                continue
            p = self.recorded[name]["parsed"]
            self.assertEqual(p["date"], t["date"], name)
            self.assertEqual(Decimal(str(p["amount"])), Decimal(t["amount"]), name)
            self.assertEqual(p["business_line"], t["business_line"], name)
            self.assertTrue(str(p["schedule_c_line"]).startswith(t["line"]), name)

    def test_yesterday_resolved_against_recording_date(self):
        self.assertEqual(self.recorded["2026-10-25_carwash-tip.wav"]["parsed"]["date"], "2026-10-24")

    def test_booked_transactions_are_cash_outflows(self):
        for name, r in self.recorded.items():
            txn = to_transaction(r["parsed"], name)
            if self.truth[name]["business_line"] == "personal":
                self.assertIsNone(txn, name)
                continue
            self.assertIsNotNone(txn, name)
            self.assertEqual((txn.account, txn.kind, txn.source), ("cash_on_hand", "cash", "voice"))
            self.assertIn(txn.kind, KINDS)
            self.assertEqual(txn.amount, -Decimal(self.truth[name]["amount"]))
            self.assertTrue(txn.txn_id)

    def test_a_question_holds_the_entry(self):
        parsed = {"date": "2026-10-20", "amount": 40, "payee": "?", "business_line": "rideshare",
                  "schedule_c_line": "9 Car and truck expenses", "question": "What was the $40 for?"}
        self.assertIsNone(to_transaction(parsed, "2026-10-20_x.wav"))


if __name__ == "__main__":
    unittest.main()
