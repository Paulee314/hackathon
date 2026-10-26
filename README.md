# hackathon

Entry for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Startup track.

An agentic month-end close for gig-economy operators: rideshare drivers, Turo hosts and other 1099 workers. It takes platform payout reports, bank feeds, receipt photos and voice notes, reconciles them to the penny, and produces a ledger, a profit and loss statement and a Schedule C draft.

## Meta models used

| Model | Job |
| --- | --- |
| Muse Spark 1.3 (Model API) | Reads receipt photos; turns voice-note transcripts into cash entries; reconciles payouts to deposits |
| Muse Voice Transcribe | Transcribes spoken notes about cash spending |
| Muse Spark Open (open weights) | Categorizes transactions to Schedule C lines |
| Muse Image | Generates the synthetic receipts in the public demo dataset |

## Setup

```bash
cp .env.example .env                     # then add your Meta Model API key to .env
python3 test_api.py                      # smoke test: text, image generation, receipt reading
python3 synth/generate_october.py        # writes the synthetic month to samples/october
python3 -m ingest.load samples/october   # normalizes every export into data/ledger/transactions.csv
python3 -m agents.receipts               # Muse Spark reads the receipt photos and attaches each to its card charge
python3 -m agents.voice                  # Muse Voice Transcribe + Muse Spark turn voice notes into cash entries
python3 -m unittest discover tests       # end-to-end checks (offline; model output is replayed from tests/fixtures)
```

Every Meta API call is logged to `data/usage.jsonl` with its token or audio usage, so spend is visible.

`.env` and `data/` are git-ignored. Real financial data never goes in this repo; the demo uses synthetic data only.

## Demo data

`samples/october/` is one synthetic month for a fictional operator, Bayou City Mobility LLC: Uber and Lyft driving plus two Teslas on Turo, a checking account and two cards. Files match the column layouts of real Uber, Lyft, Turo, Chase and Amex exports.

Every payout reaches the bank except six planted problems. `expected_exceptions.json` is the answer key used to score the close agent:

| ID | Planted problem |
| --- | --- |
| E1 | Uber payout with no bank deposit |
| E2 | Lyft deposit $5.00 short of the week's rides |
| E3 | Two identical Supercharger charges on one day |
| E4 | Groceries on the business card |
| E5 | Turo deposit with no payout in the Turo export |
| E6 | Zelle from a person, no platform record |

The month also has:

- **`receipts/`**: five receipt photos made with Muse Image for fictional merchants, each adding up to a real card charge in the month. `receipts_truth.json` is what was printed.
- **`voice/`**: four spoken notes about cash spending (16 kHz mono WAV, recorded with macOS `say`). One says "yesterday", one is personal. `voice_truth.json` is the answer key.
- **`car/`**: Tessie-style drive and tire-pressure logs and every ride offer, accepted or declined, for one Tesla. The rear-left tire leaks slowly from Oct 12 until it is filled on Oct 26. `vehicle_profile.json` holds the cost assumptions (electricity rates, depreciation, tires).

## How ingestion works

Each export is recognized by its columns, not its file name, and turned into one record shape (`ingest/schema.py`). Amounts are signed from the account's point of view, and platform balances act as clearing accounts, so a payout is a minus in `uber_balance` and a plus in checking. Overlapping exports are loaded once.

## How receipts and voice notes work

`agents/receipts.py` asks Muse Spark to read each photo into JSON, then checks it without the model: the lines must add up to the subtotal, subtotal plus tax must equal the total, and there must be a card charge for exactly that amount within three days. A receipt that fails a check, or looks personal, is flagged for review.

`agents/voice.py` sends each note to Muse Voice Transcribe, then asks Muse Spark for one cash expense: the date ("yesterday" is worked out from the day the note was recorded), amount, payee, business line and Schedule C line. Speech recognition often drops the decimal point in spoken prices ("nine ninety nine" comes back as "999"), so the parser books the plausible reading and records the assumption it made, and asks the driver only when both readings are plausible. Personal spending is left out of the books.

## Built during the hackathon

Everything here was written during the event unless listed in [PRE-EXISTING.md](PRE-EXISTING.md).
