# CoFoundr: card payment data and privacy

How CoFoundr handles payment data from card providers (EVO, Square, SumUp). The demo runs on **synthetic data only**. This policy covers what happens when a business connects its real data.

## Purpose
Card payments are used for two things only: **forecasting takings** and **checking the card provider's fees**. Nothing else, and never to identify or profile customers.

## Sources

| Source | How | What it knows | Stored as |
|---|---|---|---|
| **EVO** | No merchant API: the owner exports *Transactions* as CSV from EVO's portal and uploads it (Sales page, or `python -m backend.inputs.evo`) | Date, amount, fee, card type. No time, no items | `payments` |
| **SumUp** | Read-only API, OAuth scope `transactions.history` | Time, amount, card brand, entry mode. No items | `payments` |
| **Square** | Read-only API, OAuth scope `ORDERS_READ` | Time, items sold, discounts, tender type | `orders` + `sales` (like the till) |

Only read endpoints are called. CoFoundr can't take, refund or move money through these connections.

## What is stored, per payment
Date (and time when the provider gives it), amount, fee, sale or refund, debit/credit, consumer/corporate, domestic/EEA/international, card brand, entry mode (contactless/chip), and a **salted hash** of the provider's transaction id (only so a re-import doesn't count a payment twice).

## What is never stored
- **Card numbers**, including masked ones (`535522******1234`). With the first 6 and last 4 digits, one customer's visits can be followed, which makes it personal data under UK GDPR.
- Cardholder names, emails, phone numbers, addresses (Square fulfilment recipients, customer ids).
- Authorisation codes, card-network references (ARN/RRN), card tokens and fingerprints.
- Card issuer country per payment (with date and amount it can single out a visitor).
- The provider's raw transaction id (hash only).
- Free text (SumUp product summaries, Square notes) and staff user accounts.

**How this is enforced:**
- Readers only read allow-listed columns (`backend/inputs/evo.py` `KEEP`; the Square and SumUp mappers).
- `backend/payments.py` stores only allow-listed fields, whatever an input passes.
- Tests check that no card-number-shaped text reaches the database from any connector (`tests/test_payments.py`).

## Where real data lives
- **Never in the shared demo database** (`cofoundr-db`). That database is read by `/brief` through a token in the URL, by both developers' local machines, and by demo resets, which is only acceptable for synthetic data. Storing real data there raises `RealDataRefused` and writes nothing (dashboard upload, command line, Square production alike).
- Real data goes into the business's own database (`MONGODB_DB=gaia-real`) and needs a secret `PAYMENT_ID_SALT`. The salt must never change: the first import remembers a fingerprint of it (not the salt itself), and an import with a different salt is refused rather than counting every payment twice.
- Raw exports **never enter the repo or the Docker image**: `*.csv` and `private/` are in `.gitignore` and `.dockerignore`, apart from `data/samples/*_synthetic.csv`. Keep them outside the project folder, and delete or archive them privately after import.
- Gemini only ever sees **daily totals**, never individual payments.

## Synthetic vs real
- **Square / SumUp:** sandbox counts as synthetic, production as real (`SQUARE_ENV`, `SUMUP_ENV`).
- **EVO CSV:** a file is synthetic only if every row's merchant name ends with `(SYNTHETIC)`, as in `data/samples/evo_transactions_synthetic.csv`. A single unmarked row makes the whole file real.

## Retention and rights
- Payment records are kept while the business uses CoFoundr and deleted on the owner's request (`db.payments.delete_many({"business_id": …})`).
- Customers can't be identified from what is stored, so there's nothing to look up per customer.
- The owner gives written consent before their real data is connected.
