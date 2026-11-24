"""Real prices for calls. Never uses a price observed at another time than the one it is stored for.

now      DexScreener best pair (USD price, market cap, liquidity) - observed_at = fetch time
history  GeckoTerminal 1-minute candles in USD (free API): the candle of the requested minute
validate a call address is a token mint when DexScreener lists it as a base token; an address that
         is a pair (DexScreener links carry pairs) is mapped to the pair's base token
"""
from __future__ import annotations

import datetime as dt
import logging

from ..net.http import HttpClient

log = logging.getLogger(__name__)
DS_TOKENS = "https://api.dexscreener.com/tokens/v1/solana/"
DS_PAIRS = "https://api.dexscreener.com/latest/dex/pairs/solana/"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
GT_HEADERS = {"Accept": "application/json;version=20230302"}
UTC = dt.timezone.utc


def _f(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


async def snapshot(http: HttpClient, mints: list[str]) -> dict[str, dict]:
    """Current price of each mint (best pair by USD liquidity)."""
    out: dict[str, dict] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        data = await http.request_json("dexscreener", "GET", DS_TOKENS + ",".join(chunk)) or []
        now = dt.datetime.now(UTC)
        for p in data if isinstance(data, list) else []:
            base = (p.get("baseToken") or {}).get("address")
            if base not in chunk:
                continue
            liq = _f((p.get("liquidity") or {}).get("usd")) or 0.0
            cur = out.get(base)
            if cur is None or liq > (cur["liquidity_usd"] or 0):
                out[base] = {"price_usd": _f(p.get("priceUsd")), "price_sol": _f(p.get("priceNative")),
                             "mc_usd": _f(p.get("marketCap")) or _f(p.get("fdv")), "liquidity_usd": liq,
                             "pair": p.get("pairAddress"), "dex": p.get("dexId"), "observed_at": now,
                             "symbol": (p.get("baseToken") or {}).get("symbol"), "name": (p.get("baseToken") or {}).get("name"),
                             "pair_created_at": p.get("pairCreatedAt")}
    return out


async def resolve_addresses(http: HttpClient, addrs: list[str]) -> dict[str, str | None]:
    """address -> token mint (itself, or the base token of a pair), None when neither."""
    res: dict[str, str | None] = {a: None for a in addrs}
    snap = await snapshot(http, addrs)
    for a in addrs:
        if a in snap:
            res[a] = a
    for a in [a for a in addrs if res[a] is None]:
        d = await http.request_json("dexscreener", "GET", DS_PAIRS + a, cache_ttl=3600) or {}
        pairs = d.get("pairs") or ([d["pair"]] if d.get("pair") else [])
        for p in pairs:
            base = (p.get("baseToken") or {}).get("address")
            quote = (p.get("quoteToken") or {}).get("address")
            if p.get("pairAddress") == a and base:
                res[a] = base if quote == "So11111111111111111111111111111111111111112" or base != quote else base
    return res


async def pools(http: HttpClient, mint: str) -> list[dict]:
    d = await http.request_json("geckoterminal", "GET", f"{GT}/tokens/{mint}/pools", headers=GT_HEADERS, cache_ttl=1800) or {}
    out = []
    for p in d.get("data") or []:
        a = p.get("attributes") or {}
        dex = (((p.get("relationships") or {}).get("dex") or {}).get("data") or {}).get("id")
        out.append({"pool": a.get("address"), "dex": dex, "reserve_usd": _f(a.get("reserve_in_usd")) or 0.0,
                    "created_at": a.get("pool_created_at")})
    return sorted(out, key=lambda p: -p["reserve_usd"])


async def candles_usd(http: HttpClient, pool: str, start: int, end: int) -> list[list]:
    """1-minute USD candles [ts, o, h, l, c, v] between start and end (unix s), oldest first."""
    out: dict[int, list] = {}
    before = end + 60
    for _ in range(4):
        d = await http.request_json("geckoterminal", "GET", f"{GT}/pools/{pool}/ohlcv/minute", headers=GT_HEADERS,
                                    params={"aggregate": "1", "limit": "1000", "currency": "usd", "token": "base",
                                            "before_timestamp": str(before)}) or {}
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        if not rows:
            break
        for r in rows:
            if start - 60 <= r[0] <= end:
                out[int(r[0])] = [int(r[0])] + [float(x) for x in r[1:]]
        oldest = min(r[0] for r in rows)
        if oldest <= start or len(rows) < 1000:
            break
        before = oldest
    return [out[k] for k in sorted(out)]


async def candles_for_mint(http: HttpClient, mint: str, start: int, end: int, max_pools: int = 2) -> tuple[list[list], list[str]]:
    """Candles of the mint's main pools merged per minute (a pump.fun token moves from the curve to
    PumpSwap: both are read). Per minute: highest high, lowest low, close of the busier pool."""
    ps = [p for p in await pools(http, mint) if p["pool"]][:max_pools]
    merged: dict[int, list] = {}
    for p in ps:
        for c in await candles_usd(http, p["pool"], start, end):
            m = merged.get(c[0])
            if m is None:
                merged[c[0]] = list(c)
            else:
                m[2] = max(m[2], c[2])
                m[3] = min(m[3], c[3])
                if c[5] > m[5]:
                    m[1], m[4], m[5] = c[1], c[4], c[5]
    return [merged[k] for k in sorted(merged)], [p["pool"] for p in ps]
