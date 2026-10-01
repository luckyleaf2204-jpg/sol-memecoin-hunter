"""Anti-rug research report (read-only) over one or more research databases.

usage: python tools/antirug_report.py --db data/research.db [--db other.db ...] [--out report.json]

Labels (no look-ahead): from an anchor (time t0, price p0) only LATER prices are used:
  RUG      some later price <= 0.2 x p0 (MAE <= -80 %) within --window seconds (default 1800)
  NON_RUG  no such price and the token was observed for >= --min-obs seconds (default 600)
  UNKNOWN  otherwise (not enough forward data)
Features are the ones stored IN the anchor snapshot (research.antirug.features at that moment).
Anchors: CANDIDATE (first candidate snapshot), BUY (bought), POP_2M / POP_5M (timeline snapshots ~+2 / +5 min).
Every metric says INSUFFICIENT SAMPLE when it rests on too few rugs / labelled tokens. Nothing here changes trading.
"""
from __future__ import annotations

import argparse
import bisect
import json
import sqlite3
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from research.antirug import BINARY, shadow_score  # noqa: E402

MIN_RUGS, MIN_LABELLED = 10, 30


# ---------------------------------------------------------------- loading
def load(paths):
    snaps, pathp, cands, forensics, lat = [], defaultdict(list), [], [], []
    for i, p in enumerate(paths):
        db = sqlite3.connect(f"file:{Path(p).as_posix()}?mode=ro", uri=True)
        db.row_factory = sqlite3.Row
        cols = {r[1] for r in db.execute("PRAGMA table_info(token_snapshots)")}
        for r in db.execute("SELECT * FROM token_snapshots ORDER BY ts"):
            d = dict(r)
            d["_db"] = i
            if "antirug" not in cols:
                d["antirug"] = None
            snaps.append(d)
        for ca, ts, price in db.execute("SELECT ca, ts, price FROM price_path WHERE price > 0 ORDER BY ts"):
            pathp[ca].append((ts, price))
        cands += [dict(r) for r in db.execute("SELECT * FROM candidates")]
        tabs = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "buy_forensics" in tabs:
            forensics += [json.loads(r[0]) for r in db.execute("SELECT data FROM buy_forensics")]
        if "latency_samples" in tabs:
            lat += [json.loads(r[0]) for r in db.execute("SELECT data FROM latency_samples")]
    for v in pathp.values():
        v.sort()
    return snaps, pathp, cands, forensics, lat


def feats(s: dict) -> dict:
    """Features of a snapshot: the stored anti-rug dict, else a partial one rebuilt from its columns (older DBs)."""
    if s.get("antirug"):
        try:
            return json.loads(s["antirug"])
        except ValueError:
            pass
    f = {"top10_pct": s.get("top10_pct"), "holder_count": s.get("holders"), "holders_ok": s.get("holders") is not None,
         "dev_verified": s.get("creator_pct") is not None, "dev_pct": s.get("creator_pct"), "risk": s.get("risk_score"),
         "risk_factors": json.loads(s["risk_reasons"]) if s.get("risk_reasons") else [],
         "liq_usd": s.get("liq_usd"), "liq_source": "pumpfun_curve" if s.get("dex_id") == "pumpfun" else "dexscreener_amm",
         "is_curve": s.get("dex_id") == "pumpfun",
         "amm_equivalent_usd": s.get("liquidity_equivalent_usd") or (s.get("liq_usd") if s.get("dex_id") != "pumpfun" else None),
         "buy_share_5m": (s["buy_pressure_pct"] / 100) if s.get("buy_pressure_pct") is not None else None,
         "tx_5m": s.get("tx_5m"), "age_s": s.get("age_sec"),
         "mint_authority_active": None if s.get("authority_mint") is None else s["authority_mint"] == "active",
         "freeze_authority_active": None if s.get("authority_freeze") is None else s["authority_freeze"] == "active",
         "token2022": None if s.get("is_token2022") is None else bool(s["is_token2022"])}
    f["shadow_antirug_score"], f["shadow_antirug_reasons"] = shadow_score(f)
    f["_partial"] = True
    return f


# ---------------------------------------------------------------- labels
def label(path, t0, p0, window=1800, min_obs=600):
    if not path or not p0:
        return "UNKNOWN", None, None
    ts = [x[0] for x in path]
    i = bisect.bisect_right(ts, t0)
    after = [(t, p) for t, p in path[i:] if t <= t0 + window]
    for t, p in after:
        if p <= 0.2 * p0:
            return "RUG", round(t - t0), round(100 * (min(q for _, q in after) / p0 - 1), 1)
    if after and after[-1][0] - t0 >= min_obs:
        return "NON_RUG", None, round(100 * (min(q for _, q in after) / p0 - 1), 1)
    return "UNKNOWN", None, None


def fwd(path, t0, p0):
    if not path or not p0:
        return {}
    after = [(t, p) for t, p in path if t > t0]
    out = {}
    for h in (60, 300, 600, 1800, 3600):
        pts = [p for t, p in after if t0 + h - 30 <= t <= t0 + h + 60]
        out[f"ret_{h // 60}m"] = round(100 * (pts[0] / p0 - 1), 1) if pts else None
    rets = [p / p0 - 1 for _, p in after]
    out["mfe"] = round(100 * max(rets), 1) if rets else None
    out["mae"] = round(100 * min(rets), 1) if rets else None
    return out


# ---------------------------------------------------------------- anchors
def anchors(snaps, pathp, kind):
    """First qualifying snapshot per token. POP_2M / POP_5M = first timeline snapshot >= 115 s / 295 s after the
    token's discovery snapshot (real elapsed time, robust to ticks that cover several offsets at once)."""
    first, seen0 = {}, {}
    for s in snaps:
        key = (s["_db"], s["ca"])
        seen0.setdefault(key, s["ts"])
        if s.get("price_usd") is None or key in first:
            continue
        if kind == "CANDIDATE" and s.get("reason") == "candidate":
            first[key] = s
        elif kind == "BUY" and s.get("stage") == "bought" and s.get("reason") == "quote":
            first[key] = s
        elif kind in ("POP_2M", "POP_5M") and s.get("reason") == "timeline"                 and s["ts"] - seen0[key] >= (115 if kind == "POP_2M" else 295):
            first[key] = s
    out = []
    for key, s in first.items():
        lab, ttr, mae = label(pathp.get(s["ca"]), s["ts"], s["price_usd"])
        out.append({"db": key[0], "ca": s["ca"], "ts": s["ts"], "price": s["price_usd"], "snap": s, "f": feats(s),
                    "label": lab, "time_to_rug_s": ttr, "mae": mae})
    return out


# ---------------------------------------------------------------- metrics
def feature_table(units, snaps_by_ca):
    lab = [u for u in units if u["label"] in ("RUG", "NON_RUG")]
    rugs = sum(1 for u in lab if u["label"] == "RUG")
    base = rugs / len(lab) if lab else None
    rows = []
    for name, pred in BINARY.items():
        tp = fp = fn = tn = unk = 0
        strata = defaultdict(lambda: [0, 0, 0, 0])          # (holders_ok, is_curve) -> tp, fp, rugs, n
        first_age = []
        for u in lab:
            v = pred(u["f"])
            rug = u["label"] == "RUG"
            if v is None:
                unk += 1
                continue
            k = (bool(u["f"].get("holders_ok")), bool(u["f"].get("is_curve")))
            strata[k][3] += 1
            strata[k][2] += rug
            if v:
                strata[k][0 if rug else 1] += 1
                tp += rug
                fp += not rug
            else:
                fn += rug
                tn += not rug
            if rug:
                for s in snaps_by_ca.get((u["db"], u["ca"]), []):
                    if s["ts"] > u["ts"]:
                        break
                    fv = pred(feats(s))
                    if fv:
                        first_age.append(s.get("age_sec"))
                        break
        known = tp + fp + fn + tn
        rr_t = tp / (tp + fp) if tp + fp else None
        rr_f = fn / (fn + tn) if fn + tn else None
        num = den = 0.0
        for a_tp, a_fp, a_r, a_n in strata.values():
            if a_tp + a_fp >= 3 and a_n and a_r:
                num += a_n * ((a_tp / (a_tp + a_fp)) / (a_r / a_n))
                den += a_n
        rug_known = tp + fn
        rows.append({"feature": name, "coverage": round(known / len(lab), 2) if lab else 0,
                     "missing_rate": round(unk / len(lab), 2) if lab else None, "n_true": tp + fp,
                     "rug_rate_true": None if rr_t is None else round(rr_t, 3),
                     "rug_rate_false": None if rr_f is None else round(rr_f, 3),
                     "lift": round(rr_t / base, 2) if rr_t is not None and base else None,
                     "precision": None if rr_t is None else round(rr_t, 3),
                     "recall": round(tp / rug_known, 3) if rug_known else None,
                     "fpr": round(fp / (fp + tn), 3) if fp + tn else None,
                     "fnr": round(fn / rug_known, 3) if rug_known else None,
                     "conditional_lift": round(num / den, 2) if den else None,
                     "time_to_signal_age_s_median": statistics.median([a for a in first_age if a is not None])
                     if any(a is not None for a in first_age) else None,
                     "sample": "OK" if rug_known >= MIN_RUGS and known >= MIN_LABELLED else "INSUFFICIENT SAMPLE"})
    return rows, {"labelled": len(lab), "rugs": rugs, "non_rugs": len(lab) - rugs,
                  "base_rug_rate": None if base is None else round(base, 3),
                  "unknown": sum(1 for u in units if u["label"] == "UNKNOWN")}


def rank(rows):
    ok = [r for r in rows if r["sample"] == "OK" and r["coverage"] >= 0.3 and r["lift"] is not None and r["n_true"] >= 5]
    return sorted(ok, key=lambda r: -(r["lift"] or 0))


def groups(units):
    out = {}
    defs = {"A_full_holder_dev": lambda f: bool(f.get("holders_ok")) and bool(f.get("dev_verified")),
            "B_missing_holder_or_dev": lambda f: not (f.get("holders_ok") and f.get("dev_verified")),
            "C_full_data_risk<=20": lambda f: bool(f.get("holders_ok")) and bool(f.get("dev_verified"))
            and f.get("risk") is not None and f["risk"] <= 20}
    for g, fn in defs.items():
        us = [u for u in units if fn(u["f"]) and u["label"] in ("RUG", "NON_RUG")]
        r = sum(1 for u in us if u["label"] == "RUG")
        out[g] = {"labelled": len(us), "rugs": r, "rug_rate": round(r / len(us), 3) if us else None,
                  "sample": "OK" if r >= MIN_RUGS and len(us) >= MIN_LABELLED else "INSUFFICIENT SAMPLE"}
    return out


def shadow(units):
    lab = [u for u in units if u["label"] in ("RUG", "NON_RUG")]
    rugs = sum(1 for u in lab if u["label"] == "RUG")
    out = {}
    for t in (50, 60, 70, 80):
        flagged = [u for u in lab if (u["f"].get("shadow_antirug_score") or 0) >= t]
        tp = sum(1 for u in flagged if u["label"] == "RUG")
        out[str(t)] = {"flagged": len(flagged), "rugs_caught": tp, "recall": round(tp / rugs, 3) if rugs else None,
                       "precision": round(tp / len(flagged), 3) if flagged else None,
                       "false_positives": len(flagged) - tp,
                       "non_rugs_flagged_pct": round(100 * (len(flagged) - tp) / (len(lab) - rugs), 1) if len(lab) > rugs else None,
                       "coverage": len(lab)}
    out["sample"] = "OK" if rugs >= MIN_RUGS and len(lab) >= MIN_LABELLED else "INSUFFICIENT SAMPLE"
    return out


def pctl(xs, ps=(0.5, 0.75, 0.9, 0.95)):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    return {f"P{int(p * 100)}": round(xs[min(len(xs) - 1, int(p * (len(xs) - 1)))], 1) for p in ps} | {"n": len(xs)}


def latency(lat):
    out = {"overall_drift_bps": pctl(x.get("drift_bps") for x in lat),
           "overall_latency_s": pctl(x.get("latency_s") for x in lat)}
    bucket = {
        "age": lambda x: None if x.get("age_s") is None else ("<90s" if x["age_s"] < 90 else "90s-5m" if x["age_s"] < 300 else ">5m"),
        "liquidity": lambda x: None if x.get("liquidity_usd") is None else (
            "<20K" if x["liquidity_usd"] < 2e4 else "20-50K" if x["liquidity_usd"] < 5e4 else "50-100K" if x["liquidity_usd"] < 1e5 else ">100K"),
        "pool": lambda x: "curve" if x.get("is_curve") else "amm",
        "route": lambda x: x.get("route"),
        "impact": lambda x: None if x.get("impact_pct") is None else ("<1%" if x["impact_pct"] < 1 else "1-2%" if x["impact_pct"] < 2 else ">=2%"),
        "quote_size": lambda x: None if x.get("quote_size_usd") is None else ("<$15" if x["quote_size_usd"] < 15 else "$15-30" if x["quote_size_usd"] < 30 else ">=$30"),
        "hour_utc": lambda x: x.get("hour_utc"),
        "chg5m": lambda x: x.get("bucket")}
    for name, fn in bucket.items():
        g = defaultdict(list)
        for x in lat:
            g[fn(x)].append(x.get("drift_bps"))
        out[name] = {str(k): (pctl(v) if len(v) >= 50 else {"n": len(v), "estimate": "UNKNOWN (n<50) -> pooled"})
                     for k, v in g.items()}
    return out


def data_refresh(forensics, pathp):
    out = {"buys": len(forensics), "spikes": 0, "DATA_REFRESH": 0, "RISK_NEW": 0, "DATA_STALE": 0,
           "data_refresh_then_rug": 0, "data_refresh_no_rug": 0, "data_refresh_unknown": 0, "rows": []}
    for fx in forensics:
        snaps = fx["snaps"] + ([fx["exit"]] if fx.get("exit") else [])
        types = [x.get("spike_type") for x in snaps if x.get("spike_type")]
        st = types[0] if types else None
        entry = snaps[0]
        lab, ttr, mae = label(pathp.get(fx["mint"]), fx["entry_ts"], entry.get("price"))
        if st:
            out["spikes"] += 1
            out[st] = out.get(st, 0) + 1
            if st == "DATA_REFRESH":
                out[{"RUG": "data_refresh_then_rug", "NON_RUG": "data_refresh_no_rug"}.get(lab, "data_refresh_unknown")] += 1
        out["rows"].append({"symbol": fx.get("symbol"), "spike_type": st, "outcome": lab, "mae": mae,
                            "buy_ts": fx["entry_ts"],
                            "path": [(x["at"], x.get("risk"), x.get("risk_new"), x.get("risk_data_refresh"),
                                      x.get("holders_stamp"), x.get("dev_stamp")) for x in snaps],
                            "exit": (fx.get("exit") or {}).get("reason")})
    if out["spikes"]:
        out["pct_data_refresh"] = round(100 * out["DATA_REFRESH"] / out["spikes"], 1)
        out["pct_risk_new"] = round(100 * out["RISK_NEW"] / out["spikes"], 1)
    return out


def candidate_table(cands_units, snaps_by_ca, cands_rows, pathp):
    out = []
    by = {(c["ca"]): c for c in cands_rows}
    for u in cands_units:
        f, s = u["f"], u["snap"]
        before = sorted(k for k, p in BINARY.items() if p(f))
        after = set()
        for x in snaps_by_ca.get((u["db"], u["ca"]), []):
            if x["ts"] > u["ts"]:
                after |= {k for k, p in BINARY.items() if p(feats(x))}
        c = by.get(u["ca"], {})
        out.append({"ca": u["ca"], "symbol": (s.get("notes") and None) or c.get("symbol"), "label": u["label"],
                    "mae": u["mae"], "time_to_rug_s": u["time_to_rug_s"], "age_s": f.get("age_s"),
                    "risk": f.get("risk"), "opportunity": s.get("opportunity"), "confidence": s.get("confidence"),
                    "early_score": s.get("early_score"), "liquidity_eq": f.get("amm_equivalent_usd"),
                    "top10": f.get("top10_pct"), "top1": f.get("top1_pct"), "creator_pct": f.get("dev_pct"),
                    "holders": f.get("holder_count"), "buy_share": f.get("buy_share_5m"), "vol_accel": f.get("vol_accel"),
                    "whale": f.get("whale_state"), "dev": f.get("dev_status"),
                    "bundle_proxy": {"dev_snipe": f.get("dev_snipe"), "suspicious_holders": f.get("suspicious_holders")},
                    "liq_change_5m": f.get("liq_change_5m_pct"),
                    "authority_active": f.get("mint_authority_active") or f.get("freeze_authority_active"),
                    "token2022": f.get("token2022"), "shadow_score": f.get("shadow_antirug_score"),
                    "shadow_reasons": f.get("shadow_antirug_reasons"),
                    "features_before_candidate": before, "features_only_after": sorted(after - set(before)),
                    "old_decision": s.get("old_decision"), "new_decision": s.get("new_decision"),
                    "blocked_by": s.get("blocked_by"), "would_have_bought": c.get("would_have_bought_if_quote_ok"),
                    "actual_buy": c.get("bought"), "quote_result": c.get("quote_status"),
                    "fill_result": c.get("fill_result") or c.get("fill_status"),
                    "candidate_status": c.get("candidate_status"), **fwd(pathp.get(u["ca"]), u["ts"], u["price"]),
                    "partial_features": bool(f.get("_partial"))})
    return out


def liquidity_ab(snaps, pathp):
    first = {}
    for s in snaps:
        if s.get("new_liquidity_decision") == "PASS" and s.get("price_usd"):
            first.setdefault((s["_db"], s["ca"]), s)
    bands = defaultdict(lambda: Counter())
    rows = []
    for key, s in first.items():
        lab, _, mae = label(pathp.get(s["ca"]), s["ts"], s["price_usd"])
        eq = s.get("liquidity_equivalent_usd")
        b = None if eq is None else ("10-20K" if eq < 2e4 else "20-50K" if eq < 5e4 else "50-100K" if eq < 1e5 else ">100K")
        old = s.get("old_liquidity_decision")
        bands[b][lab] += 1
        bands[b]["old_blocked" if old == "FAIL" else "old_passed"] += 1
        rows.append({"old": old, "label": lab, **fwd(pathp.get(s["ca"]), s["ts"], s["price_usd"])})
    promoted = [r for r in rows if r["old"] == "FAIL"]

    def summ(rs):
        out = {"n": len(rs), "labels": dict(Counter(r["label"] for r in rs))}
        for k in ("ret_1m", "ret_5m", "ret_10m", "ret_30m", "ret_60m", "mfe", "mae"):
            v = [r[k] for r in rs if r.get(k) is not None]
            out[k] = {"n": len(v), "median": statistics.median(v)} if v else None
        return out
    return {"new_pass_tokens": len(rows), "old_blocked_new_pass": summ(promoted),
            "old_and_new_pass": summ([r for r in rows if r["old"] != "FAIL"]),
            "bands_by_amm_equivalent": {str(k): dict(v) | {
                "rug_rate": round(v["RUG"] / (v["RUG"] + v["NON_RUG"]), 3) if v["RUG"] + v["NON_RUG"] else None}
                for k, v in sorted(bands.items(), key=lambda x: str(x[0]))}}


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", action="append", required=True)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    snaps, pathp, cands, forensics, lat = load(a.db)
    by = defaultdict(list)
    for s in snaps:
        by[(s["_db"], s["ca"])].append(s)
    rep = {"dataset": {"dbs": len(a.db), "tokens_discovered": len({(s['_db'], s['ca']) for s in snaps}),
                       "snapshots": len(snaps), "snapshots_with_antirug_features": sum(1 for s in snaps if s.get("antirug")),
                       "price_points": sum(len(v) for v in pathp.values()), "candidates_rows": len(cands),
                       "buys_with_forensics": len(forensics), "latency_samples": len(lat)}}
    for kind in ("CANDIDATE", "BUY", "POP_2M", "POP_5M"):
        units = anchors(snaps, pathp, kind)
        tab, base = feature_table(units, by)
        rep[kind] = {"labels": base, "groups": groups(units), "shadow": shadow(units),
                     "top_features": rank(tab)[:10], "features": tab}
        if kind == "CANDIDATE":
            rep["candidate_table"] = candidate_table(units, by, cands, pathp)
    cov_units = anchors(snaps, pathp, "CANDIDATE") + anchors(snaps, pathp, "POP_5M")
    rep["missing_data"] = {k: round(sum(1 for u in cov_units if not u["f"].get(k)) / len(cov_units), 3) if cov_units else None
                           for k in ("holders_ok", "dev_verified")} | {
        "lp_events": "NOT COLLECTED", "bundle_slot_concentration": "NOT COLLECTED",
        "shared_funding_cluster": "NOT COLLECTED", "creator_wallet_age": "NOT COLLECTED"}
    rep["data_refresh"] = data_refresh(forensics, pathp)
    rep["latency"] = latency(lat)
    rep["liquidity_ab"] = liquidity_ab(snaps, pathp)
    strong = [r for kind in ("CANDIDATE", "POP_5M", "POP_2M") for r in rep[kind]["top_features"]
              if (r["lift"] or 0) >= 1.5 and (r["conditional_lift"] or 0) >= 1.3]
    rep["recommendation"] = ("SHADOW ONLY — candidate signals exist on the broad population; candidate-level sample "
                             "is too small for a production change" if strong else
                             "NO PRODUCTION CHANGE — INSUFFICIENT SAMPLE")
    print(json.dumps({k: v for k, v in rep.items() if k not in ("candidate_table",)}, default=str)[:3000])
    if a.out:
        Path(a.out).write_text(json.dumps(rep, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
