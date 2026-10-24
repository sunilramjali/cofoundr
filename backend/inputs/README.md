# backend/inputs: input channels
**Owner: Jade**

Gmail intake, WhatsApp photos via Saint, external APIs (weather, events, Treg).

Every channel hands documents to the middle with **one call**:
```python
from backend.pipeline import ingest_invoice
result = ingest_invoice(data, mime_type, source="email", origin="email from billing@x.com: 'Invoice 7802'")
# result.ok, result.message (show/log it), result.invoice, result.needs_review, result.drafted, result.error
```
Don't call Gemini, save to MongoDB or run the overdue check yourself; the pipeline does it.

## Weather + events (for the forecast)
Both return plain dicts and never write MongoDB; storing them is the middle's job (shapes to agree in `schema.py`).
Failures raise `fetch.FetchError` with a plain-English message (API keys never appear in it).

| File | Source | Call | One dict per |
|---|---|---|---|
| `pitches.py` | where the truck trades (**assumption: Broadway Market only**, to confirm) | `PITCHES` | pitch |
| `weather.py` | Open-Meteo, no key | `forecast(pitch, days=7)`, `forecast_all()` | pitch per London day |
| `events.py` | Ticketmaster Discovery, `TICKETMASTER_API_KEY` | `nearby(pitch, days=7, radius_km=3)`, `nearby_all()` | event (cancelled ones left out) |

```python
# weather day
{"pitch_id": "broadway-market", "date": "2026-10-25", "summary": "Light drizzle", "weather_code": 51,
 "temp_max_c": 13.5, "temp_min_c": 9.4, "precip_mm": 1.2, "precip_chance_pct": 97, "wind_max_kmh": 18.0,
 "source": "open-meteo", "fetched_at": "<ISO UTC>"}           # any value can be None if Open-Meteo has no data
# event
{"source": "ticketmaster", "source_id": "...", "name": "...", "category": "Music", "genre": "Rock",
 "date": "2026-10-25", "starts_at": "2026-10-25T18:30:00Z" or None (time TBA), "status": "onsale",
 "venue": "...", "lat": 51.545, "lon": -0.055, "distance_km": 1.1, "pitch_id": "broadway-market",
 "url": "...", "fetched_at": "<ISO UTC>"}
```
Try them: `python -m backend.inputs.weather`, `python -m backend.inputs.events`.

## Gmail intake (`gmail.py`)
The demo inbox (`GMAIL_ADDRESS` + a 16-letter `GMAIL_APP_PASSWORD`) is checked over IMAP every `GMAIL_POLL_SECONDS` (60 on Fly, off locally) and by the dashboard's **Check inbox now** button (`POST /check-inbox`).
- PDF/image attachments are always read; an email without one is read as text **only if its subject mentions an invoice** (pitches and Google alerts never reach Gemini). Logos under 10 KB are ignored.
- Each email is handled once: it gets the Gmail label `CoFoundr/processed` (no DB writes here). If Gemini was busy, it stays unlabelled and the next check retries it. Nothing is marked as read.
- `check_inbox()` never raises → `InboxResult(ok, message, emails, error)`; one `gmail_checked` log line per check with new email; login/connection failures logged once, not every minute.
- Try it: `python -m backend.inputs.gmail` (real inbox + real Gemini + Atlas: it labels emails and saves invoices).

## Card payments: EVO CSV, Square, SumUp (`evo.py`, `square.py`, `sumup.py`; built by Sunil, flagged for Jade)
Read-only. Card data is never stored: see `docs/privacy.md`. Real data is refused in the demo database.
- **EVO** has no merchant API: the owner exports *Transactions* as CSV and uploads it on the **Sales** page (`POST /import/evo`) or runs `python -m backend.inputs.evo <path> [--dry-run]`. Only allow-listed columns are read → `backend.payments.record_payments`. Demo sample: `data/samples/evo_transactions_synthetic.csv` (`python -m data.make_evo_sample`).
- **Square** (`SQUARE_ACCESS_TOKEN`, `SQUARE_ENV`, scope `ORDERS_READ`): completed orders **with items** → POS format → `sales.record_orders`. `python -m backend.inputs.square --days 7 --dry-run`
- **SumUp** (`SUMUP_ACCESS_TOKEN`, `SUMUP_ENV`, scope `transactions.history`): payments → `record_payments`. `python -m backend.inputs.sumup --days 7 --dry-run`
- Sandbox = synthetic, production = real (needs a non-demo `MONGODB_DB` + `PAYMENT_ID_SALT`). Not on the poller yet.
