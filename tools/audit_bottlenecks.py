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


async def run(minutes):
    eng = ScannerEngine(Settings(), Database(Path(tempfile.mkdtemp()) / "a.db"), keys=ApiKeys.from_env(),
                        on_log=lambda m: print(m, flush=True) if "PIPELINE" in m else None)
    bot = PaperBot(eng, TradingConfig(experimental=True, latency_probe=True, latency_slippage_model="AUTO"))
    bot.jupiter = JupiterQuotes(eng.http)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(eng.run()), asyncio.create_task(bot.run(stop))]
    shocks, es_pass, liq_blocked, best = {}, {}, {}, {}
    promo, cand_first, paths = {}, {}, {}
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
            if (k in promo or k in cand_first) and px:
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
    a = ap.parse_args()
    res = report(*asyncio.run(run(a.minutes)))
    print(json.dumps({k: v for k, v in res.items() if k not in ("es_pass_table",)}, indent=1, default=str)[:20000])
    if a.out:
        Path(a.out).write_text(json.dumps(res, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
