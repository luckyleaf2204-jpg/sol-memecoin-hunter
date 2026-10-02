"""One Jupiter quote budget for the whole bot (lite-api: 50 quotes / minute), with SELL priority.

  sell                  always allowed while the minute has room (up to the full PER_MIN)
  buy / truth / probe   only below PER_MIN - SELL_RESERVE, and never while a SELL intent is waiting

Counted per real HTTP call (JupiterQuotes.record on every attempt). A refused SELL / BUY comes back as a transient
BUDGET quote result (not RATE_LIMITED: Jupiter did not answer 429) (retried by the bot's own backoff); a
refused truth / probe quote is simply skipped. Without a budget object (unit tests, backtests) nothing is limited."""
from __future__ import annotations

import time
from collections import deque

PER_MIN = 50
SELL_RESERVE = 10                    # quotes per minute only a SELL may use


class QuoteBudget:
    def __init__(self, per_min: int = PER_MIN, sell_reserve: int = SELL_RESERVE):
        self.per_min, self.sell_reserve = per_min, sell_reserve
        self.calls: deque = deque()                 # (ts, kind)
        self.refused: dict[str, int] = {}

    def used(self, now: float) -> int:
        while self.calls and now - self.calls[0][0] >= 60:
            self.calls.popleft()
        return len(self.calls)

    def allow(self, kind: str, sells_pending: bool = False, now: float | None = None) -> bool:
        """May a quote of this kind be sent now? (does not count it)"""
        now = time.time() if now is None else now
        used = self.used(now)
        ok = used < self.per_min if kind == "sell" else (not sells_pending and used < self.per_min - self.sell_reserve)
        if not ok:
            self.refused[kind] = self.refused.get(kind, 0) + 1
        return ok

    def record(self, kind: str, now: float | None = None) -> None:
        """One real HTTP call (the Jupiter client calls this for every attempt, retries included)."""
        self.calls.append((time.time() if now is None else now, kind))

    def take(self, kind: str, now: float | None = None, sells_pending: bool = False) -> bool:
        ok = self.allow(kind, sells_pending, now)
        if ok:
            self.record(kind, now)
        return ok

    def as_dict(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        used = self.used(now)
        by = {}
        for _, k in self.calls:
            by[k] = by.get(k, 0) + 1
        return {"per_min": self.per_min, "sell_reserve": self.sell_reserve, "used_last_min": used, "by_kind": by,
                "refused": dict(self.refused)}
