# hackathon

Entry for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Startup track.

An agentic month-end close for gig-economy operators: rideshare drivers, Turo hosts and other 1099 workers. It takes platform payout reports, bank feeds, receipt photos and voice notes, reconciles them to the penny, and produces a ledger, a profit and loss statement and a Schedule C draft.

## Meta models used

| Model | Job |
| --- | --- |
| Muse Spark 1.3 (Model API) | Reads receipts and statements; reconciles payouts to deposits |
| Muse Voice Transcribe | Turns spoken expense notes into transactions |
| Muse Spark Open (open weights) | Categorizes transactions to Schedule C lines |
| Muse Image | Generates the synthetic receipts in the public demo dataset |

## Setup

```bash
cp .env.example .env                     # then add your Meta Model API key to .env
python3 test_api.py                      # smoke test: text, image generation, receipt reading
python3 synth/generate_october.py        # writes the synthetic month to samples/october
python3 -m ingest.load samples/october   # normalizes every export into data/ledger/transactions.csv
python3 -m unittest discover tests       # end-to-end checks
```

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

## How ingestion works

Each export is recognized by its columns, not its file name, and turned into one record shape (`ingest/schema.py`). Amounts are signed from the account's point of view, and platform balances act as clearing accounts, so a payout is a minus in `uber_balance` and a plus in checking. Overlapping exports are loaded once.

## Built during the hackathon

Everything here was written during the event unless listed in [PRE-EXISTING.md](PRE-EXISTING.md).
