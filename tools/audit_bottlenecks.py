"""Live bottleneck audit (read-only): runs the real scanner + EXPERIMENTAL paper bot for N minutes and records

  1. every liquidity-SHOCK event under the OLD measure (raw curve SOL, pair-only filter) with the corrected measure
     (same pair + same source; curve depth = real + virtual SOL) and a classification:
     REAL_SHOCK · MIGRATION_ARTIFACT · BONDING_CURVE_ARTIFACT · STALE_DATA · MISSING_DATA · PAIR_CHANGE · UNKNOWN
  2. an Opportunity decomposition of every EarlyScore-PASS token (components, unavailable factors, Risk points by
     category) with counterfactuals: Risk without missing-data ("data") points; curve depth valued as the
     AMM-equivalent of its virtual reserves -> OPP_LOW_* classification
  3. liquidity-blocked tokens: real SOL value vs AMM-equivalent depth, curve / graduated / pair type
  4. the Opportunity x Liquidity cross table (with EarlyScore, Risk, Confidence, age bucket)
It decides nothing and changes no threshold.

usage: python tools/audit_bottlenecks.py --minutes 15 --out audit.json   (HELIUS_API_KEY from the environment)
"""
import argparse
import asyncio
import collections
import json
import statistics
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.config import ApiKeys, Settings  # noqa: E402
from database.db import Database  # noqa: E402
from intel.liquidity import classify_state, depth_points  # noqa: E402
from scanner.engine import ScannerEngine  # noqa: E402
from scoring.subscores import lin  # noqa: E402
from trading import decision as D  # noqa: E402
from trading.bot import PaperBot  # noqa: E402
from trading.config import TradingConfig  # noqa: E402
from trading.jupiter import JupiterQuotes  # noqa: E402


def shock_event(st, h, now, sol):
    cur = h.latest()
    pair = cur.pair if cur and cur.pair else None
    win = [p for p in h.points if p.ts >= now - 900 and p.liq is not None]
    raw_pts = [p for p in win if pair is None or p.pair == pair]
    basis, dp = depth_points(h.points, cur, now, st.info, sol)
    new_state = "UNKNOWN" if dp is None else classify_state(dp)[0]
    pairs = {p.pair for p in raw_pts}
    srcs = {p.liq_src for p in raw_pts}
    breaks = [b for b in h.breaks if b[0] >= now - 900]
    # the raw drop
    drop, a, b = 0.0, None, None
    for i, pi in enumerate(raw_pts):
        for pj in raw_pts[i + 1:]:
            if pj.ts - pi.ts > 300:
                break
            if pi.liq > 0 and 100 * (pi.liq - pj.liq) / pi.liq > drop:
                drop, a, b = 100 * (pi.liq - pj.liq) / pi.liq, pi, pj
    price_chg = (100 * (b.price / a.price - 1)) if a and b and a.price and b.price else None
    stale = st.info.pump_updated_at is not None and now - st.info.pump_updated_at > 120 and cur.liq_src == "pumpfun_curve"
    if new_state == "SHOCK":
        cat = "REAL_SHOCK"
    elif dp is None and basis:
        cat = "MISSING_DATA"
    elif len(pairs) > 1:
        cat = "MIGRATION_ARTIFACT" if breaks or st.info.complete else "PAIR_CHANGE"
    elif len(srcs) > 1:
        cat = "MIGRATION_ARTIFACT" if st.info.complete else "BONDING_CURVE_ARTIFACT"
    elif stale:
        cat = "STALE_DATA"
    elif cur.liq_src == "pumpfun_curve":
        cat = "BONDING_CURVE_ARTIFACT"
    else:
        cat = "UNKNOWN"
    m = st.market
    return {"ca": st.mint, "symbol": st.info.symbol, "age_s": round(now - (st.info.created_at or now)),
            "mc": m.market_cap if m else None, "price": m.price_usd if m else None,
            "raw_liquidity": cur.liq if cur else None, "liquidity_source": cur.liq_src if cur else None,
            "pair_address": cur.pair if cur else None, "pair_type": ("curve" if m and m.is_curve else m.dex_id) if m else None,
            "bonding_curve": bool(m and m.is_curve), "graduated": bool(st.info.complete),
            "migrated": bool(breaks), "previous_pair": breaks[-1][1] if breaks else None,
            "current_pair": breaks[-1][2] if breaks else (cur.pair if cur else None),
            "previous_liquidity": a.liq if a else None, "current_liquidity": b.liq if b else None,
            "liquidity_delta_pct": -round(drop, 1), "liquidity_baseline_ts": round(raw_pts[0].ts) if raw_pts else None,
            "shock_calc": f"max drop {drop:.1f}% between points <=5 min apart (raw, {len(raw_pts)} pts, "
                          f"{len(pairs)} pair(s), sources {sorted(srcs)})",
            "price_change_same_interval_pct": None if price_chg is None else round(price_chg, 1),
            "corrected_basis": basis, "corrected_state": new_state,
            "corrected_max_drop_pct": None if dp is None else round(classify_state(dp)[2] or 0, 1),
            "virtual_base_sol": (st.info.virtual_sol_reserves - st.info.real_sol_reserves)
            if st.info.virtual_sol_reserves and st.info.real_sol_reserves is not None else None,
            "category": cat}


def amm_equivalent(st, sol):
    """Curve: 2 x virtual SOL x SOL price (DexScreener counts both sides of an AMM pool)."""
    m = st.market
    if not m or not m.is_curve or not sol:
        return None
    v = st.info.virtual_sol_reserves
    if not v and st.info.real_sol_reserves is not None:
        v = st.info.real_sol_reserves + 30
    return 2 * v * sol if v else None


def decompose(st, rec, settings, sol):
    comp = {k: v for k, v in (rec.get("components") or {}).items() if v is not None and D.WEIGHTS.get(k)}
    aw = sum(D.WEIGHTS[k] for k in comp)
    opp = rec.get("opportunity")
    rk = st.risk
    cats = collections.Counter()
    for f in (rk.factors if rk else []):
        cats[f.category] += f.points
    unavailable = {k: [f.key for f in s.factors if not f.available] for k, s in st.subscores.items()
                   if k in ("momentum", "liquidity", "holder", "whale", "onchain")}

    def opp_with(over):
        c = dict(comp, **{k: v for k, v in over.items() if k in comp or v is not None})
        w = sum(D.WEIGHTS[k] for k in c)
        return round(sum(D.WEIGHTS[k] * c[k] for k in c) / w) if w else None
    cf = {}
    if rk is not None and "risk" in comp:
        cf["risk_without_missing_data"] = opp_with({"risk": 100 - max(0, rk.score - cats.get("data", 0))})
        cf["risk_without_data_and_age"] = opp_with({"risk": 100 - max(0, rk.score - cats.get("data", 0) - cats.get("age", 0))})
    liq_sub = st.subscores.get("liquidity")
    eq = amm_equivalent(st, sol)
    if liq_sub is not None and liq_sub.score is not None and eq is not None:
        f = {x.key: x for x in liq_sub.factors}
        if "depth" in f:
            avail = [x for x in liq_sub.factors if x.available]
            mx = sum(x.max_points for x in avail)
            pts = sum(x.points for x in avail) - f["depth"].points + lin(eq, settings.min_liquidity * 0.3,
                                                                       settings.min_liquidity * 2, 30)
            cf["curve_depth_amm_equivalent"] = opp_with({"liquidity": round(100 * pts / mx)})
    weak = {k: v for k, v in comp.items() if v < 65}
    reasons = []
    if opp is not None and opp < 65:
        if cf.get("risk_without_missing_data") is not None and cf["risk_without_missing_data"] >= 65:
            reasons.append("OPP_LOW_MISSING_DATA")
        if cf.get("curve_depth_amm_equivalent") is not None and cf["curve_depth_amm_equivalent"] >= 65:
            reasons.append("OPP_LOW_LIQUIDITY_DATA")
        if "onchain" not in comp and st.holder_status != "ok":
            reasons.append("OPP_LOW_HOLDER_DATA")
        mom_un = len(unavailable.get("momentum", []))
        if "momentum" in weak and mom_un >= 2:
            reasons.append("OPP_LOW_BASELINE")
        if (st.info.created_at and time.time() - st.info.created_at < 90) and "early" in weak:
            reasons.append("OPP_LOW_AGE")
        if not reasons:
            reasons.append("OPP_LOW_REAL_SIGNAL")
    return {"opportunity": opp, "components": comp, "weights_with_data": aw, "weak_components": weak,
            "risk_points_by_category": dict(cats), "unavailable_factors": unavailable,
            "counterfactual_opportunity": cf, "classification": reasons}


async def run(minutes, research_db=""):
    eng = ScannerEngine(Settings(), Database(Path(tempfile.mkdtemp()) / "a.db"), keys=ApiKeys.from_env(),
                        on_log=lambda m: print(m, flush=True) if "PIPELINE" in m else None)
    bot = PaperBot(eng, TradingConfig(experimental=True, latency_probe=True, latency_slippage_model="AUTO",
                                      lifecycle=True))
    bot.jupiter = JupiterQuotes(eng.http)
    if research_db:
        from research.dataset import DatasetRecorder
        bot.recorder = DatasetRecorder(research_db, dex=eng.dex)
        from research.onchain import OnchainResearch
        bot.onchain = OnchainResearch(eng.rpc, bot.recorder)
    from trading.money_flow import MoneyFlowCollector
    bot.money_flow = MoneyFlowCollector(eng.rpc)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(eng.run()), asyncio.create_task(bot.run(stop))]
    shocks, es_pass, liq_blocked, best = {}, {}, {}, {}
    promo, cand_first, paths = {}, {}, {}
    mf_measured = set()
    lc = {"seen": {}, "setup_pass": {}, "es_pass": {}, "old_cand": {}, "exp_cand": {}, "lc_cand": {},
          "b_cand": {}, "c_cand": {}, "pre_would": {}, "edge_cand": {}}
    buffer_only, buffer_any = set(), set()
    end = time.time() + minutes * 60
    while time.time() < end:
        await asyncio.sleep(10)
        now, sol = time.time(), eng.sol_price
        for st in list(eng.tracked.values()):
            h = eng.history.get(st.mint)
            li = st.liquidity_intel
            rec = bot.decisions.get(st.mint)
            if li is not None and li.state_raw == "SHOCK" and st.mint not in shocks and h is not None:
                ev = shock_event(st, h, now, sol)
                ev["blocked_by"] = (rec or {}).get("blocked_by")
                shocks[st.mint] = ev
            if not rec or now - rec.get("ts", 0) > 30:
                continue
            es = rec.get("early_score") or {}
            m = st.market
            row = {"ca": st.mint, "symbol": st.info.symbol, "age_s": round(es.get("age_s") or 0), "bucket": es.get("bucket"),
                   "mc": m.market_cap if m else None, "liquidity": m.liquidity_usd if m else None,
                   "liq_source": m.liquidity_source if m else None, "amm_equiv": amm_equivalent(st, sol),
                   "volume_5m": m.vol_5m if m else None, "tx_5m": m.txns_5m if m else None,
                   "buy_pressure": (round(100 * m.buys_5m / m.txns_5m) if m and m.txns_5m else None),
                   "holders": st.holders.holder_count if st.holders and st.holder_status == "ok" else None,
                   "top10": st.holders.top10_pct if st.holders and st.holder_status == "ok" else None,
                   "creator_pct": st.dev.current_pct if st.dev and st.dev.balance_verified else None,
                   "early_score": es.get("score"), "early_conf": es.get("confidence"),
                   "momentum": (rec.get("components") or {}).get("momentum"), "opportunity": rec.get("opportunity"),
                   "confidence": rec.get("confidence"), "risk": st.risk.score if st.risk else None,
                   "prior_risk": es.get("prior_risk"), "identity": st.identity.status,
                   "vet": "PASS" if rec.get("vet_passed") else ",".join(c["key"] for c in rec.get("checks", [])
                                                                       if c["result"] == "FAIL") or "UNKNOWN",
                   "decision": rec.get("decision"), "old_decision": rec.get("old_decision"),
                   "blocked_by": rec.get("blocked_by"), "curve": bool(m and m.is_curve), "graduated": bool(st.info.complete)}
            k = st.mint
            px = m.price_usd if m else None
            if rec.get("old_liquidity") == D.FAIL and rec.get("new_liquidity") == "PASS":
                g = {"early_score": bool(es.get("score") is not None and es["score"] >= es["theta"]
                                         and es["confidence"] >= es["gamma"]),
                     "opportunity_ge_65": (rec.get("opportunity") or 0) >= 65,
                     "confidence_ge_60": (rec.get("confidence") or 0) >= 60,
                     "risk_le_60": st.risk is not None and st.risk.score <= 60,
                     "vet_no_true_fail": not any(b.startswith("hard:") for b in rec.get("blocked_by") or []),
                     "candidate": rec.get("decision") == "TRADE"}
                if k not in promo:
                    promo[k] = {"ca": k, "symbol": st.info.symbol, "ts": now, "price": px, "age_s": row["age_s"],
                                "real_usd": m.liquidity_usd if m else None,
                                "equivalent_usd": rec.get("liquidity_equivalent_usd"),
                                "gates_ever": dict(g), "blocked_by_first": rec.get("blocked_by")}
                else:
                    for gk, gv in g.items():
                        promo[k]["gates_ever"][gk] = promo[k]["gates_ever"][gk] or gv
            bl = rec.get("blocked_by") or []
            if any(b.startswith("entry_risk_buffer") for b in bl):
                buffer_any.add(k)
                if all(b.startswith("entry_risk_buffer") for b in bl):
                    buffer_only.add(k)                       # would be a candidate without the buffer
            if rec.get("decision") == "TRADE" and k not in cand_first:
                cand_first[k] = {"ts": now, "price": px, "symbol": st.info.symbol,
                                 "liquidity_promoted": k in promo, "liquidity_model": rec.get("liquidity_model"),
                                 "risk": st.risk.score if st.risk else None,
                                 "risk_factors": sorted(f"{f.category}:{f.key}:{f.points}" for f in (st.risk.factors if st.risk else [])),
                                 "holder_status": st.holder_status, "dev_verified": bool(st.dev and st.dev.balance_verified),
                                 "early_score": (rec.get("early_score") or {}).get("score"),
                                 "prior_risk": (rec.get("early_score") or {}).get("prior_risk")}
            if rec.get("money_flow_score") is not None:
                mf_measured.add(k)
            if rec.get("engine") == "lifecycle":
                name = rec.get("lifecycle_name")
                eqv = rec.get("liquidity_equivalent_usd")
                lc["seen"][k] = {"lifecycle": name, "reason": ((rec.get("lifecycle") or {}).get("reasons") or [""])[0],
                                 "liq_bucket": None if eqv is None else ("<10K" if eqv < 1e4 else "10-20K" if eqv < 2e4 else
                                                                       "20-50K" if eqv < 5e4 else "50-100K" if eqv < 1e5 else "100K+")}
                su = rec.get("setup") or {}
                thr = rec.get("setup_threshold")
                ctx = {"ts": now, "price": px, "lifecycle": name, "setup_type": rec.get("setup_type"),
                       "setup_score": su.get("score"), "liq_bucket": lc["seen"][k]["liq_bucket"],
                       "money_flow_score": rec.get("money_flow_score"), "exit_liquidity_risk": rec.get("exit_liquidity_risk"),
                       "entry_location": rec.get("entry_location"), "cluster_risk": rec.get("cluster_risk"),
                       "independent_buyer_score": rec.get("independent_buyer_score")}
                if rec.get("shadow_B_would_buy"):
                    lc["b_cand"].setdefault(k, ctx)
                if rec.get("shadow_C_would_buy"):
                    lc["c_cand"].setdefault(k, ctx)
                if rec.get("new_edge_shadow_would_buy"):
                    lc["edge_cand"].setdefault(k, ctx)
                if rec.get("pre_shadow_decision") == "WOULD_BUY":
                    lc["pre_would"].setdefault(k, ctx)
                if su.get("score") is not None and thr is not None and su["score"] >= thr \
                        and su.get("data_confidence", 0) >= bot.cfg.min_setup_confidence and k not in lc["setup_pass"]:
                    try:
                        from research.antirug import features as _arf
                        ctx["shadow_antirug"] = _arf(st, now).get("shadow_antirug_score")
                    except Exception:
                        ctx["shadow_antirug"] = None
                    lc["setup_pass"][k] = ctx
                if es.get("score") is not None and es["score"] >= es["theta"] and es["confidence"] >= es["gamma"]:
                    lc["es_pass"].setdefault(k, ctx)
                if rec.get("would_have_bought_old"):
                    lc["old_cand"].setdefault(k, ctx)
                if rec.get("would_have_bought_experimental"):
                    lc["exp_cand"].setdefault(k, ctx)
                if rec.get("decision") == "TRADE":
                    lc["lc_cand"].setdefault(k, ctx)
            if (k in promo or k in cand_first or any(k in lc[x] for x in ("setup_pass", "es_pass", "old_cand",
                                                                         "exp_cand", "lc_cand", "pre_would"))) and px:
                paths.setdefault(k, []).append((now, px))
            if k not in best or (row["opportunity"] or 0) >= (best[k]["opportunity"] or 0):
                best[k] = row
            if es.get("score") is not None and es["score"] >= es["theta"] and es["confidence"] >= es["gamma"]:
                if k not in es_pass or (row["opportunity"] or 0) >= (es_pass[k]["opportunity"] or 0):
                    es_pass[k] = row | {"decomposition": decompose(st, rec, eng.settings, sol)}
            if any(b == "hard:liquidity" for b in rec.get("blocked_by") or []):
                liq_blocked.setdefault(k, row)
    stop.set()
    eng.stop()
    await asyncio.gather(*tasks, return_exceptions=True)
    bot._audit_buffer = {"any": len(buffer_any), "only_blocker": len(buffer_only)}
    bot._audit_lc = lc
    bot._audit_mf_measured = len(mf_measured)
    bot._audit_breaks = [(m, list(h.breaks), [(p.ts, p.price, p.pair) for p in h.points])
                         for m, h in getattr(eng.history, "_h", {}).items() if h.breaks]
    bot._audit_minutes = minutes
    return bot, eng, shocks, es_pass, liq_blocked, best, promo, cand_first, paths


def fwd(path, t0, p0, horizons=(60, 300, 600, 900, 1800, 3600)):
    if not path or not p0:
        return {}
    after = [(t, p) for t, p in path if t >= t0]
    rets = [p / p0 - 1 for _, p in after]
    out = {"mfe_pct": round(100 * max(rets), 1) if rets else None, "mae_pct": round(100 * min(rets), 1) if rets else None,
           "observed_s": round(after[-1][0] - t0) if after else 0}
    for h in horizons:
        pts = [p for t, p in after if t0 + h - 15 <= t <= t0 + h + 15]
        out[f"ret_{h // 60}m_pct"] = round(100 * (pts[-1] / p0 - 1), 1) if pts else None
    return out


def sim_trade(path, t0, p0, cost):
    """Simplified: entry p0*(1+cost); first of +30 % / -15 %; else marked at the last observed price (end of run)."""
    if not path or not p0:
        return None, "no_price"
    entry = p0 * (1 + cost)
    last = None
    for t, p in path:
        if t < t0:
            continue
        r = p / entry - 1
        last = p
        if r >= 0.30:
            return round(100 * (p * (1 - cost) / entry - 1), 1), "tp30"
        if r <= -0.15:
            return round(100 * (p * (1 - cost) / entry - 1), 1), "sl15"
    return (round(100 * (last * (1 - cost) / entry - 1), 1), "mark_end_of_run") if last else (None, "no_price")


def pctl(xs, ps=(0.5, 0.75, 0.9)):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return {f"P{int(p * 100)}": round(xs[min(len(xs) - 1, int(p * (len(xs) - 1)))], 3) for p in ps} | {"n": len(xs)}


def classify_spike(fx, max_age=30.0):
    snaps = fx["snaps"] + ([fx["exit"]] if fx.get("exit") else [])
    entry = snaps[0]
    spike = next((x for x in snaps[1:] if (x.get("risk") or 0) > 60), None)
    if spike is None and fx.get("exit") and fx["exit"].get("reason") == "risk_spike":
        spike = fx["exit"]
    if spike is None:
        return None
    key = lambda f: f.rsplit(":", 1)[0]  # noqa: E731
    before = {key(f) for f in entry["factors"]}
    added = sorted({f for f in spike["factors"] if key(f) not in before})
    cats = {f.split(":")[0] for f in added}
    later = [x for x in snaps if x["t"] > spike["t"]]
    px = (spike["price"] / entry["price"] - 1) * 100 if spike.get("price") and entry.get("price") else None
    gone = later and all(not ({key(f) for f in x["factors"]} & {key(f) for f in added}) for x in later[-1:])
    if (spike.get("market_age_s") or 0) > max_age or (cats and cats <= {"data"}):
        cls = "STALE_DATA"
    elif cats & {"rug", "liquidity", "dev", "manipulation"} and (
            (px is not None and px <= -5) or spike.get("liq_state") != entry.get("liq_state")
            or (spike.get("dev_status") in ("SOLD ALL", "MAJOR SELL", "PARTIAL SELL")
                and spike.get("dev_status") != entry.get("dev_status"))):
        cls = "REAL_NEW_RISK"                         # something actually happened: dump, dev sell, liquidity move
    elif cats and cats <= {"holders", "dev", "manipulation"} and (
            spike.get("holders_fetched") != entry.get("holders_fetched") or spike.get("dev_status") != entry.get("dev_status")):
        cls = "DATA_REFRESH"                          # newly fetched holder/dev data revealed pre-existing risk
    elif added and gone and (px is None or abs(px) < 5):
        cls = "FALSE_POSITIVE"
    else:
        cls = "UNKNOWN"
    return {"symbol": fx["symbol"], "class": cls, "risk_path": [(x["at"], x["risk"]) for x in snaps],
            "added_factors": added, "price_change_to_spike_pct": None if px is None else round(px, 1),
            "spike_at_s": spike["t"], "entry_ctx": fx["ctx"]}


def v13_section(bot):
    """V1.3 TRUTH PRICE: executable SELL-quote coverage, source agreement, DS staleness, migration epochs,
    OLD vs TRUTH P&L, MFE / MAE, fast-SL research, exit shadow, lifecycle P&L on truth, data quality."""
    from trading.truth_price import VALID

    def med(v):
        v = [x for x in v if x is not None]
        return round(statistics.median(v), 3) if v else None

    def q(v, f):
        v = sorted(x for x in v if x is not None)
        return round(v[int(f * (len(v) - 1))], 3) if v else None
    now = time.time()
    trs = list(bot.cs_shadow.values())
    snaps = [x for t in trs for x in t.snapshots]
    status = collections.Counter(x["source_status"] for x in snaps)
    ds_cls = collections.Counter(x["ds_class"] for x in snaps)
    valid = [x for x in snaps if x["source_status"] == VALID]
    ds_age = [x["ds_age_s"] for x in snaps if x.get("ds_age_s") is not None]
    age_b = collections.Counter("<1s" if a < 1 else "1-2s" if a < 2 else "2-5s" if a < 5 else "5-10s" if a < 10
                                else "10-20s" if a < 20 else ">=20s" for a in ds_age)
    disc_by_age = {}
    for x in valid:
        if x.get("ds_vs_truth_pct") is None or x.get("ds_age_s") is None:
            continue
        b = "<5s" if x["ds_age_s"] < 5 else "5-10s" if x["ds_age_s"] < 10 else ">=10s"
        disc_by_age.setdefault(b, []).append(abs(x["ds_vs_truth_pct"]))
    curve = [x for x in valid if x.get("curve_price")]
    trades = []
    for t in trs:
        sm = t.summary(now)
        row = getattr(t, "trade_row", None) or {}
        trades.append({"symbol": t.symbol, "lifecycle": t.lifecycle, "trade_id": t.trade_id,
                       "entry_fill": t.entry_fill, "entry_quote": t.entry_quote_price,
                       "execution_impact_pct": t.execution_impact_pct, "latency_model_pct": t.latency_model_pct,
                       "total_simulated_entry_cost_pct": row.get("total_simulated_entry_cost_pct"),
                       "production_closed": t.old_exit is not None, "truth_closed": sm["exit_reason"] != "",
                       "exit_reason_old": (t.old_exit or {}).get("reason"), "exit_reason_truth": sm["exit_reason"],
                       "exit_reason_old_cat": sm["exit_reason_old"], "exit_reason_truth_cat": sm["exit_reason_truth"],
                       "held_old_s": (t.old_exit or {}).get("held_s"), "closed_after_truth_s": sm["closed_after_s"],
                       "old_pnl": (t.old_exit or {}).get("pnl_pct"), "pnl_ds": row.get("pnl_ds"),
                       "pnl_truth": sm["pnl_pct"], "pnl_common_source": sm["pnl_common_source"],
                       "mfe_ds": row.get("mfe_ds"), "mae_ds": row.get("mae_ds"), "mfe_truth": sm["mfe_pct"],
                       "mae_truth": sm["mae_pct"], "t_mfe": sm["time_to_mfe_s"], "t_mae": sm["time_to_mae_s"],
                       "snapshots": sm["snapshots"], "coverage_valid_pct": sm["coverage_valid_pct"],
                       "status_counts": sm["status_counts"], "fast_sl": sm["fast_sl"],
                       "old_fast_sl_class": sm["old_fast_sl_class"], "pair_changes": sm["pair_changes"],
                       "crosses_migration_unverified": sm["crosses_migration_unverified"]})
    done = [r for r in trades if r["production_closed"] and r["truth_closed"]]

    def exp(key, rows):
        v = [r[key] for r in rows if r.get(key) is not None]
        return {"n": len(v), "mean": round(statistics.fmean(v), 2) if v else None, "median": med(v)}
    by_lc = {}
    for r in done:
        by_lc.setdefault(r["lifecycle"] or "UNKNOWN", []).append(r)
    agree = collections.Counter((r["exit_reason_old_cat"], r["exit_reason_truth_cat"]) for r in trades if r["production_closed"])
    conf = [x["confidence"] for x in valid if x.get("confidence") is not None]
    return {
        "trades_tracked": len(trades), "completed_both": len(done),
        "snapshots": len(snaps), "status_counts": dict(status),
        "coverage_valid_pct": round(100 * status.get(VALID, 0) / len(snaps), 1) if snaps else None,
        "context_slot_pct": round(100 * sum(1 for x in valid if x.get("context_slot")) / len(valid), 1) if valid else None,
        "quote_latency_ms_p50_p90": (q([x.get("latency_ms") for x in snaps], 0.5), q([x.get("latency_ms") for x in snaps], 0.9)),
        "skipped_budget": bot.truth_skipped_budget,
        "ds_class_counts": dict(ds_cls), "ds_age_s_p50_p90": (q(ds_age, 0.5), q(ds_age, 0.9)), "ds_age_buckets": dict(age_b),
        "abs_ds_vs_truth_by_ds_age": {k: {"n": len(v), "median": med(v), "p90": q(v, 0.9)} for k, v in disc_by_age.items()},
        "abs_ds_vs_truth_pct_p50_p90": (q([abs(x["ds_vs_truth_pct"]) for x in valid if x.get("ds_vs_truth_pct") is not None], 0.5),
                                         q([abs(x["ds_vs_truth_pct"]) for x in valid if x.get("ds_vs_truth_pct") is not None], 0.9)),
        "curve": {"n": len(curve), "curve_vs_truth_p50": med([x["curve_vs_truth_pct"] for x in curve]),
                  "abs_curve_vs_truth_p90": q([abs(x["curve_vs_truth_pct"]) for x in curve if x.get("curve_vs_truth_pct") is not None], 0.9),
                  "ds_vs_curve_p50": med([x["ds_vs_curve_pct"] for x in curve]),
                  "abs_ds_vs_curve_p90": q([abs(x["ds_vs_curve_pct"]) for x in curve if x.get("ds_vs_curve_pct") is not None], 0.9),
                  "curve_status": dict(collections.Counter(x.get("curve_status") for x in snaps))},
        "migration_events": [e for t in trs for e in t.pair_changes],
        "confidence_p10_p50": (q(conf, 0.1), q(conf, 0.5)),
        "pnl": {"old": exp("old_pnl", done), "ds": exp("pnl_ds", done), "truth": exp("pnl_truth", done),
                "common_source": exp("pnl_common_source", done)},
        "lifecycle_truth_pnl": {k: {"old": exp("old_pnl", v), "truth": exp("pnl_truth", v)} for k, v in by_lc.items()},
        "exit_reason_matrix_old_truth": {f"{a}->{b}": n for (a, b), n in agree.items()},
        "fast_sl": {"production_fast_sl_classes": dict(collections.Counter(r["old_fast_sl_class"] for r in trades if r["old_fast_sl_class"])),
                    "truth_sl_hit_by_offset": {o: sum(1 for r in trades if (r["fast_sl"].get(o) or {}).get("sl_hit"))
                                               for o in ("5s", "10s", "15s", "30s")},
                    "truth_known_by_offset": {o: sum(1 for r in trades if r["fast_sl"].get(o)) for o in ("5s", "10s", "15s", "30s")}},
        "trades": trades}


def v12_section(bot):
    """V1.2 price accounting: provenance, discrepancy classes, staleness, slippage decomposition, pair consistency,
    migration price jumps, CURRENT vs DEXSCREENER vs COMMON-SOURCE (Jupiter) reconciliation, money-flow coverage."""
    from research.v11_analysis import slippage_bucket
    from trading.price_provenance import CommonSourceExit, classify_discrepancy, pct

    def lst(x):
        return list(x) if x is not None else []

    def age_bucket(ms):
        if ms is None:
            return "UNKNOWN"
        return "<100ms" if ms < 100 else "100-250ms" if ms < 250 else "250-500ms" if ms < 500 else \
            "500ms-1s" if ms < 1000 else "1-2s" if ms < 2000 else ">2s"
    q_rows = []
    for q in bot.quote_obs_log:
        trail = [t for t in lst(q.get("trail")) if t.get("ts", 0) > q["market"].get("ts", 0)]
        before = [q["decision_market"]] if q.get("decision_market") and q["decision_market"].get("ts", 0) < q["market"].get("ts", 0) else []
        c = classify_discrepancy(q["market"], q["quote"], trail, before)
        q_rows.append({"symbol": q["symbol"], "lifecycle": q.get("lifecycle"), "market_age_ms": q["market"].get("age_ms"),
                       "quote_latency_ms": q["quote"].get("latency_ms"), "decision_to_quote_ms": q.get("decision_to_quote_ms"),
                       "discrepancy_pct": c["discrepancy_pct"], "class": c["class"], "evidence": c["evidence"],
                       "pair_match": None if not q["market"].get("pair") or not q["quote"].get("pair")
                       else q["market"]["pair"] == q["quote"]["pair"], "market_source": q["market"].get("source"),
                       "route_note": q["quote"].get("note"), "decimal_source": q.get("decimal_source")})
    cls = collections.Counter(r["class"] for r in q_rows)
    stale = {}
    for r in q_rows:
        b = age_bucket(r["market_age_ms"])
        e = stale.setdefault(b, {"n": 0, "abs_disc": []})
        e["n"] += 1
        if r["discrepancy_pct"] is not None:
            e["abs_disc"].append(abs(r["discrepancy_pct"]))
    staleness = {b: {"n": e["n"], "mean_abs_discrepancy_pct": round(statistics.fmean(e["abs_disc"]), 2) if e["abs_disc"] else None,
                     "median_abs_discrepancy_pct": statistics.median(e["abs_disc"]) if e["abs_disc"] else None}
                 for b, e in stale.items()}
    lat = {k: collections.Counter(age_bucket(r[k]) for r in q_rows) for k in ("quote_latency_ms", "decision_to_quote_ms")}
    ages = sorted(r["market_age_ms"] for r in q_rows if r["market_age_ms"] is not None)
    pairs = collections.Counter("match" if r["pair_match"] else ("mismatch" if r["pair_match"] is False else "unknown")
                                for r in q_rows)
    # slippage decomposition per fill attempt
    fills = []
    for f in bot.fill_log:
        q = next((x for x in reversed(bot.quote_obs_log) if x["mint"] == f["mint"] and abs(x["ts"] - f.get("quote_ts", 0)) < 5), None)
        mkt = (q or {}).get("market", {}).get("price")
        qp = (q or {}).get("quote", {}).get("price")
        fills.append({"route_impact_pct": f.get("jupiter_impact_pct"), "latency_slip_pct": f.get("latency_slippage_pct"),
                      "total_reported_pct": f.get("total_slippage_pct"), "quote_vs_market_pct": pct(qp, mkt),
                      "fill_vs_quote_pct": pct(f.get("simulated_execution_price"), qp),
                      "fill_vs_market_pct": pct(f.get("simulated_execution_price"), mkt), "status": f.get("status"),
                      "lifecycle": f.get("lifecycle"), "usd": f.get("position_size_usd"), "liq": f.get("liquidity_usd")})
    sb = {}
    for f in fills:
        for key, label in (("total_reported_pct", "by_reported_total"), ("fill_vs_market_pct", "by_fill_vs_market")):
            v = f.get(key)
            b = slippage_bucket(abs(v) if v is not None else None)
            e = sb.setdefault(label, {}).setdefault(b, {"attempts": 0, "filled": 0})
            e["attempts"] += 1
            e["filled"] += f["status"] == "FILLED"
    # reconciliation per paper trade
    rec_rows = []
    c = bot.cfg
    for mint, pv in bot.provenance.items():
        ex = pv.get("exit") or {}
        dm = (pv.get("quote_market") or {}).get("price")
        model_c = None
        if dm:
            mc = CommonSourceExit(dm, pv["entry_ts"], c.stop_loss_pct, c.tp1_pct, c.tp1_sell_frac, c.tp2_pct,
                                  c.trailing_pct, c.max_hold_min * 60)
            for mk in pv.get("marks") or []:
                if mk.get("price") and mk.get("post_entry", True):
                    mc.on_price(mk["price"], mk["ts"])
            if mc.closed_at is None and ex.get("exit_market", {}).get("price"):
                mc.force_close(ex["exit_market"]["price"], ex["ts"], "mirror:" + (ex.get("reason") or "open"))
            model_c = mc.summary()
        cs = pv.get("common_source") or {}
        cur = ex.get("realized_pnl_pct")
        rec_rows.append({"symbol": pv["symbol"], "lifecycle": pv.get("lifecycle"),
                         "entry_market": dm, "entry_market_age_ms": (pv.get("quote_market") or {}).get("age_ms"),
                         "entry_quote": (pv.get("entry_quote") or {}).get("price"),
                         "entry_fill": (pv.get("entry_fill") or {}).get("price"),
                         "exit_market": (ex.get("exit_market") or {}).get("price"), "exit_reason_current": ex.get("reason"),
                         "exit_fill_source": ex.get("exit_fill_source"),
                         "current_pnl_pct": cur, "dexscreener_pnl_pct": (model_c or {}).get("pnl_pct"),
                         "common_source_pnl_pct": cs.get("pnl_pct"),
                         "difference_current_minus_common": round(cur - cs["pnl_pct"], 2)
                         if cur is not None and cs.get("pnl_pct") is not None else None,
                         "current_sl": ex.get("reason") in ("stop_loss",), "dexscreener_sl": (model_c or {}).get("exit_reason") == "stop_loss",
                         "common_sl": cs.get("exit_reason") == "stop_loss",
                         "current_mfe_mae": ((ex.get("path") or {}).get("mfe_pct"), (ex.get("path") or {}).get("mae_pct")),
                         "dexscreener_mfe_mae": ((model_c or {}).get("mfe_pct"), (model_c or {}).get("mae_pct")),
                         "common_mfe_mae": (cs.get("mfe_pct"), cs.get("mae_pct")),
                         "common_exit": (cs.get("exit_reason"), cs.get("closed_after_s")),
                         "jupiter_marks": len(pv.get("jupiter_marks") or []), "dex_marks": len(pv.get("marks") or []),
                         "pre_entry_prints_ignored": sum(1 for mk in pv.get("marks") or [] if mk.get("post_entry") is False),
                         "exit_print_fetched_before_entry": None if not ex.get("exit_market") else
                         (ex["exit_market"].get("age_ms") or 0) / 1000 > ex["ts"] - pv["entry_ts"],
                         "fill_vs_market_pct": pv.get("fill_vs_market_pct"), "pair_at_entry": (pv.get("pair_at_entry") or "")[:8],
                         "route_pool": (pv.get("route_pool") or "")[:8]})

    def expectancy(key):
        v = [r[key] for r in rec_rows if r.get(key) is not None]
        return {"n": len(v), "mean_pct": round(statistics.fmean(v), 2) if v else None}
    migr = []
    for m, breaks, pts in getattr(bot, "_audit_breaks", []):
        for ts, old, new in breaks:
            before = [p for p in pts if p[2] == old and p[0] <= ts]
            after = [p for p in pts if p[2] == new and p[0] >= ts]
            jump = pct(after[0][1], before[-1][1]) if before and after else None
            migr.append({"mint": m[:8], "ts": ts, "old_pair": old[:8], "new_pair": new[:8], "price_jump_pct": jump,
                         "position_spanning": m in bot.provenance})
    pre = [r for r in bot.decisions.values() if r.get("pre_shadow_decision") == "WOULD_BUY"]
    return {
        "price_map": {"entry": "Jupiter buy quote x (1 + simulated latency slip)",
                      "stop/TP/trailing/MFE/MAE/unrealized": "DexScreener mark (bot._price, <= 4 x max_data_age_s old)",
                      "hard exits fill": "liquidity model at the DexScreener mark",
                      "other exits fill": "Jupiter sell quote (model fallback)",
                      "reported slippage": "total = Jupiter priceImpactPct (route vs mid at quote time) + simulated latency slip; "
                                           "it does NOT include the quote-vs-market discrepancy"},
        "quotes": len(q_rows), "discrepancy_classes": dict(cls), "quote_rows": q_rows[:60],
        "market_age_ms_p50_p90": (ages[len(ages) // 2], ages[int(0.9 * (len(ages) - 1))]) if ages else None,
        "staleness": staleness, "latency_buckets": {k: dict(v) for k, v in lat.items()},
        "pair_consistency": dict(pairs), "slippage_decomposition": fills[:60], "slippage_buckets": sb,
        "reconciliation": rec_rows, "expectancy": {"current": expectancy("current_pnl_pct"),
                                                   "dexscreener": expectancy("dexscreener_pnl_pct"),
                                                   "common_source": expectancy("common_source_pnl_pct")},
        "sl_counts": {"current": sum(r["current_sl"] for r in rec_rows), "dexscreener": sum(r["dexscreener_sl"] for r in rec_rows),
                      "common_source": sum(r["common_sl"] for r in rec_rows)},
        "migrations": migr, "money_flow_measured_tokens": getattr(bot, "_audit_mf_measured", None),
        "money_flow_collector": bot.money_flow.stats() if bot.money_flow is not None else None,
        "pre_shadow_would_buy_now": len(pre)}


def v11_section(bot, paths):
    """Lifecycle V1.1 research: continuation by money flow / exit liquidity / entry location, A/B (A lifecycle, B +MF,
    C +MF +exit liquidity, D OLD), NEW fast-SL forensics, slippage buckets, PRE shadow outcomes. Net of slippage."""
    from research.v11_analysis import pattern_effect, sim_net, slippage_bucket, walk_forward_plan
    lc = getattr(bot, "_audit_lc", None)
    if not lc:
        return None
    fl = bot.fill_log
    tot = sorted(f["total_slippage_pct"] for f in fl if f.get("total_slippage_pct") is not None)
    pooled = tot[len(tot) // 2] if tot else 3.0

    def outcome(k, c, cost=None):
        f = fwd(paths.get(k), c["ts"], c["price"])
        mae = f.get("mae_pct")
        f["rug"] = True if mae is not None and mae <= -80 else (False if f.get("observed_s", 0) >= 600 else None)
        f["sim_net_pct"], f["sim_exit"] = sim_net(paths.get(k) or [], c["ts"], c["price"], cost if cost is not None else pooled)
        return f

    def group(d):
        outs = [outcome(k, c) for k, c in d.items()]
        med = lambda key: statistics.median([o[key] for o in outs if o.get(key) is not None]) \
            if any(o.get(key) is not None for o in outs) else None  # noqa: E731
        lab = [o for o in outs if o["rug"] is not None]
        sims = [o["sim_net_pct"] for o in outs if o["sim_net_pct"] is not None]
        run, peak, dd = 0.0, 0.0, 0.0
        for x in sims:
            run += x
            peak = max(peak, run)
            dd = min(dd, run - peak)
        return {"n": len(d), "rug": sum(1 for o in lab if o["rug"]), "labelled": len(lab),
                "ret_5m_med": med("ret_5m_pct"), "ret_10m_med": med("ret_10m_pct"), "ret_30m_med": med("ret_30m_pct"),
                "ret_60m_med": med("ret_60m_pct"), "mfe_med": med("mfe_pct"), "mae_med": med("mae_pct"),
                "sim_expectancy_net_pct": round(statistics.fmean(sims), 2) if sims else None,
                "sim_drawdown_pct": round(dd, 2), "sample": "OK" if len(d) >= 30 else "INSUFFICIENT SAMPLE"}

    def split(d, key, buckets):
        out = {}
        for name, pred in buckets.items():
            out[name] = group({k: c for k, c in d.items() if pred(c.get(key))})
        return out
    base = lc["setup_pass"]
    res = {"pooled_total_slippage_pct": pooled,
           "money_flow": split(base, "money_flow_score", {">=50": lambda v: v is not None and v >= 50,
                                                          "<50": lambda v: v is not None and v < 50,
                                                          "UNKNOWN": lambda v: v is None}),
           "exit_liquidity": split(base, "exit_liquidity_risk", {"<50": lambda v: v is not None and v < 50,
                                                                 ">=50": lambda v: v is not None and v >= 50,
                                                                 "UNKNOWN": lambda v: v is None}),
           "entry_location": split(base, "entry_location", {n: (lambda n: lambda v: (v or "UNKNOWN") == n)(n) for n in
                                                            ("EARLY_ENTRY", "MID_MOVE", "EXTENDED", "PULLBACK",
                                                             "SECOND_WAVE", "UNKNOWN")}),
           "independent_buyers": split(base, "independent_buyer_score",
                                       {">=50": lambda v: v is not None and v >= 50, "<50": lambda v: v is not None and v < 50,
                                        "UNKNOWN": lambda v: v is None}),
           "ab": {"A_lifecycle": group(lc["lc_cand"]), "B_lifecycle+money_flow": group(lc["b_cand"]),
                  "C_lifecycle+mf+exit_liq": group(lc["c_cand"]), "NEW_EDGE_shadow": group(lc["edge_cand"]),
                  "D_old": group(lc["old_cand"])},
           "pre_shadow": group(lc["pre_would"]) | {"note": "hypothetical entry at the would-buy price, cost = pooled slippage"}}
    # fast stop-loss forensics (NEW buys, production positions)
    trades = []
    for fx in bot.forensics.values():
        if fx.get("lifecycle") != "NEW":
            continue
        p = next((x for x in list(bot.book.closed) + list(bot.book.positions.values()) if x.mint == fx["mint"]), None)
        if p is None:
            continue
        sh = fx.get("shadow_at_entry") or {}
        pre = (fx.get("pre_entry") or {}).get("T-30s") or {}
        ent = fx["snaps"][0]
        runup = (100 * (ent["price"] / pre["price"] - 1)) if pre.get("price") and ent.get("price") else None
        fill = next((f for f in fl if f.get("mint") == fx["mint"] and f.get("status") == "FILLED"), {})
        pnl = (100 * (p.realized_usd - p.cost_usd) / p.cost_usd) if p.status == "CLOSED" and p.cost_usd else None
        trades.append({"symbol": fx.get("symbol"), "fast_sl": bool(fx.get("fast_sl_flag")), "pnl_pct": pnl,
                       "win": pnl is not None and pnl > 0, "mfe_pct": p.path_log()["mfe_pct"],
                       "rug": (p.path_log()["mae_pct"] or 0) <= -80, "entry_location": sh.get("entry_location"),
                       "extension": sh.get("entry_extension"), "money_flow": sh.get("money_flow_score"),
                       "exit_liq": sh.get("exit_liquidity_risk"), "setup_score": fx.get("setup_score"),
                       "runup_30s_pct": None if runup is None else round(runup, 2),
                       "fill_vs_ref_pct": fill.get("fill_vs_ref_pct"), "impact_pct": fill.get("jupiter_impact_pct"),
                       "latency_slip_pct": fill.get("latency_slippage_pct"), "exit": p.exit_reason,
                       "held_s": p.path_log()["time_to_exit_s"]})
    patterns = {
        "entry EXTENDED": lambda t: None if t["entry_location"] in (None, "UNKNOWN") else t["entry_location"] == "EXTENDED",
        "extension_5m >= 50%": lambda t: None if t["extension"] is None else t["extension"] >= 50,
        "run-up 30s before entry >= 10%": lambda t: None if t["runup_30s_pct"] is None else t["runup_30s_pct"] >= 10,
        "fill above market ref >= 5%": lambda t: None if t["fill_vs_ref_pct"] is None else t["fill_vs_ref_pct"] >= 5,
        "Jupiter impact >= 2%": lambda t: None if t["impact_pct"] is None else t["impact_pct"] >= 2,
        "money flow UNKNOWN": lambda t: t["money_flow"] is None,
        "money flow < 50": lambda t: None if t["money_flow"] is None else t["money_flow"] < 50,
        "exit liquidity >= 50": lambda t: None if t["exit_liq"] is None else t["exit_liq"] >= 50}
    res["new_fast_sl"] = {"trades": trades, "fast_sl": sum(1 for t in trades if t["fast_sl"]),
                          "patterns": {n: pattern_effect(trades, pr) for n, pr in patterns.items()}}
    # slippage forensics: every fill attempt, simulated from the quote time net of its own estimated slippage
    sb = {}
    for f in fl:
        b = slippage_bucket(f.get("total_slippage_pct"))
        e = sb.setdefault(b, {"attempts": 0, "filled": 0, "sim": [], "by_lifecycle": collections.Counter()})
        e["attempts"] += 1
        e["filled"] += f.get("status") == "FILLED"
        e["by_lifecycle"][f.get("lifecycle") or "?"] += 1
        if f.get("quote_price") and f.get("quote_ts"):
            e["sim"].append(sim_net(paths.get(f["mint"]) or [], f["quote_ts"], f["quote_price"],
                                    f.get("total_slippage_pct") or pooled)[0])
    res["slippage_buckets"] = {b: {"attempts": e["attempts"], "fill_probability": round(e["filled"] / e["attempts"], 3),
                                   "sim_expectancy_net_pct": round(statistics.fmean([x for x in e["sim"] if x is not None]), 2)
                                   if any(x is not None for x in e["sim"]) else None,
                                   "by_lifecycle": dict(e["by_lifecycle"])} for b, e in sorted(sb.items())}
    res["walk_forward"] = walk_forward_plan([time.strftime("%Y-%m-%d", time.gmtime(c["ts"])) for c in base.values()])
    if bot.money_flow is not None:
        res["money_flow_collector"] = bot.money_flow.stats()
    return res


def lifecycle_section(bot, paths):
    """Per-lifecycle report (never one pooled number). Rug = MAE <= -80 % within the observed path."""
    lc = getattr(bot, "_audit_lc", None)
    if not lc:
        return None

    def outcome(k, c):
        f = fwd(paths.get(k), c["ts"], c["price"])
        mae = f.get("mae_pct")
        rug = True if mae is not None and mae <= -80 else (False if f.get("observed_s", 0) >= 600 else None)
        return f | {"rug": rug}                       # NON_RUG only after >= 10 min observed, else UNKNOWN

    names = ("NEW", "PRE_MIGRATION", "POST_MIGRATION", "UNKNOWN")
    out = {"tokens_by_lifecycle": dict(collections.Counter(v["lifecycle"] or "UNKNOWN" for v in lc["seen"].values())),
           "unknown_reasons": dict(collections.Counter(v["reason"] for v in lc["seen"].values()
                                                       if (v["lifecycle"] or "UNKNOWN") == "UNKNOWN").most_common(8))}
    fills = bot.fill_log
    by = {}
    for n in names:
        cands = {k: c for k, c in lc["lc_cand"].items() if c["lifecycle"] == n}
        outs = [outcome(k, c) for k, c in cands.items()]
        pos = [p for p in list(bot.book.positions.values()) + list(bot.book.closed) if (p.entry_lifecycle or "UNKNOWN") == n]
        closed = [p for p in pos if p.status == "CLOSED"]
        pnls = [p.realized_usd - p.cost_usd for p in closed]
        run, peak, dd = 0.0, 0.0, 0.0
        for x in sorted(closed, key=lambda p: p.closed_at or 0):
            run += x.realized_usd - x.cost_usd
            peak = max(peak, run)
            dd = min(dd, run - peak)
        fl = [f for f in fills if f.get("lifecycle") == n]
        rugs = [o for o in outs if o["rug"] is not None]
        by[n] = {"tokens": out["tokens_by_lifecycle"].get(n, 0), "setup_pass": sum(1 for c in lc["setup_pass"].values() if c["lifecycle"] == n),
                 "candidates": len(cands), "buys": len(pos), "fill_attempts": len(fl),
                 "fills": sum(1 for f in fl if f["status"] == "FILLED"),
                 "win": sum(1 for x in pnls if x > 0), "loss": sum(1 for x in pnls if x <= 0),
                 "rug_candidates": sum(1 for o in rugs if o["rug"]), "rug_labelled": len(rugs),
                 "mfe_pct": [p.path_log()["mfe_pct"] for p in pos], "mae_pct": [p.path_log()["mae_pct"] for p in pos],
                 "expectancy_usd": round(statistics.fmean(pnls), 2) if pnls else None,
                 "net_pnl_usd": round(sum(pnls) + sum((p.pnl_usd() or 0) for p in pos if p.status == "OPEN"), 2),
                 "max_drawdown_usd": round(dd, 2),
                 "candidate_mfe_median": statistics.median([o["mfe_pct"] for o in outs if o.get("mfe_pct") is not None])
                 if any(o.get("mfe_pct") is not None for o in outs) else None,
                 "sample": "INSUFFICIENT SAMPLE" if len(pos) < 30 else "OK"}
    out["by_lifecycle"] = by

    def engine_stats(d):
        outs = {k: outcome(k, c) for k, c in d.items()}
        lab = [o for o in outs.values() if o["rug"] is not None]
        return {"candidates": len(d), "rug": sum(1 for o in lab if o["rug"]), "labelled": len(lab),
                "big_movers_mfe_ge_30": sum(1 for o in outs.values() if (o.get("mfe_pct") or 0) >= 30)}
    old, exp, lcc = lc["old_cand"], lc["exp_cand"], lc["lc_cand"]
    out["old_vs_lifecycle"] = {
        "OLD": engine_stats(old), "EXPERIMENTAL": engine_stats(exp), "LIFECYCLE": engine_stats(lcc),
        "missed_by_lifecycle_mfe_ge_30": sum(1 for k, c in {**old, **exp}.items() if k not in lcc
                                             and (outcome(k, c).get("mfe_pct") or 0) >= 30),
        "missed_by_old_mfe_ge_30": sum(1 for k, c in lcc.items() if k not in old and (outcome(k, c).get("mfe_pct") or 0) >= 30)}

    def gated(d, pred=lambda c: True):
        sel = {k: c for k, c in d.items() if pred(c)}
        lab = [outcome(k, c) for k, c in sel.items()]
        lab = [o for o in lab if o["rug"] is not None]
        r = sum(1 for o in lab if o["rug"])
        return {"n": len(sel), "labelled": len(lab), "rug": r, "rug_rate": round(r / len(lab), 3) if lab else None}
    out["incremental_rug"] = {"EarlyScore_alone": gated(lc["es_pass"]), "Setup_alone": gated(lc["setup_pass"]),
                              "Setup_plus_AntiRug(shadow<50)": gated(lc["setup_pass"],
                                                                     lambda c: (c.get("shadow_antirug") or 0) < 50),
                              "note": "rug = MAE <= -80% within the observed window; descriptive, tiny samples"}
    liq = {}
    for k, c in lc["setup_pass"].items():
        o = outcome(k, c)
        key = f"{c['lifecycle']}|{c['liq_bucket']}"
        e = liq.setdefault(key, {"n": 0, "rug": 0, "labelled": 0, "mfe": [], "mae": []})
        e["n"] += 1
        if o["rug"] is not None:
            e["labelled"] += 1
            e["rug"] += o["rug"]
        if o.get("mfe_pct") is not None:
            e["mfe"].append(o["mfe_pct"])
            e["mae"].append(o["mae_pct"])
    out["liquidity_by_lifecycle"] = {k: {"n": v["n"], "rug": v["rug"], "labelled": v["labelled"],
                                         "mfe_median": statistics.median(v["mfe"]) if v["mfe"] else None,
                                         "mae_median": statistics.median(v["mae"]) if v["mae"] else None}
                                     for k, v in sorted(liq.items())}
    return out


def report(bot, eng, shocks, es_pass, liq_blocked, best, promo=None, cand_first=None, paths=None):
    out = {}
    fl = bot.fill_log
    out["slippage"] = {"jupiter_impact_pct": pctl(f.get("jupiter_impact_pct") for f in fl),
                       "latency_slippage_pct": pctl(f.get("latency_slippage_pct") for f in fl),
                       "total_slippage_pct": pctl(f.get("total_slippage_pct") for f in fl),
                       "max_allowed_pct": bot.cfg.max_slippage_pct, "model": bot.cfg.latency_slippage_model,
                       "models_used": dict(collections.Counter(f.get("latency_model") for f in fl))}
    ls = bot.latency_stats()
    drifts = sorted(max(0.0, d) for _, d in bot.latency_samples)
    p75 = drifts[min(len(drifts) - 1, int(0.75 * (len(drifts) - 1)))] * 100 if drifts else None
    attempts = [f for f in fl if f.get("jupiter_impact_pct") is not None]
    out["latency_real"] = ls | {
        "indicative_fill_rate_if_empirical_p75_pct": round(100 * sum(
            1 for f in attempts if f["jupiter_impact_pct"] + p75 <= bot.cfg.max_slippage_pct) / len(attempts), 1)
        if attempts and p75 is not None else None,
        "note": "indicative only: EMPIRICAL is used for fills only after empirical_min_samples samples"}
    spikes = [c for c in (classify_spike(fx) for fx in bot.forensics.values()) if c]
    paths_risk = []
    for fx in bot.forensics.values():
        snaps = fx["snaps"] + ([fx["exit"]] if fx.get("exit") else [])
        st_types = [x.get("spike_type") for x in snaps if x.get("spike_type")]
        paths_risk.append({"symbol": fx["symbol"], "buy_ts": fx["entry_ts"],
                           "spike_type": st_types[0] if st_types else None,
                           "path": [{"at": x["at"], "risk": x["risk"], "risk_new": x.get("risk_new"),
                                     "risk_data_refresh": x.get("risk_data_refresh"),
                                     "risk_data_stale": x.get("risk_data_stale"),
                                     "holders_stamp": x.get("holders_stamp"), "dev_stamp": x.get("dev_stamp"),
                                     "changed": x.get("changed")} for x in snaps]})
    out["risk_attribution"] = {
        "buys": len(paths_risk),
        "spike_types": dict(collections.Counter(r["spike_type"] for r in paths_risk if r["spike_type"])),
        "risk_spike_exits": sum(1 for p in bot.book.closed if p.exit_reason == "risk_spike"),
        "paths": paths_risk}
    out["risk_spike_forensics"] = {"buys_tracked": len(bot.forensics),
                                   "spikes": len(spikes), "classes": dict(collections.Counter(c["class"] for c in spikes)),
                                   "details": spikes}
    out["entry_risk_buffer"] = {"max_entry_risk": bot.cfg.entry_max_risk,
                                "tokens_blocked_any": getattr(bot, "_audit_buffer", {}).get("any"),
                                "tokens_where_buffer_was_the_only_blocker": getattr(bot, "_audit_buffer", {}).get("only_blocker"),
                                "blocked_at_entry": len(bot.buffer_blocks),
                                "entry_blocks": bot.buffer_blocks[-20:]}
    mins = getattr(bot, "_audit_minutes", None)
    fs = bot.fast_lane_stats()
    out["fast_lane_rate"] = {"calls": fs.get("fast_getasset_calls"),
                             "calls_per_min": round(fs.get("fast_getasset_calls", 0) / mins, 2) if mins else None,
                             "limit_per_min": fs.get("per_min_limit"), "throttled": fs.get("throttled_by_quota_governor")}
    promo, cand_first, paths = promo or {}, cand_first or {}, paths or {}
    fills = bot.fill_log
    ok = [f for f in fills if f["status"] == "FILLED"]
    fail = [f for f in fills if f["status"] != "FILLED"]

    def avg(xs, k):
        return round(sum(x[k] for x in xs) / len(xs), 2) if xs else None
    out["fills"] = {"attempts": len(fills), "filled": len(ok), "failed": len(fail),
                    "success_rate_pct": round(100 * len(ok) / len(fills), 1) if fills else None,
                    "failure_reasons": dict(collections.Counter(f["fail_reason"].split(" (")[0] for f in fail)),
                    "avg_jupiter_impact_pct": avg(fills, "jupiter_impact_pct"),
                    "avg_latency_slippage_pct": avg(fills, "latency_slippage_pct"),
                    "avg_total_pct": avg(fills, "total_slippage_pct"), "max_allowed_pct": bot.cfg.max_slippage_pct,
                    "failed_avg_impact_pct": avg(fail, "jupiter_impact_pct"),
                    "failed_avg_latency_pct": avg(fail, "latency_slippage_pct")}
    pr = list(promo.values())
    gate_counts = collections.Counter(gk for r in pr for gk, gv in r["gates_ever"].items() if gv)
    all_gates = sum(1 for r in pr if all(v for k, v in r["gates_ever"].items() if k != "candidate"))
    bought = {e.mint for e in bot.book.executions if e.side == "BUY" and e.status == "FILLED"}
    fs = [fwd(paths.get(r["ca"]), r["ts"], r["price"]) for r in pr]
    summary = {}
    for k in ("ret_1m_pct", "ret_5m_pct", "ret_10m_pct", "ret_15m_pct", "mfe_pct", "mae_pct"):
        v = [x[k] for x in fs if x.get(k) is not None]
        if v:
            summary[k] = {"n": len(v), "median": statistics.median(v), "mean": round(statistics.fmean(v), 1)}
    out["liquidity_ab"] = {
        "liquidity_only_promotions": len(pr),
        "promoted_meeting_each_gate_ever": dict(gate_counts),
        "promoted_meeting_all_gates_ever": all_gates,
        "promoted_became_candidate": sum(1 for r in pr if r["ca"] in cand_first),
        "promoted_paper_buy": sum(1 for r in pr if r["ca"] in bought),
        "promoted_forward_summary": summary,
        "promoted_forward": [{"symbol": r["symbol"], "age_s": r["age_s"], "real_usd": r["real_usd"],
                              "equivalent_usd": r["equivalent_usd"], "gates": r["gates_ever"],
                              "blocked_by_first": r["blocked_by_first"], **f} for r, f in zip(pr, fs)][:60]}
    sims = []
    for k, c in cand_first.items():
        f = next((x for x in fills if x["mint"] == k), None)
        cost = ((f["jupiter_impact_pct"] + f["latency_slippage_pct"]) / 100) if f else 0.03
        pnl, how = sim_trade(paths.get(k), c["ts"], c["price"], cost)
        sims.append({"symbol": c["symbol"], "liquidity_promoted": c["liquidity_promoted"], "model": c["liquidity_model"],
                     "bought": k in bought, "sim_pnl_pct": pnl, "exit": how, **fwd(paths.get(k), c["ts"], c["price"])})
    out["candidates_simulated"] = sims
    out["lifecycle"] = lifecycle_section(bot, paths)
    out["v11"] = v11_section(bot, paths)
    out["v12"] = v12_section(bot)
    out["v13"] = v13_section(bot)
    rugs = []
    for k, c in cand_first.items():
        f = fwd(paths.get(k), c["ts"], c["price"])
        worst = f.get("mae_pct")
        if worst is not None and worst <= -80:
            rugs.append({"symbol": c["symbol"], "mae_pct": worst, "liquidity_model": c["liquidity_model"],
                         "risk_at_candidate": c["risk"], "prior_risk": c["prior_risk"],
                         "risk_factors_at_candidate": c["risk_factors"], "holder_status": c["holder_status"],
                         "dev_verified": c["dev_verified"], "early_score": c["early_score"]})
    out["rug_research"] = {"candidates_with_mae_le_-80pct": len(rugs), "cases": rugs,
                           "note": "research examples for risk-component predictive power; no rule changed"}
    st_counts = collections.Counter(f.get("candidate_status") for f in bot.fill_log)
    out["candidate_status"] = dict(st_counts) | {"no_route_quotes": bot.quote_stats.get("NO_ROUTE", 0)}
    out["risk_spike_after_entry"] = [{"symbol": p.symbol, "after_s": round((p.closed_at or 0) - p.opened_at),
                                      "net": round(p.realized_usd - p.cost_usd, 2)}
                                     for p in bot.book.closed if p.exit_reason == "risk_spike"]
    cats = collections.Counter(e["category"] for e in shocks.values())
    n = len(shocks)
    out["liquidity_shock"] = {"events_old_measure": n, "categories": dict(cats),
                              "share_pct": {k: round(100 * v / n, 1) for k, v in cats.items()} if n else {},
                              "false_positive": n - cats.get("REAL_SHOCK", 0),
                              "false_positive_rate_pct": round(100 * (n - cats.get("REAL_SHOCK", 0)) / n, 1) if n else None,
                              "examples": list(shocks.values())[:12]}
    lb = list(liq_blocked.values())
    curve = [r for r in lb if r["curve"]]
    out["liquidity_blocked"] = {
        "tokens": len(lb), "bonding_curve": len(curve), "amm": len(lb) - len(curve),
        "curve_real_sol_usd_median": statistics.median([r["liquidity"] for r in curve if r["liquidity"]]) if curve else None,
        "curve_amm_equivalent_median": statistics.median([r["amm_equiv"] for r in curve if r["amm_equiv"]]) if any(r["amm_equiv"] for r in curve) else None,
        "curve_amm_equivalent_ge_10k": sum(1 for r in curve if (r["amm_equiv"] or 0) >= 10_000),
        "amm_below_10k": sum(1 for r in lb if not r["curve"])}
    rows = list(es_pass.values())
    cls = collections.Counter(c for r in rows for c in r["decomposition"]["classification"])
    low = [r for r in rows if (r["opportunity"] or 0) < 65]
    out["opportunity"] = {"earlyscore_pass_tokens": len(rows), "opp_lt_65": len(low),
                          "classification_counts": dict(cls),
                          "primary": dict(collections.Counter(r["decomposition"]["classification"][0] for r in low))}
    cross = collections.defaultdict(list)
    for r in best.values():
        lok = not any(b in ("hard:liquidity", "gate_unknown:liquidity") for b in r["blocked_by"] or [])
        ok = (r["opportunity"] or 0) >= 65
        cross[("Opp>=65" if ok else "Opp<65", "Liquidity OK" if lok else "Liquidity blocked")].append(r)

    def summ(v):
        g = lambda k: [x[k] for x in v if x[k] is not None]  # noqa: E731
        return {"tokens": len(v), "early_score_med": round(statistics.median(g("early_score")), 2) if g("early_score") else None,
                "risk_med": statistics.median(g("risk")) if g("risk") else None,
                "conf_med": statistics.median(g("confidence")) if g("confidence") else None,
                "age": dict(collections.Counter(x["bucket"] for x in v))}
    out["cross_table"] = {f"{a} | {b}": summ(v) for (a, b), v in sorted(cross.items())}
    out["es_pass_table"] = [{k: v for k, v in r.items() if k != "decomposition"} | {
        "opp_class": r["decomposition"]["classification"], "opp_components": r["decomposition"]["components"],
        "risk_by_cat": r["decomposition"]["risk_points_by_category"],
        "counterfactual": r["decomposition"]["counterfactual_opportunity"],
        "unavailable": r["decomposition"]["unavailable_factors"]} for r in rows]
    a = bot.audit.report(top=50)
    out["run"] = {k: a["stats"].get(k) for k in ("discovery", "pre_early", "early_watch", "early_score_pass",
                                                  "old_candidates", "new_candidates", "liquidity_promoted",
                                                  "liquidity_promoted_candidates", "trade_candidate", "buy_candidate",
                                                  "quote_ok", "fill_ok", "fill_fail", "buy_executed",
                                                  "buy_simulated_noquote", "buy_skipped")}
    out["jupiter"] = bot.quote_stats
    out["fast_lane"] = bot.fast_lane_stats()
    out["helius"] = (eng.feeds().get("helius") or {}).get("credits")
    st = bot.book.stats()
    out["paper"] = {"open": [dict(mint=p.mint, symbol=p.symbol, entry=p.entry_price, size=round(p.cost_usd, 2),
                                  setup=p.setup, path=p.path_log()) for p in bot.book.positions.values()],
                    "closed": [dict(mint=p.mint, symbol=p.symbol, entry=p.entry_price, exit=p.exit_reason,
                                    net=round(p.realized_usd - p.cost_usd, 2), path=p.path_log()) for p in bot.book.closed],
                    "net_pnl": st["net_pnl"], "executions": [dict(side=e.side, mint=e.mint, status=e.status,
                                                                   route=e.route, usd=e.usd_in) for e in bot.book.executions]}
    out["activity"] = [f"{time.strftime('%H:%M:%S', time.localtime(x.ts))} {x.kind} {x.symbol} {x.text[:160]}"
                       for x in bot.activity if x.text.startswith(("BUY", "SELL")) or x.kind in ("BUY", "SELL", "FAILED")][-40:]
    return out


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=15)
    ap.add_argument("--out", default="")
    ap.add_argument("--research-db", default="")
    a = ap.parse_args()
    res = report(*asyncio.run(run(a.minutes, a.research_db)))
    print(json.dumps({k: v for k, v in res.items() if k not in ("es_pass_table",)}, indent=1, default=str)[:20000])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
