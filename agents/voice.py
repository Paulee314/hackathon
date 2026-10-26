"""Turn a spoken note about cash spending into a cash transaction.

    python3 -m agents.voice samples/october

Muse Voice Transcribe turns the audio into text; Muse Spark turns the text into one cash
expense: date (words like "yesterday" are resolved against the day the note was recorded),
amount, payee, business line and Schedule C line. Personal spending is reported and left out
of the books. Results go to data/ledger/voice.json and data/ledger/cash.csv.
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Dict, Optional

from agents.meta import chat_json, transcribe
from ingest.schema import COLUMNS, Transaction

ROOT = Path(__file__).resolve().parents[1]
KEYWORDS = ["Hobby Airport", "Turo", "Uber", "Lyft", "Tesla", "Supercharger"]

PROMPT = """A driver who drives for Uber and Lyft and rents cars out on Turo recorded this voice note
on {recorded} ({weekday}). The words were transcribed by speech recognition:
"{transcript}"

Speech recognition often drops the decimal point in spoken prices: "eight fifty" can come out as "850" and
"nine ninety nine" as "999". When a number without a decimal point would be an implausible price for what
was bought, read it as dollars and cents and say so in "assumption". Ask in "question" only when both
readings are plausible, or the amount, date or purpose is missing.

Turn it into one cash expense. Return only JSON, no prose:
{{"date": "YYYY-MM-DD" (resolve words like "yesterday" against the recording date),
 "amount": number (dollars spent, positive),
 "payee": string, "description": short string,
 "business_line": "rideshare" | "turo" | "personal" | "unclear",
 "schedule_c_line": "<line number> <line name>" such as "9 Car and truck expenses" or "22 Supplies", or null if personal,
 "assumption": one short sentence naming any reading you made, such as "Heard 999, read as $9.99", otherwise null,
 "question": one short question for the driver when the note cannot be booked without an answer, otherwise null}}"""


def recorded_on(wav: Path) -> date:
    m = re.match(r"(\d{4}-\d{2}-\d{2})", wav.name)
    if m:
        return date.fromisoformat(m.group(1))
    return datetime.fromtimestamp(wav.stat().st_mtime).date()


def parse_note(transcript: str, recorded: date) -> Dict:
    prompt = PROMPT.format(recorded=recorded.isoformat(), weekday=recorded.strftime("%A"), transcript=transcript)
    return chat_json([{"role": "user", "content": prompt}])


def to_transaction(parsed: Dict, wav_name: str) -> Optional[Transaction]:
    """None when the note is personal or too unclear to book without asking."""
    if parsed.get("business_line") not in ("rideshare", "turo") or parsed.get("question"):
        return None
    amount = Decimal(str(parsed["amount"])).quantize(Decimal("0.01"))
    t = Transaction(date=parsed["date"], amount=-amount, source="voice", account="cash_on_hand", kind="cash",
                    description=parsed.get("description", ""), counterparty=parsed.get("payee", ""),
                    business_line=parsed["business_line"], category=parsed.get("schedule_c_line") or "",
                    ref=Path(wav_name).stem, evidence=f"voice/{wav_name}")
    t.assign_id(0)
    return t


def process(month: Path):
    notes = sorted((month / "voice").glob("*.wav"))
    results, booked = [], []
    for wav in notes:
        heard = transcribe(wav, keywords=KEYWORDS)
        text = heard.get("transcript", "").strip()
        parsed = parse_note(text, recorded_on(wav))
        txn = to_transaction(parsed, wav.name)
        if txn:
            booked.append(txn)
        if txn:
            status = "booked" + (f" ({parsed['assumption']})" if parsed.get("assumption") else "")
        elif parsed.get("question"):
            status = "asked: " + parsed["question"]
        else:
            status = "left out: " + str(parsed.get("business_line"))
        results.append({"file": wav.name, "recorded": recorded_on(wav).isoformat(), "transcript": text,
                        "audio_ms": heard.get("audioDurationMs"), "parsed": parsed, "status": status,
                        "txn_id": txn.txn_id if txn else None})
        print(f"  {wav.name:<26} \"{text}\"\n  {'':<26} -> {parsed.get('date')} ${parsed.get('amount')} "
              f"{parsed.get('business_line')} | {parsed.get('schedule_c_line')} | {status}")
    return results, booked


def main():
    ap = argparse.ArgumentParser(description="Turn voice notes into cash transactions.")
    ap.add_argument("month", nargs="?", default="samples/october")
    ap.add_argument("--out", default=str(ROOT / "data" / "ledger"))
    args = ap.parse_args()
    results, booked = process(Path(args.month))
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "voice.json").write_text(json.dumps(results, indent=2) + "\n")
    with open(out / "cash.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for t in booked:
            w.writerow({c: getattr(t, c) if getattr(t, c) is not None else "" for c in COLUMNS})
    print(f"\n{len(booked)} of {len(results)} notes booked as cash expenses. Wrote {out / 'voice.json'} and cash.csv")


if __name__ == "__main__":
    sys.exit(main())
