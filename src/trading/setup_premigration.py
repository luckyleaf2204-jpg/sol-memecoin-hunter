"""PRE-MIGRATION setup engine — bonding curve close to graduation. Goal: catch accumulation before migration.

Components (0-1, None = UNKNOWN), all from data with timestamp <= decision time:
  proximity        how close the curve is to completion (reported progress)
  progression      real SOL added to the curve over the last 5 min (same curve pair history)
  accumulation     buy pressure + transaction acceleration + volume acceleration
  independence     synchronized / creator-funded early buying and first-buyer supply share (research.onchain);
                   UNKNOWN when not researched — never assumed independent
  smart_money      no data source in this build -> UNKNOWN (one KOL is never counted as many wallets)
  depth            liquidity / depth expansion (AMM-equivalent depth of the curve)
  holder           holder growth and concentration
  dev_safety       dev not selling / not holding a large share
  whale            whale state (ACCUMULATION good, DISTRIBUTION bad)
"""
from __future__ import annotations

from trading.setup_common import SetupScore, clamp, combine, ramp

SETUP = "PRE_MIGRATION"
WEIGHTS = {"proximity": 0.15, "progression": 0.15, "accumulation": 0.15, "independence": 0.15, "smart_money": 0.10,
           "depth": 0.10, "holder": 0.10, "dev_safety": 0.05, "whale": 0.05}


def progression_component(points: list, now: float) -> float | None:
    """points: [(ts, liq_usd)] of the CURRENT curve pair, ts <= now. Real-SOL growth over ~5 min."""
    pts = [(t, v) for t, v in points if t <= now and v is not None]
    if len(pts) < 2:
        return None
    t1, v1 = pts[-1]
    old = [(t, v) for t, v in pts if t1 - t >= 240]
    if not old:
        return None
    t0, v0 = old[-1]
    if not v0:
        return None
    return ramp(100 * (v1 / v0 - 1), 0, 40)


def independence_component(oc: dict | None) -> float | None:
    if not oc or oc.get("onchain_status") != "ok":
        return None
    bad = 0.0
    if (oc.get("sync_buy_slots") or 0) >= 1:
        bad += 0.3
    if (oc.get("creator_funded_buyers") or 0) >= 1:
        bad += 0.4
    if oc.get("funder_bought"):
        bad += 0.3
    if (oc.get("early_buyer_supply_pct") or 0) >= 30:
        bad += 0.2
    return clamp(1 - bad)


def score(f: dict, oc: dict | None, curve_points: list, now: float) -> SetupScore:
    p = f.get("curve_progress")
    acc = []
    if f.get("buy_share_5m") is not None and (f.get("tx_5m") or 0) >= 5:
        acc.append(ramp(f["buy_share_5m"], 0.5, 0.75))
    for k in ("txn_accel", "vol_accel"):
        if f.get(k) is not None:
            acc.append(ramp(f[k], 1, 3))
    holder = None
    if f.get("holders_ok"):
        g = f.get("holder_growth_5m_pct")
        t10 = f.get("top10_pct")
        holder = clamp(0.5 * (ramp(g, 0, 20) if g is not None else 0.4) + 0.5 * (1 - ramp(t10, 25, 60) if t10 is not None else 0.5))
    dev = None
    if f.get("dev_verified"):
        sold = f.get("dev_status") in ("SOLD ALL", "MAJOR SELL") or (f.get("dev_sold_pct") or 0) >= 50
        dev = 0.0 if sold else 1 - ramp(f.get("dev_pct") or 0, 3, 15)
    ws = f.get("whale_state")
    whale = None if ws in (None, "UNKNOWN", "") else {"ACCUMULATION": 1.0, "NEUTRAL": 0.5, "DISTRIBUTION": 0.0}.get(ws, 0.5)
    comps = {"proximity": None if p is None else ramp(p, 60, 98),
             "progression": progression_component(curve_points, now),
             "accumulation": sum(acc) / len(acc) if acc else None,
             "independence": independence_component(oc), "smart_money": None,
             "depth": None if f.get("amm_equivalent_usd") is None else ramp(f["amm_equivalent_usd"], 10_000, 60_000),
             "holder": holder, "dev_safety": dev, "whale": whale}
    pen = [] if f.get("holders_ok") else [("holder data missing", 0.10)]
    s = combine(SETUP, comps, WEIGHTS, pen)
    s.extra = {"smart_money_count": None, "independent_smart_money_count": None, "unknown_independence": True,
               "synchronized_buy_count": (oc or {}).get("sync_buy_slots"), "funding_source_count": None,
               "migration_confirmed": False}
    return s
