"""Reference solution for Bead A (proof that R1-R3 are satisfiable).

Never shown to workers.
"""

from __future__ import annotations

import re

_AMOUNT_RE = re.compile(r"^\$?(\d+)(?:\.(\d{1,2}))?$")


def parse_amount(text: str) -> int:
    match = _AMOUNT_RE.match(text.strip())
    if match is None:
        raise ValueError(f"not a valid amount: {text!r}")
    dollars, cents = match.group(1), (match.group(2) or "").ljust(2, "0")
    return int(dollars) * 100 + int(cents)


def split(total_cents: int, people: int, tip_percent: int = 0) -> list[int]:
    if people < 1:
        raise ValueError("people must be at least 1")
    if not 0 <= tip_percent <= 100:
        raise ValueError("tip percent must be between 0 and 100")
    tip = (total_cents * tip_percent + 50) // 100
    grand_total = total_cents + tip
    base, extra = divmod(grand_total, people)
    return [base + 1 if index < extra else base for index in range(people)]
