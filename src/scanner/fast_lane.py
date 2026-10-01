"""FAST LANE for the on-chain hard gates (mint / freeze authority, Token-2022 extensions) — Helius getAsset only.

Protects the Helius quota:
  * only tokens the bot flags as genuinely near a NEW-engine BUY (engine.deep_hint; the bot applies the eligibility:
    EarlyScore >= threshold - 0.05 AND Confidence >= threshold - 0.10, no hard failure, identity not CONFLICT,
    authorities not checked yet) — never "every token";
  * global limit: at most `per_min` getAsset calls in any sliding 60 s (FAST_GETASSET_PER_MIN, default 5); the quota
    governor drops it to THROTTLED_PER_MIN (2) whenever today's Helius spend runs ahead of the paced daily budget
    (more than 80 % of what the day's elapsed share allows) or Helius reported its quota exhausted;
  * per-CA cooldown (default 20 s) between attempts; a CA already checked is never re-fetched by the lane;
  * cache of successful results (default 60 s TTL) shared with the deep scan;
  * Helius quota / budget unavailable -> no call, the gate stays UNKNOWN -> WATCH (never BUY, never REJECT for it).
Every call, cache hit / miss, quota refusal and latency is counted (stats()).
"""
from __future__ import annotations

import os
import time
from collections import deque

DAS_CREDITS = 10
THROTTLED_PER_MIN = 2


class FastLane:
    def __init__(self, rpc, per_min: int | None = None, cooldown_s: float = 20.0, cache_ttl_s: float = 60.0,
                 max_per_round: int = 4):
        self.rpc = rpc
        self.per_min_cfg = int(per_min if per_min is not None else os.environ.get("FAST_GETASSET_PER_MIN", 5))
        self.throttled = False
        self.cooldown_s = cooldown_s
        self.cache_ttl_s = cache_ttl_s
        self.max_per_round = max_per_round
        self.calls: deque[float] = deque()             # timestamps of real getAsset calls (sliding minute)
        self.last_try: dict[str, float] = {}
        self.cache: dict[str, tuple[float, dict]] = {}
        self.checked: dict[str, float] = {}            # mint -> when the lane resolved its gates
        self.n = {"fast_getasset_calls": 0, "cache_hits": 0, "cache_misses": 0, "quota_errors": 0, "failures": 0,
                  "rate_limited_skips": 0, "cooldown_skips": 0}
        self.latency_ms: deque[float] = deque(maxlen=200)
        self.paused_until = 0.0

    def per_min(self, now: float | None = None) -> int:
        """Configured limit (default 5/min); THROTTLED_PER_MIN while the quota governor sees spend ahead of pace."""
        lim = self.per_min_cfg
        self.throttled = False
        try:
            st = self.rpc.credits.state()
            now = now or time.time()
            allowed = (st.get("daily_budget") or 0) * min(1.0, (now % 86400) / 86400 + 0.05)
            if st.get("quota_exhausted") or (allowed and st.get("used", 0) > 0.8 * allowed):
                self.throttled = True
                lim = min(lim, THROTTLED_PER_MIN)
        except (AttributeError, TypeError):
            pass
        return max(0, lim)

    def _window(self, now: float) -> int:
        while self.calls and now - self.calls[0] >= 60:
            self.calls.popleft()
        return len(self.calls)

    def cached(self, mint: str, now: float) -> dict | None:
        c = self.cache.get(mint)
        if c and now - c[0] <= self.cache_ttl_s:
            return c[1]
        return None

    def quota_ok(self) -> bool:
        try:
            return bool(self.rpc.has_das) and self.rpc.credits.allow(DAS_CREDITS)
        except AttributeError:
            return bool(getattr(self.rpc, "has_das", False))

    async def fetch(self, mint: str, now: float | None = None) -> dict | None:
        """Cached getAsset (used by the lane and by the deep scan)."""
        now = now or time.time()
        hit = self.cached(mint, now)
        if hit is not None:
            self.n["cache_hits"] += 1
            return hit
        self.n["cache_misses"] += 1
        t0 = time.monotonic()
        asset = await self.rpc.das_get_asset(mint)
        self.latency_ms.append((time.monotonic() - t0) * 1000)
        if asset is not None:
            self.cache[mint] = (now, asset)
        return asset

    async def round(self, candidates: list, apply, now: float | None = None) -> int:
        """One lane round over the hinted states; `apply(st, asset)` writes the result. Returns calls made."""
        now = now or time.time()
        if now < self.paused_until:
            return 0
        for m in [m for m, (t, _) in self.cache.items() if now - t > self.cache_ttl_s]:
            self.cache.pop(m, None)
        made = 0
        for st in candidates:
            if made >= self.max_per_round:
                break
            mint = st.mint
            hit = self.cached(mint, now)
            if hit is not None:
                self.n["cache_hits"] += 1
                apply(st, hit)
                self.checked.setdefault(mint, now)
                continue
            if now - self.last_try.get(mint, 0) < self.cooldown_s:
                self.n["cooldown_skips"] += 1
                continue
            if self._window(now) >= self.per_min(now):
                self.n["rate_limited_skips"] += 1
                break
            if not self.quota_ok():
                self.n["quota_errors"] += 1
                self.paused_until = now + 60               # gate stays UNKNOWN -> WATCH; retry in a minute
                break
            self.last_try[mint] = now
            self.calls.append(now)
            self.n["fast_getasset_calls"] += 1
            made += 1
            asset = await self.fetch(mint, now)
            if asset is None:
                self.n["failures"] += 1
                continue
            apply(st, asset)
            self.checked.setdefault(mint, now)
        return made

    def stats(self, now: float | None = None) -> dict:
        now = now or time.time()
        hits, miss = self.n["cache_hits"], self.n["cache_misses"]
        return {**self.n, "calls_last_min": self._window(now), "per_min_limit": self.per_min(now),
                "throttled_by_quota_governor": self.throttled,
                "cache_hit_rate": round(hits / (hits + miss), 3) if hits + miss else None,
                "avg_latency_ms": round(sum(self.latency_ms) / len(self.latency_ms)) if self.latency_ms else None,
                "resolved_tokens": len(self.checked), "paused": now < self.paused_until}
