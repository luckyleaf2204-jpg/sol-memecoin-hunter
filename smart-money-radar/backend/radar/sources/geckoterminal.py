"""GeckoTerminal public API v2 (https://apiguide.geckoterminal.com), free, 30 calls/min.
Used for HISTORICAL minute candles of pump.fun curves ('pump-fun' dex) and PumpSwap pools
when our own on-chain bars do not cover a position (history before the stream started).
Candles are requested with currency=token so prices are in SOL (the pool's quote)."""
from __future__ import annotations

from ..net.http import HttpClient

BASE = "https://api.geckoterminal.com/api/v2/networks/solana"
HEADERS = {"Accept": "application/json;version=20230302"}


async def token_pools(http: HttpClient, mint: str) -> list[dict]:
    data = await http.request_json("geckoterminal", "GET", f"{BASE}/tokens/{mint}/pools", headers=HEADERS,
                                   cache_ttl=3600)
    out = []
    for p in (data or {}).get("data") or []:
        a = p.get("attributes") or {}
        dex = (((p.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id")
        base = (((p.get("relationships") or {}).get("base_token") or {}).get("data") or {}).get("id", "")
        if dex in ("pump-fun", "pumpswap") and base.endswith(mint):
            out.append({"pool": a.get("address"), "dex": dex, "created_at": a.get("pool_created_at")})
    return out


async def minute_candles(http: HttpClient, pool: str, before_ts: int | None = None, limit: int = 1000) -> list[list]:
    """[[ts, open, high, low, close, volume_usd], ...] newest first, prices in SOL."""
    params = {"aggregate": "1", "limit": str(limit), "currency": "token"}
    if before_ts:
        params["before_timestamp"] = str(before_ts)
    data = await http.request_json("geckoterminal", "GET", f"{BASE}/pools/{pool}/ohlcv/minute", params=params,
                                   headers=HEADERS)
    return (((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
