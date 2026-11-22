"""DexScreener public API (https://docs.dexscreener.com/api/reference), no key needed.
GET /tokens/v1/solana/{up to 30 comma separated mints} -> list of pairs."""
from __future__ import annotations

from ..net.http import HttpClient

URL = "https://api.dexscreener.com/tokens/v1/solana/"


async def token_pairs(http: HttpClient, mints: list[str]) -> dict[str, dict]:
    """Best pair (highest USD liquidity) per mint."""
    out: dict[str, dict] = {}
    for i in range(0, len(mints), 30):
        chunk = mints[i:i + 30]
        data = await http.request_json("dexscreener", "GET", URL + ",".join(chunk), cache_ttl=20) or []
        for p in data if isinstance(data, list) else []:
            base = (p.get("baseToken") or {}).get("address")
            if base not in chunk:
                continue
            liq = float((p.get("liquidity") or {}).get("usd") or 0)
            cur = out.get(base)
            if cur is None or liq > float((cur.get("liquidity") or {}).get("usd") or 0):
                out[base] = p
    return out
