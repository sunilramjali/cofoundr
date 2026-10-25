"""One-off: move the synthetic till export to GAIA's real trading week, Thursday to Sunday (Sun 25 Oct).

The synthetic POS export traded Mon/Wed/Fri/Sat, while the synthetic card payments (EVO sample) and the real GAIA
pattern are Thu-Sun, so the forecast showed the truck open on Mondays. Each order and stock count moves to the matching
day of the SAME week (Mon→Thu, Wed→Fri, Fri→Sat, Sat→Sun), at the same London wall-clock time; items, amounts and
order sequence numbers are unchanged, the date inside order/count ids follows the new date. All data stays synthetic.

    python -m data.shift_trading_week          # rewrite backend/inputs/pos/data/*.json (refuses to run twice)
"""
import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).resolve().parent.parent / "backend" / "inputs" / "pos" / "data"
LONDON = ZoneInfo("Europe/London")
SHIFT = {0: 3, 2: 2, 4: 1, 5: 1}  # weekday → days later: Mon→Thu, Wed→Fri, Fri→Sat, Sat→Sun
OLD_SCHEDULE = "Monday, Wednesday, Friday, Saturday (4 days per week)"
NEW_SCHEDULE = "Thursday, Friday, Saturday, Sunday (4 days per week)"


def shift(stamp: str) -> datetime:
    """ISO time → the same London wall-clock time on the new day (the UTC offset follows the clocks going back)."""
    local = datetime.fromisoformat(stamp).astimezone(LONDON)
    if local.weekday() not in SHIFT:
        raise ValueError(f"{stamp} is a {local:%A}: not one of the old trading days")
    return (local.replace(tzinfo=None) + timedelta(days=SHIFT[local.weekday()])).replace(tzinfo=LONDON)


def _new_id(old_id: str, when: datetime) -> str:
    return re.sub(r"\d{8}", f"{when:%Y%m%d}", old_id, count=1)


def shift_day(day: str) -> str:
    return f"{shift(f'{day}T12:00:00+00:00'):%Y-%m-%d}"


def run() -> None:
    for path in sorted(DATA_DIR.glob("GAIA_POS_Sales_*.json")):
        doc = json.loads(path.read_text())
        if doc.get("trading_schedule") != OLD_SCHEDULE:
            raise SystemExit(f"{path.name}: already {doc.get('trading_schedule')!r}, nothing done")
        for order in doc["orders"]:
            when = shift(order["placed_at"])
            order["placed_at"] = when.isoformat()
            order["order_id"] = _new_id(order["order_id"], when)
        doc["orders"].sort(key=lambda o: o["placed_at"])
        doc["trading_schedule"] = NEW_SCHEDULE
        doc["trading_dates"] = sorted({o["placed_at"][:10] for o in doc["orders"]})
        for day in doc.get("daily_pos_summary") or []:
            day["date"] = shift_day(day["date"])
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"{path.name}: {len(doc['orders'])} orders → {doc['trading_dates'][0]} … {doc['trading_dates'][-1]}")
    for path in sorted(DATA_DIR.glob("GAIA_*Stock_Count_*.json")):
        doc = json.loads(path.read_text())
        when = shift(doc["counted_at"])
        doc["counted_at"], doc["count_id"] = when.isoformat(), _new_id(doc["count_id"], when)
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False) + "\n")
        print(f"{path.name}: counted {when:%a %d %b}")


if __name__ == "__main__":
    run()
