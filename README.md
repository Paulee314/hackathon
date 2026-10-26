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
cp .env.example .env        # then add your Meta Model API key to .env
python3 test_api.py         # smoke test: text, image generation, receipt reading
```

`.env` and `data/` are git-ignored. Real financial data never goes in this repo; the demo uses synthetic data only.

## Built during the hackathon

Everything here was written during the event unless listed in [PRE-EXISTING.md](PRE-EXISTING.md).
