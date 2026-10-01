"""Jupiter swap API — QUOTES ONLY (lite-api.jup.ag/swap/v1/quote). Read-only: no transaction is built, signed or sent.

Used by PAPER execution so simulated fills follow a real route: Jupiter's best route, its output amount and its
price impact for the exact size. If Jupiter is unavailable or the quote does not match the request, a BUY is not
taken (route not verifiable) and a protective SELL falls back to the liquidity model.

Every quote is classified (QuoteResult.status) so the bot can tell a real "no route" from a transient API problem:
  OK            200, input/output mint and inAmount match the request, outAmount > 0       -> MATCH
  NO_ROUTE      400 TOKEN_NOT_TRADABLE / COULD_NOT_FIND_ANY_ROUTE / ... (no real route)    -> SKIP, final
  INVALID       other 4xx, or a 200 quote that does not match the request                  -> SKIP, final
  RATE_LIMITED  429 · TIMEOUT · API_ERROR (5xx / network / bad JSON) · COOLDOWN (source
                circuit breaker open)                                                      -> transient: retried
Transient failures are retried here with a short backoff (honouring Retry-After) inside a small time budget.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

from core.http import HttpClient

BASE = "https://lite-api.jup.ag/swap/v1"
SOURCE = "jupiter"
WSOL = "So11111111111111111111111111111111111111112"

OK, NO_ROUTE, INVALID = "OK", "NO_ROUTE", "INVALID"
RATE_LIMITED, TIMEOUT, API_ERROR, COOLDOWN = "RATE_LIMITED", "TIMEOUT", "API_ERROR", "COOLDOWN"
TRANSIENT = {RATE_LIMITED, TIMEOUT, API_ERROR, COOLDOWN}
NO_ROUTE_CODES = {"TOKEN_NOT_TRADABLE", "COULD_NOT_FIND_ANY_ROUTE", "NO_ROUTES_FOUND", "ROUTE_NOT_FOUND",
                  "NO_ROUTE_FOUND", "MARKET_NOT_FOUND"}
BACKOFF_S = (0.5, 1.0, 2.0)


@dataclass
class QuoteResult:
    status: str
    quote: dict | None = None
    http: int | None = None
    code: str = ""                 # Jupiter errorCode, when it sent one
    detail: str = ""
    attempts: int = 0

    @property
    def ok(self) -> bool:
        return self.status == OK

    @property
    def transient(self) -> bool:
        return self.status in TRANSIENT

    def label(self) -> str:
        bits = [self.status] + ([self.code] if self.code else []) + ([f"HTTP {self.http}"] if self.http else [])
        return " · ".join(bits) + (f" · {self.detail}" if self.detail else "") + f" · {self.attempts} try"


class JupiterQuotes:
    def __init__(self, http: HttpClient, base: str = BASE):
        self.http = http
        self.base = base.rstrip("/")
        http.set_rate("lite-api.jup.ag", 50)

    async def quote(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int) -> dict | None:
        """Quote or None (any failure). Kept for callers that only need the route (SELL path)."""
        return (await self.quote_result(input_mint, output_mint, amount_raw, slippage_bps)).quote

    async def quote_result(self, input_mint: str, output_mint: str, amount_raw: int, slippage_bps: int,
                           attempts: int = 3, budget_s: float = 8.0) -> QuoteResult:
        if amount_raw <= 0:
            return QuoteResult(INVALID, detail="amount <= 0")
        params = {"inputMint": input_mint, "outputMint": output_mint, "amount": int(amount_raw),
                  "slippageBps": int(slippage_bps), "restrictIntermediateTokens": "true"}
        end = time.monotonic() + budget_s
        res = QuoteResult(API_ERROR, detail="not attempted")
        for i in range(attempts):
            left = end - time.monotonic()
            if left <= 0.3:
                break
            cool = self.http.health.cooling(SOURCE)
            if cool > 0:
                res = QuoteResult(COOLDOWN, detail=f"source cooling {cool:.0f}s", attempts=i)
                if cool >= left:
                    break
                await asyncio.sleep(cool)
                continue
            status, data, err, retry_after = await self.http.request_once(
                "GET", f"{self.base}/quote", source=SOURCE, params=params, timeout=min(5.0, left))
            res = classify(status, data, err, input_mint, output_mint, int(amount_raw))
            res.attempts = i + 1
            if not res.transient:
                return res
            delay = min(retry_after if retry_after is not None else BACKOFF_S[min(i, len(BACKOFF_S) - 1)], 3.0)
            if i + 1 < attempts and end - time.monotonic() > delay + 0.3:
                await asyncio.sleep(delay)
        if res.transient and res.status != COOLDOWN:
            self.http.health.trip(SOURCE)                # every attempt failed: the circuit breaker counts it
        return res


def classify(status: int | None, data, err: str, input_mint: str, output_mint: str, amount_raw: int) -> QuoteResult:
    code = str(data.get("errorCode") or "") if isinstance(data, dict) else ""
    if status is None:
        return QuoteResult(TIMEOUT if err == "timeout" else API_ERROR, detail=err)
    if status == 429:
        return QuoteResult(RATE_LIMITED, http=429, code=code)
    if status >= 500:
        return QuoteResult(API_ERROR, http=status, code=code)
    if status != 200:
        msg = str(data.get("error") or "")[:120] if isinstance(data, dict) else ""
        low = msg.lower()
        no_route = code in NO_ROUTE_CODES or "not tradable" in low or "no route" in low or "could not find" in low
        return QuoteResult(NO_ROUTE if no_route else INVALID, http=status, code=code, detail=msg)
    if not isinstance(data, dict):
        return QuoteResult(API_ERROR, http=200, detail=err or "invalid JSON")
    if data.get("inputMint") != input_mint or data.get("outputMint") != output_mint:
        return QuoteResult(INVALID, http=200, detail="quote mints do not match the request")
    if str(data.get("inAmount", amount_raw)) != str(amount_raw):
        return QuoteResult(INVALID, http=200, detail=f"inAmount {data.get('inAmount')} != {amount_raw}")
    try:
        out = int(data.get("outAmount") or 0)
    except (TypeError, ValueError):
        out = 0
    if out <= 0:
        return QuoteResult(NO_ROUTE, http=200, detail="outAmount 0")
    return QuoteResult(OK, quote=data, http=200)


def route_label(quote: dict) -> str:
    labels = [((r.get("swapInfo") or {}).get("label") or "?") for r in quote.get("routePlan") or []]
    return "Jupiter: " + " → ".join(labels[:3]) if labels else "Jupiter"


def price_impact(quote: dict) -> float | None:
    """Fraction (0.012 = 1.2 %)."""
    try:
        return float(quote.get("priceImpactPct"))
    except (TypeError, ValueError):
        return None
