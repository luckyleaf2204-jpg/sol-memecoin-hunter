"""Replay on research.db (step 4): do Trade Candidates hit TP before SL more often than random tokens?

Inputs: forward_returns (anchor 'candidate' = price at the first candidate time; anchor 'discovery' = price when the
token was first seen) with the dataset's fixed levels TP +30 % / SL -15 % and `first_hit` (which came first inside
the horizon), joined to candidates (bought flag, engine).

Metric: TP share = TP-first / (TP-first + SL-first). The raw TP-first rate is NOT an edge measure: volatile tokens
hit both levels more often. With TP +30 % / SL -15 % the cost-free break-even TP share is 15 / 45 = 33.3 %.
Baseline: random NON-candidate tokens (discovery anchor) from the same time window, bootstrap-resampled to the
candidate sample size -> mean TP share, 95 % interval and the share of random draws at least as good (one-sided p). Walk-forward: candidates sorted by time, first 60 % = in-sample, last 40 % = out-of-sample, each with
its own same-window baseline. Read-only; nothing here feeds trading.
"""
from __future__ import annotations

import random
import sqlite3


def _rows(db: sqlite3.Connection, anchor: str, horizon: str) -> list[dict]:
    q = ("SELECT ca, anchor_ts, first_hit, hit_tp30, hit_sl15, return_pct, mfe_pct, mae_pct FROM forward_returns "
         "WHERE anchor=? AND horizon=? AND samples > 0")
    return [{"ca": r[0], "ts": r[1], "first_hit": r[2], "hit_tp": r[3], "hit_sl": r[4], "ret": r[5], "mfe": r[6],
             "mae": r[7]} for r in db.execute(q, (anchor, horizon))]


def load(db: sqlite3.Connection, horizon: str = "1h") -> tuple[list[dict], list[dict]]:
    cand = _rows(db, "candidate", horizon)
    info = {}
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(candidates)")}
        eng = "MAX(engine)" if "engine" in cols else "NULL"          # older databases: no engine column
        for ca, bought, engine in db.execute(f"SELECT ca, MAX(bought), {eng} FROM candidates GROUP BY ca"):
            info[ca] = (bool(bought), engine)
    except sqlite3.OperationalError:
        pass
    for r in cand:
        r["bought"], r["engine"] = info.get(r["ca"], (False, None))
    cas = {r["ca"] for r in cand}
    base = [r for r in _rows(db, "discovery", horizon) if r["ca"] not in cas]
    return cand, base


TP_LEVEL, SL_LEVEL = 30.0, 15.0
BREAK_EVEN_TP_SHARE = SL_LEVEL / (TP_LEVEL + SL_LEVEL)


def _share(rows) -> float | None:
    tp = sum(1 for r in rows if r["first_hit"] == "tp30")
    sl = sum(1 for r in rows if r["first_hit"] == "sl15")
    return tp / (tp + sl) if tp + sl else None


def rates(rows: list[dict]) -> dict:
    n = len(rows)
    tp = sum(1 for r in rows if r["first_hit"] == "tp30")
    sl = sum(1 for r in rows if r["first_hit"] == "sl15")
    rets = sorted(r["ret"] for r in rows if r["ret"] is not None)
    return {"n": n, "tp_first": tp, "sl_first": sl, "neither": n - tp - sl,
            "tp_first_rate": round(tp / n, 4) if n else None, "sl_first_rate": round(sl / n, 4) if n else None,
            "tp_minus_sl_rate": round((tp - sl) / n, 4) if n else None,
            "tp_share": round(tp / (tp + sl), 4) if tp + sl else None,
            "median_return_pct": round(rets[len(rets) // 2], 2) if rets else None}


def baseline(base: list[dict], n: int, observed: float | None, iters: int = 2000, seed: int = 7) -> dict:
    if not base or not n:
        return {"n_pool": len(base), "status": "NO BASELINE"}
    rng = random.Random(seed)
    tp_rates, shares = [], []
    for _ in range(iters):
        sample = rng.choices(base, k=n)
        tp_rates.append(sum(1 for r in sample if r["first_hit"] == "tp30") / n)
        sh = _share(sample)
        if sh is not None:
            shares.append(sh)
    shares.sort()
    out = {"n_pool": len(base), "mean_tp_first_rate": round(sum(tp_rates) / iters, 4),
           "mean_tp_share": round(sum(shares) / len(shares), 4) if shares else None,
           "tp_share_ci95": (round(shares[int(0.025 * len(shares))], 4), round(shares[int(0.975 * len(shares)) - 1], 4))
           if shares else None}
    if observed is not None and shares:
        out["p_random_at_least_as_good"] = round(sum(1 for d in shares if d >= observed) / len(shares), 4)
    return out


def _window(base: list[dict], rows: list[dict]) -> list[dict]:
    if not rows:
        return []
    lo, hi = min(r["ts"] for r in rows), max(r["ts"] for r in rows)
    return [b for b in base if lo <= b["ts"] <= hi]


def compare(cand: list[dict], base: list[dict], seed: int = 7) -> dict:
    r = rates(cand)
    win = _window(base, cand) or base
    return {"candidates": r, "baseline_random": baseline(win, r["n"], r["tp_share"], seed=seed),
            "baseline_window": "same time window" if _window(base, cand) else "all (no tokens in the window)"}


def replay(db_path: str, horizon: str = "1h", split: float = 0.6, bought_only: bool = False, seed: int = 7) -> dict:
    db = sqlite3.connect(db_path)
    try:
        cand, base = load(db, horizon)
    finally:
        db.close()
    if bought_only:
        cand = [r for r in cand if r["bought"]]
    cand.sort(key=lambda r: r["ts"])
    k = int(len(cand) * split)
    ins, oos = cand[:k], cand[k:]
    out = {"horizon": horizon, "levels": "TP +30 % / SL -15 % (dataset)", "bought_only": bought_only,
           "break_even_tp_share": round(BREAK_EVEN_TP_SHARE, 4),
           "all": compare(cand, base, seed),
           "walk_forward": {"split": split, "in_sample": compare(ins, base, seed), "out_of_sample": compare(oos, base, seed)}}
    n = len(cand)
    o = out["walk_forward"]["out_of_sample"]
    share, p = o["candidates"]["tp_share"], o["baseline_random"].get("p_random_at_least_as_good", 1)
    if n < 30:
        out["verdict"] = "INSUFFICIENT SAMPLE (<30 candidates)"
    elif share is None or p >= 0.05:
        out["verdict"] = "no evidence of an edge over random tokens out-of-sample"
    elif share <= BREAK_EVEN_TP_SHARE:
        out["verdict"] = "better than random out-of-sample, but TP share below break-even (33 %): no tradable edge"
    else:
        out["verdict"] = "out-of-sample TP share beats random (p<0.05) and break-even (before costs)"
    return out
