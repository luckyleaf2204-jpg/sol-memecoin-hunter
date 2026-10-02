"""One Jupiter quote budget for the whole bot (lite-api: 50 quotes / minute), with SELL priority.

  sell                  always allowed while the minute has room (up to the full PER_MIN)
  buy / truth / probe   only below PER_MIN - SELL_RESERVE, and never while a SELL intent is waiting

A refused SELL / BUY comes back as a transient RATE_LIMITED quote result (retried by the bot's own backoff); a
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

    def take(self, kind: str, now: float | None = None, sells_pending: bool = False) -> bool:
        now = time.time() if now is None else now
        used = self.used(now)
        ok = used < self.per_min if kind == "sell" else (not sells_pending and used < self.per_min - self.sell_reserve)
        if ok:
            self.calls.append((now, kind))
        else:
            self.refused[kind] = self.refused.get(kind, 0) + 1
        return ok

    def as_dict(self, now: float | None = None) -> dict:
        now = time.time() if now is None else now
        used = self.used(now)
        by = {}
        for _, k in self.calls:
            by[k] = by.get(k, 0) + 1
        return {"per_min": self.per_min, "sell_reserve": self.sell_reserve, "used_last_min": used, "by_kind": by,
                "refused": dict(self.refused)}
