"""Which exits are PROTECTIVE (filled even on a bad route; haircut after the retry window; SL-gap scenario).

trading/exits.py (frozen) defines HARD = the risk / stop exits. A break-even stop and a trailing stop protect the
same way — they fire when the price is FALLING — so they are protective too. An exit that was escalated because
the price fell through the stop while waiting carries the reason "<original>-><final>" (e.g.
"take_profit_1->stop_loss"); base_reason() gives the final one for the report."""
from __future__ import annotations

from trading.exits import HARD

PROTECTIVE = tuple(HARD) + ("break_even_stop", "trailing_stop")
STOPS = ("stop_loss", "break_even_stop", "trailing_stop")


def base_reason(reason: str | None) -> str:
    return (reason or "").split("->")[-1]


def is_protective(reason: str | None) -> bool:
    return base_reason(reason) in PROTECTIVE
