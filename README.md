# Ryde Off

Your AI bookkeeper for gig drivers: say what happened, and your month is closed.


Entry for the **Meta Global AI Developer Hackathon** (Oct 26 – Nov 4, 2026), Startup track.

An agentic month-end close for gig-economy operators: rideshare drivers, Turo hosts and other 1099 workers. It takes platform payout reports, bank feeds, receipt photos and voice notes, reconciles them to the penny, and produces a ledger, a profit and loss statement and a Schedule C draft.

## Meta models used

| Model | Job |
| --- | --- |
| Muse Spark 1.3 (Model API) | Reads receipt photos; turns voice-note transcripts into cash entries; reconciles payouts to deposits |
| SAM 3.1 (Model API) | Finds every receipt in one photo of the month's pile, so each can be cropped and read |
| Muse Voice Transcribe | Transcribes spoken notes about cash spending |
| Muse Spark Open (open weights, served by Baseten) | Categorizes spending to Schedule C lines; matches Muse Spark 1.3 on all 22 merchants in the demo month |
| Muse Image | Generates the synthetic receipts in the public demo dataset, and the car seat they are photographed on |

## Setup

```bash
cp .env.example .env                     # then add your Meta Model API key (and a Baseten key, for the categorizer) to .env
python3 test_api.py                      # smoke test: text, image generation, receipt reading
python3 synth/generate_october.py        # writes the synthetic month to samples/october
python3 -m ingest.load samples/october   # normalizes every export into data/ledger/transactions.csv
python3 -m agents.receipts               # Muse Spark reads the receipt photos and attaches each to its card charge
python3 -m agents.segment samples/october/receipt_pile.jpg   # SAM 3.1 finds each receipt in one photo, Muse Spark reads every crop (needs Pillow)
python3 -m agents.voice                  # Muse Voice Transcribe + Muse Spark turn voice notes into cash entries
python3 -m agents.categorize             # Muse Spark Open (on Baseten) maps every merchant to a Schedule C line
python3 -m agents.close                  # tie-out checks, exceptions, and the score against the answer key
python3 -m car.profit                    # what each ride really paid after car costs
python3 -m car.rules                     # replays ride rules ("skip pickups over 3 miles") on every offer in the month
python3 -m unittest discover tests       # end-to-end checks (offline; model output is replayed from tests/fixtures)
```

Every model call (Meta's API and Baseten) is logged to `data/usage.jsonl` with its token or audio usage, so spend is visible.

## Phone app

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.server:app --port 8765     # then open http://localhost:8765
```

A cloud sits in the middle of the screen with bubbles around it: profit, items to review, Turo, rides, receipts and the tire. Hold the cloud and talk to log cash you spent or ask about your month; tap a bubble to open it. The browser records 16 kHz WAV, Muse Voice Transcribe turns it into words, and Muse Spark either books a cash entry or answers from the books. Every number on screen is computed in code (`app/books.py`); the model may only use those numbers when it answers. The review bubble opens one card at a time with the evidence (the receipt photo or the voice note), Muse Spark's suggestion, and Approve, Change or Explain. You can also say a ride rule out loud ("what if I skipped pickups over three miles?"): Muse Spark turns it into a rule and the app replays it on the month.

The microphone and camera need `localhost` or https. The app pretends today is Oct 31, 2026 (`APP_TODAY`), so the synthetic month reads as almost closed. Each browser gets its own copy of the month (a cookie holds a random visitor id; state lives in `data/app/visitors/<id>/`), so several people can use it at once without seeing each other's answers. Settings has a reset that clears only yours. The first visit shows a notice that the data is made up and that nothing real should be uploaded or said. On a laptop (960 px and wider) a menu sits on the left, whatever you open slides in on the right while the clouds stay in view, the typing bar is always open, and holding Space talks; the menu hides with the button at the top left.

`.env` and `data/` are git-ignored. Real financial data never goes in this repo; the demo uses synthetic data only.

## Hosting

The live demo runs on [Modal](https://modal.com) from `deploy/modal_app.py`: one warm container serving the same FastAPI app over https, with visitor state and the usage log on a Modal Volume. `.env`, `data/` and `.venv` are never uploaded; the Meta key comes from a Modal Secret.

```bash
.venv/bin/pip install modal
.venv/bin/modal setup                                   # once: log in to Modal in the browser
.venv/bin/modal secret create ride-off-meta META_API_KEY="$(sed -n 's/^META_API_KEY=//p' .env)"   # once; the key is never printed
.venv/bin/modal deploy deploy/modal_app.py              # prints the https URL
```

Guards for a public link (`app/limits.py`), checked before any request that calls a Meta model:

- **Daily spend cap**: every model call writes its cost to the usage log (`agents/meta.py` prices tokens, images and audio at Meta's list prices). Once today's total reaches `APP_DAILY_BUDGET` (default $5), model requests return a plain message until tomorrow; everything already on screen keeps working.
- **Request limits**: 30 model requests per visitor and 90 per network address in any 10 minutes (`APP_VISITOR_LIMIT`, `APP_ADDRESS_LIMIT`). Explanations are cached, and a receipt photo that was already read isn't read again.
- **Size limits**: photos under 15 MB, voice notes under a minute.
- **Clean-up**: visitors idle for 2 days are removed (`APP_KEEP_DAYS`), and at most 500 are kept (`APP_MAX_VISITORS`).

After judging, set `MIN_CONTAINERS = 0` in `deploy/modal_app.py` and deploy again, or run `modal app stop ride-off`.

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
- **`receipt_pile.jpg`**: the same five receipts laid out on a car seat in one photo, the way a driver would snap them at month end. `synth/receipt_pile.py` builds it from the receipt photos and a Muse Image background.
- **`voice/`**: four spoken notes about cash spending (16 kHz mono WAV, recorded with macOS `say`). One says "yesterday", one is personal. `voice_truth.json` is the answer key.
- **`car/`**: Tessie-style drive and tire-pressure logs and every ride offer, accepted or declined, for one Tesla. The rear-left tire leaks slowly from Oct 12 until it is filled on Oct 26. `vehicle_profile.json` holds the cost assumptions (electricity rates, depreciation, tires).

## How ingestion works

Each export is recognized by its columns, not its file name, and turned into one record shape (`ingest/schema.py`). Amounts are signed from the account's point of view, and platform balances act as clearing accounts, so a payout is a minus in `uber_balance` and a plus in checking. Overlapping exports are loaded once.

## How receipts and voice notes work

`agents/receipts.py` asks Muse Spark to read each photo into JSON, then checks it without the model: the lines must add up to the subtotal, subtotal plus tax must equal the total, and there must be a card charge for exactly that amount within three days. A receipt that fails a check, or looks personal, is flagged for review.

`agents/segment.py` handles a whole pile in one photo. SAM 3.1 is asked for "receipt" and for "white paper" (on the demo photo, "receipt" alone missed one slip), and the two sets of boxes are merged in code: low-confidence boxes, near-duplicates and a box that wraps several slips are dropped, and the rest are put in reading order. Each box is cropped with a small margin, every crop is read by Muse Spark at the same time with the single-receipt prompt, and each reading goes through the same checks. One slip that can't be read doesn't stop the others. In the phone app the month starts with no receipts captured: tap **Snap receipts**, then take a photo or use the sample one. The boxes appear as SAM finds them (about 3 seconds), then fill in with amounts and matches as Muse Spark reads them (about 8 seconds). Personal-looking receipts join the review queue. Set `APP_RECEIPTS=preloaded` to start with the five receipts already read instead.

`agents/voice.py` sends each note to Muse Voice Transcribe, then asks Muse Spark for one cash expense: the date ("yesterday" is worked out from the day the note was recorded), amount, payee, business line and Schedule C line. Speech recognition often drops the decimal point in spoken prices ("nine ninety nine" comes back as "999"), so the parser books the plausible reading and records the assumption it made, and asks the driver only when both readings are plausible. Personal spending is left out of the books.

## How the reports work

`app/reports.py` builds three reports from the same booked amounts as the profit on the home screen, so they always agree. No model is called.

- **Profit and loss** by business line: Uber and Lyft are rideshare, Turo is Turo, and costs that serve both (phone, software, business insurance, bank fees) stay in a Shared column rather than being split by a guess. Loan payments, transfers and personal spending are listed but kept out of profit.
- **Schedule C draft** for the month: Part I, Part II on the lines Muse Spark gave each merchant, lines 28 to 31, and Part IV miles from the mileage log, with the standard-rate comparison for the rideshare car.
- **Mileage log**: every drive of the rideshare car with its odometer readings, places and purpose (rider on board, driving to a pickup, between rides on the apps, commuting, personal).

Each opens in the app with a draft or final status and downloads as CSV (`/api/reports/{name}/download`). Spending the categorizer labels personal stays out of profit unless the driver answers a receipt card "business".

## How the close agent works

`agents/close.py` makes every decision about a number in code, so each finding carries the rows that prove it. It matches Uber and Turo payouts to bank deposits to the penny, matches each Lyft deposit to the Monday-to-Sunday week of rides it pays for (Lyft exports no payouts), matches card payments between accounts, looks for identical charges on one day and for money in that nothing explains, and ties checking to its statement: opening balance plus the month's activity must equal the closing balance. On the demo month it catches all six planted problems with no false alarms, and asks the owner one question the records can't answer (below).

What it can't explain becomes a review card, biggest amount first. Each answer has a defined effect on the books (a Lyft shortfall booked as a fee adds $5.00 of fees; a duplicate charge left out stays out), and each tie-out check reads Passes, Answered or Needs you. Where the records can't tell (a Zelle from a person), the card asks instead of suggesting.

A meal on the business card is one of those. Tax law makes a meal a business cost only on travel, away from home overnight or long enough to need sleep or rest, and then at 50% (IRC §162(a)(2) and §274(n)(1), Schedule C line 24b); a meal between rides in your own city is personal (Treas. Reg. §1.262-1(b)(5); IRS Publication 463). No receipt can say which, so the card states the rule, shows what each answer does, and lets the driver choose. The answer key lists it as an owner question (`owner_questions` in `expected_exceptions.json`), not a planted error.

Corrections become rules. A receipt you call personal or business is treated that way for every charge at that merchant, and the review screen lists what your answers taught the books. `/api/rules` downloads them; `python3 -m agents.categorize --rules rules.json` applies them next month before the model sees anything: a merchant you called personal is never sent to the model, and one you called business is sent with that said, so the model only picks its line.

`agents/categorize.py` sends each merchant once to Muse Spark Open, Meta's open-weights model, served by Baseten. It labels each one with a Schedule C line and a treatment: business expense, vehicle cost (counts only under the actual-expense method), personal, transfer, loan payment or owner draw. Totals are always added up in code. Because the weights are open, a firm could run this step on its own hardware and keep client data in-house. On the demo month it matches Muse Spark 1.3 on all 22 merchants, treatment and Schedule C line ($5,612.11 of spending), in two runs; the one difference is a business-line call (DetailPro Supply, $38.62: Turo rather than shared). `scripts/compare_categorizers.py` reruns the comparison. The app reads the saved labels, so the hosted demo makes no Baseten calls.

`car/profit.py` ties each accepted ride offer to the drive to the pickup and the trip itself, by time and distance, since Tessie drives carry no trip ids (all 134 rides link correctly against the answer key). Each ride's true profit is its fare and tip minus electricity, wear on the car and tire wear, with the unpaid pickup miles charged to the ride.

## How ride rules work

Drivers trade rules of thumb like "never take a pickup over 3 miles". `car/rules.py` tests one against the driver's own month. The rule sits on top of the driver's judgment: rides the driver took keep their real times and real profit, and the rule drops the ones it would have skipped. In the time that frees, offers the driver passed on that fit the rule are taken if they arrived within 20 minutes of the car being free. Their profit is estimated from the offer card, the month's tip rate and the cost per mile. Hours on the road come from merging offers and rides into stretches (a gap over 30 minutes counts as off), so rules are compared by true profit and by profit per hour on the road. With no rule, the replay gives back the real month exactly.

On the demo month, skipping pickups over 3 miles would have cost $332.58, because those rides paid about the same per hour as the rest. Holding out for $1 a mile would have made $42.45 more. Muse Spark only turns the spoken rule into JSON; every number comes from the replay.

`demo/offers_sheet.py` writes the same replay to `demo/ride_offers_october.xlsx` for checking by hand: all 238 synthetic offers, what the driver did, the true profit used, and Take, Skip or Busy under five rules, with the cost assumptions as live inputs.

```bash
.venv/bin/python -m demo.offers_sheet samples/october
```

## Built during the hackathon

Everything here was written during the event unless listed in [PRE-EXISTING.md](PRE-EXISTING.md).
