"""PAPER execution model — no transaction is ever built, signed or sent.

Every fill is simulated on the token's current VALIDATED market data:
  route         Pump.fun bonding curve (dex "pumpfun") · PumpSwap / Raydium / other AMM "via Jupiter"
  price impact  constant-product pool, quote reserve = liquidity / 2:
                  buy  avg price = P × (1 + x / R)          sell avg price = P / (1 + v / R)
  slippage      adverse, seeded RNG: 0–0.5 % base + volatility term (|5m price change| × 5 %, max 2 %)
  fees          route fee (curve 1.25 %, PumpSwap 0.30 %, Raydium 0.25 %, other 0.30 %) +
                network/priority fee 0.00011 SOL (USD via the scanner's SOL price, else $0.02)
  latency       400–1500 ms (seeded)
  failure       seeded: 3 % (+3 % on curve, + price impact); a failed tx still pays the network fee;
                impact + slippage above the configured tolerance -> FAILED (slippage exceeded), fee paid
"""
from __future__ import annotations

import random
import time

from core.models import TokenState
from trading.models import Execution

ROUTE_FEE = {"pumpfun": 0.0125, "pumpswap": 0.0030, "raydium": 0.0025}
DEFAULT_FEE = 0.0030
NETWORK_FEE_SOL = 0.00011
FALLBACK_NETWORK_USD = 0.02


def route_of(st: TokenState) -> tuple[str, float]:
    m = st.market
    dex = (m.dex_id if m else "") or "unknown"
    if m and m.is_curve:
        return "Pump.fun bonding curve", ROUTE_FEE["pumpfun"]
    name = {"pumpswap": "PumpSwap AMM", "raydium": "Raydium"}.get(dex, dex)
    return f"{name} via Jupiter", ROUTE_FEE.get(dex, DEFAULT_FEE)


def price_impact(usd: float, liquidity_usd: float | None) -> float | None:
    if not liquidity_usd or liquidity_usd <= 0:
        return None
    return usd / (liquidity_usd / 2)


class LiveExecutorUnavailable(RuntimeError):
    pass


class ExecutionInterface:
    """What every executor provides. The bot's decision logic is identical for PAPER / CONFIRM / AUTO; only the
    executor differs. This build ships PaperExecutor only: a live executor (transaction building + signing with a
    dedicated wallet) is NOT included — `live_available()` is False and AUTO stays locked."""
    name = "interface"
    live = False

    def buy(self, st, usd, sol_price, now=None):                      # model fill
        raise NotImplementedError

    def buy_from_quote(self, st, usd, quote, sol_price, now=None):    # fill on a Jupiter route
        raise NotImplementedError

    def sell(self, st, tokens, price, sol_price, reason, now=None, force=False):
        raise NotImplementedError

    def sell_from_quote(self, st, tokens, quote, sol_price, reason, now=None):
        raise NotImplementedError


def live_available() -> bool:
    return False                      # no live executor in this build (signing is not allowed in this environment)


class PaperExecutor(ExecutionInterface):
    name = "PAPER"
    def __init__(self, seed: int = 7, max_slippage_pct: float = 3.0):
        self.rng = random.Random(seed)
        self.max_slippage = max_slippage_pct / 100

    def network_fee(self, sol_price: float | None) -> float:
        return NETWORK_FEE_SOL * sol_price if sol_price else FALLBACK_NETWORK_USD

    def estimate(self, st: TokenState, usd: float) -> dict:
        """Pre-trade estimate used by the Risk Engine (no randomness)."""
        m = st.market
        imp = price_impact(usd, m.liquidity_usd if m else None)
        route, fee = route_of(st)
        return {"route": route, "fee_rate": fee, "impact": imp}

    def _slip(self, st: TokenState) -> float:
        pc5 = abs(st.market.price_change_5m) if st.market and st.market.price_change_5m is not None else 0.0
        return self.rng.uniform(0, 0.005) + min(0.02, pc5 / 100 * 0.05)

    def _fail(self, st: TokenState, impact: float) -> bool:
        p = 0.03 + (0.03 if st.market and st.market.is_curve else 0.0) + min(0.2, impact)
        return self.rng.random() < p

    def buy(self, st: TokenState, usd: float, sol_price: float | None, now: float | None = None) -> Execution:
        now = now or time.time()
        m = st.market
        route, fee_rate = route_of(st)
        net_fee = self.network_fee(sol_price)
        latency = self.rng.randint(400, 1500)
        ref = m.price_usd if m else None
        imp = price_impact(usd, m.liquidity_usd if m else None)
        base = dict(ts=now, mint=st.mint, symbol=st.info.symbol, side="BUY", route=route, ref_price=ref,
                    latency_ms=latency)
        if ref is None or ref <= 0 or imp is None:
            return Execution(**base, status="REJECTED", reason="no validated price / liquidity")
        slip = self._slip(st)
        if imp + slip > self.max_slippage:
            return Execution(**base, status="FAILED", usd_in=0.0, network_fee_usd=net_fee, price_impact_pct=100 * imp,
                             slippage_pct=100 * slip, reason=f"slippage tolerance exceeded ({100 * (imp + slip):.2f}%)")
        if self._fail(st, imp):
            return Execution(**base, status="FAILED", network_fee_usd=net_fee, price_impact_pct=100 * imp,
                             slippage_pct=100 * slip, reason="transaction failed (simulated: dropped / expired blockhash)")
        fee = usd * fee_rate
        fill = ref * (1 + imp) * (1 + slip)
        tokens = (usd - fee) / fill
        return Execution(**base, status="FILLED", usd_in=usd, tokens=tokens, fill_price=fill,
                         price_impact_pct=100 * imp, slippage_pct=100 * slip, fee_usd=fee, network_fee_usd=net_fee)

    def buy_from_quote(self, st: TokenState, usd: float, quote: dict, sol_price: float | None,
                       now: float | None = None) -> Execution:
        """PAPER fill on a real Jupiter route: output amount and price impact come from Jupiter for this exact size
        (pool fees are inside Jupiter's output); latency slippage, failures and network fee are still simulated."""
        from trading.jupiter import price_impact, route_label
        now = now or time.time()
        m = st.market
        net_fee = self.network_fee(sol_price)
        latency = self.rng.randint(400, 1500)
        ref = m.price_usd if m else None
        imp = price_impact(quote)
        base = dict(ts=now, mint=st.mint, symbol=st.info.symbol, side="BUY", route=route_label(quote), ref_price=ref,
                    latency_ms=latency)
        try:
            out_tokens = int(quote["outAmount"]) / 10 ** (st.info.decimals or 6)
        except (KeyError, TypeError, ValueError):
            out_tokens = 0.0
        if ref is None or ref <= 0 or imp is None or out_tokens <= 0:
            return Execution(**base, status="REJECTED", reason="Jupiter quote unusable")
        slip = self._slip(st)
        if imp + slip > self.max_slippage:
            return Execution(**base, status="FAILED", network_fee_usd=net_fee, price_impact_pct=100 * imp,
                             slippage_pct=100 * slip, reason=f"slippage tolerance exceeded ({100 * (imp + slip):.2f}%)")
        if self._fail(st, imp):
            return Execution(**base, status="FAILED", network_fee_usd=net_fee, price_impact_pct=100 * imp,
                             slippage_pct=100 * slip, reason="transaction failed (simulated: dropped / expired blockhash)")
        tokens = out_tokens / (1 + slip)
        fill = usd / tokens
        return Execution(**base, status="FILLED", usd_in=usd, tokens=tokens, fill_price=fill, price_impact_pct=100 * imp,
                         slippage_pct=100 * slip, fee_usd=0.0, network_fee_usd=net_fee,
                         model="PAPER on a real Jupiter quote (no transaction sent)")

    def sell_from_quote(self, st: TokenState, tokens: float, quote: dict, sol_price: float | None, reason: str,
                        now: float | None = None) -> Execution:
        """PAPER SELL on a real Jupiter route (token -> SOL): output, price impact and route from Jupiter."""
        from trading.jupiter import price_impact, route_label
        now = now or time.time()
        net_fee = self.network_fee(sol_price)
        latency = self.rng.randint(400, 1500)
        ref = st.market.price_usd if st.market else None
        imp = price_impact(quote)
        base = dict(ts=now, mint=st.mint, symbol=st.info.symbol, side="SELL", route=route_label(quote), ref_price=ref,
                    latency_ms=latency, reason=reason)
        try:
            usd_out = int(quote["outAmount"]) / 1e9 * (sol_price or 0)
        except (KeyError, TypeError, ValueError):
            usd_out = 0.0
        if not ref or imp is None or usd_out <= 0 or tokens <= 0:
            return Execution(**base, status="REJECTED", reason=f"{reason}: Jupiter quote unusable")
        slip = self._slip(st)
        if self._fail(st, imp):
            return Execution(**base, status="FAILED", network_fee_usd=net_fee, price_impact_pct=100 * imp,
                             slippage_pct=100 * slip, reason=f"{reason}: transaction failed (simulated)")
        proceeds = usd_out * (1 - slip)
        return Execution(**base, status="FILLED", usd_in=proceeds, tokens=tokens, fill_price=proceeds / tokens,
                         price_impact_pct=100 * imp, slippage_pct=100 * slip, fee_usd=0.0, network_fee_usd=net_fee,
                         model="PAPER on a real Jupiter quote (no transaction sent)")

    def sell(self, st: TokenState, tokens: float, price: float, sol_price: float | None, reason: str,
             now: float | None = None, force: bool = False) -> Execution:
        """`force` (stop loss / emergency): no slippage tolerance — a real bot would accept a worse fill."""
        now = now or time.time()
        m = st.market
        route, fee_rate = route_of(st)
        net_fee = self.network_fee(sol_price)
        latency = self.rng.randint(400, 1500)
        value = tokens * price
        imp = price_impact(value, m.liquidity_usd if m else None)
        if imp is None:
            imp = 0.05                                    # liquidity unknown: assume a harsh 5 % impact
        imp = imp / (1 + imp)
        slip = self._slip(st)
        base = dict(ts=now, mint=st.mint, symbol=st.info.symbol, side="SELL", route=route, ref_price=price,
                    latency_ms=latency, reason=reason, price_impact_pct=100 * imp, slippage_pct=100 * slip)
        if not force and imp + slip > self.max_slippage:
            return Execution(**base, status="FAILED", network_fee_usd=net_fee,
                             reason=f"{reason}: slippage tolerance exceeded ({100 * (imp + slip):.2f}%)")
        if self._fail(st, imp):
            return Execution(**base, status="FAILED", network_fee_usd=net_fee, reason=f"{reason}: transaction failed (simulated)")
        fill = price * (1 - imp) * (1 - slip)
        gross = tokens * fill
        fee = gross * fee_rate
        return Execution(**base, status="FILLED", usd_in=gross - fee, tokens=tokens, fill_price=fill, fee_usd=fee,
                         network_fee_usd=net_fee)
