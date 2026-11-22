"""Jupiter (https://dev.jup.ag/docs): price API v3 and swap quote API (lite-api, free tier)."""
from __future__ import annotations

from ..net.http import HttpClient, HttpError
from ..solana.constants import WSOL_MINT

PRICE_URL = "https://lite-api.jup.ag/price/v3"
QUOTE_URL = "https://lite-api.jup.ag/swap/v1/quote"


async def sol_usd(http: HttpClient) -> float | None:
    data = await http.request_json("jupiter", "GET", PRICE_URL, params={"ids": WSOL_MINT}, cache_ttl=20)
    v = (data or {}).get(WSOL_MINT) or {}
    p = v.get("usdPrice")
    return float(p) if p else None


async def sell_quote(http: HttpClient, mint: str, price_sol: float, decimals: int = 6,
                     sol_value: float = 1.0) -> tuple[bool, float | None] | None:
    """Quote selling ~`sol_value` SOL worth of the token. Returns (route_exists, price_impact)."""
    if not price_sol or price_sol <= 0:
        return None
    amount = int(sol_value / price_sol * 10 ** decimals)
    if amount <= 0:
        return None
    try:
        q = await http.request_json("jupiter", "GET", QUOTE_URL, cache_ttl=60, params={
            "inputMint": mint, "outputMint": WSOL_MINT, "amount": str(amount), "slippageBps": "500"})
    except HttpError as e:
        if e.status in (400, 404):
            return False, None
        raise
    if not q or not q.get("routePlan"):
        return False, None
    try:
        impact = abs(float(q.get("priceImpactPct") or 0))
    except ValueError:
        impact = None
    return True, impact
