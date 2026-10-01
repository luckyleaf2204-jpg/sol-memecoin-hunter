"""EXIT LIQUIDITY detector (SHADOW — never a gate). Pattern: price up / volume up while large holders or the dev use
the incoming buyers to get out.

Point-in-time inputs (<= decision time): the money-flow trade sample (research, Helius), the token's holder / whale /
liquidity intel and market data. Components (0-1 risk, None = UNKNOWN):
  dev_sell            creator wallet sold in the last minute (sampled trades)
  top_holder_sell     a seller held >= 2 % of supply before selling
  sell_imbalance      SOL sold vs SOL bought in the last minute
  sell_acceleration   sells now vs the previous minute
  whale_distribution  whale intel state DISTRIBUTION
  top_wallet_outflow  share of the minute's sell SOL taken by the top-3 sellers
  volume_concentration share of all traded SOL done by the top-3 wallets
  buyers_vs_volume    volume accelerating while unique buyers do not
  liquidity_change    liquidity falling over 5 min
  new_vs_repeat       repeat buyers dominating (churn / wash) instead of new buyers
EXIT_LIQUIDITY_RISK = 100 x weighted mean of the KNOWN components (needs >= 3 known).
"""
from __future__ import annotations

from collections import Counter

from research.onchain import pool_owners, trades
from trading.money_flow import WINDOW_S, _sample
from trading.setup_common import ramp

WEIGHTS = {"dev_sell": 0.20, "top_holder_sell": 0.15, "sell_imbalance": 0.15, "sell_acceleration": 0.10,
           "whale_distribution": 0.10, "top_wallet_outflow": 0.08, "volume_concentration": 0.07,
           "buyers_vs_volume": 0.07, "liquidity_change": 0.05, "new_vs_repeat": 0.03}


def exit_liquidity(rec: dict | None, st, t: float, mf: dict | None = None) -> dict:
    comp = {k: None for k in WEIGHTS}
    detail = {}
    if rec and rec.get("status") == "ok":
        pools = pool_owners(rec.get("txs", []))
        creator = rec.get("creator")
        supply = st.info.total_supply
        cur = _sample(rec, t, t - WINDOW_S, t)
        prev = _sample(rec, t, t - 2 * WINDOW_S, t - WINDOW_S)
        if len(cur) >= 5:
            buy_sol, sell_sol, sellers, vol = 0.0, Counter(), Counter(), Counter()
            dev_sold, big_sold = False, False
            for x in cur:
                b, s = trades(x, pools)
                sol = abs(x.get("sol") or 0.0)
                if b and x.get("who") in b:
                    buy_sol += sol
                    vol[x["who"]] += sol
                for o, amt in s.items():
                    if x.get("who") == o:
                        sellers[o] += sol
                        vol[o] += sol
                    if creator and o == creator:
                        dev_sold = True
                    pre = (x.get("pre_bal") or {}).get(o)
                    if supply and pre and pre >= 0.02 * supply:
                        big_sold = True
            ssum = sum(sellers.values())
            comp["dev_sell"] = 1.0 if dev_sold else 0.0
            comp["top_holder_sell"] = 1.0 if big_sold else (0.0 if supply else None)
            comp["sell_imbalance"] = ramp(ssum / (buy_sol + ssum), 0.4, 0.8) if buy_sol + ssum else None
            comp["top_wallet_outflow"] = ramp(sum(v for _, v in sellers.most_common(3)) / ssum, 0.5, 0.95) if ssum else 0.0
            tv = sum(vol.values())
            comp["volume_concentration"] = ramp(sum(v for _, v in vol.most_common(3)) / tv, 0.4, 0.9) if tv else None
            if len(prev) >= 5:
                ps = sum(1 for x in prev if trades(x, pools)[1])
                cs = sum(1 for x in cur if trades(x, pools)[1])
                comp["sell_acceleration"] = ramp(cs / max(1, ps), 1.0, 3.0)
            detail.update(buy_sol=round(buy_sol, 4), sell_sol=round(ssum, 4), dev_sold=dev_sold, top_holder_sold=big_sold)
    wi = st.whale_intel
    if wi is not None and wi.state not in ("", "UNKNOWN", None):
        comp["whale_distribution"] = 1.0 if wi.state == "DISTRIBUTION" else 0.0
    li = st.liquidity_intel
    if li is not None and li.change_5m_pct is not None:
        comp["liquidity_change"] = ramp(-li.change_5m_pct, 0, 30)
    if mf and mf.get("status") == "ok":
        va = st.market.vol_accel if st.market else None
        ba = mf.get("buyer_acceleration")
        if va is not None and ba is not None:
            comp["buyers_vs_volume"] = ramp(va - ba, 0.0, 1.5)       # volume growing faster than unique buyers
        if mf.get("repeat_buyer_ratio") is not None:
            comp["new_vs_repeat"] = ramp(mf["repeat_buyer_ratio"], 0.3, 0.8)
    known = {k: v for k, v in comp.items() if v is not None}
    risk = round(100 * sum(WEIGHTS[k] * v for k, v in known.items()) / sum(WEIGHTS[k] for k in known), 1) \
        if len(known) >= 3 else None
    return {"exit_liquidity_risk": risk, "components": comp, "known": len(known), "detail": detail,
            "status": "ok" if risk is not None else "UNKNOWN (fewer than 3 components measurable)"}
