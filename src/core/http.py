"""Shared async HTTP client with retries, per-host throttling and source health tracking.

Every data-source adapter goes through this client, so a failing source only marks
itself unhealthy and returns None — it never crashes the scanner.
"""
from __future__ import annotations

import asyncio
import re
import ssl
import time
from dataclasses import dataclass, field
from urllib.parse import urlparse

import httpx

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


class HttpClient:
    def __init__(self, timeout: float = 15.0):
        # System trust store (Windows cert store) instead of certifi, so machines with
        # antivirus/corporate SSL inspection still verify certificates correctly.
        self._client = httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True,
            verify=ssl.create_default_context(),
        )
        self.health = Health()
        self._min_interval: dict[str, float] = {}
        self._last: dict[str, float] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    def set_rate(self, host: str, per_minute: float) -> None:
        self._min_interval[host] = 60.0 / per_minute

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
                           json=None, headers=None, retries: int = 2):
        host = urlparse(url).netloc
        endpoint = safe_endpoint(url)
        call = json.get("method", "") if isinstance(json, dict) else method
        err = ""
        for attempt in range(retries + 1):
            await self._throttle(host)
            t0 = time.monotonic()
            try:
                r = await self._client.request(method, url, params=params, json=json, headers=headers)
            except (httpx.HTTPError, OSError) as e:
                err = f"{type(e).__name__}: {e}"
                self.health.mark(source, status=None, endpoint=endpoint, call=call, ms=None)
                await asyncio.sleep(min(8, 1.5 * 2 ** attempt))
                continue
            self.health.mark(source, status=r.status_code, endpoint=endpoint, call=call,
                             ms=round((time.monotonic() - t0) * 1000))
            if r.status_code == 429 or r.status_code >= 500:
                err = f"HTTP {r.status_code}"
                retry_after = r.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else 1.5 * 2 ** attempt
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
            return data
        self.health.fail(source, err or "request failed")
        return None

    async def get_json(self, url: str, *, source: str, params=None, headers=None, retries: int = 2):
        return await self.request_json("GET", url, source=source, params=params, headers=headers, retries=retries)

    async def post_json(self, url: str, payload, *, source: str, headers=None, retries: int = 2):
        return await self.request_json("POST", url, source=source, json=payload, headers=headers, retries=retries)

    async def aclose(self) -> None:
        await self._client.aclose()
