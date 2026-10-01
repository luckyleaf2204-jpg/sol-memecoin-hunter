"""EXIT ENGINE — evaluated for every open position on each bot tick, from validated data only.

Order (first match wins, full exit unless noted):
  identity conflict / holder anomaly · risk spike (Risk > 60 or a rug flag) · liquidity collapse (< 60 % of entry
  liquidity or state SHOCK) · whale dump (whale state DISTRIBUTION) · stop loss · TP2 · TP1 (partial, then the
  stop moves to break-even and the trailing stop is armed) · trailing stop · momentum deterioration (lifecycle
  DISTRIBUTION / DECLINING) · volume collapse (5m volume < 20 % of entry volume while below entry) · max hold.
X Alpha "signal lost" has no data source (NOT AVAILABLE) and is therefore never used.
Without a validated current price nothing is sold on a guess: the position is marked STALE.
"""
from __future__ import annotations

from core.models import TokenState
from trading.config import TradingConfig
from trading.models import Position

HARD = ("identity_conflict", "holder_anomaly", "risk_spike", "liquidity_collapse", "whale_dump", "stop_loss")


def exit_signal(p: Position, st: TokenState | None, price: float | None, cfg: TradingConfig, now: float,
                entry_liq: float | None = None, entry_vol: float | None = None) -> tuple[float, str] | None:
    """(fraction of the remaining tokens to sell, reason) or None."""
    if st is not None:
        if st.identity.status == "CONFLICT":
            return 1.0, "identity_conflict"
        if st.holder_status == "invalid":
            return 1.0, "holder_anomaly"
        rk = st.risk
        if rk and (rk.score > 60 or any(f.category == "rug" for f in rk.factors)):
            return 1.0, "risk_spike"
        m = st.market
        liq = m.liquidity_usd if m else None
        shock = bool(st.liquidity_intel and st.liquidity_intel.state == "SHOCK")
        if shock or (entry_liq and liq is not None and liq < 0.6 * entry_liq):
            return 1.0, "liquidity_collapse"
        if st.whale_intel and st.whale_intel.state == "DISTRIBUTION":
            return 1.0, "whale_dump"
    if price is None:
        return None
    if price <= p.stop_price:
        return 1.0, "stop_loss" if not p.tp1_done else "break_even_stop"
    if price >= p.tp2_price:
        return 1.0, "take_profit_2"
    if not p.tp1_done and price >= p.tp1_price:
        return cfg.tp1_sell_frac, "take_profit_1"
    if p.tp1_done and price <= p.high_price * (1 - p.trailing_pct / 100):
        return 1.0, "trailing_stop"
    if st is not None:
        if st.lifecycle in ("DISTRIBUTION", "DECLINING"):
            return 1.0, "momentum_deterioration"
        vol = st.market.vol_5m if st.market else None
        if entry_vol and vol is not None and vol < 0.2 * entry_vol and price < p.entry_price:
            return 1.0, "volume_collapse"
    if (now - p.opened_at) / 60 >= cfg.max_hold_min:
        return 1.0, "max_hold_time"
    return None
