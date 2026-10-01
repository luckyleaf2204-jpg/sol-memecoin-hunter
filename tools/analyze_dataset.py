"""Predictive-power analysis of the research dataset (Implementation Spec, Part 2). Read-only; standard library only.

usage: python tools/analyze_dataset.py [--db data/research.db] [--out report.md] [--cost 4]

Method (no look-ahead): every rule is evaluated on TIMELINE snapshots (+1m, +2m, +5m, +10m after discovery); the
outcome is the price path AFTER that snapshot (price_path), so a rule only ever "sees" what was known at that time.
Labels:  A  max return within 5 min >= +40 % AND worst return within 5 min > -25 %
         B  return at +10 min >= +20 % after round-trip cost (--cost, default 4 %)
         C  MFE / |MAE| within 10 min (continuous; reported as its mean)
Per rule: coverage, precision P(A|pass), recall P(pass|A), lift, E[ret10m | pass / fail / unknown], false-negative
cost (= E[ret|fail]: high means the rule throws edge away), conditional lift inside age x liquidity x holder strata
(does the rule add information beyond those?), and median age when it first passes.
Experiments: 1 marginal value (single-feature AUC + conditional lift) · 2 redundancy (phi correlation) ·
3 latency (time to Early TRUE vs time to +40 %) · 4 holder threshold N = 20/30/40/50/80 · 5 Risk by age bucket ·
execution: Jupiter quote outcome vs simulated P&L (missed edge).
Small samples are flagged: nothing here is significant below a few hundred labelled tokens.
"""
from __future__ import annotations

import argparse
import bisect
import json
import math
import sqlite3
import statistics
from collections import defaultdict
from pathlib import Path

ANCHOR_OFFSETS = (60, 120, 300, 600)
MIN_N = 30


def load(db_path: str):
    db = sqlite3.connect(f"file:{Path(db_path).as_posix()}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    disc = {r["ca"]: dict(r) for r in db.execute("SELECT * FROM token_discovery")}
    snaps = [dict(r) for r in db.execute("SELECT * FROM token_snapshots ORDER BY ca, ts")]
    path = defaultdict(list)
    for ca, ts, p in db.execute("SELECT ca, ts, price FROM price_path WHERE price > 0 ORDER BY ca, ts"):
        path[ca].append((ts, p))
    cands = [dict(r) for r in db.execute("SELECT * FROM candidates")]
    fwd = [dict(r) for r in db.execute("SELECT * FROM forward_returns")]
    return disc, snaps, path, cands, fwd


def outcome(path, t0, p0, cost):
    """Forward outcome from (t0, p0) using only later points."""
    if not path or not p0:
        return None
    ts = [x[0] for x in path]
    i = bisect.bisect_right(ts, t0)
    w5 = [p / p0 - 1 for t, p in path[i:] if t <= t0 + 300]
    w10 = [p / p0 - 1 for t, p in path[i:] if t <= t0 + 600]
    j = bisect.bisect_left(ts, t0 + 600)
    end = None
    if j < len(path) and path[j][0] - (t0 + 600) <= 150:
        end = path[j][1] / p0 - 1
    elif w10 and (t0 + 600) - path[i + len(w10) - 1][0] <= 60:
        end = w10[-1]
    if not w5 and end is None:
        return None
    mfe5, mae5 = (max(w5), min(w5)) if w5 else (None, None)
    mfe10, mae10 = (max(w10), min(w10)) if w10 else (None, None)
    return {"A": int(mfe5 is not None and mfe5 >= 0.40 and mae5 > -0.25),
            "B": None if end is None else int(end - cost >= 0.20),
            "C": None if not w10 or mae10 is None else (mfe10 / abs(mae10) if mae10 < 0 else None),
            "ret10": end}


def anchors(snaps, path, cost):
    """One row per timeline snapshot at the anchor offsets, with its forward outcome."""
    out = []
    for s in snaps:
        if s["reason"] != "timeline" or s["price_usd"] is None:
            continue
        o = outcome(path.get(s["ca"]), s["ts"], s["price_usd"], cost)
        if o is None:
            continue
        out.append(s | o)
    return out


def bucket_age(a):
    return "<60s" if a < 60 else "1-3m" if a < 180 else "3-10m" if a < 600 else ">10m"


def strata(r):
    liq = r["liq_usd"]
    h = r["holders"]
    return (bucket_age(r["age_sec"] or 0), "liq?" if liq is None else "liq<8k" if liq < 8000 else "liq<30k" if liq < 30000 else "liq30k+",
            "h?" if h is None else "h<20" if h < 20 else "h<50" if h < 50 else "h50+")


def mean(xs):
    xs = [x for x in xs if x is not None]
    return statistics.fmean(xs) if xs else None


def pct(x):
    return "—" if x is None else f"{100 * x:+.1f}%"


def auc(pos, neg):
    """Mann-Whitney AUC of a numeric feature (higher = predicts positive)."""
    if len(pos) < 5 or len(neg) < 5:
        return None
    allv = sorted([(v, 1) for v in pos] + [(v, 0) for v in neg])
    rank, i, r_pos = 1, 0, 0.0
    while i < len(allv):
        j = i
        while j < len(allv) and allv[j][0] == allv[i][0]:
            j += 1
        avg = (rank + rank + (j - i) - 1) / 2
        r_pos += avg * sum(1 for k in range(i, j) if allv[k][1])
        rank += j - i
        i = j
    return (r_pos - len(pos) * (len(pos) + 1) / 2) / (len(pos) * len(neg))


RULES = {
    **{f"d{i}": (lambda r, i=i: None if r[f"d{i}"] in (None, -1) else bool(r[f"d{i}"])) for i in range(1, 9)},
    **{f"s_{k}": (lambda r, k=k: None if r[f"s_{k}"] in (None, -1) else bool(r[f"s_{k}"]))
       for k in ("volume_accel", "txn_accel", "buy_pressure", "mc_accel", "holder_accel", "liquidity_growth", "whale_accum")},
    "early_signal_true": lambda r: None if r["early_signal"] in (None, "unknown") else r["early_signal"] == "true",
    "holders>=50": lambda r: None if r["holders"] is None else r["holders"] >= 50,
    "top10<=35%": lambda r: None if r["top10_pct"] is None else r["top10_pct"] <= 35,
    "risk==0": lambda r: None if r["risk_score"] is None else r["risk_score"] == 0,
    "risk<=60": lambda r: None if r["risk_score"] is None else r["risk_score"] <= 60,
    "vet_pass": lambda r: None if r["vet_status"] in (None, "pending") else r["vet_status"] == "pass",
    "opportunity>=65": lambda r: None if r["opportunity"] is None else r["opportunity"] >= 65,
    "confidence>=60": lambda r: None if r["confidence"] is None else r["confidence"] >= 60,
    "buy_pressure>=55%": lambda r: None if r["buy_pressure_pct"] is None else r["buy_pressure_pct"] >= 55,
    "authorities_revoked": lambda r: None if r["authority_mint"] is None else (r["authority_mint"] == "revoked"
                                                                               and r["authority_freeze"] == "revoked"),
}


def rule_table(rows, label="A"):
    base = [r for r in rows if r[label] is not None]
    if not base:
        return [], None
    base_rate = mean([r[label] for r in base])
    npos = sum(r[label] for r in base)
    out = []
    for name, f in RULES.items():
        g = {"pass": [], "fail": [], "unknown": []}
        for r in base:
            v = f(r)
            g["unknown" if v is None else "pass" if v else "fail"].append(r)
        known = len(g["pass"]) + len(g["fail"])
        prec = mean([r[label] for r in g["pass"]]) if g["pass"] else None
        rec = (sum(r[label] for r in g["pass"]) / npos) if npos else None
        # conditional lift: precision ratio inside age x liquidity x holder strata, weighted by stratum size
        st = defaultdict(lambda: {"pass": [], "all": []})
        for r in g["pass"] + g["fail"]:
            k = strata(r)
            st[k]["all"].append(r[label])
            if f(r):
                st[k]["pass"].append(r[label])
        num = den = 0.0
        for v in st.values():
            if len(v["pass"]) >= 3 and v["all"] and mean(v["all"]):
                num += len(v["all"]) * (mean(v["pass"]) / mean(v["all"]))
                den += len(v["all"])
        first_pass = defaultdict(lambda: None)
        for r in sorted(g["pass"], key=lambda r: r["ts"]):
            if first_pass[r["ca"]] is None:
                first_pass[r["ca"]] = r["age_sec"]
        tp = [v for v in first_pass.values() if v is not None]
        out.append({"rule": name, "n": len(base), "coverage": known / len(base), "n_pass": len(g["pass"]),
                    "n_fail": len(g["fail"]), "precision": prec, "recall": rec,
                    "lift": (prec / base_rate) if prec is not None and base_rate else None,
                    "cond_lift": (num / den) if den else None,
                    "E_pass": mean([r["ret10"] for r in g["pass"]]), "E_fail": mean([r["ret10"] for r in g["fail"]]),
                    "E_unknown": mean([r["ret10"] for r in g["unknown"]]),
                    "time_to_pass_s": statistics.median(tp) if tp else None,
                    "inverted": (mean([r["ret10"] for r in g["fail"]]) or -9) > (mean([r["ret10"] for r in g["pass"]]) or 9),
                    "small": len(g["pass"]) < MIN_N or len(g["fail"]) < MIN_N})
    return out, base_rate


def phi(rows, a, b):
    xs = [(RULES[a](r), RULES[b](r)) for r in rows]
    xs = [(int(x), int(y)) for x, y in xs if x is not None and y is not None]
    if len(xs) < MIN_N:
        return None
    n11 = sum(1 for x, y in xs if x and y); n10 = sum(1 for x, y in xs if x and not y)  # noqa: E702
    n01 = sum(1 for x, y in xs if not x and y); n00 = len(xs) - n11 - n10 - n01  # noqa: E702
    d = math.sqrt((n11 + n10) * (n01 + n00) * (n11 + n01) * (n10 + n00))
    return (n11 * n00 - n10 * n01) / d if d else None


def latency(disc, path, cost):
    pos = []
    for ca, d in disc.items():
        p0, t0 = d["initial_price"], d["initial_price_ts"]
        pts = path.get(ca)
        if not p0 or not pts:
            continue
        o = outcome(pts, t0, p0, cost)
        if not o or not o["A"]:
            continue
        t40 = next((t for t, p in pts if t >= t0 and p >= p0 * 1.4), None)
        t30 = next((t for t, p in pts if t >= t0 and p >= p0 * 1.3), None)
        et = d["first_early_true_ts"]
        pos.append({"t30": t30 - d["first_seen_ts"] if t30 else None, "t40": t40 - d["first_seen_ts"] if t40 else None,
                    "early": et - d["first_seen_ts"] if et else None,
                    "complete": d["early_complete_ts"] - d["first_seen_ts"] if d["early_complete_ts"] else None})
    med = lambda k: statistics.median([x[k] for x in pos if x[k] is not None]) if any(x[k] is not None for x in pos) else None  # noqa: E731
    late = sum(1 for x in pos if x["t40"] is not None and (x["early"] is None or x["early"] > x["t40"]))
    return {"positive_tokens_A": len(pos), "median_time_to_+30%_s": med("t30"), "median_time_to_+40%_s": med("t40"),
            "median_time_to_early_true_s": med("early"), "median_time_to_7of7_groups_s": med("complete"),
            "early_true_never_or_after_+40%": late, "share_late": (late / len(pos)) if pos else None}


def holder_curve(rows, label="A"):
    out = []
    at = [r for r in rows if r[label] is not None and r["holders"] is not None and r["age_sec"] is not None]
    npos = sum(r[label] for r in at)
    for n in (20, 30, 40, 50, 80):
        p = [r for r in at if r["holders"] >= n]
        out.append({"N": n, "trades": len(p), "precision": mean([r[label] for r in p]),
                    "recall": (sum(r[label] for r in p) / npos) if npos else None,
                    "expectancy": mean([r["ret10"] for r in p])})
    return out, len(at)


def risk_by_age(rows):
    out = defaultdict(lambda: defaultdict(list))
    for r in rows:
        if r["risk_score"] is None or r["ret10"] is None:
            continue
        rs = r["risk_score"]
        rb = "risk=0" if rs == 0 else "1-9" if rs < 10 else "10-30" if rs <= 30 else "31-40" if rs <= 40 else ">40"
        out[bucket_age(r["age_sec"] or 0)][rb].append(r["ret10"])
    return {a: {b: {"n": len(v), "E_ret10": mean(v), "share_+20%": mean([int(x >= 0.2) for x in v])} for b, v in d.items()}
            for a, d in out.items()}


def execution(cands):
    by = defaultdict(list)
    for c in cands:
        k = "bought" if c["bought"] else ("quote_" + (c["quote_status"] or "none")) if c["risk_allowed"] else "risk_blocked"
        by[k].append(c["simulated_pnl_if_forced"])
    return {k: {"n": len(v), "sim_pnl_mean_pct": mean(v), "sim_win_rate": mean([int(x > 0) for x in v if x is not None])}
            for k, v in by.items()}


def main():
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default="data/research.db")
    ap.add_argument("--out", default="")
    ap.add_argument("--cost", type=float, default=4.0, help="round-trip cost %% for label B")
    a = ap.parse_args()
    cost = a.cost / 100
    disc, snaps, path, cands, fwd = load(a.db)
    rows = anchors(snaps, path, cost)
    L = []
    L.append(f"# Research dataset — predictive power\n\ntokens {len(disc)} · snapshots {len(snaps)} · anchored rows "
             f"{len(rows)} (with forward outcome) · price points {sum(len(v) for v in path.values())} · candidates {len(cands)}")
    if len(rows) < 300:
        L.append(f"\n> ⚠ only {len(rows)} anchored rows — far below the few hundred labelled tokens needed. "
                 "Figures below are descriptive, NOT evidence.")
    stages = defaultdict(list)
    for d in disc.values():
        for k in ("first_pre_early_ts", "first_early_watch_ts", "early_complete_ts", "first_early_true_ts",
                  "first_candidate_ts", "first_bought_ts"):
            if d[k]:
                stages[k].append(d[k] - d["first_seen_ts"])
    L.append("\n## Funnel (tokens reaching each stage · median seconds after discovery)\n")
    L.append("| stage | tokens | median s |\n|---|---|---|")
    L.append(f"| discovered | {len(disc)} | 0 |")
    for k, v in stages.items():
        L.append(f"| {k.replace('first_', '').replace('_ts', '')} | {len(v)} | {statistics.median(v):.0f} |")
    for label in ("A", "B"):
        tab, br = rule_table(rows, label)
        L.append(f"\n## Rules vs label {label} (base rate {pct(br) if br is not None else '—'})\n")
        L.append("| rule | coverage | pass | fail | precision | recall | lift | cond. lift | E[ret10|pass] | E[ret10|fail] "
                 "| E[ret10|unknown] | time-to-pass | flags |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        num = lambda x, f="{:.2f}": "—" if x is None else f.format(x)  # noqa: E731
        for t in tab:
            flags = ("⚠ INVERTED (fail > pass) " if t["inverted"] else "") + ("small n" if t["small"] else "")
            L.append(f"| {t['rule']} | {t['coverage']:.2f} | {t['n_pass']} | {t['n_fail']} | {pct(t['precision'])} | "
                     f"{pct(t['recall'])} | {num(t['lift'])} | {num(t['cond_lift'])} | {pct(t['E_pass'])} | "
                     f"{pct(t['E_fail'])} | {pct(t['E_unknown'])} | {num(t['time_to_pass_s'], '{:.0f}s')} | {flags} |")
    L.append("\n## Experiment 1 — single-feature AUC (label A)\n")
    lab = [r for r in rows if r["A"] is not None]
    for feat in ("opportunity", "momentum", "confidence", "early_strength", "risk_score", "holders", "buy_pressure_pct",
                 "liq_usd", "age_sec", "top10_pct"):
        pos = [r[feat] for r in lab if r["A"] and r[feat] is not None]
        neg = [r[feat] for r in lab if not r["A"] and r[feat] is not None]
        v = auc(pos, neg)
        L.append(f"- {feat}: AUC {v:.3f} (n+ {len(pos)}, n- {len(neg)})" if v is not None else f"- {feat}: not enough data")
    L.append("\n## Experiment 2 — redundancy (phi correlation, |phi| > 0.7 flagged)\n")
    names = [f"d{i}" for i in range(1, 9)] + ["s_volume_accel", "s_txn_accel", "s_buy_pressure", "s_mc_accel",
                                              "s_holder_accel", "s_liquidity_growth", "s_whale_accum"]
    hi = []
    for i, x in enumerate(names):
        for y in names[i + 1:]:
            v = phi(rows, x, y)
            if v is not None and abs(v) > 0.7:
                hi.append(f"{x}~{y} {v:+.2f}")
    L.append(", ".join(hi) or "no pair above 0.7 (or not enough data)")
    L.append("\n## Experiment 3 — latency (positive tokens, label A from discovery)\n")
    L.append("```\n" + json.dumps(latency(disc, path, cost), indent=1) + "\n```")
    L.append("If median time to Early TRUE > median time to +40 %, latency bias is demonstrated.")
    hc, n = holder_curve(rows)
    L.append(f"\n## Experiment 4 — holder threshold (label A, {n} rows with holder data)\n")
    L.append("| N | trades | precision | recall | E[ret10] |\n|---|---|---|---|---|")
    for h in hc:
        L.append(f"| {h['N']} | {h['trades']} | {pct(h['precision'])} | {pct(h['recall'])} | {pct(h['expectancy'])} |")
    L.append("\n## Experiment 5 — Risk by age bucket (E[ret10])\n")
    L.append("```\n" + json.dumps(risk_by_age(rows), indent=1, default=lambda x: round(x, 4)) + "\n```")
    L.append("\n## Execution — candidates by outcome (simulated P&L if forced: entry+cost, TP+30/SL-15/1h)\n")
    L.append("```\n" + json.dumps(execution(cands), indent=1, default=lambda x: round(x, 4)) + "\n```")
    text = "\n".join(L)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
