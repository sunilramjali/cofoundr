"""Card fee audit (MVP2): Gemini reads the card provider's fee summary (a photo or PDF), code checks every payment.

UK card providers must give merchants a one-page "card payments summary" (Payment Systems Regulator). It has the
rates per card type and a worked example ("£10 on a debit card in person = 9p").

1. Read (Gemini, multimodal): rates, extra charges, contract end, and the worked examples printed on the page.
2. Verify the reading (code): rates are sensible, and the rates Gemini read reproduce the page's own worked examples
   to the penny. If not, Gemini looks again once with the exact problems; still wrong = the owner checks, no audit.
3. Audit (code): every card payment's charged fee vs the contract (UK cards, in person; the authorisation fee is billed
   separately, so it isn't in a payment's fee). Overcharges are listed; months whose fees came to less than the minimum
   monthly charge are flagged (the provider tops them up).
4. A ranked task asks the owner to query it with the provider. Nothing is sent or claimed without the owner.

Usage (from the repo root):
    python -m backend.ai.fees data/samples/card_fees_synthetic.png             # read, verify, audit, store
    python -m backend.ai.fees data/samples/card_fees_synthetic.png --dry-run
"""
import argparse
import json
import mimetypes
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Literal, Optional, Tuple

from google.genai import types
from pydantic import BaseModel, Field

from backend.ai.gemini import MODEL, get_client, with_retry
from backend.db import schema

BUSINESS_ID = "gaia-coffee"
PENNY = 0.011  # a payment is overcharged if it cost more than a penny (rounding) above the contract
CardKind = Literal["debit", "credit", "business"]


class Rate(BaseModel):
    card: CardKind
    channel: Literal["in_person", "online"]
    pct: float = Field(description="Percentage, e.g. 0.30 for 0.30%")
    fixed_gbp: float = Field(description="Fixed pence per payment in pounds, e.g. 0.03")


class Example(BaseModel):
    description: str
    amount_gbp: float
    card: CardKind
    channel: Literal["in_person", "online"]
    total_fee_gbp: float = Field(description="The total fee the page states for this example, including any authorisation fee")


class FeeSchedule(BaseModel):
    """Exactly what the page says. Missing values are null, never guessed."""
    provider: Optional[str] = None
    contract_end: Optional[str] = Field(None, description="YYYY-MM-DD")
    rates: List[Rate] = Field(default_factory=list)
    auth_fee_gbp: Optional[float] = Field(None, description="Per-transaction authorisation fee, e.g. 0.03")
    chargeback_fee_gbp: Optional[float] = None
    min_monthly_gbp: Optional[float] = Field(None, description="Minimum monthly service charge")
    examples: List[Example] = Field(default_factory=list, description="Every worked example printed on the page")
    marked_synthetic: bool = Field(False, description="True only if the page says it is synthetic / a demo")
    notes: Optional[str] = None


PROMPT = """This is a card provider's fee summary for a small UK business. Extract exactly what is printed:
every rate (card type × in person / online, as a percentage plus fixed amount), the authorisation, chargeback and
minimum monthly charges, the contract end date, and EVERY worked example with the total fee it states.
Never compute or correct numbers: copy them as printed. Missing values are null; say why in notes."""

RECHECK = """You previously read this page as:
{previous}

Automatic checks found these problems:
{problems}

Look at the page again very carefully and re-read it. Copy every number exactly as printed."""


# ---------------------------------------------------------------- read + verify

def rate_for(schedule: FeeSchedule, card: str, channel: str = "in_person") -> Optional[Rate]:
    return next((r for r in schedule.rates if r.card == card and r.channel == channel), None)


def example_fee(schedule: FeeSchedule, e: Example) -> Optional[float]:
    rate = rate_for(schedule, e.card, e.channel)
    if rate is None:
        return None
    return round(round(e.amount_gbp * rate.pct / 100 + rate.fixed_gbp, 2) + (schedule.auth_fee_gbp or 0), 2)


def verify(schedule: FeeSchedule) -> List[str]:
    """Empty list = the reading is trusted. The page's own worked examples are the answer key."""
    problems = []
    for card in ("debit", "credit"):
        if rate_for(schedule, card) is None:
            problems.append(f"no in-person {card} rate")
    for r in schedule.rates:
        if not 0 <= r.pct <= 5 or not 0 <= r.fixed_gbp <= 1:
            problems.append(f"{r.card} {r.channel} rate {r.pct}% + £{r.fixed_gbp} isn't a plausible card fee")
    if not schedule.examples:
        problems.append("no worked example found to check the rates against")
    for e in schedule.examples:
        fee = example_fee(schedule, e)
        if fee is None:
            problems.append(f"example '{e.description}' uses a {e.card} {e.channel} rate that wasn't read")
        elif abs(fee - e.total_fee_gbp) > 0.005:
            problems.append(f"example '{e.description}': the rates read give £{fee:.2f}, the page says £{e.total_fee_gbp:.2f}")
    if schedule.contract_end:
        try:
            datetime.strptime(schedule.contract_end, "%Y-%m-%d")
        except ValueError:
            problems.append(f"contract_end {schedule.contract_end!r} isn't a YYYY-MM-DD date")
    return problems


def ask_gemini(data: bytes, mime: str, prompt: str) -> FeeSchedule:
    response = with_retry(lambda: get_client().models.generate_content(
        model=MODEL, contents=[types.Part.from_bytes(data=data, mime_type=mime), prompt],
        config=types.GenerateContentConfig(response_mime_type="application/json", response_schema=FeeSchedule),
    ))
    if not response.text:
        raise ValueError("Gemini returned an empty response.")
    return FeeSchedule.model_validate_json(response.text)


def read_schedule(data: bytes, mime: str) -> Tuple[FeeSchedule, List[str], int]:
    """(schedule, problems still left, attempts). Re-reads once with the problems, like invoice extraction."""
    schedule = ask_gemini(data, mime, PROMPT)
    problems = verify(schedule)
    if not problems:
        return schedule, [], 1
    schedule = ask_gemini(data, mime, PROMPT + "\n\n" + RECHECK.format(previous=schedule.model_dump_json(), problems="\n".join(f"- {p}" for p in problems)))
    return schedule, verify(schedule), 2


# ---------------------------------------------------------------- audit (code only)

def audit(payments: List[dict], schedule: FeeSchedule) -> dict:
    """Charged vs contract for every stored card payment with a fee. In person; UK cards only (the summary's scope)."""
    over, checked, matched, not_covered = [], 0, 0, 0
    monthly = defaultdict(float)
    for p in payments:
        if p.get("fee") is None or p.get("kind") != "sale":
            continue
        monthly[p["date"][:7]] += p["fee"]
        if p.get("region") not in (None, "domestic"):
            not_covered += 1
            continue
        card = "business" if p.get("card_category") == "corporate" else p.get("card_product")
        rate = rate_for(schedule, card) if card else None
        if rate is None:
            not_covered += 1
            continue
        checked += 1
        expected = round(p["amount"] * rate.pct / 100 + rate.fixed_gbp, 2)
        if p["fee"] - expected > PENNY:
            over.append({"date": p["date"], "amount": p["amount"], "card": card, "charged": p["fee"], "expected": expected,
                         "extra": round(p["fee"] - expected, 2)})
        else:
            matched += 1
    minimum = schedule.min_monthly_gbp or 0
    dates = sorted(p["date"] for p in payments if p.get("fee") is not None and p.get("kind") == "sale")
    first_month, last_month = (dates[0][:7], dates[-1][:7]) if dates else (None, None)
    # Only whole months can be judged: the export's first and last months are usually partly covered
    below_minimum = [{"month": m, "fees": round(f, 2), "minimum": minimum, "topped_up_by": round(minimum - f, 2)}
                     for m, f in sorted(monthly.items()) if minimum and f < minimum and first_month < m < last_month]
    return {"checked": checked, "matched": matched, "overcharged": over, "extra_total": round(sum(o["extra"] for o in over), 2),
            "not_covered": not_covered, "below_minimum": below_minimum,
            "first_day": min((p["date"] for p in payments), default=None), "last_day": max((p["date"] for p in payments), default=None)}


# ---------------------------------------------------------------- run

def run(data: bytes, mime: str, now: Optional[datetime] = None, dry_run: bool = False) -> dict:
    """Read → verify → audit → store. Returns the audit doc. Raises ValueError for a real document on the demo database."""
    from backend.ai import board
    from backend.db import activity
    from backend.db.client import RealDataRefused, get_db

    db = get_db()
    now = now or datetime.now(timezone.utc)
    schedule, problems, attempts = read_schedule(data, mime)
    real, kind = board.data_filter(db)
    if db.name == schema.DB_NAME and not schedule.marked_synthetic:
        raise RealDataRefused("That looks like a real fee summary. This demo only takes synthetic documents, so nothing was "
                              "stored. Run the audit on your own machine against your own database.")
    doc = {"_id": f"{BUSINESS_ID}:{now.date().isoformat()}", "business_id": BUSINESS_ID, "schedule": schedule.model_dump(),
           "attempts": attempts, "problems": problems, "audit": None, "created_at": now, "synthetic": not real}
    if not problems:  # never audit against rates we couldn't verify
        doc["audit"] = audit(list(db[schema.PAYMENTS].find({"business_id": BUSINESS_ID, **kind})), schedule)
    if dry_run:
        return doc
    db[schema.FEE_AUDITS].replace_one({"_id": doc["_id"]}, doc, upsert=True)
    if problems:
        activity.log("fee_audit", f"Fee summary read but not trusted after {attempts} reading(s): {'; '.join(problems)[:200]}. "
                     "Please check it; no audit was run.", problems=problems)
    else:
        a = doc["audit"]
        activity.log("fee_audit", f"Checked {a['checked']} card payment(s) against {schedule.provider or 'the provider'}'s fee summary "
                     f"(read and checked against its own worked example{'s' if len(schedule.examples) != 1 else ''}"
                     f"{', after one re-read' if attempts > 1 else ''}): {a['matched']} charged correctly, "
                     f"{len(a['overcharged'])} overcharged by £{a['extra_total']:.2f} in total"
                     + (f"; {len(a['below_minimum'])} month(s) under the £{schedule.min_monthly_gbp:.2f} minimum" if a["below_minimum"] else ""),
                     overcharged=len(a["overcharged"]), extra=a["extra_total"])
    return doc


def main() -> None:
    parser = argparse.ArgumentParser(description="Read a card fee summary with Gemini and audit every card payment.")
    parser.add_argument("path")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    path = Path(args.path)
    doc = run(path.read_bytes(), mimetypes.guess_type(path.name)[0] or "image/png", dry_run=args.dry_run)
    print(json.dumps({k: doc[k] for k in ("attempts", "problems")}, indent=1))
    s = doc["schedule"]
    print(f"{s['provider']} · contract ends {s['contract_end']} · auth {s['auth_fee_gbp']} · min monthly {s['min_monthly_gbp']}")
    for r in s["rates"]:
        print(f"  {r['card']:<8} {r['channel']:<9} {r['pct']}% + £{r['fixed_gbp']}")
    if doc["audit"]:
        a = doc["audit"]
        print(f"checked {a['checked']}, matched {a['matched']}, overcharged {len(a['overcharged'])} (£{a['extra_total']}), "
              f"not covered {a['not_covered']}, months under minimum {a['below_minimum']}")
        for o in a["overcharged"]:
            print("  ", o)


if __name__ == "__main__":
    main()
