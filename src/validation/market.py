"""Market-data validation — runs BEFORE any scoring.

Every field that is missing, non-numeric, <= 0 or internally inconsistent is set to None and
recorded as an Issue (i18n key + params). A "critical" issue makes the token's data quality
INVALID, which removes it from every score that ranks tokens. Nothing is estimated or zero-filled.

critical: pair_missing, price_bad, mc_bad, mc_implausible (< $1,000), mc_inconsistent (MC vs price×supply > ×3),
          volume_bad (5m/1h missing or <= 0), txns_missing, txns_inconsistent (volume with 0 txns),
          liq_bad (AMM liquidity missing/<= 0/< $100), liq_not_reported,
          curve_unavailable, curve_stale (> 180 s), curve_inconsistent (virtual − real ≠ 30 SOL ±1),
          sol_price_missing, curve_too_small (< $100), market_stale (> 120 s at ingest)
warning:  fdv_bad, txns_1h_missing
"""
from __future__ import annotations

import math
import time

from core.models import Issue, MarketData, TokenInfo

MIN_PLAUSIBLE_MC = 1_000          # a Pump.fun curve starts at ~28 SOL MC (≈ $3K); below $1K = malformed
MIN_PLAUSIBLE_LIQ = 100           # USD; smaller = broken/empty pool or malformed field
MC_PRICE_TOLERANCE = 3.0          # MC must be within ×3 of price × supply
PUMP_INITIAL_VIRTUAL_SOL = 30.0   # standard Pump.fun curve: virtual SOL = 30 + real SOL
CURVE_CONSISTENCY_SOL = 1.0
CURVE_MAX_AGE_S = 180             # Pump.fun curve reserve older than this is not trusted


def _bad(v) -> bool:
    return v is None or not isinstance(v, (int, float)) or math.isnan(v) or math.isinf(v) or v <= 0


def _raw(v) -> str:
    return repr(v)


def validate_market(m: MarketData, info: TokenInfo, sol_price: float | None,
                    now: float | None = None) -> list[Issue]:
    """Clean `m` in place and return the issues found."""
    now = now or time.time()
    issues: list[Issue] = []

    def crit(fld, key, **p):
        issues.append(Issue("critical", fld, key, p))

    def warn(fld, key, **p):
        issues.append(Issue("warning", fld, key, p))

    if not m.pair_address or not m.dex_id:
        crit("pair", "pair_missing")

    if _bad(m.price_usd):
        crit("price", "price_bad", raw=_raw(m.price_usd))
        m.price_usd = None
    if _bad(m.market_cap):
        crit("market_cap", "mc_bad", raw=_raw(m.market_cap))
        m.market_cap = None
    elif m.market_cap < MIN_PLAUSIBLE_MC:
        crit("market_cap", "mc_implausible", value=round(m.market_cap, 2), min=MIN_PLAUSIBLE_MC)
        m.market_cap = None
    if _bad(m.fdv):
        if m.fdv is not None:
            warn("fdv", "fdv_bad", raw=_raw(m.fdv))
        m.fdv = None
    supply = info.total_supply
    if m.price_usd and m.market_cap and supply:
        ratio = m.market_cap / (m.price_usd * supply)
        if not (1 / MC_PRICE_TOLERANCE <= ratio <= MC_PRICE_TOLERANCE):
            crit("market_cap", "mc_inconsistent", mc=round(m.market_cap), implied=round(m.price_usd * supply),
                 ratio=round(ratio, 2))
            m.market_cap = None

    for fld in ("vol_5m", "vol_1h"):
        v = getattr(m, fld)
        if _bad(v):
            crit(fld, "volume_bad", field=fld, raw=_raw(v))
            setattr(m, fld, None)
    for fld in ("vol_6h", "vol_24h"):
        if _bad(getattr(m, fld)):
            setattr(m, fld, None)
    if m.buys_5m is None or m.sells_5m is None or m.buys_5m < 0 or m.sells_5m < 0:
        crit("txns_5m", "txns_missing")
        m.buys_5m = m.sells_5m = None
    elif m.buys_5m + m.sells_5m == 0 and m.vol_5m:
        crit("txns_5m", "txns_inconsistent", volume=round(m.vol_5m))
        m.buys_5m = m.sells_5m = None
    if m.buys_1h is None or m.sells_1h is None:
        warn("txns_1h", "txns_1h_missing")
        m.buys_1h = m.sells_1h = None

    if m.liquidity_source == "dexscreener_amm":
        if _bad(m.liquidity_usd) or m.liquidity_usd < MIN_PLAUSIBLE_LIQ:
            crit("liquidity", "liq_bad", raw=_raw(m.liquidity_usd))
            m.liquidity_usd, m.liquidity_source = None, ""
    else:
        m.liquidity_usd, m.liquidity_source = None, ""
        if m.is_curve:
            usd, problem = curve_reserve_usd(info, sol_price, now)
            if problem:
                crit("liquidity", problem[0], **problem[1])
            else:
                m.liquidity_usd, m.liquidity_source = usd, "pumpfun_curve"
        else:
            crit("liquidity", "liq_not_reported")

    if now - m.updated_at > 120:
        crit("market", "market_stale", age=round(now - m.updated_at))
    return issues


def curve_reserve_usd(info: TokenInfo, sol_price: float | None, now: float):
    """SOL actually held by the bonding curve (reported by Pump.fun) × SOL price — reported, not estimated.
    Returns (usd, None) if every check passes, else (None, (issue_key, params))."""
    real, virt = info.real_sol_reserves, info.virtual_sol_reserves
    if info.pump_updated_at is None or real is None:
        return None, ("curve_unavailable", {})
    if now - info.pump_updated_at > CURVE_MAX_AGE_S:
        return None, ("curve_stale", {"age": round(now - info.pump_updated_at)})
    if virt is None or abs((virt - real) - PUMP_INITIAL_VIRTUAL_SOL) > CURVE_CONSISTENCY_SOL:
        return None, ("curve_inconsistent", {"virtual": virt, "real": real})
    if _bad(sol_price):
        return None, ("sol_price_missing", {})
    usd = real * sol_price
    if usd < MIN_PLAUSIBLE_LIQ:
        return None, ("curve_too_small", {"value": round(usd, 2), "min": MIN_PLAUSIBLE_LIQ})
    return usd, None
