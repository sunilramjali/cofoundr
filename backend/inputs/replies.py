"""The owner's approval replies ("approve 4", "reject 2", "yes 1"), however they arrive: WhatsApp (Twilio)
or the dashboard chat bar. Anything looser is not a decision."""
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


# A no may carry the owner's reason, remembered for next time: "reject 2: they're regulars", "no 2 because they always pay".
# Only a no: it never moves money, so a reason after it is safe. A yes must still be the whole message (REPLY above).
MIXED = re.compile(r"\b(approve|approved|yes|go ahead|send it|pay it)\b", re.IGNORECASE)
REJECT_WHY = re.compile(r"^\s*(?:reject|rejected|no)\s*#?\s*(\d{1,6})\s*(?:[:,;\-–—]|\s(?:because|as|since|cos)\b)\s*(\S.*?)\s*$",
                        re.IGNORECASE | re.DOTALL)


def parse_rejection(text: str) -> Optional[Tuple[int, str]]:
    """(ref, reason) for "reject 2: reason", else None. Plain "reject 2" is parse_reply's."""
    m = REJECT_WHY.match(text or "")
    if not m or MIXED.search(m.group(2)):  # "no 2, approve it instead": unclear, so not a decision at all
        return None
    return int(m.group(1)), m.group(2)


# The owner's own to-do list (backend/todos.py), read exactly, never by Gemini: "todo: order oat milk",
# "add to my list: cups", "remind me to call the dairy"; "done T3" ticks one off.
TODO = re.compile(r"^\s*(?:to-?\s?do|add\s+(?:to\s+(?:my|the)\s+(?:list|to-?\s?do\s+list)|an?\s+to-?\s?do|to-?\s?do)|remind\s+me\s+to)"
                  r"(?:\s*[:,\-–—]\s*|\s+)(\S.*?)\s*$", re.IGNORECASE | re.DOTALL)
NOT_A_TODO = re.compile(r"^(list|lists)\b|\?$", re.IGNORECASE)  # "todo list?", "to do today?": questions, not to-dos
DONE = re.compile(r"^\s*(?:done|did|ticked?|finished)\s*#?\s*T(\d{1,6})\s*[.!]*\s*$", re.IGNORECASE)


def parse_todo(text: str) -> Optional[str]:
    """The to-do's words for "todo: …", else None."""
    m = TODO.match(text or "")
    return m.group(1) if m and not NOT_A_TODO.search(m.group(1)) else None


def parse_done(text: str) -> Optional[int]:
    """The to-do number for "done T3", else None."""
    m = DONE.match(text or "")
    return int(m.group(1)) if m else None
