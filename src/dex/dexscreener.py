"""DexScreener adapter (official public API, no key). Docs: https://docs.dexscreener.com/api/reference

  GET /tokens/v1/solana/{up to 30 comma-separated mints}   market data (pump.fun curve + PumpSwap/Raydium pairs)
  GET /token-profiles/latest/v1                           fallback discovery
Verified 2026-09-29. Bonding-curve pairs (dexId "pumpfun") come back WITHOUT a liquidity
field; validation then uses the Pump.fun-reported curve reserve only if it passes consistency checks.
"""
from __future__ import annotations

from core.http import HttpClient
from core.models import MarketData

BASE_URL = "https://api.dexscreener.com"
SOURCE = "dexscreener"
CHAIN = "solana"
BATCH = 30


def _num(v) -> float | None:
    """Parse a numeric field; anything missing or non-numeric becomes None (never 0)."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and f not in (float("inf"), float("-inf")) else None


def _count(bucket) -> tuple[int | None, int | None]:
    if not isinstance(bucket, dict) or "buys" not in bucket or "sells" not in bucket:
        return None, None
    b, s = _num(bucket.get("buys")), _num(bucket.get("sells"))
    return (int(b) if b is not None else None, int(s) if s is not None else None)


def parse_pair(p: dict) -> MarketData:
    """Raw DexScreener pair -> MarketData. No validation here (see validation/market.py),
    but missing fields stay None so they can never be mistaken for real zeros."""
    txns, vol, pc = p.get("txns") or {}, p.get("volume") or {}, p.get("priceChange") or {}
    liq = (p.get("liquidity") or {}).get("usd")
    b5, s5 = _count(txns.get("m5"))
    b1, s1 = _count(txns.get("h1"))
    created = _num(p.get("pairCreatedAt"))
    return MarketData(
        price_usd=_num(p.get("priceUsd")),
        market_cap=_num(p.get("marketCap")),
        fdv=_num(p.get("fdv")),
        liquidity_usd=_num(liq),
        liquidity_source="dexscreener_amm" if liq is not None else "",
        vol_5m=_num(vol.get("m5")),
        vol_1h=_num(vol.get("h1")),
        vol_6h=_num(vol.get("h6")),
        vol_24h=_num(vol.get("h24")),
        buys_5m=b5, sells_5m=s5, buys_1h=b1, sells_1h=s1,
        price_change_5m=_num(pc.get("m5")),
        price_change_1h=_num(pc.get("h1")),
        price_change_6h=_num(pc.get("h6")),
        price_change_24h=_num(pc.get("h24")),
        dex_id=p.get("dexId") or "",
        pair_address=p.get("pairAddress") or "",
        quote_symbol=(p.get("quoteToken") or {}).get("symbol") or "",
        pair_created_at=created / 1000 if created else None,
    )


def parse_socials(p: dict) -> dict[str, str]:
    info = p.get("info") or {}
    out: dict[str, str] = {}
    for s in info.get("socials") or []:
        t, url = (s.get("type") or "").lower(), s.get("url") or ""
        if t in ("twitter", "x") and url:
            out.setdefault("twitter", url)
        elif t == "telegram" and url:
            out.setdefault("telegram", url)
    for w in info.get("websites") or []:
        if w.get("url"):
            out.setdefault("website", w["url"])
            break
    return out


def pick_best_pair(pairs: list[dict]) -> dict:
    """Highest-liquidity pair wins; bonding-curve pairs (no liquidity) fall back to 24h volume."""
    return max(pairs, key=lambda p: (((p.get("liquidity") or {}).get("usd") or 0),
                                     ((p.get("volume") or {}).get("h24") or 0)))


class DexScreenerClient:
    def __init__(self, http: HttpClient, base_url: str = BASE_URL):
        self.http = http
        self.base = base_url.rstrip("/")
        http.set_rate("api.dexscreener.com", 240)  # documented 300/min for pair endpoints

    async def tokens(self, mints: list[str]) -> dict[str, tuple[MarketData, dict]] | None:
        """Returns {mint: (MarketData, socials)}; None only if every batch failed."""
        out: dict[str, tuple[MarketData, dict]] = {}
        any_ok = False
        for i in range(0, len(mints), BATCH):
            chunk = mints[i:i + BATCH]
            data = await self.http.get_json(f"{self.base}/tokens/v1/{CHAIN}/{','.join(chunk)}", source=SOURCE,
                                            timeout=8.0, retries=1)
            if not isinstance(data, list):
                continue
            any_ok = True
            grouped: dict[str, list[dict]] = {}
            for p in data:
                base = (p.get("baseToken") or {}).get("address")
                if p.get("chainId") == CHAIN and base in chunk:
                    grouped.setdefault(base, []).append(p)
            for mint, pairs in grouped.items():
                best = pick_best_pair(pairs)
                socials: dict[str, str] = {}
                for p in pairs:
                    for k, v in parse_socials(p).items():
                        socials.setdefault(k, v)
                out[mint] = (parse_pair(best), socials)
        return out if any_ok or not mints else None

    async def latest_profiles(self) -> list[str] | None:
        data = await self.http.get_json(f"{self.base}/token-profiles/latest/v1", source=SOURCE)
        if not isinstance(data, list):
            return None
        return [d["tokenAddress"] for d in data if d.get("chainId") == CHAIN and d.get("tokenAddress")]
