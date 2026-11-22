"""Shared HTTP layer: per-source rate limits, retry with backoff, Redis cache, health stats.

* Rate limits are enforced per *source* (host family). When a Redis client is attached
  the limit is shared by every worker process (fixed 1-second windows in Redis),
  otherwise an in-process token bucket is used.
* Retries: timeouts, connection errors, HTTP 429 and 5xx -> exponential backoff with
  jitter (Retry-After honoured, capped). 4xx other than 429 are NOT retried.
* Every outcome is counted in `STATS` and periodically written to `api_sync_status`
  by radar.services.health so the dashboard shows the state of each data source.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any

import httpx

from ..logging_setup import redact

log = logging.getLogger(__name__)


class HttpError(Exception):
    def __init__(self, source: str, status: int | None, message: str):
        super().__init__(f"{source}: HTTP {status}: {redact(message)[:300]}")
        self.source = source
        self.status = status


@dataclass
class SourceStats:
    ok: int = 0
    failed: int = 0
    rate_limited: int = 0
    retries: int = 0
    last_ok_at: float | None = None
    last_error_at: float | None = None
    last_error: str | None = None
    latency_ms_ewma: float | None = None

    def record_ok(self, latency_ms: float) -> None:
        self.ok += 1
        self.last_ok_at = time.time()
        self.latency_ms_ewma = latency_ms if self.latency_ms_ewma is None else (
            0.8 * self.latency_ms_ewma + 0.2 * latency_ms)

    def record_error(self, msg: str, rate_limited: bool = False) -> None:
        self.failed += 1
        if rate_limited:
            self.rate_limited += 1
        self.last_error_at = time.time()
        self.last_error = redact(msg)[:500]


STATS: dict[str, SourceStats] = {}


def stats_for(source: str) -> SourceStats:
    s = STATS.get(source)
    if s is None:
        s = STATS[source] = SourceStats()
    return s


class RateLimiter:
    """Requests-per-second limiter, optionally shared across processes through Redis."""

    def __init__(self, name: str, rps: float, redis: Any | None = None):
        self.name = name
        self.rps = max(rps, 0.05)
        self.redis = redis
        self._lock = asyncio.Lock()
        self._next_at = 0.0
        self.penalty = 1.0  # widened after 429s, narrowed back on success

    async def acquire(self) -> None:
        interval = self.penalty / self.rps
        async with self._lock:
            now = time.monotonic()
            wait = self._next_at - now
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_at = max(now, self._next_at) + interval
        if self.redis is not None:
            limit = max(1, int(self.rps / self.penalty))
            while True:
                sec = int(time.time())
                key = f"rl:{self.name}:{sec}"
                try:
                    n = await self.redis.incr(key)
                    if n == 1:
                        await self.redis.expire(key, 3)
                except Exception:  # Redis down -> local limit only
                    return
                if n <= limit:
                    return
                await asyncio.sleep(max(0.01, sec + 1 - time.time()))

    def on_rate_limited(self) -> None:
        self.penalty = min(self.penalty * 1.6, 8.0)

    def on_success(self) -> None:
        if self.penalty > 1.0:
            self.penalty = max(1.0, self.penalty * 0.98)


@dataclass
class SourcePolicy:
    rps: float
    timeout: float = 20.0
    max_retries: int = 4
    base_backoff: float = 0.6
    max_backoff: float = 20.0
    headers: dict[str, str] = field(default_factory=dict)


DEFAULT_POLICIES: dict[str, SourcePolicy] = {
    # https://docs.dexscreener.com/api/reference : 300 req/min for token/pair endpoints
    "dexscreener": SourcePolicy(rps=4.0),
    # https://dev.jup.ag/docs : lite-api free tier ~60 req/min
    "jupiter": SourcePolicy(rps=0.9),
    # https://apiguide.geckoterminal.com : 30 calls/min on the free API
    "geckoterminal": SourcePolicy(rps=0.45, max_retries=5, base_backoff=2.0),
    # unofficial website API — be gentle
    "pumpfun_api": SourcePolicy(rps=0.5),
    "telegram": SourcePolicy(rps=1.0, max_retries=3),
    "birdeye": SourcePolicy(rps=0.8),
    "solscan": SourcePolicy(rps=1.0),
    "helius_api": SourcePolicy(rps=2.0),
}


class HttpClient:
    def __init__(self, redis: Any | None = None, policies: dict[str, SourcePolicy] | None = None):
        self.redis = redis
        self.policies = dict(DEFAULT_POLICIES)
        if policies:
            self.policies.update(policies)
        self._limiters: dict[str, RateLimiter] = {}
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(20.0, connect=10.0),
            headers={"User-Agent": "smart-money-radar/1.0", "Accept": "application/json"},
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
            follow_redirects=True,
        )

    def limiter(self, source: str) -> RateLimiter:
        lim = self._limiters.get(source)
        if lim is None:
            pol = self.policies.get(source) or SourcePolicy(rps=1.0)
            lim = self._limiters[source] = RateLimiter(source, pol.rps, self.redis)
        return lim

    async def close(self) -> None:
        await self._client.aclose()

    async def _cache_get(self, key: str) -> Any | None:
        if self.redis is None:
            return None
        try:
            raw = await self.redis.get(key)
            return json.loads(raw) if raw else None
        except Exception:
            return None

    async def _cache_set(self, key: str, value: Any, ttl: int) -> None:
        if self.redis is None or ttl <= 0:
            return
        try:
            await self.redis.set(key, json.dumps(value), ex=ttl)
        except Exception:
            pass

    async def request_json(self, source: str, method: str, url: str, *,
                           params: dict | None = None, json_body: Any = None,
                           headers: dict | None = None, cache_ttl: int = 0,
                           retry_statuses: tuple[int, ...] = (429, 500, 502, 503, 504, 520, 522, 524),
                           ) -> Any:
        pol = self.policies.get(source) or SourcePolicy(rps=1.0)
        cache_key = None
        if cache_ttl > 0:
            h = hashlib.sha256(json.dumps([method, url, params, json_body], sort_keys=True,
                                          default=str).encode()).hexdigest()[:32]
            cache_key = f"http:{source}:{h}"
            cached = await self._cache_get(cache_key)
            if cached is not None:
                return cached
        st = stats_for(source)
        lim = self.limiter(source)
        last_err: Exception | None = None
        for attempt in range(pol.max_retries + 1):
            await lim.acquire()
            t0 = time.monotonic()
            try:
                resp = await self._client.request(method, url, params=params, json=json_body,
                                                  headers={**pol.headers, **(headers or {})},
                                                  timeout=pol.timeout)
            except (httpx.TimeoutException, httpx.TransportError) as e:
                last_err = HttpError(source, None, f"{type(e).__name__}: {e}")
                st.record_error(str(last_err))
                st.retries += 1
                await asyncio.sleep(self._backoff(pol, attempt))
                continue
            if resp.status_code < 400:
                lim.on_success()
                st.record_ok((time.monotonic() - t0) * 1000)
                try:
                    data = resp.json()
                except ValueError as e:
                    st.record_error(f"invalid JSON: {e}")
                    raise HttpError(source, resp.status_code, "invalid JSON body") from e
                if cache_key:
                    await self._cache_set(cache_key, data, cache_ttl)
                return data
            body = resp.text[:300]
            if resp.status_code == 429:
                lim.on_rate_limited()
            st.record_error(f"HTTP {resp.status_code}: {body}", rate_limited=resp.status_code == 429)
            last_err = HttpError(source, resp.status_code, body)
            if resp.status_code not in retry_statuses:
                raise last_err
            st.retries += 1
            ra = resp.headers.get("retry-after")
            delay = self._backoff(pol, attempt)
            if ra:
                try:
                    delay = min(max(float(ra), delay), pol.max_backoff)
                except ValueError:
                    pass
            await asyncio.sleep(delay)
        assert last_err is not None
        raise last_err

    @staticmethod
    def _backoff(pol: SourcePolicy, attempt: int) -> float:
        return min(pol.max_backoff, pol.base_backoff * (2 ** attempt)) * (0.7 + 0.6 * random.random())
