"""Anti-rug RESEARCH features + SHADOW anti-rug score. Read-only: nothing here blocks a BUY, changes Risk,
Opportunity, EarlyScore, exits or any gate. Used by the research recorder (stored per snapshot) and the report.

features(st)  -> dict of values AVAILABLE AT THAT MOMENT (no look-ahead); None = data not available (UNKNOWN).
shadow_score  -> 0-100 with reasons. Weights were fixed BEFORE looking at outcomes (not fitted), so measuring its
                 recall / precision on later data is an honest out-of-sample test.

Groups (spec C): creator/dev · top holders · liquidity · bundle / early-buyer structure · market microstructure.
Bundle / slot / funding-cluster data is NOT collected by this scanner: only the proxies it does have
(dev snipe at launch, suspicious-holder pattern, dust share of new holders) are reported, the rest are UNKNOWN.
"""
from __future__ import annotations

import time

BAD_DEV = ("SOLD ALL", "MAJOR SELL")


def _risk_keys(st) -> set[str]:
    return {f.key for f in (st.risk.factors if st.risk else [])}


def features(st, now: float | None = None) -> dict:
    now = now or time.time()
    m, info = st.market, st.info
    h = st.holders if st.holder_status == "ok" and st.holders is not None else None
    hi, wi, li, d = st.holder_intel, st.whale_intel, st.liquidity_intel, st.dev
    dev_ok = bool(d and d.balance_verified)
    rk = _risk_keys(st)
    b, s = (m.buys_5m, m.sells_5m) if m else (None, None)
    tx = (b + s) if b is not None and s is not None else None
    share = (b / tx) if tx else None
    virt, real = info.virtual_sol_reserves, info.real_sol_reserves
    sol = info.sol_price
    amm_eq = (m.liquidity_usd if m and m.liquidity_source == "dexscreener_amm" else
              (2 * virt * sol if m and m.is_curve and virt and sol else None))
    created = info.created_at or (m.pair_created_at if m else None)
    f = {
        # --- creator / dev
        "dev_verified": dev_ok,
        "dev_pct": d.current_pct if dev_ok else None,
        "dev_sold_pct": d.sold_pct if dev_ok else None,
        "dev_status": d.status if dev_ok else None,
        "dev_sol_balance": d.sol_balance if dev_ok else None,
        "dev_prev_tokens": d.prev_tokens_count if d and d.history_verified else None,
        "dev_prev_dead": d.prev_dead if d and d.history_verified else None,
        "dev_prev_graduated": d.prev_graduated if d and d.history_verified else None,
        "dev_funding_sol": d.funding_sol if d else None,
        "dev_initial_sol": info.dev_initial_sol,
        "dev_initial_buy_tokens": info.dev_initial_buy,
        "creator_wallet_age": None,                 # not collected (UNKNOWN)
        "creator_transfers": None,                  # not collected (UNKNOWN)
        # --- top holders
        "holders_ok": h is not None,
        "holder_count": h.holder_count if h else None,
        "top1_pct": h.max_single_pct if h else None,
        "top5_pct": h.top5_pct if h else None,
        "top10_pct": h.top10_pct if h else None,
        "creator_pct_holders": h.creator_pct if h else None,
        "top10_plus_creator_pct": ((h.top10_pct or 0) + (h.creator_pct or 0)) if h and h.top10_pct is not None else None,
        "holders_valid": h.valid if h else None,
        "holder_growth_5m_pct": hi.growth_5m_pct if hi else None,
        "holders_lost": hi.lost_holders if hi else None,
        "holder_churn_pct": hi.churn_pct if hi else None,
        "holder_dust_share_new_pct": hi.dust_share_new_pct if hi else None,
        "holder_organic": hi.organic if hi else None,
        "whale_state": wi.state if wi else None,
        "whale_delta_pct": wi.delta_pct if wi else None,
        # --- liquidity
        "liq_usd": m.liquidity_usd if m else None,
        "liq_source": m.liquidity_source if m else None,
        "is_curve": bool(m and m.is_curve),
        "graduated": bool(info.complete),
        "curve_progress": info.curve_progress,
        "real_sol": real, "virtual_sol": virt, "amm_equivalent_usd": amm_eq,
        "liq_change_5m_pct": li.change_5m_pct if li else None,
        "liq_state": li.state if li else None,
        "liq_state_raw": li.state_raw if li else None,
        "pair_age_s": (now - m.pair_created_at) if m and m.pair_created_at else None,
        "lp_events": None,                          # LP add/remove not observable from these sources (UNKNOWN)
        # --- bundle / early-buyer structure (proxies only)
        "dev_snipe": "dev_snipe" in rk if st.risk else None,
        "suspicious_holders": "suspicious_holders" in rk if st.risk else None,
        "bundle_slot_concentration": None,          # not collected (UNKNOWN)
        "shared_funding_cluster": None,             # not collected (UNKNOWN)
        # --- market microstructure
        "age_s": (now - created) if created else None,
        "mc": m.market_cap if m else None,
        "tx_5m": tx, "buy_share_5m": share,
        "vol_5m": m.vol_5m if m else None,
        "vol_mc_5m": (m.vol_5m / m.market_cap) if m and m.vol_5m is not None and m.market_cap else None,
        "vol_accel": m.vol_accel if m else None,
        "txn_accel": m.txn_accel if m else None,
        "price_change_5m": m.price_change_5m if m else None,
        "price_change_1h": m.price_change_1h if m else None,
        "price_up_holders_flat": (m.price_change_5m > 50 and hi.growth_5m_pct is not None and hi.growth_5m_pct <= 0)
        if m and m.price_change_5m is not None and hi is not None else None,
        # --- risk engine view (as computed at that moment)
        "risk": st.risk.score if st.risk else None,
        "risk_factors": sorted(rk),
        "mint_authority_active": bool(st.identity.mint_authority) if st.identity.helius_checked else None,
        "freeze_authority_active": bool(st.identity.freeze_authority) if st.identity.helius_checked else None,
        "token2022": st.identity.token_program.startswith("Tokenz") if st.identity.helius_checked else None,
    }
    sc, why = shadow_score(f)
    f["shadow_antirug_score"], f["shadow_antirug_reasons"] = sc, why
    return f


def shadow_score(f: dict) -> tuple[int, list[str]]:
    """SHADOW ANTI_RUG_SCORE 0-100 (pre-registered weights, never used for a decision)."""
    pts, why = 0, []

    def add(p, r):
        nonlocal pts
        pts += p
        why.append(f"{r} +{p}")
    t10, t1, cp = f.get("top10_pct"), f.get("top1_pct"), f.get("dev_pct")
    if t10 is not None:
        if t10 > 50:
            add(20, "top10 > 50%")
        elif t10 > 35:
            add(10, "top10 35-50%")
    if t1 is not None:
        if t1 > 15:
            add(15, "top1 > 15%")
        elif t1 > 8:
            add(7, "top1 8-15%")
    if cp is not None:
        if cp > 10:
            add(15, "dev holds > 10%")
        elif cp > 5:
            add(7, "dev holds 5-10%")
    if f.get("dev_status") in BAD_DEV or (f.get("dev_sold_pct") or 0) >= 50:
        add(25, "dev sold")
    elif (f.get("dev_sold_pct") or 0) >= 10:
        add(10, "dev partial sell")
    pd_, pt, pg = f.get("dev_prev_dead"), f.get("dev_prev_tokens"), f.get("dev_prev_graduated")
    if (pd_ is not None and pd_ >= 3) or (pt is not None and pt >= 5 and not pg):
        add(15, "dev bad history / serial launcher")
    if f.get("dev_snipe"):
        add(10, "dev snipe at launch")
    if f.get("suspicious_holders"):
        add(10, "suspicious holder pattern")
    eq = f.get("amm_equivalent_usd")
    if eq is not None and eq < 20_000:
        add(10, "liquidity < $20K")
    lc = f.get("liq_change_5m_pct")
    if lc is not None and lc <= -15:
        add(10, "liquidity -15% / 5m")
    if f.get("price_up_holders_flat"):
        add(10, "price up, holders flat")
    if f.get("buy_share_5m") is not None and (f.get("tx_5m") or 0) >= 20 and f["buy_share_5m"] < 0.45:
        add(10, "sell-side flow (buys < 45%)")
    rf = set(f.get("risk_factors") or [])
    for k in ("sudden_spike", "volume_anomaly", "wash_pattern", "volume_without_holders", "curve_drain"):
        if k in rf:
            add(5, k)
    if not f.get("holders_ok"):
        add(10, "holder data missing")
    if not f.get("dev_verified"):
        add(5, "dev data missing")
    return min(100, pts), why


# binary research features evaluated by the report: name -> (predicate on the feature dict, needs)
def _gt(k, v):
    return lambda f: None if f.get(k) is None else f[k] > v


def _lt(k, v):
    return lambda f: None if f.get(k) is None else f[k] < v


def _has(key):
    return lambda f: None if f.get("risk") is None else key in (f.get("risk_factors") or [])


BINARY = {
    "dev:holds>5%": _gt("dev_pct", 5), "dev:holds>10%": _gt("dev_pct", 10),
    "dev:sold>=10%": lambda f: None if f.get("dev_sold_pct") is None else f["dev_sold_pct"] >= 10,
    "dev:bad_status": lambda f: None if f.get("dev_status") is None else f["dev_status"] in BAD_DEV,
    "dev:prev_dead>=3": lambda f: None if f.get("dev_prev_dead") is None else f["dev_prev_dead"] >= 3,
    "dev:initial_sol>2": _gt("dev_initial_sol", 2),
    "dev:data_missing": lambda f: not f.get("dev_verified"),
    "holders:top1>10%": _gt("top1_pct", 10), "holders:top10>35%": _gt("top10_pct", 35),
    "holders:top10>50%": _gt("top10_pct", 50), "holders:top10+creator>50%": _gt("top10_plus_creator_pct", 50),
    "holders:count<50": _lt("holder_count", 50), "holders:growth5m<=0": lambda f: None if f.get(
        "holder_growth_5m_pct") is None else f["holder_growth_5m_pct"] <= 0,
    "holders:churn>20%": _gt("holder_churn_pct", 20), "holders:suspicious_org": lambda f: None if f.get(
        "holder_organic") in (None, "UNKNOWN") else f["holder_organic"] == "SUSPICIOUS",
    "holders:whale_distribution": lambda f: None if f.get("whale_state") in (None, "UNKNOWN") else
    f["whale_state"] == "DISTRIBUTION",
    "holders:data_missing": lambda f: not f.get("holders_ok"),
    "liq:amm_eq<20K": _lt("amm_equivalent_usd", 20_000), "liq:change5m<=-15%": lambda f: None if f.get(
        "liq_change_5m_pct") is None else f["liq_change_5m_pct"] <= -15,
    "liq:bonding_curve": lambda f: None if f.get("liq_source") is None else bool(f.get("is_curve")),
    "liq:shock_raw": lambda f: None if f.get("liq_state_raw") is None else f["liq_state_raw"] == "SHOCK",
    "bundle:dev_snipe": lambda f: f.get("dev_snipe"), "bundle:suspicious_holders": lambda f: f.get("suspicious_holders"),
    "micro:buy_share<50%": lambda f: None if f.get("buy_share_5m") is None or (f.get("tx_5m") or 0) < 10 else
    f["buy_share_5m"] < 0.5,
    "micro:vol/mc>1": _gt("vol_mc_5m", 1.0), "micro:pc5m>100%": _gt("price_change_5m", 100),
    "micro:price_up_holders_flat": lambda f: f.get("price_up_holders_flat"),
    "micro:vol_accel>3": _gt("vol_accel", 3), "micro:txn_accel>3": _gt("txn_accel", 3),
    "risk:>=40": _gt("risk", 39.99), "risk:<=20": lambda f: None if f.get("risk") is None else f["risk"] <= 20,
    "auth:mint_or_freeze_active": lambda f: None if f.get("mint_authority_active") is None else
    bool(f["mint_authority_active"] or f["freeze_authority_active"]),
    "token2022": lambda f: f.get("token2022"),
}
for _k in ("sudden_spike", "volume_anomaly", "wash_pattern", "volume_without_holders", "curve_drain", "dev_snipe",
           "serial_launcher", "dev_history_bad", "dev_concentration", "single_whale", "top10_high", "few_holders",
           "high_churn", "whale_distribution", "low_liquidity", "liquidity_falling", "mc_liq_high",
           "pump_without_liquidity", "sell_pressure", "suspicious_holders", "no_socials", "new_token"):
    BINARY[f"riskfactor:{_k}"] = _has(_k)
for _t in (50, 60, 70, 80):
    BINARY[f"shadow_score>={_t}"] = (lambda t: lambda f: None if f.get("shadow_antirug_score") is None
                                     else f["shadow_antirug_score"] >= t)(_t)
