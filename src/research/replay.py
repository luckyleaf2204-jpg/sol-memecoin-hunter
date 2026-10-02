"""Replay on research.db: do Trade Candidates of the PRODUCTION strategy hit TP before SL more often than comparable
random tokens? (read-only; nothing here feeds trading)

Candidates: forward_returns anchor 'candidate' (price at the first candidate time, TP +30 % / SL -15 % first hit
inside the horizon), joined to the candidates table. Only candidates of the gated LIFECYCLE engine count (the
production entry engine; the no-chasing gate runs before a candidate is recorded) — other / unknown engines are
excluded and counted.

Baseline: for each candidate, up to 3 random OTHER tokens in the same age bucket and liquidity bucket, chosen from
token snapshots taken at or before the decision (research.gate_eval, no look-ahead); their outcome is the same TP /
SL first hit computed from the price path after that snapshot. Bootstrap-resampled to the candidate count -> mean
TP share, 95 % interval, one-sided p.

Metric: TP share = TP-first / (TP-first + SL-first), REFERENCE ONLY (it reflects volatility); break-even 33.3 %.

Walk-forward: the cut is a time point (60 % of all candidates in time order, before filters). In-sample = before the
cut; holdout = from max(cut, last in-sample decision + EMBARGO) with EMBARGO = the horizon, so no in-sample outcome
window reaches into the holdout (decisions in between are dropped and counted).
The test is frozen by a hash of every choice — horizon, split, filters, seed, min n, engine, the strategy's
parameter fingerprint (sample_id) and the code commit — written with the lock time; the holdout opens only with the
same hash and >= min n candidates.
"""
from __future__ import annotations

import random
import sqlite3
from pathlib import Path

TP_LEVEL, SL_LEVEL = 30.0, 15.0
EPISODE_S = 300.0                    # research.dataset.CANDIDATE_GAP_S: one candidate episode
BREAK_EVEN_TP_SHARE = SL_LEVEL / (TP_LEVEL + SL_LEVEL)
MIN_N = 30                           # per part: below this the holdout is not opened (docs/sample_plan.md)
ENGINE = "lifecycle"                 # the production entry engine (trading.config.production_config)


def horizon_s(horizon: str) -> int:
    from research.dataset import HORIZONS
    return dict(HORIZONS)[horizon]


def _rows(db: sqlite3.Connection, anchor: str, horizon: str) -> list[dict]:
    q = ("SELECT ca, anchor_ts, first_hit, hit_tp30, hit_sl15, return_pct, mfe_pct, mae_pct FROM forward_returns "
         "WHERE anchor=? AND horizon=? AND samples > 0")
    return [{"ca": r[0], "ts": r[1], "first_hit": r[2], "hit_tp": r[3], "hit_sl": r[4], "ret": r[5], "mfe": r[6],
             "mae": r[7]} for r in db.execute(q, (anchor, horizon))]


def load(db: sqlite3.Connection, horizon: str = "1h", engine: str | None = ENGINE) -> tuple[list[dict], dict]:
    cand = _rows(db, "candidate", horizon)
    eps: dict[str, list] = {}
    try:
        cols = {r[1] for r in db.execute("PRAGMA table_info(candidates)")}
        eng = "engine" if "engine" in cols else "NULL"               # older databases: no engine column
        for ca, ts, bought, e in db.execute(f"SELECT ca, ts, bought, {eng} FROM candidates"):
            eps.setdefault(ca, []).append((ts or 0.0, bool(bought), e))
    except sqlite3.OperationalError:
        pass
    for r in cand:
        # NO LOOK-AHEAD: "bought" only from the episode of this decision (its candidate row, ts within
        # EPISODE_S of the anchor) — never from a later episode of the same token
        same = [e for e in eps.get(r["ca"], []) if r["ts"] - 1 <= e[0] <= r["ts"] + EPISODE_S]
        r["bought"] = any(b for _, b, _ in same)
        r["engine"] = next((e for _, _, e in same if e), None)
        snap = db.execute("SELECT age_sec, liq_usd FROM token_snapshots WHERE ca=? AND ts<=? ORDER BY ts DESC LIMIT 1",
                          (r["ca"], r["ts"])).fetchone()
        r["age_s"], r["liq"] = (snap[0], snap[1]) if snap else (None, None)
    kept = [r for r in cand if engine is None or r["engine"] == engine]
    return kept, {"engine": engine, "excluded_other_engine": len(cand) - len(kept), "all_candidates": len(cand)}


def _share(rows) -> float | None:
    tp = sum(1 for r in rows if r["first_hit"] == "tp30")
    sl = sum(1 for r in rows if r["first_hit"] == "sl15")
    return tp / (tp + sl) if tp + sl else None


def rates(rows: list[dict]) -> dict:
    n = len(rows)
    tp = sum(1 for r in rows if r["first_hit"] == "tp30")
    sl = sum(1 for r in rows if r["first_hit"] == "sl15")
    rets = sorted(r["ret"] for r in rows if r.get("ret") is not None)
    return {"n": n, "tp_first": tp, "sl_first": sl, "neither": n - tp - sl,
            "tp_first_rate": round(tp / n, 4) if n else None, "sl_first_rate": round(sl / n, 4) if n else None,
            "tp_minus_sl_rate": round((tp - sl) / n, 4) if n else None,
            "tp_share": round(tp / (tp + sl), 4) if tp + sl else None,
            "median_return_pct": round(rets[len(rets) // 2], 2) if rets else None}


def baseline(pool: list[dict], n: int, observed: float | None, iters: int = 2000, seed: int = 7) -> dict:
    if not pool or not n:
        return {"n_pool": len(pool), "status": "NO BASELINE"}
    rng = random.Random(seed)
    tp_rates, shares = [], []
    for _ in range(iters):
        sample = rng.choices(pool, k=n)
        tp_rates.append(sum(1 for r in sample if r["first_hit"] == "tp30") / n)
        sh = _share(sample)
        if sh is not None:
            shares.append(sh)
    shares.sort()
    out = {"n_pool": len(pool), "mean_tp_first_rate": round(sum(tp_rates) / iters, 4),
           "mean_tp_share": round(sum(shares) / len(shares), 4) if shares else None,
           "tp_share_ci95": (round(shares[int(0.025 * len(shares))], 4), round(shares[int(0.975 * len(shares)) - 1], 4))
           if shares else None}
    if observed is not None and shares:
        out["p_random_at_least_as_good"] = round(sum(1 for d in shares if d >= observed) / len(shares), 4)
    return out


def matched_pool(db: sqlite3.Connection, rows: list[dict], exclude: set[str], h_s: int, seed: int) -> list[dict]:
    """Same age / liquidity bucket tokens per candidate (snapshots <= the decision), outcome after their snapshot."""
    from research.gate_eval import baseline_matches, outcome
    rng = random.Random(seed)
    pool = []
    for r in rows:
        for ca, ts, price in baseline_matches(db, {"ts": r["ts"], "age_s": r["age_s"], "liq": r["liq"]}, exclude, rng):
            o = outcome(db, ca, ts, price, h_s)
            if o is not None:
                pool.append({"ca": ca, "ts": ts, "first_hit": o["first_hit"], "ret": o["ret"]})
    return pool


def compare(db: sqlite3.Connection, rows: list[dict], exclude: set[str], h_s: int, seed: int = 7) -> dict:
    r = rates(rows)
    pool = matched_pool(db, rows, exclude, h_s, seed)
    return {"candidates": r, "baseline_random": baseline(pool, r["n"], r["tp_share"], seed=seed),
            "baseline": "same age / liquidity bucket, chosen from snapshots at or before each decision"}


def frozen_params(horizon: str, split: float, bought_only: bool, seed: int, min_n: int = MIN_N,
                  engine: str | None = ENGINE, sample_id: str | None = None, commit: str | None = None) -> dict:
    """Everything that defines the test. Chosen on the in-sample part BEFORE the holdout is looked at."""
    import hashlib
    import json
    if sample_id is None:
        from trading.config import production_config
        sample_id = production_config().sample_id()
    if commit is None:
        from core.version import git_commit
        commit = git_commit()
    p = {"horizon": horizon, "split": split, "bought_only": bought_only, "seed": seed, "min_n": min_n,
         "engine": engine, "sample_id": sample_id, "commit": commit, "embargo_s": horizon_s(horizon),
         "tp_pct": TP_LEVEL, "sl_pct": SL_LEVEL, "baseline": "age/liquidity-matched random tokens, bootstrap"}
    p["hash"] = hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:10]
    return p


def _cut(cand: list[dict], split: float) -> float:
    """The cut is a TIME point on all candidates (before any filter): filters are parameters."""
    ts = sorted(r["ts"] for r in cand)
    k = int(len(ts) * split)
    return ts[k] if k < len(ts) else float("inf")


def _parts(cand: list[dict], t_cut: float, embargo_s: float, bought_only: bool):
    """Embargo: the holdout starts only once the outcome window of the LAST in-sample decision has closed
    (start = max(cut, last in-sample ts + horizon)), so no in-sample outcome overlaps the holdout."""
    rows = sorted((r for r in cand if r["bought"] or not bought_only), key=lambda r: r["ts"])
    ins = [r for r in rows if r["ts"] < t_cut]
    start = max(t_cut, (ins[-1]["ts"] + embargo_s) if ins else t_cut)
    oos = [r for r in rows if r["ts"] >= start]
    embargoed = [r for r in rows if t_cut <= r["ts"] < start]
    return ins, oos, embargoed


def lock_holdout(db_path: str, lock_path: str, horizon: str = "1h", split: float = 0.6, bought_only: bool = False,
                 seed: int = 7, now: float | None = None, min_n: int = MIN_N, engine: str | None = ENGINE,
                 sample_id: str | None = None, commit: str | None = None) -> dict:
    """Freeze the test on the in-sample part: write the parameter hash, the lock time, the time cut and the embargo.
    Refused when the in-sample part has fewer than min_n candidates or a lock already exists."""
    import json
    import time
    if Path(lock_path).exists():
        return {"status": "REFUSED", "reason": "a holdout lock already exists (one lock per holdout)",
                "lock": json.loads(Path(lock_path).read_text(encoding="utf-8"))}
    params = frozen_params(horizon, split, bought_only, seed, min_n, engine, sample_id, commit)
    db = sqlite3.connect(db_path)
    try:
        cand, info = load(db, horizon, engine)
        t_cut = _cut(cand, split)
        ins, _, _ = _parts(cand, t_cut, params["embargo_s"], bought_only)
        if len(ins) < min_n:
            return {"status": "REFUSED", "reason": f"in-sample n = {len(ins)} < {min_n}: nothing to freeze yet"}
        lock = {"params_hash": params["hash"], "params": params, "locked_at": now or time.time(), "t_cut": t_cut,
                "embargo_s": params["embargo_s"], "n_in_sample": len(ins)}
        from core.snapshot import write_atomic
        write_atomic(lock_path, json.dumps(lock, indent=1).encode("utf-8"))
        return {"status": "LOCKED", "lock": lock, "selection": info,
                "in_sample": compare(db, ins, {r["ca"] for r in cand}, horizon_s(horizon), seed)}
    finally:
        db.close()


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
           holdout_lock: str | None = None, min_n: int = MIN_N, engine: str | None = ENGINE,
           sample_id: str | None = None, commit: str | None = None) -> dict:
    import json
    params = frozen_params(horizon, split, bought_only, seed, min_n, engine, sample_id, commit)
    db = sqlite3.connect(db_path)
    try:
        cand, info = load(db, horizon, engine)
        t_cut = _cut(cand, split)
        if holdout_lock and Path(holdout_lock).exists():
            t_cut = json.loads(Path(holdout_lock).read_text(encoding="utf-8")).get("t_cut", t_cut)   # the locked cut
        ins, oos, emb = _parts(cand, t_cut, params["embargo_s"], bought_only)
        lock, refused = _open_holdout(holdout_lock, params, len(oos), min_n)
        exclude, h = {r["ca"] for r in cand}, horizon_s(horizon)
        out = {"horizon": horizon, "levels": "TP +30 % / SL -15 % (dataset)", "bought_only": bought_only,
               "selection": info, "frozen_params": params, "holdout_lock": lock,
               "protocol": "parameters (incl. sample_id, commit, min_n, engine) fixed on the in-sample part and locked "
                           "(hash + time); holdout = after cut + embargo (= horizon), opened only with the same hash "
                           f"and n >= {min_n}",
               "break_even_tp_share": round(BREAK_EVEN_TP_SHARE, 4),
               "metric_note": "REFERENCE ONLY: TP share reflects volatility. The decision metric is net expectancy "
                              "after 5 / 7 / 10 % round-trip cost (trading/sample_report.py).",
               "n_in_sample": len(ins), "n_holdout": len(oos), "n_embargoed": len(emb)}
        wf = {"split": split, "embargo_s": params["embargo_s"], "in_sample": compare(db, ins, exclude, h, seed)}
        if refused:
            wf["out_of_sample"] = {"status": "REFUSED", "reason": refused}
            out["all"] = None                             # the pooled view would leak the holdout
            out["walk_forward"] = wf
            out["verdict"] = refused
            return out
        wf["out_of_sample"] = compare(db, oos, exclude, h, seed)
        out["all"] = compare(db, ins + oos, exclude, h, seed)
        out["walk_forward"] = wf
    finally:
        db.close()
    o = wf["out_of_sample"]
    share, p = o["candidates"]["tp_share"], o["baseline_random"].get("p_random_at_least_as_good", 1)
    if share is None or p >= 0.05:
        out["verdict"] = "no evidence of an edge over comparable random tokens out-of-sample"
    elif share <= BREAK_EVEN_TP_SHARE:
        out["verdict"] = "better than random out-of-sample, but TP share below break-even (33 %): no tradable edge"
    else:
        out["verdict"] = "out-of-sample TP share beats random (p<0.05) and break-even (before costs)"
    return out
