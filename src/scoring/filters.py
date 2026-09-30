"""User filters. A failing filter never deletes a token — it is only flagged.
Missing (unvalidated) values FAIL the filter; they are never treated as passing.
Returns i18n keys under "filter." (e.g. "liq_below", "liq_unknown")."""
from __future__ import annotations

from core.config import Settings
from core.models import TokenState


def check_filters(st: TokenState, s: Settings) -> list[str]:
    fails: list[str] = []
    m, h = st.market, st.holders
    age = st.age_minutes
    if age is None:
        fails.append("age_unknown")
    elif age > s.max_age_hours * 60:
        fails.append("age_over")
    if not m:
        return fails + ["no_market"]

    def need(value, ok, name):
        if value is None:
            fails.append(f"{name}_unknown")
        elif not ok(value):
            fails.append(f"{name}_{'above' if name == 'top10' else 'below'}")

    if m.market_cap is None:
        fails.append("mc_unknown")
    elif m.market_cap < s.min_mc:
        fails.append("mc_below")
    elif m.market_cap > s.max_mc:
        fails.append("mc_above")
    need(m.liquidity_usd, lambda v: v >= s.min_liquidity, "liq")
    need(m.vol_5m, lambda v: v >= s.min_volume_5m, "vol")
    need(m.txns_5m, lambda v: v >= s.min_txns_5m, "txns")
    need(m.buy_sell_ratio_5m, lambda v: v >= s.min_buy_sell_ratio, "bs")
    if s.min_holders:
        need(h.holder_count if h else None, lambda v: v >= s.min_holders, "holders")
    if s.max_top10_pct < 100:  # 100 = filter disabled
        need(h.top10_pct if h else None, lambda v: v <= s.max_top10_pct, "top10")
    return fails
