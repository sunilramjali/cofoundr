"""GAIA's till as an API: sales orders and stock counts, served from the synthetic POS export files in data/.

Shaped like a real POS API (e.g. Square): filter by time, page with a cursor. Like a real till it never returns
anything after `now`, so nothing downstream (e.g. the forecast) can see the future. A real POS only changes this file.

    from backend.inputs.pos.client import list_sales, list_stock
    page = list_sales(since="2026-10-01", until="2026-10-08")   # {"orders": [...], "cursor": "100" or None}
    stock = list_stock()                                       # latest physical stock count, or None
"""
import copy
import json
from datetime import date, datetime, time
from pathlib import Path
from typing import Iterator, List, Optional, Union
from zoneinfo import ZoneInfo

DATA_DIR = Path(__file__).parent / "data"
LONDON = ZoneInfo("Europe/London")  # the truck's time zone: bare dates mean London midnight
MAX_PAGE = 200

When = Union[datetime, date, str, None]


def _to_dt(value: When) -> Optional[datetime]:
    """datetime, date or ISO string → time-zone-aware datetime (naive values are taken as London time)."""
    if value is None:
        return None
    if isinstance(value, str):
        value = date.fromisoformat(value) if len(value) == 10 else datetime.fromisoformat(value)
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min)
    return value if value.tzinfo else value.replace(tzinfo=LONDON)


class PosClient:
    def __init__(self, data_dir: Union[Path, str] = DATA_DIR, now: When = None):
        """`now` pins the till's clock (tests, demo replays); by default it's the real time."""
        self.data_dir = Path(data_dir)
        self._now = _to_dt(now)
        self._orders: Optional[list] = None  # [(placed_at, order)], oldest first
        self._counts: Optional[list] = None  # [(counted_at, count)], oldest first

    @property
    def now(self) -> datetime:
        return self._now or datetime.now(LONDON)

    def _load(self) -> None:
        if self._orders is not None:
            return
        orders, counts = [], []
        for path in sorted(self.data_dir.glob("*.json")):
            doc = json.loads(path.read_text())
            if doc.get("data_type") != "synthetic_mock_data":
                raise ValueError(f"{path.name} is not labelled synthetic_mock_data; refusing to load it.")
            if "orders" in doc:
                orders += [(_to_dt(o["placed_at"]), o) for o in doc["orders"]]
            elif doc.get("record_type") == "physical_inventory_count":
                counts.append((_to_dt(doc["counted_at"]), doc))
        self._orders = sorted(orders, key=lambda pair: pair[0])
        self._counts = sorted(counts, key=lambda pair: pair[0])

    def list_sales(self, since: When = None, until: When = None, limit: int = 100, cursor: Optional[str] = None) -> dict:
        """One page of orders placed in [since, until), oldest first: {"orders": [...], "cursor": next page or None}."""
        if not 1 <= limit <= MAX_PAGE:
            raise ValueError(f"limit must be between 1 and {MAX_PAGE}.")
        if cursor is not None and not cursor.isdigit():
            raise ValueError(f"Invalid cursor: {cursor!r}.")
        self._load()
        start, now = _to_dt(since), self.now
        end = min(_to_dt(until) or now, now)
        matching = [o for at, o in self._orders if (start is None or at >= start) and at < end]
        offset = int(cursor or 0)
        nxt = offset + limit
        return {"orders": copy.deepcopy(matching[offset:nxt]), "cursor": str(nxt) if nxt < len(matching) else None}

    def iter_sales(self, since: When = None, until: When = None) -> Iterator[dict]:
        """Every order in [since, until), following the cursor page by page."""
        cursor = None
        while True:
            page = self.list_sales(since, until, MAX_PAGE, cursor)
            yield from page["orders"]
            cursor = page["cursor"]
            if cursor is None:
                return

    def list_stock_counts(self, since: When = None, until: When = None) -> List[dict]:
        """Physical stock counts taken in [since, until), oldest first."""
        self._load()
        start, now = _to_dt(since), self.now
        end = min(_to_dt(until) or now, now)
        return [copy.deepcopy(c) for at, c in self._counts if (start is None or at >= start) and at < end]

    def list_stock(self) -> Optional[dict]:
        """The most recent stock count (what's on the truck as of the last count), or None before the first one."""
        counts = self.list_stock_counts()
        return counts[-1] if counts else None


_default = PosClient()


def list_sales(since: When = None, until: When = None, limit: int = 100, cursor: Optional[str] = None) -> dict:
    return _default.list_sales(since, until, limit, cursor)


def iter_sales(since: When = None, until: When = None) -> Iterator[dict]:
    return _default.iter_sales(since, until)


def list_stock() -> Optional[dict]:
    return _default.list_stock()
