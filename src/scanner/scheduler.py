"""Tiered refresh scheduler + smart priority.

Not every token is deep-scanned every round. Each tier has its own cadence, and inside a tier every token
gets an interval from its PRIORITY CLASS:

  tier            hot      normal   quiet     what
  discovery       5s       -        -         PumpPortal WS queue drain (+ Pump.fun lists, see engine)
  market          5s       10s      30s       DexScreener batch: price / MC / volume / txns / buy-sell / liq
  holders         30s      60s      -         Helius DAS holder list (+ whale intel), candidates only
  dev             60s      120s     -         creator balance / history / funding (history is cached)
  persist         20s                         SQLite snapshots + alerts (unchanged cadence)

HOT (priority order, spec §8): new token, MC rising fast, volume rising, buy pressure rising, holders rising,
"Cơ hội" group, signal watchlist group, and every token the user starred. QUIET: has market data, is not hot
and shows almost no activity (5m volume < $500 and < 10 txns) -> scanned 3× less often.

Priority only decides WHEN data is fetched. It never touches any score, Risk, validation or Early Signal.
Failures push a tier back with exponential backoff (and core.http has a per-source cooldown), so one broken
API slows only itself and the scanner keeps running.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from core.models import TokenState

TIERS = {"discovery": 5.0, "market": 5.0, "persist": 20.0, "holders": 3.0}   # loop ticks
MARKET_S = {"hot": 5.0, "normal": 10.0, "quiet": 30.0}
HOLDER_S = {"hot": 30.0, "normal": 60.0}
DEV_S = {"hot": 60.0, "normal": 120.0}
NEW_TOKEN_MIN = 15
QUIET_VOL_5M, QUIET_TXNS_5M = 500.0, 10
TOLERANCE_S = 1.0          # a token due in < 1s is refreshed now (keeps batches aligned)
MAX_BACKOFF_S = 120.0
TIER_MAX_BACKOFF_S = {"discovery": 30.0, "market": 30.0}   # live data: a failure must not stall them for minutes

# (reason, weight) — weights follow the order requested in the spec
PRIORITY = {"pre_early": 9, "new": 7, "mc_rising": 6, "vol_rising": 5, "buy_rising": 4, "holders_rising": 3,
            "group_opportunity": 2, "group_watch": 1, "starred": 8}


def hot_reasons(st: TokenState, now: float | None = None) -> list[str]:
    """Why this token deserves the fast lane. Uses only already-validated values (None never counts)."""
    now = now or time.time()
    out = []
    if st.watch:
        out.append("starred")
    age = st.age_minutes
    seen_min = (now - st.info.discovered_at) / 60
    if (age < NEW_TOKEN_MIN) if age is not None else seen_min < NEW_TOKEN_MIN / 3:
        out.append("new")
    tr = st.trend or {}
    m = st.market
    mc_chg = tr.get("mc_chg_5m_pct")
    if mc_chg is None and m:
        mc_chg = m.price_change_5m
    if mc_chg is not None and mc_chg >= 20:
        out.append("mc_rising")
    if m and m.vol_accel is not None and m.vol_accel >= 2:
        out.append("vol_rising")
    elif tr.get("vol_chg_5m_pct") is not None and tr["vol_chg_5m_pct"] >= 100:
        out.append("vol_rising")
    if tr.get("buy_pp_5m") is not None and tr["buy_pp_5m"] >= 10:
        out.append("buy_rising")
    if st.holder_intel and st.holder_intel.abs_growth_5m is not None and st.holder_intel.abs_growth_5m >= 10:
        out.append("holders_rising")
    if st.pre_early is not None and getattr(st.pre_early, "status", "") == "PRE_EARLY":
        out.append("pre_early")
    if st.group == "opportunity":
        out.append("group_opportunity")
    elif st.group == "watch":
        out.append("group_watch")
    return out


def priority_class(st: TokenState, now: float | None = None) -> str:
    reasons = hot_reasons(st, now)
    st.priority_reasons = reasons
    if reasons:
        return "hot"
    m = st.market
    if m and (m.vol_5m is not None and m.vol_5m < QUIET_VOL_5M) and (m.txns_5m is None or m.txns_5m < QUIET_TXNS_5M):
        return "quiet"
    return "normal"


def priority_score(st: TokenState) -> int:
    return sum(PRIORITY.get(r, 0) for r in st.priority_reasons)


def due(st: TokenState, kind: str, interval: float, now: float) -> bool:
    return now - st.refreshed.get(kind, 0.0) >= interval - TOLERANCE_S


def market_due(states: list[TokenState], now: float) -> list[TokenState]:
    """Tokens whose market data should be fetched now, highest priority first."""
    out = []
    for st in states:
        cls = priority_class(st, now)
        if due(st, "market", MARKET_S[cls], now):
            out.append(st)
    out.sort(key=lambda s: -priority_score(s))
    return out


def deep_due(candidates: list[TokenState], now: float, limit: int) -> list[TokenState]:
    """Holder/whale (and dev when its own interval expired) work for candidates, hot first."""
    out = []
    for st in candidates:
        cls = "hot" if priority_class(st, now) == "hot" else "normal"
        if due(st, "holders", HOLDER_S[cls], now):
            out.append(st)
    out.sort(key=lambda s: -priority_score(s))
    return out[:limit]


def dev_due(st: TokenState, now: float) -> bool:
    cls = "hot" if st.priority_reasons else "normal"
    return due(st, "dev", DEV_S[cls], now)


@dataclass
class TierStat:
    interval: float
    last_start: float = 0.0
    last_ok: float = 0.0
    last_ms: float | None = None
    failures: int = 0
    next_at: float = 0.0
    runs: int = 0
    gaps: deque = field(default_factory=lambda: deque(maxlen=30))   # measured seconds between runs

    @property
    def measured_s(self) -> float | None:
        return round(sum(self.gaps) / len(self.gaps), 1) if self.gaps else None


class TierClock:
    """Cadence + exponential backoff per tier; also records the ACTUAL interval achieved."""

    def __init__(self, tiers: dict[str, float] | None = None, clock=time.monotonic):
        self.clock = clock
        self.tiers = {k: TierStat(v) for k, v in (tiers or TIERS).items()}

    def due(self, name: str) -> bool:
        return self.clock() >= self.tiers[name].next_at

    def start(self, name: str) -> float:
        t = self.tiers[name]
        now = self.clock()
        if t.last_start:
            t.gaps.append(now - t.last_start)
        t.last_start = now
        t.runs += 1
        return now

    def done(self, name: str, ok: bool = True) -> float:
        """Schedule the next run. Failure -> interval × 2^failures (capped). Returns the delay used."""
        t = self.tiers[name]
        now = self.clock()
        t.last_ms = round((now - t.last_start) * 1000) if t.last_start else None
        if ok:
            t.failures, t.last_ok = 0, now
            delay = t.interval
        else:
            t.failures += 1
            delay = min(TIER_MAX_BACKOFF_S.get(name, MAX_BACKOFF_S), t.interval * 2 ** t.failures)
        # next run is measured from the START of this run so a slow round does not stretch the cadence
        t.next_at = max(now, t.last_start + delay) if ok else now + delay
        return delay

    def seconds_until_next(self) -> float:
        return max(0.0, min(t.next_at for t in self.tiers.values()) - self.clock())

    def stats(self) -> dict:
        return {k: {"target_s": t.interval, "measured_s": t.measured_s, "runs": t.runs, "failures": t.failures,
                    "last_ms": t.last_ms} for k, t in self.tiers.items()}
