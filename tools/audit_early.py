"""Early Signal audit — runs the real scanner for N minutes, then explains every early-signal component
for the top tokens using the engine's in-memory history (exact values the engine used).

  python tools/audit_early.py --minutes 13 --top 20

Scoring is NOT modified. Output: console report + docs/audit/early_audit_<time>.json
"""
import argparse
import asyncio
import json
import os
import statistics
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from core.config import DATA_DIR, Settings  # noqa: E402
from database.db import Database  # noqa: E402
from intel.early_signal import BASELINE_LOW_USD, BASELINE_MULT, WEIGHTS  # noqa: E402
from scanner.engine import ScannerEngine  # noqa: E402

COLS = [("volume_accel", "VOLUME_ACCELERATION"), ("holder_accel", "HOLDER_ACCELERATION"),
        ("buy_pressure", "BUY_PRESSURE"), ("liquidity_growth", "LIQUIDITY_GROWTH"),
        ("txn_accel", "TRANSACTION_ACCELERATION"), ("mc_accel", "MC_ACCELERATION"),
        ("smart_money", "SMART_MONEY"), ("whale_accum", "WHALE"), ("social_accel", "SOCIAL_ACCELERATION"),
        ("narrative_accel", "NARRATIVE")]


def usd(v):
    return "n/a" if v is None else f"${v:,.0f}"


def component_view(x, denom):
    """points actually contributed + what it would contribute if it fired."""
    if x.fired is None:
        return {"status": "UNKNOWN", "source": x.source or "—", "reason": x.raw.get("missing") or x.note,
                "raw": x.raw}
    pts = round(100 * x.weight * (x.strength or 0) / denom, 1) if denom else 0
    return {"status": "FIRED" if x.fired else "NOT FIRED", "points": pts if x.fired else 0.0,
            "points_if_fired": pts, "strength": x.strength, "weight": x.weight, "value": x.value,
            "rule": x.raw.get("rule"), "raw": {k: v for k, v in x.raw.items() if k != "rule"}}


def baseline_info(h, now, cur_vol):
    pts = [p.vol_5m for p in h.window(1800, 600, now) if p.vol_5m is not None]
    if len(pts) < 3:
        return {"baseline_points": len(pts), "baseline_median": None, "transition": None,
                "reason": "fewer than 3 snapshots 10-30 min ago"}
    med = statistics.median(pts)
    return {"baseline_points": len(pts), "baseline_median": med, "current_vol_5m": cur_vol,
            "multiple": round(cur_vol / med, 2) if med and cur_vol is not None else None,
            "transition": med < BASELINE_LOW_USD and cur_vol is not None and cur_vol >= BASELINE_MULT * max(med, 1),
            "rule": f"median < ${BASELINE_LOW_USD:,} and now >= {BASELINE_MULT}x median"}


def holder_view(st, h) -> dict:
    """Holder / whale / top10 facts for the report (reporting only)."""
    hs, hi, wi = st.holders, st.holder_intel, st.whale_intel
    if not hs:
        return {"status": "UNKNOWN", "holder_status": st.holder_status, "reason": st.holder_error or st.holder_status,
                "snapshots": len(h.holders)}
    return {"status": "OK", "holders": hs.holder_count, "top10_pct": hs.top10_pct, "top20_pct": hs.top20_pct,
            "source": hs.source, "snapshots": len(h.holders),
            "growth_5m_pct": hi.growth_5m_pct if hi else None, "growth_prev_5m_pct": hi.prev_growth_5m_pct if hi else None,
            "holder_quality": hi.organic if hi else None, "churn_pct": hi.churn_pct if hi else None,
            "whale_state": wi.state if wi else None, "whale_count": wi.whale_count if wi else None,
            "whale_pct": wi.whale_pct if wi else None, "whale_delta_pct": wi.delta_pct if wi else None}


def audit(engine: ScannerEngine, top_n: int) -> dict:
    now = time.time()
    rows, stats = [], {k: {"computable": 0, "fired": 0, "near_miss": 0, "unknown": 0} for k, _ in COLS}
    eligible = transition_true = three_fired = strength50 = invalid = early_true = suppressed_n = 0
    dq_counts = {"VALID": 0, "PARTIAL": 0, "INVALID": 0}
    early_unknown = sum(1 for s in engine.tracked.values() if not s.early or s.early.strength is None)
    unknown_reasons = {}
    for s in engine.tracked.values():
        if s.early and s.early.strength is None:
            unknown_reasons[s.early.note] = unknown_reasons.get(s.early.note, 0) + 1
    for s in engine.tracked.values():
        dq_counts[s.dq_status] = dq_counts.get(s.dq_status, 0) + 1
    with_holders = sum(1 for s in engine.tracked.values() if s.holders)
    holder_status = {}
    for s in engine.tracked.values():
        holder_status[s.holder_status or "?"] = holder_status.get(s.holder_status or "?", 0) + 1
    for st in engine.tracked.values():
        e = st.early
        if not e or e.strength is None:
            continue
        eligible += 1
        invalid += st.dq_status == "INVALID"
        transition_true += bool(e.transition)
        three_fired += e.fired_count >= 3
        strength50 += e.strength >= 50
        early_true += bool(e.is_early)
        suppressed_n += bool(e.suppressed)
        for x in e.signals:
            s = stats[x.key]
            if x.fired is None:
                s["unknown"] += 1
            else:
                s["computable"] += 1
                s["fired"] += bool(x.fired)
                s["near_miss"] += (not x.fired) and (x.strength or 0) > 0
    ranked = sorted((s for s in engine.tracked.values() if s.early and s.early.strength is not None),
                    key=lambda s: -s.early.strength)[:top_n]
    for st in ranked:
        e, h = st.early, engine.history.get(st.mint)
        denom = sum(x.weight for x in e.signals if x.fired is not None)
        cur = h.latest()
        rows.append({
            "token": f"${st.info.symbol}", "mint": st.mint, "data_quality": st.dq_status,
            "lifecycle": st.lifecycle, "age_min": round(st.age_minutes or 0, 1),
            "history_min": e.history_min, "EARLY_SIGNAL_SCORE": e.strength, "is_early": e.is_early,
            "fired_count": e.fired_count, "coverage_pct": e.coverage_pct, "denominator_weight": denom,
            "groups_computable": e.groups_computable,
            "suppressed_by": e.suppressed, "data_breaks": len(h.breaks),
            "transition": baseline_info(h, now, cur.vol_5m if cur else None),
            "holders": holder_view(st, h),
            "components": {label: component_view(next(x for x in e.signals if x.key == key), denom)
                           for key, label in COLS},
        })
    return {"generated_at": time.strftime("%Y-%m-%d %H:%M:%S"), "tracked": len(engine.tracked),
            "eligible_with_10m_history": eligible, "eligible_invalid": invalid,
            "transition_true": transition_true, "three_or_more_fired": three_fired, "strength_ge_50": strength50,
            "component_stats": stats, "weights": WEIGHTS, "tracked_with_holders": with_holders,
            "holder_status_counts": holder_status, "helius": engine.helius_state,
            "data_quality_counts": dq_counts, "early_unknown": early_unknown, "early_true": early_true,
            "early_suppressed": suppressed_n, "early_unknown_reasons": unknown_reasons,
            "data_breaks_total": sum(len(engine.history.get(m).breaks) for m in engine.tracked), "top": rows}


def print_report(rep: dict) -> None:
    print(f"\n=== EARLY SIGNAL AUDIT {rep['generated_at']} — tracked {rep['tracked']}, "
          f"with >=10 min history {rep['eligible_with_10m_history']} ===")
    print(f"transition TRUE: {rep['transition_true']} · >=3 signals fired: {rep['three_or_more_fired']} · "
          f"strength>=50: {rep['strength_ge_50']} · INVALID among eligible: {rep['eligible_invalid']}")
    print("\nComponent availability across all eligible tokens:")
    for key, label in COLS:
        s = rep["component_stats"][key]
        print(f"  {label:26s} computable {s['computable']:4d} · fired {s['fired']:3d} · "
              f"near-miss {s['near_miss']:4d} · UNKNOWN {s['unknown']:4d}")
    for r in rep["top"]:
        t = r["transition"]
        print(f"\n--- {r['token']} {r['mint']}  EARLY {r['EARLY_SIGNAL_SCORE']} (is_early={r['is_early']}) "
              f"DQ {r['data_quality']} · age {r['age_min']}m · history {r['history_min']}m · "
              f"fired {r['fired_count']} · groups {r['groups_computable']}/7 · denominator weight {r['denominator_weight']}"
              + (f" · SUPPRESSED: {'; '.join(r['suppressed_by'])}" if r["suppressed_by"] else "")
              + (f" · data breaks {r['data_breaks']}" if r["data_breaks"] else ""))
        print(f"    transition: baseline median {usd(t.get('baseline_median'))} over {t['baseline_points']} pts, "
              f"now {usd(t.get('current_vol_5m'))} (x{t.get('multiple')}) -> {t['transition']}"
              + (f"  [{t['reason']}]" if t.get("reason") else ""))
        hv = r["holders"]
        if hv["status"] == "OK":
            print(f"    HOLDERS: {hv['holders']} (snapshots {hv['snapshots']}) · top10 {hv['top10_pct']}% · "
                  f"growth 5m {hv['growth_5m_pct']} (prev {hv['growth_prev_5m_pct']}) · quality {hv['holder_quality']} · "
                  f"churn {hv['churn_pct']} · WHALE {hv['whale_state']} n={hv['whale_count']} "
                  f"held={hv['whale_pct']}% Δ={hv['whale_delta_pct']}")
        else:
            print(f"    HOLDERS: UNKNOWN | SOURCE: Helius DAS | REASON: {hv['reason']} (snapshots {hv['snapshots']})")
        for label, c in r["components"].items():
            if c["status"] == "UNKNOWN":
                print(f"    {label:26s} UNKNOWN | SOURCE: {c['source']} | REASON: {c['reason']}")
            else:
                raw = ", ".join(f"{k}={v}" for k, v in c["raw"].items())
                print(f"    {label:26s} {c['status']:9s} points {c['points']:5.1f} (if fired {c['points_if_fired']:5.1f}) "
                      f"| {raw} | rule: {c['rule']}")


async def main(minutes: float, top: int) -> None:
    db = Database(os.path.join(DATA_DIR, "hunter.db"))
    eng = ScannerEngine(Settings.load(), db, on_log=lambda m: None)
    task = asyncio.create_task(eng.run())
    t0 = time.time()
    while time.time() - t0 < minutes * 60:
        await asyncio.sleep(30)
        print(f"  … {int(time.time()-t0)}s, tracking {len(eng.tracked)}, cycle {eng.cycle_no}", flush=True)
    rep = audit(eng, top)
    eng.stop()
    await task
    print_report(rep)
    out = os.path.join(ROOT, "docs", "audit")
    os.makedirs(out, exist_ok=True)
    path = os.path.join(out, f"early_audit_{time.strftime('%Y%m%d_%H%M')}.json")
    with open(path, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1, ensure_ascii=False, default=str)
    print(f"\nJSON: {path}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--minutes", type=float, default=13)
    ap.add_argument("--top", type=int, default=20)
    a = ap.parse_args()
    asyncio.run(main(a.minutes, a.top))
