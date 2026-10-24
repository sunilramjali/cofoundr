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

- **`main` = the working version. Nobody commits to it directly.** All changes go through small pull requests.
- Each person works on their own branches: `sunil/<topic>`, `ceyda/<topic>` (e.g. `sunil/atlas-schema`).
- **Pull from `main` often, at least every 1–2 hours** (`git pull origin main` into your branch). Small, frequent merges rarely clash.
- Never keep two separate copies of the project. One repo, one `main`.
- Before starting work, Claude checks the current branch. If it's `main`, it creates/switches to a personal branch first.

## Code layout & ownership

| Folder | Contents | Owner |
|---|---|---|
| `backend/db/` | MongoDB Atlas: connection, collections, indexes, search | **Sunil** |
| `backend/db/schema.py` | **Shared data contract**: collection names + document shapes | **Sunil** |
| `data/` | Synthetic seed data | **Sunil** |
| `backend/ai/` | Gemini, forecast, invoice extraction, prioritisation | TBD |
| `bridge/` | FastAPI `/brief`, `/approve` for Saint (Fly.io) | TBD |
| `dashboard/` | Streamlit | TBD |
| `.claude/` | Shared docs (protected by the hook) | Both |

- **Each folder has one owner.** To change something in your partner's folder, tell them first or ask them to make the change.
- **Data contract:** never hard-code collection names or change document shapes outside `backend/db/schema.py`. Any change there gets announced to the partner and noted in your log *before* merging.
- `.github/CODEOWNERS` mirrors this table (placeholders until GitHub usernames are added).

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
- **Every sponsor tool must visibly do the job it's best at** (MongoDB Atlas features, Gemini 4 Argon reasoning/multimodal, Saint on WhatsApp, Treg as tool gateway). When a feature ships, tick it in `winning-criteria.md`.
- **Synthetic data only**, clearly labelled. No real customer data, no secrets committed.
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
