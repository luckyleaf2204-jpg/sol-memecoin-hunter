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


MIN_N = 30                           # per part: below this the holdout is not opened (docs/sample_plan.md)


def _split(cand: list[dict], split: float) -> tuple[float, list[dict]]:
    """The holdout is a TIME window defined on all candidates (before any filter): filters are parameters."""
    cand = sorted(cand, key=lambda r: r["ts"])
    k = int(len(cand) * split)
    return (cand[k]["ts"] if k < len(cand) else float("inf")), cand[k:]


def lock_holdout(db_path: str, lock_path: str, horizon: str = "1h", split: float = 0.6, bought_only: bool = False,
                 seed: int = 7, now: float | None = None, min_n: int = MIN_N) -> dict:
    """Freeze the test on the in-sample part: write the parameter hash, the lock time and the time cut.
    Refused when the in-sample part has fewer than min_n candidates or a lock already exists."""
    import json
    import time
    if Path(lock_path).exists():
        return {"status": "REFUSED", "reason": "a holdout lock already exists (one lock per holdout)",
                "lock": json.loads(Path(lock_path).read_text(encoding="utf-8"))}
    db = sqlite3.connect(db_path)
    try:
        cand, base = load(db, horizon)
    finally:
        db.close()
    t_cut, _ = _split(cand, split)
    ins = [r for r in cand if r["ts"] < t_cut and (r["bought"] or not bought_only)]
    if len(ins) < min_n:
        return {"status": "REFUSED", "reason": f"in-sample n = {len(ins)} < {min_n}: nothing to freeze yet"}
    params = frozen_params(horizon, split, bought_only, seed)
    lock = {"params_hash": params["hash"], "params": params, "locked_at": now or time.time(), "t_cut": t_cut,
            "n_in_sample": len(ins)}
    Path(lock_path).write_text(json.dumps(lock, indent=1), encoding="utf-8")
    return {"status": "LOCKED", "lock": lock, "in_sample": compare(ins, base, seed)}


def _open_holdout(lock_path: str | None, params: dict, n_oos: int, min_n: int) -> tuple[dict | None, str | None]:
    import json
    if not lock_path or not Path(lock_path).exists():
        return None, "HOLDOUT NOT LOCKED: run with --lock-holdout on the in-sample part first"
    lock = json.loads(Path(lock_path).read_text(encoding="utf-8"))
    if lock["params_hash"] != params["hash"]:
        return lock, (f"PARAMETERS CHANGED since the lock ({lock['params_hash']} -> {params['hash']}): "
                      "the holdout stays closed")
    if n_oos < min_n:
        return lock, f"holdout n = {n_oos} < {min_n}: not opened yet"
    return lock, None


def replay(db_path: str, horizon: str = "1h", split: float = 0.6, bought_only: bool = False, seed: int = 7,
           holdout_lock: str | None = None, min_n: int = MIN_N) -> dict:
    db = sqlite3.connect(db_path)
    try:
        cand, base = load(db, horizon)
    finally:
        db.close()
    params = frozen_params(horizon, split, bought_only, seed)
    lock, refused = None, None
    t_cut, _ = _split(cand, split)
    if holdout_lock and Path(holdout_lock).exists():
        import json
        t_cut = json.loads(Path(holdout_lock).read_text(encoding="utf-8")).get("t_cut", t_cut)   # the locked cut
    if bought_only:
        cand = [r for r in cand if r["bought"]]
    cand.sort(key=lambda r: r["ts"])
    ins, oos = [r for r in cand if r["ts"] < t_cut], [r for r in cand if r["ts"] >= t_cut]
    lock, refused = _open_holdout(holdout_lock, params, len(oos), min_n)
    out = {"horizon": horizon, "levels": "TP +30 % / SL -15 % (dataset)", "bought_only": bought_only,
           "frozen_params": params, "holdout_lock": lock,
           "protocol": "parameters fixed on the first 60 % (time order) and locked (hash + time); the last 40 % "
                       "opens only with the same hash and n >= 30",
           "break_even_tp_share": round(BREAK_EVEN_TP_SHARE, 4),
           "metric_note": "REFERENCE ONLY: TP share reflects volatility. The decision metric is net expectancy "
                          "after 5 / 7 / 10 % round-trip cost (trading/sample_report.py).",
           "n_in_sample": len(ins), "n_holdout": len(oos)}
    wf = {"split": split, "in_sample": compare(ins, base, seed)}
    if refused:
        wf["out_of_sample"] = {"status": "REFUSED", "reason": refused}
        out["all"] = None                                 # the pooled view would leak the holdout
        out["walk_forward"] = wf
        out["verdict"] = refused
        return out
    wf["out_of_sample"] = compare(oos, base, seed)
    out["all"] = compare(cand, base, seed)
    out["walk_forward"] = wf
    o = wf["out_of_sample"]
    share, p = o["candidates"]["tp_share"], o["baseline_random"].get("p_random_at_least_as_good", 1)
    if share is None or p >= 0.05:
        out["verdict"] = "no evidence of an edge over random tokens out-of-sample"
    elif share <= BREAK_EVEN_TP_SHARE:
        out["verdict"] = "better than random out-of-sample, but TP share below break-even (33 %): no tradable edge"
    else:
        out["verdict"] = "out-of-sample TP share beats random (p<0.05) and break-even (before costs)"
    return out
