"""PRICE PROVENANCE + same-source SHADOW accounting (V1.2). Read-only with respect to trading.

Production price map (audited 2026-10-02):
  ENTRY            Jupiter BUY quote: price = USD spent / (outAmount / 10^decimals), then x (1 + simulated latency slip)
  MARKS            DexScreener priceUsd of the token's current pair (bot._price: up to 4 x max_data_age_s old) -> stop,
                   TP1 / TP2 / trailing triggers, MFE / MAE, unrealized P&L.
                   V1.2 FIX: only prints FETCHED AT OR AFTER the fill count (bot._post_entry). Before, the print that
                   was already 5-25 s old at the decision was compared with the Jupiter fill on the next tick, which
                   fired fake stop losses / take profits (BLOCUS, >_, Papu, bwam) and polluted MFE / MAE.
                   Until the first post-entry print the position is valued at its own fill (book.open).
  HARD EXITS       (stop_loss, risk_spike, liquidity_collapse, whale_dump, holder_anomaly, identity_conflict) are filled
                   on the liquidity MODEL at the DexScreener mark
  OTHER EXITS      (take profits, trailing, momentum, volume, time) are filled on a Jupiter SELL quote
So one trade can mix two price sources. This module records every price observation with its provenance and runs
a COMMON-SOURCE shadow (Jupiter buy quote in, Jupiter sell quotes as marks / out) without touching production.

Every observation: price, source, ts, slot (None: not available from these APIs), pair, base mint, quote mint,
base / quote amounts, age_ms (now - data timestamp), latency_ms (request -> response), confidence.
DexScreener exposes neither a data timestamp nor a slot: its "timestamp" is OUR fetch time, so age_ms is a LOWER
bound of the true staleness (DexScreener's own indexing lag is invisible). Jupiter quotes: slot = contextSlot when
the response carries it, else None (never guessed).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

WSOL = "So11111111111111111111111111111111111111112"
STALE_MS = 5_000                 # a market print older than this is considered stale for an entry decision


@dataclass
class PriceObs:
    price: float | None
    source: str
    ts: float
    slot: int | None = None
    pair: str = ""
    base_mint: str = ""
    quote_mint: str = ""
    base_amount: float | None = None
    quote_amount: float | None = None
    age_ms: float | None = None
    latency_ms: float | None = None
    confidence: str = "UNKNOWN"
    note: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


def market_obs(st, now: float, label: str = "dexscreener") -> PriceObs:
    m = st.market
    if m is None or not m.price_usd:
        return PriceObs(None, label, now, base_mint=st.mint, confidence="UNKNOWN", note="no market price")
    age = 1000 * (now - m.updated_at) if m.updated_at else None
    conf = "HIGH" if age is not None and age <= STALE_MS else ("MEDIUM" if age is not None and age <= 30_000 else "LOW")
    return PriceObs(m.price_usd, f"{label}:{m.dex_id or '?'}", now, None, m.pair_address or "", st.mint,
                    m.quote_address or "", None, None, age, None, conf)


def amm_keys(quote: dict) -> list[str]:
    return [((r.get("swapInfo") or {}).get("ammKey") or "") for r in (quote or {}).get("routePlan") or []]


def _slot(quote: dict) -> int | None:
    try:
        return int(quote["contextSlot"])
    except (KeyError, TypeError, ValueError):
        return None


def buy_quote_obs(quote: dict, usd: float, decimals: int, mint: str, t_req: float, t_resp: float) -> PriceObs:
    """Implied USD price of a Jupiter BUY quote (SOL -> token): USD in / tokens out."""
    try:
        out = int(quote["outAmount"]) / 10 ** decimals
    except (KeyError, TypeError, ValueError):
        return PriceObs(None, "jupiter_buy_quote", t_resp, base_mint=mint, note="unparseable quote")
    try:
        sol_in = int(quote["inAmount"]) / 1e9
    except (KeyError, TypeError, ValueError):
        sol_in = None                    # optional: the price only needs USD in and tokens out
    keys = amm_keys(quote)
    return PriceObs(usd / out if out else None, "jupiter_buy_quote", t_resp, _slot(quote), keys[0] if keys else "", mint, WSOL,
                    out, sol_in, 0.0, 1000 * (t_resp - t_req), "HIGH" if out else "UNKNOWN",
                    f"{len(keys)} hop(s)")


def sell_quote_obs(quote: dict, tokens: float, sol_price: float | None, mint: str, t_req: float, t_resp: float) -> PriceObs:
    """Implied, EXECUTABLE USD price of a Jupiter SELL quote (token -> SOL) for the position size."""
    try:
        sol_out = int(quote["outAmount"]) / 1e9
    except (KeyError, TypeError, ValueError):
        return PriceObs(None, "jupiter_sell_quote", t_resp, base_mint=mint, note="unparseable quote")
    keys = amm_keys(quote)
    price = sol_out * sol_price / tokens if tokens and sol_price else None
    return PriceObs(price, "jupiter_sell_quote", t_resp, _slot(quote), keys[0] if keys else "", mint, WSOL, tokens, sol_out,
                    0.0, 1000 * (t_resp - t_req), "HIGH" if price else "UNKNOWN", f"{len(keys)} hop(s)")


def pct(a: float | None, b: float | None) -> float | None:
    return None if not a or not b else round(100 * (a / b - 1), 3)


def decimals_suspect(quote_price: float | None, market_price: float | None) -> bool:
    """A factor >= 20 between two prices of the same token is not a market move: decimals / unit error."""
    if not quote_price or not market_price:
        return False
    r = quote_price / market_price
    return r >= 20 or r <= 1 / 20


def classify_discrepancy(market: dict, quote: dict, later_markets: list[dict], before_markets: list[dict]) -> dict:
    """Why do the market print and the Jupiter quote differ at the decision? Uses only the observations given
    (later prints are used to VALIDATE an explanation, never to change the past decision).
      CONSISTENT          |discrepancy| < 3 %
      DECIMALS_SUSPECT    factor >= 20
      PAIR_MISMATCH       quote route pool != market pair
      STALE_MARKET        market print >= 5 s old and a later print moved most of the way to the quote
      STALE_MARKET_PROBABLE market print stale and the recent trend (before) points toward the quote
      UNEXPLAINED         none of the above"""
    mp, qp = market.get("price"), quote.get("price")
    out = {"discrepancy_pct": pct(qp, mp), "discrepancy_bps": None if pct(qp, mp) is None else round(100 * pct(qp, mp)),
           "market_age_ms": market.get("age_ms"), "class": "UNKNOWN", "evidence": ""}
    if mp is None or qp is None:
        return out
    if decimals_suspect(qp, mp):
        out["class"] = "DECIMALS_SUSPECT"
        return out
    if market.get("pair") and quote.get("pair") and market["pair"] != quote["pair"]:
        out["class"] = "PAIR_MISMATCH"
        out["evidence"] = f"market pair {market['pair'][:8]} vs route pool {quote['pair'][:8]}"
        return out
    d = abs(qp / mp - 1)
    if d < 0.03:
        out["class"] = "CONSISTENT"
        return out
    stale = (market.get("age_ms") or 0) >= STALE_MS
    if stale:
        for lm in later_markets:
            lp = lm.get("price")
            if lp and lp != mp and abs(lp - qp) <= 0.5 * abs(mp - qp):
                out["class"] = "STALE_MARKET"
                out["evidence"] = (f"market print {market.get('age_ms', 0) / 1000:.1f}s old; next print "
                                   f"{lp:.4g} after {lm['ts'] - market['ts']:.0f}s is within {100 * abs(lp / qp - 1):.1f}% of the quote")
                return out
        bm = [b["price"] for b in before_markets if b.get("price")]
        if bm and ((qp > mp and mp > bm[0]) or (qp < mp and mp < bm[0])):
            contra = [lm for lm in later_markets if lm.get("price") and lm["price"] != mp
                      and abs(lm["price"] - qp) > abs(mp - qp)]
            out["class"] = "STALE_MARKET_PROBABLE" if not contra else "UNEXPLAINED"
            out["evidence"] = ("trend before the decision points toward the quote" +
                               ("; but the next print moved away from it" if contra else ""))
            return out
    out["class"] = "UNEXPLAINED"
    return out


@dataclass
class CommonSourceExit:
    """EXIT_MODEL_COMMON_SOURCE (shadow): the production price rules (SL, TP1 partial, TP2, trailing, max hold)
    applied to ONE source — Jupiter sell-quote implied prices — from the Jupiter fill entry. Non-price production exits
    (risk, whale, liquidity, holder anomaly, identity) are mirrored at the same time but valued on the sell quote."""
    entry: float
    opened_at: float
    sl_pct: float
    tp1_pct: float
    tp1_frac: float
    tp2_pct: float
    trailing_pct: float
    max_hold_s: float
    high: float = 0.0
    low: float = 0.0
    tp1_done: bool = False
    realized_frac: float = 0.0
    realized_value: float = 0.0          # sum(frac x price) of partial exits
    closed_at: float | None = None
    exit_reason: str = ""
    exit_price: float | None = None
    events: list = field(default_factory=list)

    def __post_init__(self):
        self.high = self.low = self.entry

    def on_price(self, price: float, now: float) -> None:
        if self.closed_at is not None or not price:
            return
        self.high, self.low = max(self.high, price), min(self.low, price)
        # same order as trading/exits.py: stop (break-even after TP1) -> TP2 -> TP1 -> trailing -> max hold
        stop = self.entry if self.tp1_done else self.entry * (1 - self.sl_pct / 100)
        if price <= stop:
            self._close(price, now, "break_even_stop" if self.tp1_done else "stop_loss")
        elif price >= self.entry * (1 + self.tp2_pct / 100):
            self._close(price, now, "take_profit_2")
        elif not self.tp1_done and price >= self.entry * (1 + self.tp1_pct / 100):
            self.tp1_done = True
            self.realized_frac += self.tp1_frac
            self.realized_value += self.tp1_frac * price
            self.events.append((now, "take_profit_1", price))
        elif self.tp1_done and price <= self.high * (1 - self.trailing_pct / 100):
            self._close(price, now, "trailing_stop")
        elif now - self.opened_at >= self.max_hold_s:
            self._close(price, now, "max_hold")

    def force_close(self, price: float | None, now: float, reason: str) -> None:
        if self.closed_at is None and price:
            self._close(price, now, reason)

    def _close(self, price: float, now: float, reason: str) -> None:
        rest = 1 - self.realized_frac
        self.realized_value += rest * price
        self.realized_frac = 1.0
        self.closed_at, self.exit_reason, self.exit_price = now, reason, price
        self.events.append((now, reason, price))

    def summary(self) -> dict:
        pnl = None
        if self.closed_at is not None and self.entry:
            pnl = round(100 * (self.realized_value / self.entry - 1), 3)
        return {"entry": self.entry, "exit_price": self.exit_price, "exit_reason": self.exit_reason,
                "closed_after_s": None if self.closed_at is None else round(self.closed_at - self.opened_at, 1),
                "pnl_pct": pnl, "mfe_pct": round(100 * (self.high / self.entry - 1), 3),
                "mae_pct": round(100 * (self.low / self.entry - 1), 3), "events": self.events[-10:]}
