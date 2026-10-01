"""Jupiter swap API — QUOTES ONLY (lite-api.jup.ag/swap/v1/quote). Read-only: no transaction is built, signed or sent.

Used by PAPER execution so simulated fills follow a real route: Jupiter's best route, its output amount and its
price impact for the exact size. If Jupiter is unavailable or the quote does not match the request, a BUY is not
taken (route not verifiable) and a protective SELL falls back to the liquidity model.
"""
from __future__ import annotations

from core.http import HttpClient

BASE = "https://lite-api.jup.ag/swap/v1"
SOURCE = "jupiter"
WSOL = "So11111111111111111111111111111111111111112"


class JupiterQuotes:
    def __init__(self, http: HttpClient, base: str = BASE):
        self.http = http
        self.base = base.rstrip("/")
        http.set_rate("lite-api.jup.ag", 50)

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
        if amount_raw <= 0:
            return None
        q = await self.http.get_json(f"{self.base}/quote", source=SOURCE, timeout=8.0, retries=1, params={
            "inputMint": input_mint, "outputMint": output_mint, "amount": int(amount_raw),
            "slippageBps": int(slippage_bps), "restrictIntermediateTokens": "true"})
        if not isinstance(q, dict) or not q.get("outAmount") or q.get("inputMint") != input_mint \
                or q.get("outputMint") != output_mint:
            return None                                   # never use a malformed / mismatched quote
        return q


def route_label(quote: dict) -> str:
    labels = [((r.get("swapInfo") or {}).get("label") or "?") for r in quote.get("routePlan") or []]
    return "Jupiter: " + " → ".join(labels[:3]) if labels else "Jupiter"


def price_impact(quote: dict) -> float | None:
    """Fraction (0.012 = 1.2 %)."""
    try:
        return float(quote.get("priceImpactPct"))
    except (TypeError, ValueError):
        return None
