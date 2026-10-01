"""NEW setup engine — token still early on the bonding curve. "Clear the mines first, then look for momentum."

Components (0-1, None = UNKNOWN):
  anti_rug     inverse of independent on-chain rug evidence: creation-slot bundle proxy, synchronized buys,
               creator-funded early buyers, funder buying, first buyers' supply share, dev sells / token moves
               (research.onchain, data <= decision time). UNKNOWN until the token was researched.
  creator      dev share of supply, dev selling, dev launch history, dev snipe at launch
  holder       holder count / growth with top1 / top10 / creator+top10 concentration and suspicious-holder pattern
  smart_money  NO data source in this build -> always UNKNOWN (independence never assumed)
  curve        curve progression sweet spot, buy pressure, transaction / volume acceleration, real/virtual depth
  momentum     price acceleration (SECONDARY: lowest weight)
GMGN-style proxies (rug / forced-trader / mouse ratios) are logged as UNKNOWN: no such source exists here.
"""
from __future__ import annotations

from trading.setup_common import SetupScore, clamp, combine, ramp

SETUP = "NEW"
WEIGHTS = {"anti_rug": 0.25, "creator": 0.20, "holder": 0.15, "smart_money": 0.10, "curve": 0.20, "momentum": 0.10}


def anti_rug_component(oc: dict | None) -> float | None:
    if not oc or oc.get("onchain_status") != "ok":
        return None
    bad = 0.0
    if (oc.get("create_slot_txs") or 0) >= 3:
        bad += 0.25
    if (oc.get("sync_buy_slots") or 0) >= 1:
        bad += 0.20
    if (oc.get("creator_funded_buyers") or 0) >= 1:
        bad += 0.35
    if oc.get("funder_bought"):
        bad += 0.25
    if (oc.get("early_buyer_supply_pct") or 0) >= 30:
        bad += 0.20
    if (oc.get("dev_sells") or 0) >= 1 or (oc.get("dev_token_transfers") or 0) >= 1:
        bad += 0.35
    return clamp(1 - bad)


def creator_component(f: dict) -> float | None:
    parts = []
    if f.get("dev_pct") is not None:
        parts.append(1 - ramp(f["dev_pct"], 3, 15))
    if f.get("dev_sold_pct") is not None or f.get("dev_status") is not None:
        sold = f.get("dev_status") in ("SOLD ALL", "MAJOR SELL") or (f.get("dev_sold_pct") or 0) >= 50
        parts.append(0.0 if sold else 1 - ramp(f.get("dev_sold_pct") or 0, 0, 50))
    if f.get("dev_prev_dead") is not None:
        parts.append(1 - ramp(f["dev_prev_dead"], 0, 5))
    if f.get("dev_snipe") is not None:
        parts.append(0.4 if f["dev_snipe"] else 1.0)
    return sum(parts) / len(parts) if parts else None


def holder_component(f: dict) -> float | None:
    if not f.get("holders_ok"):
        return None
    n = f.get("holder_count") or 0
    conc = []
    for k, lo, hi in (("top1_pct", 5, 20), ("top10_pct", 25, 60), ("top10_plus_creator_pct", 30, 70)):
        if f.get(k) is not None:
            conc.append(1 - ramp(f[k], lo, hi))
    c = sum(conc) / len(conc) if conc else 0.5
    growth = f.get("holder_growth_5m_pct")
    g = ramp(growth, 0, 25) if growth is not None else 0.4
    size = ramp(n, 10, 150)
    s = 0.5 * c + 0.3 * g + 0.2 * size
    if f.get("suspicious_holders") or f.get("holder_organic") == "SUSPICIOUS":
        s *= 0.5
    return clamp(s)


def curve_component(f: dict) -> float | None:
    parts = []
    p = f.get("curve_progress")
    if p is not None:
        parts.append(ramp(p, 3, 25) if p <= 60 else 0.7)      # moving off the floor, not yet late
    share = f.get("buy_share_5m")
    if share is not None and (f.get("tx_5m") or 0) >= 5:
        parts.append(ramp(share, 0.5, 0.75))
    for k, lo, hi in (("txn_accel", 1, 3), ("vol_accel", 1, 3)):
        if f.get(k) is not None:
            parts.append(ramp(f[k], lo, hi))
    if f.get("amm_equivalent_usd") is not None:
        parts.append(ramp(f["amm_equivalent_usd"], 5_000, 40_000))
    return sum(parts) / len(parts) if parts else None


def momentum_component(f: dict) -> float | None:
    pc = f.get("price_change_5m")
    return None if pc is None else ramp(pc, 0, 60)


def score(f: dict, oc: dict | None) -> SetupScore:
    comps = {"anti_rug": anti_rug_component(oc), "creator": creator_component(f), "holder": holder_component(f),
             "smart_money": None, "curve": curve_component(f), "momentum": momentum_component(f)}
    pen = []
    if not f.get("holders_ok"):
        pen.append(("holder data missing", 0.10))
    if not f.get("dev_verified"):
        pen.append(("dev data missing", 0.05))
    s = combine(SETUP, comps, WEIGHTS, pen)
    s.extra = {"gmgn_rug_proxy": None, "forced_trader_proxy": None, "mouse_proxy": None,
               "smart_money_count": None, "independent_smart_money_count": None, "unknown_independence": True,
               "synchronized_buy_count": (oc or {}).get("sync_buy_slots"),
               "cluster_count": (oc or {}).get("creator_funded_buyers"), "funding_source_count": None}
    return s
