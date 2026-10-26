# Pre-existing work

Hackathon rules require a clear line between work done before the event and work done during it (Oct 26 – Nov 4, 2026).

Everything in this repository was written during the hackathon unless it is listed below.

| File or module | Origin | What it does | Changed during the hackathon? |
| --- | --- | --- | --- |
| _No code reused_ | | | |

## Data formats (not code)

| Format | Origin |
| --- | --- |
| Uber "fleet hub" CSV layout (`uber_trips_fleet_hub.csv`, `uber_payouts.csv`, `uber_other_earnings.csv`) | Column layout matches the output of a personal Uber data export tool written before the hackathon. Only the layout is mirrored; the parser in `ingest/parsers/uber.py` and the synthetic data were written during the hackathon. |
| Lyft, Turo, Chase and Amex CSV layouts | The platforms' and banks' own export formats. |
