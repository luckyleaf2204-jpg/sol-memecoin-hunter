"""Solana JSON-RPC client with multi-endpoint failover, shared rate limits and retries.

Endpoint order: Helius (if HELIUS_API_KEY) -> SOLANA_RPC_URLS -> public endpoints.
A failing endpoint is put on a short cool-down and the next one is tried.

IMPORTANT (verified 2026-11-22): mainnet now carries *version 1* transactions.
getTransaction must be called with maxSupportedTransactionVersion >= 1, otherwise the
node answers -32015 "Transaction version (1) is not supported".
"""
from __future__ import annotations

import asyncio
import itertools
import logging
import random
import time
from typing import Any

import httpx

from ..config import RpcEndpoint
from ..logging_setup import redact
from ..net.http import RateLimiter, stats_for

log = logging.getLogger(__name__)

MAX_TX_VERSION = 1

# JSON-RPC errors that mean "this data does not exist" -> do not retry elsewhere
_TERMINAL_CODES = {-32009, -32007, -32004, -32602}
# errors where another endpoint may succeed (method not allowed on public RPC, etc.)
_SWITCH_CODES = {-32005, -32601, -32010, -32014, -32015, -32016, -32603, 403, 429}


class RpcError(Exception):
    def __init__(self, message: str, code: int | None = None, endpoint: str | None = None):
        super().__init__(redact(message))
        self.code = code
        self.endpoint = endpoint


class SolanaRpc:
    def __init__(self, endpoints: tuple[RpcEndpoint, ...] | list[RpcEndpoint], redis: Any | None = None,
                 timeout: float = 30.0):
        if not endpoints:
            raise ValueError("no RPC endpoints configured")
        self.endpoints = list(endpoints)
        self.redis = redis
        self._limiters = {e.name: RateLimiter(f"rpc_{e.name}", e.rps, redis) for e in self.endpoints}
        self._cooldown_until: dict[str, float] = {}
        self._ids = itertools.count(1)
        self._client = httpx.AsyncClient(timeout=httpx.Timeout(timeout, connect=10.0),
                                         headers={"content-type": "application/json"},
                                         limits=httpx.Limits(max_connections=64))

    async def close(self) -> None:
        await self._client.aclose()

    def _ordered(self, heavy: bool) -> list[RpcEndpoint]:
        now = time.monotonic()
        eps = [e for e in self.endpoints if (e.supports_heavy or not heavy)]
        ready = [e for e in eps if self._cooldown_until.get(e.name, 0) <= now]
        cooling = sorted((e for e in eps if e not in ready), key=lambda e: self._cooldown_until.get(e.name, 0))
        # spread load across equally ranked public endpoints
        if len(ready) > 1 and not any(e.name == "helius" for e in ready[:1]):
            ready = ready[:]
            random.shuffle(ready)
        return ready + cooling

    async def call(self, method: str, params: list | None = None, *, heavy: bool = False,
                   max_rounds: int = 3) -> Any:
        params = params or []
        last: RpcError | None = None
        for rnd in range(max_rounds):
            for ep in self._ordered(heavy):
                try:
                    return await self._call_one(ep, method, params)
                except RpcError as e:
                    last = e
                    if e.code in _TERMINAL_CODES:
                        raise
                    cd = 2.0 if e.code in (429, -32005) else 5.0
                    self._cooldown_until[ep.name] = time.monotonic() + cd * (rnd + 1)
                    continue
            await asyncio.sleep(min(10.0, 0.8 * (2 ** rnd)) * (0.7 + 0.6 * random.random()))
        assert last is not None
        raise last

    async def _call_one(self, ep: RpcEndpoint, method: str, params: list) -> Any:
        lim = self._limiters[ep.name]
        st = stats_for(f"rpc_{ep.name}")
        await lim.acquire()
        t0 = time.monotonic()
        body = {"jsonrpc": "2.0", "id": next(self._ids), "method": method, "params": params}
        try:
            r = await self._client.post(ep.url, json=body)
        except (httpx.TimeoutException, httpx.TransportError) as e:
            st.record_error(f"{method}: {type(e).__name__}")
            raise RpcError(f"{ep.name} transport error: {type(e).__name__}", None, ep.name) from e
        if r.status_code == 429:
            lim.on_rate_limited()
            st.record_error(f"{method}: HTTP 429", rate_limited=True)
            raise RpcError(f"{ep.name} rate limited", 429, ep.name)
        if r.status_code >= 400:
            st.record_error(f"{method}: HTTP {r.status_code}")
            raise RpcError(f"{ep.name} HTTP {r.status_code}: {r.text[:200]}", r.status_code, ep.name)
        try:
            data = r.json()
        except ValueError as e:
            st.record_error(f"{method}: invalid JSON")
            raise RpcError(f"{ep.name} invalid JSON", None, ep.name) from e
        if "error" in data:
            err = data["error"] or {}
            code = err.get("code")
            st.record_error(f"{method}: {code} {err.get('message', '')[:150]}",
                            rate_limited=code == 429)
            raise RpcError(f"{ep.name} {method}: {err.get('message')}", code, ep.name)
        lim.on_success()
        st.record_ok((time.monotonic() - t0) * 1000)
        return data.get("result")

    # ---- typed helpers -------------------------------------------------------------

    async def get_transaction(self, signature: str, commitment: str = "confirmed") -> dict | None:
        try:
            return await self.call("getTransaction", [signature, {
                "encoding": "jsonParsed", "maxSupportedTransactionVersion": MAX_TX_VERSION,
                "commitment": commitment}])
        except RpcError as e:
            if e.code in _TERMINAL_CODES:
                return None
            raise

    async def get_signatures_for_address(self, address: str, *, before: str | None = None,
                                         until: str | None = None, limit: int = 1000,
                                         commitment: str = "confirmed") -> list[dict]:
        opts: dict[str, Any] = {"limit": min(max(limit, 1), 1000), "commitment": commitment}
        if before:
            opts["before"] = before
        if until:
            opts["until"] = until
        return await self.call("getSignaturesForAddress", [address, opts]) or []

    async def get_account_info(self, address: str, encoding: str = "base64") -> dict | None:
        res = await self.call("getAccountInfo", [address, {"encoding": encoding, "commitment": "confirmed"}])
        return (res or {}).get("value")

    async def get_multiple_accounts(self, addresses: list[str], encoding: str = "base64") -> list[dict | None]:
        out: list[dict | None] = []
        for i in range(0, len(addresses), 100):
            chunk = addresses[i:i + 100]
            res = await self.call("getMultipleAccounts", [chunk, {"encoding": encoding, "commitment": "confirmed"}])
            out.extend((res or {}).get("value") or [None] * len(chunk))
        return out

    async def get_token_largest_accounts(self, mint: str) -> list[dict]:
        res = await self.call("getTokenLargestAccounts", [mint, {"commitment": "confirmed"}], heavy=True)
        return (res or {}).get("value") or []

    async def get_token_supply(self, mint: str) -> dict | None:
        res = await self.call("getTokenSupply", [mint, {"commitment": "confirmed"}])
        return (res or {}).get("value")

    async def get_balance(self, address: str) -> int:
        res = await self.call("getBalance", [address, {"commitment": "confirmed"}])
        return int((res or {}).get("value") or 0)

    async def get_token_accounts_by_owner(self, owner: str, mint: str) -> list[dict]:
        res = await self.call("getTokenAccountsByOwner", [owner, {"mint": mint},
                                                          {"encoding": "jsonParsed", "commitment": "confirmed"}])
        return (res or {}).get("value") or []

    def ws_urls(self) -> list[tuple[str, str]]:
        return [(e.name, e.ws_url) for e in self.endpoints]
