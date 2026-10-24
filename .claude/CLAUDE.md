# CoFoundr — Encode London Hackathon 2026

AI co-founder for small business owners (demo business: GAIA Coffee, synthetic data).
Team of 2: **Sunil** and **Ceyda**. Submission deadline: **Sun 25 Oct, 12:00**.

## Project files

All planning docs live in `.claude/`. Read the relevant one before starting work.

| File | What it is | When to read | When to update |
|---|---|---|---|
| `project-plan.md` | Overall plan: pitch, stack, architecture, modules, timeline, demo flow, open questions | Start of every session; before any design or scope decision | When scope, stack or timeline changes. Tick off open questions as they're answered |
| `MVP.md` | Goal and done-criteria for each MVP stage | Before starting a task, to check which stage it belongs to | When a stage is finished, re-scoped or cut |
| `winning-criteria.md` | Targets to hit: tracks, sponsor bounties, judging weighting | Before choosing what to build next; before demo/README work | Tick checklist items as they ship; add confirmed judging details |
| `LOG-sunil.md` | Sunil's work log | To see what Sunil has done / is doing | After each chunk of Sunil's work |
| `LOG-ceyda.md` | Ceyda's work log | To see what Ceyda has done / is doing | After each chunk of Ceyda's work |

@project-plan.md
@MVP.md
@winning-criteria.md

## Git workflow

Repo: https://github.com/sunilramjali/cofoundr

```
sunil ──┐
        ├──► stage ──(PR only)──► main
jade  ──┘
```

| Branch | Who | Rules |
|---|---|---|
| `main` | Nobody directly | **Protected** (ruleset "main branch"): PR from `stage` only, **1 approval from the other person**, no force-push, no deletion. |
| `stage` | Integration branch | **Protected** (ruleset "stage branch"): PR from `sunil`/`jade` only, no direct pushes, no force-push, no deletion. |
| `sunil` | Sunil's working branch (GitHub `@sunilramjali`) | Sunil only |
| `jade` | Ceyda's working branch (Jade = Ceyda, GitHub `@JadeAlpay`) | Jade only |

**The formal flow (both of you, every change):**
1. Start from the latest stage: `git switch <you> && git pull origin stage`
2. Work and commit on your branch (`MVP<n>: …` messages), push: `git push origin <you>`
3. Open a PR **`<you>` → `stage`**: `gh pr create --base stage --head <you> --title "MVP<n>: …"`
4. Tell your partner; merge when green (stage PRs don't need an approval, so you're never blocked)
5. Your partner pulls: `git pull origin stage`
6. Releases: PR **`stage` → `main`**, **approved by the other person**, then merged

- **Pull from `stage` often, at least every 1–2 hours** (`git pull origin stage` into your branch). Small, frequent merges rarely clash.
- Never keep two separate copies of the project. One repo.
- Before starting work, Claude checks the current branch. If it's `main` or `stage`, it switches to the person's own branch first.
- The hook also **blocks Claude from pushing to `main` or `stage`** (🚫🔴 BLOCKED: direct push). Claude pushes only to the person's own branch and opens PRs.
- Don't create `sunil/...` or `stage/...` sub-branches: git can't have both `sunil` and `sunil/x`.

### Avoiding conflicts (working in parallel)
1. **Start synced.** A SessionStart hook (`hooks/sync-check.sh`) warns if you're on `main`/`stage`, behind `stage`, or have uncommitted changes. If it says you're behind: `git pull origin stage` before editing anything.
2. **Stay in your area.** Claude asks before editing the partner's folders (🟠 PARTNER'S AREA). Need a change there? Tell them, or ask them to make it.
3. **Small PRs, merged fast.** One feature per PR into `stage`; merge as soon as CI is green, then tell your partner to pull. Long-lived branches are what conflict.
4. **Pull again before opening a PR:** `git pull origin stage`, fix anything locally, re-run tests, then push.
5. **Shared files, by design:**
   - `requirements.txt`, `.env.example`: append-only; `.gitattributes` `merge=union` keeps both sides when you both add lines (local merges/pulls).
   - Tests: `tests/test_middle.py` (Sunil) and `tests/test_edges.py` (Jade). Never a shared test file.
   - `schema.py`: Sunil edits; Jade asks for new shapes.
   - Shared docs: tick only **your own** checkboxes; keep edits small; commit them on their own.
6. **If a conflict happens anyway:** don't force-push. Resolve on your branch (`git pull origin stage`, fix the marked sections, run tests, commit), and ask your partner when it's their code.

### CI/CD
- **CI** (`.github/workflows/ci.yml`): every PR into `stage`/`main` installs deps, compiles, runs `pytest tests` (fast, no MongoDB/Gemini needed). The `checks` job is a **required status check**: a red PR can't merge.
- **CD** (`.github/workflows/deploy.yml`): every push to `main` (= every approved release PR) runs `fly deploy`, then checks `/health`. **Live app = `main`.** Don't `fly deploy` by hand from feature branches, except to hotfix during the demo.
- Needs GitHub secret `FLY_API_TOKEN` (Fly deploy token scoped to the `cofoundr` app, 30-day expiry).
- **Tests:** `.venv/bin/python -m pytest -q tests` (385+ tests, ~20 s). They use an in-memory MongoDB (`mongomock`) and a fake Gemini from `tests/conftest.py` (`db`, `gemini_down`, `fake_extraction`, `sent` fixtures); an autouse guard makes any test that touches the real Atlas or Gemini fail. Tools: `pip install -r requirements-dev.txt`. CI also runs `pyflakes`.
- **Every new feature ships with a scenario test** for its obvious edge cases (see `docs/postmortem-2026-10-24.md`: 10 of 13 bugs would have been caught that way). Middle → `tests/test_middle.py`, edges → `tests/test_edges.py`.

### Commit messages
Format: **`MVP<n>: <one sentence of what changed>`**, where `<n>` is the MVP stage the change serves (see "Current stage" in `MVP.md`).
```
MVP1: Add Gemini invoice extraction with a line-item total check.
MVP2: Add POST /approve endpoint for WhatsApp approvals.
```
Keep the `Co-Authored-By` trailer after a blank line when Claude makes the commit.

## Code layout & ownership

**The split: Jade owns the edges (inputs + outputs), Sunil owns the middle (data + analysis + errors).**

```
 INPUTS (Jade)                 MIDDLE (Sunil)                        OUTPUTS (Jade)
 Gmail, WhatsApp in         →  pipeline.ingest_invoice()          →  dashboard UI, WhatsApp out via
 (Twilio webhook),             Gemini read + verify, MongoDB,        Twilio (replies, /updates pushed,
 external APIs, upload         overdue, approval rules, errors       brief), Gemini assistant, emails
```

| Folder / file | Contents | Owner |
|---|---|---|
| `backend/inputs/` | Input channels: Gmail intake, WhatsApp via Twilio (`whatsapp.py`: signed webhook, photos, "approve N"), external APIs (weather, events, Treg) | **Jade** |
| `backend/pipeline.py` | **Inputs → middle hand-off**: `ingest_invoice(bytes, mime, source, origin)` does extract → verify → save → log → overdue check. Every input calls this, nothing else | **Sunil** |
| `backend/sales.py` | **POS → database hand-off**: `record_orders(orders)`, `record_stock_count(count)` take the POS connector's format as-is; idempotent; writes `orders`, `sales` (time-series), `products`, `stock_counts` | **Sunil** |
| `backend/payments.py` | **Card payments → database hand-off**: `record_payments()` (EVO CSV, Square, SumUp), allow-listed fields, salted-hash ids, idempotent; `daily_takings()` | **Sunil** |
| `backend/ai/` | Gemini: invoice extraction + verification loop, overdue drafts, forecast (`forecast.py`) + self-grading (`review.py`), prioritisation, board meeting (`board.py`), card fee audit (`fees.py`) | **Sunil** |
| `backend/approvals.py` | Approval rules; the only place a decision is applied. Calls `outputs.email.send_reminder` after approval | **Sunil** |
| `backend/db/`, `backend/db/schema.py` | MongoDB connection, activity log, indexes/search; **the shared data contract** | **Sunil** |
| `data/` | Synthetic seed data, sample invoices | **Sunil** |
| `backend/outputs/` | Owner-facing output: `brief.py` (WhatsApp brief), `updates.py` (`/updates` messages), `whatsapp.py` (Twilio send + unprompted push), `assistant.py` (read-only Gemini answers), `email.py` (sending after approval) | **Jade** |
| `bridge/` | **The Fly.io web app**: routes in `main.py`, dashboard UI in `templates/` | **Jade** |
| `tests/test_middle.py` / `tests/test_edges.py` | Fast tests, no network: middle / edges | **Sunil** / **Jade** |
| `Dockerfile`, `fly.toml`, `.github/workflows/` | Deploy + CI/CD | **Sunil** |
| `.claude/` | Shared docs (protected by the hook) | Both |

- **The two seams.** Inputs → middle: invoices go through `pipeline.ingest_invoice` (use its `IngestResult`); till data goes through `sales.record_orders` / `sales.record_stock_count` (use its `SyncResult`); card payments go through `payments.record_payments`. Inputs never write MongoDB directly. Middle → outputs: read MongoDB through `schema.py` names; outputs never change data except via `approvals.decide`. Changing either seam = tell your partner first.
- **Each folder has one owner.** To change something in your partner's folder, tell them first or ask them to make the change.
- **Data contract:** never hard-code collection names or change document shapes outside `backend/db/schema.py`. Any change there gets announced to the partner and noted in your log *before* merging.
- No Streamlit. The dashboard is server-rendered HTML in the same Fly.io app.
- **No Saint** (dropped Sat 22:45). WhatsApp = Twilio; the conversation = Gemini.
- `.github/CODEOWNERS` mirrors this table.

## Edit permissions (to prevent clashes)

| File | Who can edit | How it's enforced |
|---|---|---|
| `LOG-sunil.md` | **Sunil only** | Hook blocks it when `COFOUNDR_USER=ceyda` (+ deny rule in Ceyda's local settings) |
| `LOG-ceyda.md` | **Ceyda only** | Hook blocks it when `COFOUNDR_USER=sunil` (+ deny rule in Sunil's local settings) |
| `CLAUDE.md`, `MVP.md`, `project-plan.md`, `winning-criteria.md` | Either of you, **after a prompt** | `.claude/settings.json` `ask` rules + `hooks/shared-doc-warning.sh` |

**Warnings you'll see** (from `hooks/shared-doc-warning.sh`):

| Edit to | Banner | Result |
|---|---|---|
| `CLAUDE.md` | 🟣 SHARED DOC: Claude's instructions | Asks + git pull reminder |
| `MVP.md` | 🟠 SHARED DOC: MVP stage goals | Asks + git pull reminder |
| `project-plan.md` | 🔵 SHARED DOC: scope, stack, timeline | Asks + git pull reminder |
| `winning-criteria.md` | 🟡 SHARED DOC: judging targets | Asks + git pull reminder |
| Your partner's log (Edit, Write **or** Bash) | 🚫🔴 BLOCKED | Denied |
| Your own log | 🟢 Your log | Allowed |
| Any log while `COFOUNDR_USER` is unset | ❓ LOG FILE: user not set | Asks |

- **Before editing a shared doc:** remind the user that **both partners must have pulled the latest branch** (`git pull`) first. Never edit one without the user's OK.
- **Never edit the other person's log**, including through Bash (`sed`, `echo >>`, etc.). Permission rules only cover the Edit and Write tools, so this rule covers the rest.
- `.claude/settings.local.json` is gitignored and personal. **Ceyda's setup:** create `.claude/settings.local.json` with:
  ```json
  {
    "env": { "COFOUNDR_USER": "ceyda" },
    "permissions": { "deny": ["Edit(**/.claude/LOG-sunil.md)"] }
  }
  ```

## Secrets
- Keys live in `.env` (gitignored). `.env.example` lists every variable with empty values and is committed. Add new variables to both.

## README
- `README.md` is for users and judges. It covers the problem, why this, how it solves it, how it's different, judging criteria and a user guide.
- The **judging criteria** and **user guide** sections are placeholders (_TBD_). Fill them in only once features are actually built and working, with evidence (file, endpoint, demo timestamp).

## Working rules

- **Priorities come from the docs, in this order:** `MVP.md` (current stage) → `project-plan.md` (module priority + cut order) → `winning-criteria.md` (bounty checklists). Don't build nice-to-haves before the current MVP stage is done.
- **Never cut the approval flow.** Every finance/money action needs owner approval first.
- **Every sponsor tool must visibly do the job it's best at** (MongoDB Atlas features, Gemini 4 Argon reasoning/multimodal, WhatsApp via Twilio, Treg as tool gateway). When a feature ships, tick it in `winning-criteria.md`.
- **Synthetic data only** in the repo and the demo DB, clearly labelled. No real customer data, no secrets committed. Real GAIA exports stay outside the repo and only go into a separate DB (`MONGODB_DB=gaia-real` + `PAYMENT_ID_SALT`); `cofoundr-db` refuses them. No real figures in logs, tests or docs.
- **Never wipe or re-seed the demo DB** unless Sunil asks. Anything that writes the demo DB (forecast, board, fee upload): tell the partner first.
- If a change affects scope, stack or timeline, update `project-plan.md` in the same session and note it in the log.

## Logging

Log in the file of the person you're working with (ask if unsure). Newest entry at the top. Keep entries short:

```
## Sat 24 Oct, 14:30 — <short title>
- **Did:** what was built/changed (files, endpoints, collections)
- **Status:** done / in progress / blocked
- **Next:** what comes next
- **Blockers / notes for partner:** anything the other person needs to know
```

At the start of a session, skim the top entries of **both** logs so work doesn't overlap.
