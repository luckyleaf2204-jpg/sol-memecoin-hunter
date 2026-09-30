"""In-memory time series per token — the basis of every "change over time" metric.

DATA BREAKS (D5): every point remembers its DexScreener pair. When a token graduates / migrates, the pair
changes and volume / txns / liquidity restart on the new pair. Pair-specific comparisons must pass
`pair=` so points from the old pair are never compared with the new one. Price / MC are token-level and
stay comparable across pairs.

Only VALIDATED values are stored (None where validation rejected a field), so acceleration,
early signals and events can never be computed from malformed data. The SQLite snapshots table
is the persistent copy (for charts/backtests); this store is the fast, bounded working set.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

from core.models import MarketData

MAX_POINTS = 360          # ~2h of anchor points spaced >= POINT_SPACING_S
MIN_SPACING_S = 5         # ignore duplicate ingests inside one cycle
# Faster refresh (5-10s) must not change what Early Signal / events see: the series keeps ONE anchor point
# every ~20s (the historic scan cadence, same depth and density as before) and a sliding "latest" tail that
# is replaced until it is >= POINT_SPACING_S after the previous anchor. Current values are always fresh.
POINT_SPACING_S = 18
HOLDER_ANCHOR_S = 90      # same idea for holder snapshots (the historic HOLDER_TTL)


@dataclass
class Point:
    ts: float
    price: float | None
    mc: float | None
    liq: float | None
    liq_src: str
    vol_5m: float | None
    vol_1h: float | None
    buys_5m: int | None
    sells_5m: int | None
    buys_1h: int | None
    sells_1h: int | None
    pair: str = ""

    @property
    def buy_share(self) -> float | None:
        if self.buys_5m is None or self.sells_5m is None or self.buys_5m + self.sells_5m == 0:
            return None
        return self.buys_5m / (self.buys_5m + self.sells_5m)

    @property
    def txns_5m(self) -> int | None:
        return None if self.buys_5m is None or self.sells_5m is None else self.buys_5m + self.sells_5m

    @property
    def bs(self) -> float | None:
        return self.buys_5m / self.sells_5m if self.buys_5m is not None and self.sells_5m else None


@dataclass
class HolderSnap:
    ts: float
    count: int | None
    amounts: dict[str, float]      # owner -> UI amount (non-LP owners)
    complete: bool                 # True = full holder list (DAS); False = top-20 only


@dataclass
class TokenHistory:
    points: deque = field(default_factory=lambda: deque(maxlen=MAX_POINTS))
    holders: deque = field(default_factory=lambda: deque(maxlen=8))
    first_holders: HolderSnap | None = None
    dev_balances: deque = field(default_factory=lambda: deque(maxlen=8))   # (ts, tokens)
    breaks: list = field(default_factory=list)                              # (ts, old_pair, new_pair)

    # ---------------------------------------------------------------- market points
    def add_market(self, m: MarketData, ts: float) -> None:
        if self.points and ts - self.points[-1].ts < MIN_SPACING_S:
            self.points.pop()
        elif (len(self.points) >= 2 and self.points[-1].ts - self.points[-2].ts < POINT_SPACING_S
              and self.points[-1].pair == self.points[-2].pair):
            self.points.pop()      # tail was not an anchor yet -> replace it (first point of a new pair stays)
        last = self.points[-1] if self.points else None
        if last and last.pair and m.pair_address and m.pair_address != last.pair:
            self.breaks.append((ts, last.pair, m.pair_address))
        self.points.append(Point(ts, m.price_usd, m.market_cap, m.liquidity_usd, m.liquidity_source, m.vol_5m,
                                 m.vol_1h, m.buys_5m, m.sells_5m, m.buys_1h, m.sells_1h, m.pair_address))

    @property
    def last_break_ts(self) -> float | None:
        return self.breaks[-1][0] if self.breaks else None

    @property
    def span_s(self) -> float:
        return self.points[-1].ts - self.points[0].ts if len(self.points) > 1 else 0.0

    def latest(self) -> Point | None:
        return self.points[-1] if self.points else None

    def _pts(self, pair: str | None):
        return self.points if pair is None else [p for p in self.points if p.pair == pair]

    def at(self, ago: float, now: float, tol: float | None = None, pair: str | None = None) -> Point | None:
        """Point closest to `now - ago` within the tolerance; `pair=` restricts to the same DexScreener pair."""
        pts = self._pts(pair)
        if not pts:
            return None
        target = now - ago
        tol = tol if tol is not None else max(45.0, 0.25 * ago)
        best = min(pts, key=lambda p: abs(p.ts - target))
        return best if abs(best.ts - target) <= tol else None

    def window(self, start_ago: float, end_ago: float, now: float, pair: str | None = None) -> list[Point]:
        """Points with now-start_ago <= ts <= now-end_ago (start_ago > end_ago)."""
        lo, hi = now - start_ago, now - end_ago
        return [p for p in self._pts(pair) if lo <= p.ts <= hi]

    def values(self, attr: str, since_ago: float, now: float, pair: str | None = None) -> list[float]:
        return [getattr(p, attr) for p in self._pts(pair) if p.ts >= now - since_ago and getattr(p, attr) is not None]

    # ---------------------------------------------------------------- holder snapshots
    def add_holders(self, snap: HolderSnap) -> None:
        if self.first_holders is None and snap.complete:
            self.first_holders = snap
        if len(self.holders) >= 2 and self.holders[-1].ts - self.holders[-2].ts < HOLDER_ANCHOR_S:
            self.holders.pop()     # keep ~90s anchors (unchanged depth for D3/D6/D7), newest always last
        self.holders.append(snap)

    def holders_at(self, ago: float, now: float, tol: float | None = None) -> HolderSnap | None:
        if not self.holders:
            return None
        target = now - ago
        tol = tol if tol is not None else max(90.0, 0.4 * ago)
        best = min(self.holders, key=lambda h: abs(h.ts - target))
        return best if abs(best.ts - target) <= tol else None

    def previous_holders(self, min_gap_s: float = 120) -> HolderSnap | None:
        """Most recent holder snapshot at least `min_gap_s` older than the latest one."""
        if len(self.holders) < 2:
            return None
        last = self.holders[-1]
        for h in reversed(list(self.holders)[:-1]):
            if last.ts - h.ts >= min_gap_s:
                return h
        return None

    # ---------------------------------------------------------------- dev balance
    def add_dev_balance(self, ts: float, tokens: float) -> None:
        self.dev_balances.append((ts, tokens))


class HistoryStore:
    def __init__(self):
        self._h: dict[str, TokenHistory] = {}

    def get(self, mint: str) -> TokenHistory:
        h = self._h.get(mint)
        if h is None:
            h = self._h[mint] = TokenHistory()
        return h

    def drop(self, mint: str) -> None:
        self._h.pop(mint, None)

    def __len__(self) -> int:
        return len(self._h)
