# Sunil — Work Log

Newest entry at the top. Format:

```
## <Day> <date>, <time> — <short title>
- **Did:**
- **Status:** done / in progress / blocked
- **Next:**
- **Blockers / notes for partner:**
```

---

## Sat 24 Oct — Gemini invoice extraction + verification loop (MVP1)
- **Did:** `backend/ai/gemini.py` (shared client), `backend/ai/extract.py` (Gemini 4 Argon structured extraction → checks → one re-check → `needs_review`), `data/make_samples.py` + 3 synthetic invoice images in `data/samples/`
- **Tested:** clean supplier ✓ 1st try; GAIA-issued overdue ✓ receivable/overdue; messy (total £263 vs items £237) → re-checked, kept printed value, confidence 0.4, flagged for review. Save + duplicate detection ✓ (test doc removed)
- **Contract change (`schema.py`):** `Invoice` gains `verification_issues`, `extraction_notes`
- **Status:** done
- **Next:** overdue check + reminder drafts → approvals
- **Blockers / notes for partner:** Gemini billing account intermittently hits its monthly spend cap (429), raise at Google DeepMind booth

## Sat 24 Oct — MongoDB client + seed script (MVP1)
- **Did:** `backend/db/client.py` (shared sync PyMongo client, `get_db()`, `ping()`, `ensure_indexes()`), `data/seed.py` (synthetic GAIA Coffee: 1 business, 10 invoices, 4 overdue), `requirements.txt`, `.venv` on Python 3.11
- **Contract change (`schema.py`):** added `Business`; `Invoice.supplier` → `direction` (receivable/payable) + `counterparty`, plus `invoice_number`, `issue_date`, `paid_at`, `needs_review`, `reminders_sent`, `synthetic`, `created_at`
- **Status:** done; Atlas connected (ping ok) and seeded: 3 collections, 10 invoices (4 overdue), indexes created
- **Next:** create Atlas cluster → `python -m data.seed` → Gemini extraction
- **Blockers / notes for partner:** invoices now distinguish money owed *to* GAIA (chase) from money GAIA owes (pay)
