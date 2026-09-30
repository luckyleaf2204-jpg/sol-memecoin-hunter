"""Token lifecycle stage — ordered rules, first match wins.

  UNKNOWN         creation time unknown, or no validated market data
  NEW             age < 10 min
  DECLINING       drawdown from ATH ≥ 60 % and volume pace < 0.8× the 1h average
  DISTRIBUTION    age ≥ 30 min and (whale DISTRIBUTION, or B/S 5m < 0.8 with ≥ 20 txns and 1h price ≤ +5 %)
  EARLY_MOMENTUM  early-signal engine says TRUE (leaving a low-activity baseline — checked before BREAKOUT
                  because it is the earlier stage of the progression)
  BREAKOUT        price ≥ 1.10 × highest price of the 2–30 min before, with volume pace ≥ 2× (needs history)
  MOMENTUM        volume pace ≥ 1.5× and 1h price change ≥ +20 %
  EARLY           age < 60 min
  MATURE          otherwise
"""
from __future__ import annotations

from core.models import EarlySignal, TokenState, WhaleIntel

STAGES = ("NEW", "EARLY", "EARLY_MOMENTUM", "MOMENTUM", "BREAKOUT", "MATURE", "DISTRIBUTION", "DECLINING", "UNKNOWN")


def classify_lifecycle(st: TokenState, early: EarlySignal | None, whale: WhaleIntel | None,
                       drawdown_pct: float | None, breakout: bool | None) -> str:
    age, m = st.age_minutes, st.market
    if age is None or m is None or m.market_cap is None:
        return "UNKNOWN"
    if age < 10:
        return "NEW"
    va = m.vol_accel
    if drawdown_pct is not None and drawdown_pct >= 60 and va is not None and va < 0.8:
        return "DECLINING"
    if age >= 30:
        if whale and whale.state == "DISTRIBUTION":
            return "DISTRIBUTION"
        bs, tx = m.buy_sell_ratio_5m, m.txns_5m
        if bs is not None and bs < 0.8 and (tx or 0) >= 20 and m.price_change_1h is not None and m.price_change_1h <= 5:
            return "DISTRIBUTION"
    if early and early.is_early:
        return "EARLY_MOMENTUM"
    if breakout:
        return "BREAKOUT"
    if va is not None and va >= 1.5 and m.price_change_1h is not None and m.price_change_1h >= 20:
        return "MOMENTUM"
    if age < 60:
        return "EARLY"
    return "MATURE"
