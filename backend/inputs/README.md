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
- **Saint channel:** Thea (our Saint agent) reads links but can't POST, so she emails the inbox. An email from `SAINT_EMAIL` that Gmail verified (top `Authentication-Results` from `mx.google.com`, DKIM/DMARC/SPF pass for her exact domain): a photo/PDF → `ingest_invoice(source="whatsapp_photo")`; a body line that is exactly "approve 4" / "reject 2" → `approvals.decide(via="whatsapp")` (one per email; quoted replies ignored). A forged From: is logged as `saint_email_rejected` and decides nothing. The owner hears the result via `/updates`, which Thea polls. Reply parsing lives in `replies.py` (shared with `/approve` and the chat bar).
- Try it: `python -m backend.inputs.gmail` (real inbox + real Gemini + Atlas: it labels emails and saves invoices).
