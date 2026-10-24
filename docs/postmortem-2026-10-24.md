# Post-mortem: CoFoundr build, Sat 24 Oct 2026 (to ~15:30)

**Scope:** everything from repo setup to MVP1 (6/10) + the bug review. **Author:** Sunil (with Claude). **Status:** fixes in PR "MVP1: Bug fixes from the post-mortem".

## Summary
MVP1's core loop (invoice → read + verified → overdue → drafted → approved → logged) works and is live on Fly.io. The biggest time sink was **Gemini access (~2 h of intermittent 429s)**. A code review found **13 bugs**, 3 of them serious (a negated WhatsApp reply could approve a payment; invoices with the same number from two suppliers were silently dropped; concurrent overdue checks could reuse a `#ref`). All 13 are fixed and covered by **65 tests** that run in CI without network.

## Incidents

| # | What happened | Impact | Root cause | Fix / mitigation |
|---|---|---|---|---|
| 1 | Gemini returned **429 "billing account exceeded its monthly spending cap"**, on and off, then permanently | ~2 h blocked on uploads + Gemini drafts | Key came from a **`gcplab.me` lab account**: organiser-owned billing with a small shared cap | Template fallback kept the flow alive; clean "try again" messages; switched to a paid-tier key at 15:00 |
| 2 | A second key returned **429 with `limit: 0`** | Another dead end | **Free tier has 0 quota for `gemini-4-argon`**; the model is listed but unusable | Read `QuotaFailure.quotaId` in the error details; billing must be on the key's project |
| 3 | `/brief` token appeared in the **local uvicorn access log** (it's a query parameter) | Token exposed in output | Access logs print full URLs | Rotated the token before deploy; Fly runs with `--no-access-log`; Saint can use the `X-Token` header |
| 4 | Overdue check **re-drafted a reminder the same day one was sent** | Customer chased twice in a day | No memory of the last reminder | `REMINDER_GAP` = 3 days (`due_for_action`) |
| 5 | Push guard **blocked a commit** whose message mentioned "main" | Lost a few minutes | Regex scanned the whole command | Only the `git push …` segment is inspected |
| 6 | Couldn't create branch `sunil` | Lost a few minutes | Git can't have `sunil` and `sunil/setup` at once | Deleted merged `sunil/setup`; rule added to CLAUDE.md |
| 7 | Claude was **blocked from writing secrets** (GitHub `FLY_API_TOKEN`, Fly `GEMINI_API_KEY`) | Sunil ran 2 commands by hand | Auto-mode policy on secret stores (by design) | Commands provided with `!` prefix, piped so values are never printed |
| 8 | Partner-area 🟠 prompts **didn't stop edits** in this session | Guard only warns | **Auto mode approves hook "ask" decisions** itself | Banner still shows; outside auto mode it prompts. Don't rely on it alone; ownership is also in CODEOWNERS |

## Bugs found in review (all fixed)

| Sev | Bug | Fix | Tests |
|---|---|---|---|
| 🔴 | `/approve` text `"don't approve 1"` **approved #1** (regex searched anywhere in the message) | Strict whole-message grammar (`parse_reply`); anything else → 400 | 8 accepted + 12 refused phrasings; negation leaves the action pending |
| 🔴 | Duplicate check on invoice number only: **`INV-001` from a second supplier was dropped** | Identity = business + direction + normalised counterparty + number (`invoice_identity` index; Atlas migrated) | Same supplier/punctuation = duplicate; other supplier = new |
| 🔴 | Concurrent overdue checks (Gmail poller + upload + button) could **reuse a `#ref`** or **draft one invoice twice** | Atomic `counters` sequence (`next_refs`); partial unique index `one_pending_per_invoice`; `unique_ref` index | Refs keep counting; duplicate pending insert rejected |
| 🟠 | Reminder approved after the invoice was **paid** still went out | `decide` re-checks the invoice; overdue check withdraws stale actions (`status: cancelled`, nothing sent) | Paid-meanwhile → cancelled, outbox empty |
| 🟠 | **Send failure** left the approval "approved" with nothing sent | Claim atomically, then send; on error put it back to `pending` and log `send_failed` | Failed send → pending, original draft restored, retry works |
| 🟠 | Bad/empty Gemini JSON, safety block or network error → **500 on upload** | Pipeline never raises: `unreadable` / `gemini_unavailable` results, logged | 400, ValueError, ConnectionError all handled |
| 🟠 | Non-invoices (photo, `.txt`) saved as junk `UNKNOWN-…` £0, or "Gemini unavailable" shown for a wrong file type | MIME allow-list; `looks_like_invoice` → `not_invoice`, nothing saved | Unsupported types, MIME params, cat photo |
| 🟠 | `/brief?format=html` didn't escape: an emailed invoice named `<script>` → **XSS** | `html.escape` | Script name rendered escaped |
| 🟠 | After the final notice, a "final notice" every 3 days **forever** | `MAX_REMINDERS = 3`, then stop | 3 reminders → no new draft |
| 🟡 | Credit notes (total ≤ 0) chased; non-GBP shown as £; both parties "GAIA" guessed | New checks → flagged for review | Each case |
| 🟡 | Bad approval id / `{"ref":"abc"}` / `{"ref":true}` → 500 | `approvals.find` returns None; ref validated | 303 / 400 / 404 / 409 paths |
| 🟡 | Non-ASCII token → 500 instead of 403 | Compare bytes | Odd tokens → 403 |
| 🟡 | Brief always said `Reply "approve 1"` | Uses the first pending ref | Refs starting at 5 |

## What went well
- **Template fallback** meant the demo flow never broke, even with Gemini down for hours.
- **Verification loop** caught the deliberately wrong invoice every time and refused to "fix" the total.
- **PR flow + CI** caught nothing bad yet, but every change is reviewed and tested; deploy token verified before it was needed.
- **Activity log** made every step explainable, which also made debugging easy.

## What to do differently
- **Check model access on day one with a real call**, not just the model list: listing ≠ quota.
- **Write scenario tests with the feature**, not after: 10 of the 13 bugs would have been caught by a test of the obvious edge case.
- **Assume inputs are hostile** once they come from email/WhatsApp: escape output, validate types, parse replies strictly.
- **Anything that can run twice will**: use atomic counters and unique indexes, not "check then insert".

## Action items
| Owner | Item | When |
|---|---|---|
| Sunil | `fly secrets set GEMINI_API_KEY=…` (live app still has the capped key) | Now |
| Jade | Review the edge fixes in `bridge/main.py`, `backend/outputs/` (your area) | With the PR |
| Jade | Approve PR #3 → deploys all fixes + migration-compatible code | Today |
| Sunil | Gemini retry with backoff on 429/5xx (MVP2 "error hardening") | MVP2 |
| Jade | Real email sending: keep raising on failure so `decide` can roll back | MVP2 |
| Both | Add a scenario test with every new feature (`tests/test_middle.py`, `tests/test_edges.py`) | Always |

**Note:** Atlas indexes were migrated today (`invoice_identity`, `one_pending_per_invoice`, `unique_ref`). Until PR #3 deploys, the old live code saves invoices without `counterparty_key`; two different suppliers using the *same* invoice number in that window would be rejected rather than duplicated. Low risk, gone after the deploy.
