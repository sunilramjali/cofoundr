"""The owner's approval replies ("approve 4", "reject 2", "yes 1"), however they arrive: WhatsApp via Saint's
POST /approve, the dashboard chat bar, or Saint's emails to the demo inbox. Anything looser is not a decision."""
import re
from typing import Optional, Tuple

# The whole reply must be one command: "approve 3", "Yes #3", "reject 2!". Anything else ("don't approve 3",
# "approve 1 and 2", "approve the £450 one") is refused, so a negated or ambiguous message never moves money.
REPLY = re.compile(r"^\s*(approve|approved|yes|ok|reject|rejected|no)\s*#?\s*(\d{1,6})\s*[.!]*\s*$", re.IGNORECASE)


def parse_reply(text: str) -> Optional[Tuple[str, int]]:
    """("approve" | "reject", ref) for a reply that is exactly a decision, else None."""
    m = REPLY.match(text or "")
    if not m:
        return None
    return ("reject" if m.group(1).lower() in ("reject", "rejected", "no") else "approve"), int(m.group(2))
