"""Market structure + momentum metrics.

Absolute levels (volume, MC) and ACCELERATION (change vs an earlier moment) are separate metrics.
Values DexScreener does not provide (1m / 15m volume, buy-vs-sell volume split) are UNKNOWN,
never estimated. Price change 1m / 15m is derived from our own validated snapshots.
"""
from __future__ import annotations

from dataclasses import dataclass

from core.models import TokenState
from history.store import TokenHistory
from intel.metrics import MetricBuilder, pct_change

DEX = "DexScreener"
HIST = "snapshots (DexScreener)"


@dataclass
class MarketIntel:
    ath_mc: float | None = None
    atl_mc: float | None = None
    drawdown_pct: float | None = None
    vol_trend_5m: float | None = None       # vol5m now / vol5m 5 min ago
    txn_trend_5m: float | None = None
    bs_delta_5m: float | None = None
    buy_share_5m: float | None = None
    buy_share_delta_pp: float | None = None
    data_break_min: float | None = None
    mc_growth_5m_pct: float | None = None
    mc_growth_prev_5m_pct: float | None = None
    mc_accel: float | None = None           # growth last 5m minus growth in the 5m before (pct points)
    price_change_1m: float | None = None
    price_change_15m: float | None = None
    breakout: bool | None = None
    recent_high_price: float | None = None


def _ratio(a, b):
    return a / b if a is not None and b else None


def analyze_market(st: TokenState, h: TokenHistory, M: MetricBuilder) -> MarketIntel:
    mi = MarketIntel()
    m, now = st.market, M.now
    stamp = st.stamps.get("market")
    ts = stamp.updated_at if stamp else None
    liq_src = DEX if m and m.liquidity_source == "dexscreener_amm" else "Pump.fun curve reserve"

    # ---------- absolute market structure (reported) ----------
    M.add("mc", m.market_cap if m else None, "usd", "market", DEX, ts)
    M.add("fdv", m.fdv if m else None, "usd", "market", DEX, ts)
    M.add("liquidity", m.liquidity_usd if m else None, "usd", "market", liq_src, ts)
    M.add("mc_liq", m.mc_liq_ratio if m else None, "mult", "market", DEX, ts, derived=True)
    M.add("price", m.price_usd if m else None, "usd", "market", DEX, ts)
    M.unknown("vol_1m", "usd", "market", "not_provided_dex")
    M.add("vol_5m", m.vol_5m if m else None, "usd", "market", DEX, ts)
    M.unknown("vol_15m", "usd", "market", "not_provided_dex")
    M.add("vol_1h", m.vol_1h if m else None, "usd", "market", DEX, ts)
    M.add("vol_6h", m.vol_6h if m else None, "usd", "market", DEX, ts)
    M.add("vol_24h", m.vol_24h if m else None, "usd", "market", DEX, ts)
    M.add("buys_5m", m.buys_5m if m else None, "int", "market", DEX, ts)
    M.add("sells_5m", m.sells_5m if m else None, "int", "market", DEX, ts)
    M.add("txns_5m", m.txns_5m if m else None, "int", "market", DEX, ts)
    M.add("buy_sell_5m", m.buy_sell_ratio_5m if m else None, "ratio", "market", DEX, ts, derived=True)
    avg_trade = _ratio(m.vol_5m, m.txns_5m) if m else None
    M.add("avg_trade_5m", avg_trade, "usd", "market", DEX, ts, derived=True, note="both_sides")
    M.unknown("avg_buy_size", "usd", "market", "no_side_split")
    M.unknown("avg_sell_size", "usd", "market", "no_side_split")
    M.add("vol_mc", _ratio(m.vol_5m, m.market_cap) if m else None, "ratio", "market", DEX, ts, derived=True)
    M.add("vol_liq", _ratio(m.vol_1h, m.liquidity_usd) if m else None, "ratio", "market", DEX, ts, derived=True)

    # ---------- ATH / ATL / drawdown ----------
    observed = h.values("mc", 10**9, now)
    pump_ath = st.info.ath_usd_mc
    cands = [x for x in (pump_ath, max(observed) if observed else None, m.market_cap if m else None) if x]
    mi.ath_mc = max(cands) if cands else None
    mi.atl_mc = min(observed) if observed else None
    if mi.ath_mc and m and m.market_cap:
        mi.drawdown_pct = 100 * (1 - m.market_cap / mi.ath_mc)
    M.add("ath_mc", mi.ath_mc, "usd", "market", "Pump.fun ATH + snapshots" if pump_ath else HIST, ts, derived=not pump_ath)
    M.add("atl_mc", mi.atl_mc, "usd", "market", HIST, ts, derived=True, note="since_tracking")
    M.add("drawdown", mi.drawdown_pct, "pct", "market", HIST, ts, derived=True)

    # ---------- momentum: reported price changes ----------
    M.add("pc_5m", m.price_change_5m if m else None, "pct", "momentum", DEX, ts)
    M.add("pc_1h", m.price_change_1h if m else None, "pct", "momentum", DEX, ts)
    M.add("pc_6h", m.price_change_6h if m else None, "pct", "momentum", DEX, ts)
    M.add("pc_24h", m.price_change_24h if m else None, "pct", "momentum", DEX, ts)
    M.add("vol_accel", m.vol_accel if m else None, "mult", "momentum", DEX, ts, derived=True)
    M.add("txn_accel", m.txn_accel if m else None, "mult", "momentum", DEX, ts, derived=True)
    M.add("buy_accel", m.buy_accel if m else None, "mult", "momentum", DEX, ts, derived=True)

    # ---------- momentum: changes over time from our snapshots ----------
    cur = h.latest()
    p1, p5, p10, p15 = h.at(60, now), h.at(300, now), h.at(600, now), h.at(900, now)
    p5v = h.at(300, now, pair=cur.pair) if cur and cur.pair else p5      # same pair for volume/txns (D5)
    if h.last_break_ts:
        mi.data_break_min = (now - h.last_break_ts) / 60
    if cur:
        mi.price_change_1m = pct_change(cur.price, p1.price) if p1 and p1 is not cur else None
        mi.price_change_15m = pct_change(cur.price, p15.price) if p15 else None
        mi.vol_trend_5m = _ratio(cur.vol_5m, p5v.vol_5m) if p5v else None
        mi.txn_trend_5m = _ratio(cur.txns_5m, p5v.txns_5m) if p5v else None
        if p5v and cur.bs is not None and p5v.bs is not None:
            mi.bs_delta_5m = cur.bs - p5v.bs
        mi.buy_share_5m = cur.buy_share
        if p5v and cur.buy_share is not None and p5v.buy_share is not None:
            mi.buy_share_delta_pp = 100 * (cur.buy_share - p5v.buy_share)
        mi.mc_growth_5m_pct = pct_change(cur.mc, p5.mc) if p5 else None
        mi.mc_growth_prev_5m_pct = pct_change(p5.mc, p10.mc) if p5 and p10 else None
        if mi.mc_growth_5m_pct is not None and mi.mc_growth_prev_5m_pct is not None:
            mi.mc_accel = mi.mc_growth_5m_pct - mi.mc_growth_prev_5m_pct
        prior = [p.price for p in h.window(1800, 120, now) if p.price]   # price is token-level
        if prior and cur.price and h.span_s >= 600:
            mi.recent_high_price = max(prior)
            mi.breakout = bool(cur.price >= 1.10 * mi.recent_high_price and (m.vol_accel or 0) >= 2)
    no_hist = "needs_history"
    M.add("pc_1m", mi.price_change_1m, "pct", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("pc_15m", mi.price_change_15m, "pct", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("vol_trend_5m", mi.vol_trend_5m, "mult", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("txn_trend_5m", mi.txn_trend_5m, "mult", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("bs_delta_5m", mi.bs_delta_5m, "ratio", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("buy_share_5m", 100 * mi.buy_share_5m if mi.buy_share_5m is not None else None, "pct", "momentum", DEX, ts,
          derived=True, note="needs_market")
    M.add("buy_share_delta", mi.buy_share_delta_pp, "ratio", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("data_break", mi.data_break_min, "min", "momentum", "DexScreener pair history", ts, derived=True,
          note="no_data_break")
    M.add("mc_growth_5m", mi.mc_growth_5m_pct, "pct", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("mc_accel", mi.mc_accel, "pct", "momentum", HIST, ts, derived=True, note=no_hist)
    M.add("breakout", mi.breakout, "bool", "momentum", HIST, ts, derived=True, note=no_hist)
    return mi
