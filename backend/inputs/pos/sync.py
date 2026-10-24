"""Till sync: pull orders and stock counts from the POS connector and record them through backend.sales.

Inputs never write MongoDB themselves: this reads client.py and hands everything to sales.record_orders /
sales.record_stock_count, which skip anything already stored. So each run simply re-reads a look-back window
(default 7 days) that overlaps the last one: nothing is recorded twice and a missed run loses nothing.

    python -m backend.inputs.pos.sync                          # last 7 days, real clock
    python -m backend.inputs.pos.sync --all                    # backfill everything the till has
    python -m backend.inputs.pos.sync --all --now 2026-11-30   # demo: replay the till up to a pinned date
"""
import argparse
from dataclasses import dataclass, field
from datetime import timedelta
from typing import List, Optional

from backend import sales
from backend.inputs.pos.client import PosClient

LOOKBACK_DAYS = 7


@dataclass
class PosSyncResult:
    orders: sales.SyncResult = field(default_factory=sales.SyncResult)
    stock_new: int = 0  # stock counts recorded now
    stock_errors: List[str] = field(default_factory=list)  # "<count_id>: what's wrong"; those counts were NOT recorded
    error: Optional[str] = None  # the till couldn't be read at all: nothing was recorded

    @property
    def ok(self) -> bool:
        return not (self.error or self.orders.errors or self.stock_errors)

    @property
    def message(self) -> str:
        if self.error:
            return f"Till sync failed, nothing recorded: {self.error}"
        msg = f"{self.orders.message.rstrip('.')}; {self.stock_new} new stock count(s)"
        if self.stock_errors:
            msg += f", {len(self.stock_errors)} stock count(s) rejected"
        return msg + "."


def sync(client: Optional[PosClient] = None, days: Optional[int] = LOOKBACK_DAYS) -> PosSyncResult:
    """Record the last `days` of till data (None = everything). Never raises on bad till data: see the result."""
    if days is not None and days < 1:
        raise ValueError("days must be at least 1 (or None for everything).")
    client = client or PosClient()
    now = client.now
    since = now - timedelta(days=days) if days else None
    try:  # read everything first, so a broken till file records nothing rather than half a sync
        orders = list(client.iter_sales(since=since))
        counts = client.list_stock_counts(since=since)
    except (OSError, ValueError, KeyError) as e:
        return PosSyncResult(error=f"{type(e).__name__}: {e}")

    result = PosSyncResult()
    result.orders = sales.record_orders(orders, origin=f"POS sync ({f'last {days} days' if days else 'all'})", now=now)
    for count in counts:
        try:
            result.stock_new += sales.record_stock_count(count)
        except (ValueError, TypeError, KeyError) as e:
            result.stock_errors.append(f"{count.get('count_id') or '?'}: {e}")
    return result


def main(argv: Optional[List[str]] = None) -> PosSyncResult:
    parser = argparse.ArgumentParser(description="Record the till's orders and stock counts in MongoDB.")
    window = parser.add_mutually_exclusive_group()
    window.add_argument("--days", type=int, default=LOOKBACK_DAYS, help=f"look-back window (default {LOOKBACK_DAYS})")
    window.add_argument("--all", action="store_true", help="everything the till has (backfill)")
    parser.add_argument("--now", help="pin the till's clock, e.g. 2026-11-30 (demo replays)")
    args = parser.parse_args(argv)
    result = sync(PosClient(now=args.now), days=None if args.all else args.days)
    print(result.message)
    for err in (result.orders.errors + result.stock_errors)[:10]:
        print(f"  ⚠️ {err}")
    return result


if __name__ == "__main__":
    main()
