"""Shared async HTTP client with retries, per-host throttling and source health tracking.

Every data-source adapter goes through this client, so a failing source only marks
itself unhealthy and returns None — it never crashes the scanner.

Resilience: per-request timeout, retries with exponential backoff (honours Retry-After on 429),
per-host throttling with ADAPTIVE rate (a 429 halves the host's request rate, down to MIN_RATE_FACTOR of
the configured rate; every success restores it by RATE_RECOVERY), and a per-source COOLDOWN (circuit
breaker) that only opens after COOLDOWN_AFTER consecutive failed requests (retries exhausted on 429 / 5xx /
network errors / timeouts): calls then return None immediately for 5s, 10s, 20s ... (max COOLDOWN_MAX_S)
until one succeeds. HTTP 4xx (e.g. unknown token) never trips it. A single transient failure — normal on
a shared cloud IP — therefore never blocks a whole source (production incident 2026-09-30: Render).
"""
from __future__ import annotations

import asyncio
import re
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

COOLDOWN_BASE_S, COOLDOWN_MAX_S = 5.0, 120.0
COOLDOWN_AFTER = 3                 # consecutive failed requests before a source is put on cooldown
MIN_RATE_FACTOR, RATE_RECOVERY = 0.25, 1.1
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) SOLMemecoinHunter/1.0"
_SECRET_RE = re.compile(r"(api-key=|api_key=|bot)[A-Za-z0-9:_\-]{8,}")


def redact(text: str) -> str:
    return _SECRET_RE.sub(r"\1***", str(text))


@dataclass
class SourceStatus:
    ok: bool = True
    last_ok: float = 0.0
    last_error: str = ""
    last_error_at: float = 0.0
    errors: int = 0
    requests: int = 0
    last_status: int | None = None     # HTTP status of the last response (None = no response / network error)
    last_endpoint: str = ""            # scheme://host/path only — query strings (API keys) are never stored
    last_call: str = ""                # e.g. JSON-RPC method name
    last_ms: float | None = None
    consecutive_failures: int = 0      # transient failures in a row (429 / 5xx / network / timeout)
    cooldown_until: float = 0.0        # monotonic time; calls are skipped until then
    skipped: int = 0                   # calls skipped because of the cooldown


def safe_endpoint(url: str) -> str:
    u = urlparse(url)
    return f"{u.scheme}://{u.netloc}{u.path or '/'}"


@dataclass
class Health:
    sources: dict[str, SourceStatus] = field(default_factory=dict)

    def _get(self, source: str) -> SourceStatus:
        return self.sources.setdefault(source, SourceStatus())

    def ok(self, source: str) -> None:
        s = self._get(source)
        s.ok, s.last_ok = True, time.time()
        s.requests += 1
        s.consecutive_failures, s.cooldown_until = 0, 0.0

    def fail(self, source: str, err: str) -> None:
        s = self._get(source)
        s.ok, s.last_error, s.last_error_at = False, redact(err)[:300], time.time()
        s.errors += 1
        s.requests += 1

    def is_ok(self, source: str) -> bool:
        return self._get(source).ok

    def mark(self, source: str, *, status: int | None, endpoint: str, call: str, ms: float | None) -> None:
        s = self._get(source)
        s.last_status, s.last_endpoint, s.last_call, s.last_ms = status, endpoint, call, ms

    def get(self, source: str) -> SourceStatus:
        return self._get(source)

    def trip(self, source: str) -> float:
        """Transient failure after all retries -> exponential cooldown. Returns the cooldown in seconds."""
        s = self._get(source)
        s.consecutive_failures += 1
        if s.consecutive_failures < COOLDOWN_AFTER:
            return 0.0
        delay = min(COOLDOWN_MAX_S, COOLDOWN_BASE_S * 2 ** (s.consecutive_failures - COOLDOWN_AFTER))
        s.cooldown_until = time.monotonic() + delay
        return delay

    def cooling(self, source: str) -> float:
        """Seconds left in this source's cooldown (0 = callable)."""
        return max(0.0, self._get(source).cooldown_until - time.monotonic())


class HttpClient:
    def __init__(self, timeout: float = 15.0, transport: httpx.AsyncBaseTransport | None = None,
                 backoff_base: float = 1.5):
        # System trust store (Windows cert store) instead of certifi, so machines with
        # antivirus/corporate SSL inspection still verify certificates correctly.
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
            verify=ssl.create_default_context(), transport=transport,
        )
        self.backoff_base = backoff_base
        self.health = Health()
        self._min_interval: dict[str, float] = {}
        self._base_interval: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_rate(self, host: str, per_minute: float) -> None:
        self._base_interval[host] = self._min_interval[host] = 60.0 / per_minute

    def _slow_down(self, host: str) -> None:
        """429 -> halve this host's request rate (never below MIN_RATE_FACTOR of the configured rate)."""
        base = self._base_interval.get(host)
        if base:
            self._min_interval[host] = min(base / MIN_RATE_FACTOR, self._min_interval.get(host, base) * 2)

    def _speed_up(self, host: str) -> None:
        base = self._base_interval.get(host)
        if base and self._min_interval.get(host, base) > base:
            self._min_interval[host] = max(base, self._min_interval[host] / RATE_RECOVERY)

    def rate_per_min(self, host: str) -> float | None:
        iv = self._min_interval.get(host)
        return round(60.0 / iv, 1) if iv else None

    async def _throttle(self, host: str) -> None:
        interval = self._min_interval.get(host)
        if not interval:
            return
        lock = self._locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last.get(host, 0) + interval - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._last[host] = time.monotonic()

    async def request_json(self, method: str, url: str, *, source: str, params=None,
                           json=None, headers=None, retries: int = 2, timeout: float | None = None,
                           respect_cooldown: bool = True):
        host = urlparse(url).netloc
        endpoint = safe_endpoint(url)
        call = json.get("method", "") if isinstance(json, dict) else method
        if respect_cooldown and self.health.cooling(source) > 0:
            self.health.get(source).skipped += 1
            return None
        err = ""
        extra = {"timeout": timeout} if timeout is not None else {}
        for attempt in range(retries + 1):
            await self._throttle(host)
            t0 = time.monotonic()
            try:
                r = await self._client.request(method, url, params=params, json=json, headers=headers, **extra)
            except (httpx.HTTPError, OSError) as e:
                err = f"{type(e).__name__}: {e}"
                self.health.mark(source, status=None, endpoint=endpoint, call=call, ms=None)
                if attempt < retries:
                    await asyncio.sleep(min(8, self.backoff_base * 2 ** attempt))
                continue
            self.health.mark(source, status=r.status_code, endpoint=endpoint, call=call,
                             ms=round((time.monotonic() - t0) * 1000))
            if r.status_code == 429:
                self._slow_down(host)
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else self.backoff_base * 2 ** attempt
                if attempt < retries:
                    await asyncio.sleep(min(15, delay))
                continue
            if r.status_code >= 400:
                self.health.fail(source, f"HTTP {r.status_code}: {r.text[:150]}")
                return None
            try:
                data = r.json()
            except ValueError:
                self.health.fail(source, "invalid JSON")
                return None
            self.health.ok(source)
            self._speed_up(host)
            return data
        self.health.fail(source, err or "request failed")
        self.health.trip(source)
        return None

    async def get_json(self, url: str, *, source: str, params=None, headers=None, retries: int = 2,
                       timeout: float | None = None, respect_cooldown: bool = True):
        return await self.request_json("GET", url, source=source, params=params, headers=headers, retries=retries,
                                       timeout=timeout, respect_cooldown=respect_cooldown)

    async def post_json(self, url: str, payload, *, source: str, headers=None, retries: int = 2,
                        timeout: float | None = None):
        return await self.request_json("POST", url, source=source, json=payload, headers=headers, retries=retries,
                                       timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()
