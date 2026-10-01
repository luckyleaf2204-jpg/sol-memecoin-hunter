"""Solana JSON-RPC adapter with endpoint fallback.

Order: Helius (if HELIUS_API_KEY) -> SOLANA_RPC_URL (if set) -> public mainnet RPC.
Verified 2026-09-29: the public RPC serves getBalance / getTokenAccountsByOwner /
getSignaturesForAddress / getTransaction, but answers 429 to getTokenLargestAccounts,
so top-holder data needs a Helius (free tier is enough) or other private RPC.

Helius DAS `getTokenAccounts` (https://www.helius.dev/docs/api-reference/das/gettokenaccounts)
is used for holder counts and full top-50 lists.
"""
from __future__ import annotations

import os
import time

from core.http import HttpClient

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
# Helius credit cost per call (https://docs.helius.dev/ — DAS 10, standard RPC 1). The free plan has ~1M / month:
# without a budget the holder scans burned it in about a day (production incident 2026-10-01: "max usage reached").
DAS_CREDITS, RPC_CREDITS = 10, 1


class CreditMeter:
    """Counts Helius credits per UTC day against monthly_credits / 30. Spending stops (fail-safe) at the limit."""

    def __init__(self, monthly_credits: int):
        self.daily_budget = max(0, int(monthly_credits / 30))
        self.day = time.strftime("%Y-%m-%d", time.gmtime())
        self.used = 0
        self.denied = 0
        self.exhausted_at: float | None = None      # set when Helius itself answers "max usage reached"

    def _roll(self):
        d = time.strftime("%Y-%m-%d", time.gmtime())
        if d != self.day:
            self.day, self.used, self.denied = d, 0, 0

    def paced_ok(self, now: float | None = None, burst: float = 0.05) -> bool:
        """Spend evenly over the UTC day: used <= budget × (elapsed fraction of the day + burst)."""
        self._roll()
        now = now or time.time()
        frac = (now % 86400) / 86400
        return self.used <= self.daily_budget * min(1.0, frac + burst)

    def remaining(self) -> int:
        self._roll()
        return max(0, self.daily_budget - self.used)

    def allow(self, cost: int) -> bool:
        self._roll()
        if self.exhausted_at and time.time() - self.exhausted_at < 3600:
            self.denied += 1
            return False
        if self.used + cost > self.daily_budget:
            self.denied += 1
            return False
        return True

    def spend(self, cost: int) -> None:
        self._roll()
        self.used += cost

    def state(self) -> dict:
        self._roll()
        return {"day": self.day, "used": self.used, "daily_budget": self.daily_budget, "remaining": self.remaining(),
                "denied": self.denied, "quota_exhausted": bool(self.exhausted_at and time.time() - self.exhausted_at < 3600)}
SOURCE = "solana_rpc"
SOURCE_DAS = "helius_das"


class SolanaRpc:
    def __init__(self, http: HttpClient, helius_key: str = "", rpc_url: str = ""):
        self.http = http
        self.helius_url = f"https://mainnet.helius-rpc.com/?api-key={helius_key}" if helius_key else ""
        try:
            monthly = int(os.environ.get("HELIUS_MONTHLY_CREDITS", "1000000"))
        except ValueError:
            monthly = 1_000_000
        self.credits = CreditMeter(monthly)
        self.urls = [u for u in (self.helius_url, rpc_url.strip(), PUBLIC_RPC) if u]
        http.set_rate("api.mainnet-beta.solana.com", 90)

    @property
    def has_das(self) -> bool:
        return bool(self.helius_url)

    async def call(self, method: str, params: list | dict, *, urls: list[str] | None = None):
        payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params}
        for url in urls or self.urls:
            if url == self.helius_url:
                if not self.credits.allow(RPC_CREDITS):
                    continue                                   # budget used up: fall back to the next RPC
                self.credits.spend(RPC_CREDITS)
            data = await self.http.post_json(url, payload, source=SOURCE, retries=1)
            if isinstance(data, dict) and "result" in data:
                return data["result"]
            if isinstance(data, dict) and "error" in data:
                self.http.health.fail(SOURCE, f"{method}: {data['error'].get('message', data['error'])}")
        return None

    async def token_supply(self, mint: str) -> tuple[float, int] | None:
        r = await self.call("getTokenSupply", [mint])
        v = (r or {}).get("value")
        return (float(v["uiAmount"] or 0), int(v["decimals"])) if v else None

    async def largest_accounts(self, mint: str) -> list[tuple[str, float]] | None:
        r = await self.call("getTokenLargestAccounts", [mint])
        v = (r or {}).get("value")
        if v is None:
            return None
        return [(a["address"], float(a.get("uiAmount") or 0)) for a in v]

    async def account_owners(self, addresses: list[str]) -> dict[str, str]:
        out: dict[str, str] = {}
        for i in range(0, len(addresses), 100):
            chunk = addresses[i:i + 100]
            r = await self.call("getMultipleAccounts", [chunk, {"encoding": "jsonParsed"}])
            for addr, acc in zip(chunk, (r or {}).get("value") or []):
                try:
                    out[addr] = acc["data"]["parsed"]["info"]["owner"]
                except (TypeError, KeyError):
                    pass
        return out

    async def owner_token_balance(self, owner: str, mint: str) -> float | None:
        r = await self.call("getTokenAccountsByOwner", [owner, {"mint": mint}, {"encoding": "jsonParsed"}])
        if r is None:
            return None
        total = 0.0
        for acc in r.get("value") or []:
            try:
                total += float(acc["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0)
            except (KeyError, TypeError):
                pass
        return total

    async def sol_balance(self, address: str) -> float | None:
        r = await self.call("getBalance", [address])
        return r["value"] / 1e9 if isinstance(r, dict) and "value" in r else None

    async def signatures(self, address: str, limit: int = 1000, before: str | None = None) -> list[dict] | None:
        opts: dict = {"limit": limit}
        if before:
            opts["before"] = before
        return await self.call("getSignaturesForAddress", [address, opts])

    async def transaction(self, signature: str) -> dict | None:
        return await self.call("getTransaction", [signature, {"encoding": "jsonParsed",
                                                              "maxSupportedTransactionVersion": 0}])

    async def helius_check(self) -> dict:
        """One small DAS call to prove the key works. Returns status info — never the key."""
        if not self.helius_url:
            return {"state": "NO_KEY"}
        usdc = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
        r = await self.das_token_accounts(usdc, page=1, limit=1)
        st = self.http.health.get(SOURCE_DAS)
        return {"state": "CONNECTED" if r is not None else "FAILED", "endpoint": st.last_endpoint,
                "call": st.last_call or "getTokenAccounts", "status": st.last_status, "ms": st.last_ms,
                "error": "" if r is not None else st.last_error}

    async def das_get_asset(self, mint: str) -> dict | None:
        """On-chain metadata of this exact mint (DAS getAsset): symbol, name, token program, mint extensions."""
        if not self.helius_url:
            return None
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getAsset", "params": {"id": mint}}
        if not self.credits.allow(DAS_CREDITS):
            self.http.health.fail(SOURCE_DAS, "Helius quota exhausted ('max usage reached') — paused for 1 h"
                                  if self.credits.state()["quota_exhausted"] else
                                  "Helius daily credit budget reached (HELIUS_MONTHLY_CREDITS / 30)")
            return None
        self.credits.spend(DAS_CREDITS)
        data = await self.http.post_json(self.helius_url, payload, source=SOURCE_DAS, retries=1)
        if data is None and self.http.health.get(SOURCE_DAS).last_status == 429:
            self.credits.exhausted_at = time.time()           # rate limit / "max usage reached": stop for 1 h
        r = data.get("result") if isinstance(data, dict) else None
        if not isinstance(r, dict):
            return None
        if r.get("id") and r["id"] != mint:          # never accept metadata of another address
            return None
        meta = (r.get("content") or {}).get("metadata") or {}
        return {"symbol": (meta.get("symbol") or "").strip(), "name": (meta.get("name") or "").strip(),
                "token_program": (r.get("token_info") or {}).get("token_program") or "",
                "extensions": sorted((r.get("mint_extensions") or {}).keys()), "interface": r.get("interface") or "",
                "mint_authority": (r.get("token_info") or {}).get("mint_authority") or "",
                "freeze_authority": (r.get("token_info") or {}).get("freeze_authority") or ""}

    async def das_token_accounts(self, mint: str, page: int = 1, limit: int = 1000) -> dict | None:
        if not self.helius_url:
            return None
        payload = {"jsonrpc": "2.0", "id": 1, "method": "getTokenAccounts",
                   "params": {"mint": mint, "page": page, "limit": limit}}
        if not self.credits.allow(DAS_CREDITS):
            self.http.health.fail(SOURCE_DAS, "Helius quota exhausted ('max usage reached') — paused for 1 h"
                                  if self.credits.state()["quota_exhausted"] else
                                  "Helius daily credit budget reached (HELIUS_MONTHLY_CREDITS / 30)")
            return None
        self.credits.spend(DAS_CREDITS)
        data = await self.http.post_json(self.helius_url, payload, source=SOURCE_DAS, retries=1)
        if data is None and self.http.health.get(SOURCE_DAS).last_status == 429:
            self.credits.exhausted_at = time.time()           # rate limit / "max usage reached": stop for 1 h
        if isinstance(data, dict) and "result" in data:
            return data["result"]
        if isinstance(data, dict) and "error" in data:
            err = data["error"]
            msg = err.get("message", err) if isinstance(err, dict) else err
            code = err.get("code") if isinstance(err, dict) else None
            self.http.health.fail(SOURCE_DAS, f"JSON-RPC error {code}: {msg}")
        return None
