"""Record the synthetic month's voice notes about cash spending, with macOS `say`.

Each note is a 16 kHz mono 16-bit WAV, the format Muse Voice Transcribe takes. The file name
starts with the day it was recorded, the way a phone would stamp it. One note says
"yesterday", so the agent has to work out the date; one is personal and should stay out of
the books.

    python3 synth/voice_notes.py
"""
from __future__ import annotations

import json
import re
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "samples" / "october" / "voice"

NOTES = [
    {"file": "2026-10-14_parking.wav", "voice": "Samantha",
     "text": "Twelve dollars cash for parking at Hobby Airport, waiting on a rider.",
     "expect": {"date": "2026-10-14", "amount": "12.00", "business_line": "rideshare", "line": "9"}},
    {"file": "2026-10-21_water.wav", "voice": "Eddy (English (US))",
     "text": "Bought a case of water for riders. Nine ninety nine, paid cash.",
     "expect": {"date": "2026-10-21", "amount": "9.99", "business_line": "rideshare", "line": "22"}},
    {"file": "2026-10-25_carwash-tip.wav", "voice": "Samantha",
     "text": "Yesterday I gave the car wash guys a five dollar cash tip, for the Turo car.",
     "expect": {"date": "2026-10-24", "amount": "5.00", "business_line": "turo", "line": "9"}},
    {"file": "2026-10-27_lunch.wav", "voice": "Eddy (English (US))",
     "text": "Eight fifty cash for lunch. That one's personal.",
     "expect": {"date": "2026-10-27", "amount": "8.50", "business_line": "personal", "line": None}},
]


def available_voices() -> set:
    out = subprocess.run(["say", "-v", "?"], capture_output=True, text=True).stdout
    # lines look like "Eddy (English (US)) en_US    # Hello!"; the name is everything before the locale
    return {m.group(1) for m in (re.match(r"(.+?)\s+[a-z]{2,3}_[A-Z0-9]{2,3}\s", line) for line in out.splitlines()) if m}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    voices = available_voices()
    for n in NOTES:
        voice = n["voice"] if n["voice"] in voices else "Samantha"
        with tempfile.TemporaryDirectory() as tmp:
            aiff = Path(tmp) / "note.aiff"
            subprocess.run(["say", "-v", voice, "-o", str(aiff), n["text"]], check=True)
            subprocess.run(["afconvert", "-f", "WAVE", "-d", "LEI16@16000", "-c", "1", str(aiff), str(OUT / n["file"])],
                           check=True)
        print(f"  {n['file']}  ({voice})")
    truth = [{"file": n["file"], "spoken": n["text"], **n["expect"]} for n in NOTES]
    (OUT / "voice_truth.json").write_text(json.dumps(truth, indent=2) + "\n")


if __name__ == "__main__":
    main()
