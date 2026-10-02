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
from pathlib import Path


def _rows(db: sqlite3.Connection, anchor: str, horizon: str) -> list[dict]:
    q = ("SELECT ca, anchor_ts, first_hit, hit_tp30, hit_sl15, return_pct, mfe_pct, mae_pct FROM forward_returns "
         "WHERE anchor=? AND horizon=? AND samples > 0")
    return [{"ca": r[0], "ts": r[1], "first_hit": r[2], "hit_tp": r[3], "hit_sl": r[4], "ret": r[5], "mfe": r[6],
             "mae": r[7]} for r in db.execute(q, (anchor, horizon))]


def load(db: sqlite3.Connection, horizon: str = "1h") -> tuple[list[dict], list[dict]]:
    cand = _rows(db, "candidate", horizon)
    eps: dict[str, list] = {}
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(candidates)")}
        eng = "engine" if "engine" in cols else "NULL"               # older databases: no engine column
        for ca, ts, bought, engine in db.execute(f"SELECT ca, ts, bought, {eng} FROM candidates"):
            eps.setdefault(ca, []).append((ts or 0.0, bool(bought), engine))
    except sqlite3.OperationalError:
        pass
    for r in cand:
        # NO LOOK-AHEAD: "bought" only from the episode of this decision (its candidate row, ts within
        # EPISODE_S of the anchor) — never from a later episode of the same token
        same = [e for e in eps.get(r["ca"], []) if r["ts"] - 1 <= e[0] <= r["ts"] + EPISODE_S]
        r["bought"] = any(b for _, b, _ in same)
        r["engine"] = next((e for _, _, e in same if e), None)
    cas = {r["ca"] for r in cand}
    base = [r for r in _rows(db, "discovery", horizon) if r["ca"] not in cas]
    return cand, base


TP_LEVEL, SL_LEVEL = 30.0, 15.0
EPISODE_S = 300.0                    # research.dataset.CANDIDATE_GAP_S: one candidate episode
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


def frozen_params(horizon: str, split: float, bought_only: bool, seed: int) -> dict:
    """Everything that defines the test. Chosen on the in-sample part BEFORE the out-of-sample part is looked at."""
    import hashlib
    import json
    p = {"horizon": horizon, "split": split, "bought_only": bought_only, "seed": seed, "tp_pct": TP_LEVEL,
         "sl_pct": SL_LEVEL, "baseline": "same-window random non-candidates, bootstrap"}
    p["hash"] = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]
    return p


def holdout_check(log_path: str | None, oos: list[dict], params: dict) -> str | None:
    """The 40 % holdout may be evaluated with ONE parameter set. A second, different set on the same holdout is
    flagged: it is no longer an out-of-sample test."""
    if not log_path or not oos:
        return None
    import json
    key = f"{min(r['ts'] for r in oos):.0f}-{max(r['ts'] for r in oos):.0f}-{len(oos)}"
    try:
        log = json.loads(Path(log_path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        log = {}
    prev = log.get(key)
    if prev is None:
        log[key] = params["hash"]
        Path(log_path).write_text(json.dumps(log, indent=1), encoding="utf-8")
        return None
    if prev != params["hash"]:
        return (f"HOLDOUT ALREADY USED with parameters {prev}; parameters {params['hash']} were changed after "
                "seeing the out-of-sample part: this is NOT a clean out-of-sample result")
    return None


def replay(db_path: str, horizon: str = "1h", split: float = 0.6, bought_only: bool = False, seed: int = 7,
           holdout_log: str | None = None) -> dict:
    db = sqlite3.connect(db_path)
    try:
        cand, base = load(db, horizon)
    finally:
        db.close()
    # the holdout is a TIME window defined on all candidates (before any filter): filters are parameters
    cand.sort(key=lambda r: r["ts"])
    k = int(len(cand) * split)
    t_cut = cand[k]["ts"] if k < len(cand) else float("inf")
    holdout_all = cand[k:]
    if bought_only:
        cand = [r for r in cand if r["bought"]]
    ins, oos = [r for r in cand if r["ts"] < t_cut], [r for r in cand if r["ts"] >= t_cut]
    params = frozen_params(horizon, split, bought_only, seed)
    warn = holdout_check(holdout_log, holdout_all, params)
    out = {"horizon": horizon, "levels": "TP +30 % / SL -15 % (dataset)", "bought_only": bought_only,
           "frozen_params": params, "warnings": [warn] if warn else [],
           "protocol": "parameters fixed on the first 60 % (time order) and hashed; the last 40 % is evaluated "
                       "once with that hash; a different hash on the same holdout is flagged",
           "break_even_tp_share": round(BREAK_EVEN_TP_SHARE, 4),
           "metric_note": "REFERENCE ONLY: TP share reflects volatility. The decision metric is net expectancy "
                          "after 5 / 7 / 10 % round-trip cost (trading/sample_report.py).",
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
