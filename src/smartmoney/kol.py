"""KOL copy test exactly as pre-registered (docs/kol_plan.md). Same recorder database, copy rule and costs as the
smart-money test (analysis.py); the wallet set is the public KOL roster frozen before any data was viewed
(docs/kol_wallets_20261006.json). Research only."""
from __future__ import annotations

import json
import random
import sqlite3
from pathlib import Path

from smartmoney.analysis import BIG, DELAY_S, DRAWS, Window, copies, net_pct, summary, window

MIN_TOKENS = 3                 # eligibility, KOLs and baseline alike: >= 3 distinct tokens bought with >= 0.05 SOL
POOL_CAP = 5000                # baseline pool is a fixed-seed sample of this many eligible non-KOL wallets
MIN_N = 100


def load_roster(path: Path) -> list[str]:
    return [r["wallet"] for r in json.loads(Path(path).read_text(encoding="utf-8"))["wallets"]]


def eligible(db: sqlite3.Connection, w: Window) -> set[int]:
    return {wid for wid, n in db.execute(
        "SELECT wallet_id, COUNT(DISTINCT mint_id) FROM trades WHERE is_buy=1 AND sol_lamports>=? AND ts>=? "
        "AND ts<=? GROUP BY wallet_id", (BIG, w.start, w.end)) if n >= MIN_TOKENS}


def own_returns(db: sqlite3.Connection, rows: list[dict]) -> list[float]:
    """The KOL's own net return on the copied tokens: its trigger buy price -> its first sell price (same costs).
    Report only: the gap to the copy return is what the delay costs a follower."""
    out = []
    for r in rows:
        if r["kind"] != "wallet_sold":
            continue
        b = db.execute("SELECT sol_lamports * 1.0 / token_raw FROM trades WHERE wallet_id=? AND mint_id=? AND ts=? "
                       "AND is_buy=1 ORDER BY sol_lamports DESC LIMIT 1", (r["wallet"], r["mint"], r["trigger"])).fetchone()
        s = db.execute("SELECT sol_lamports * 1.0 / token_raw FROM trades WHERE wallet_id=? AND mint_id=? AND is_buy=0 "
                       "AND ts>=? ORDER BY ts LIMIT 1", (r["wallet"], r["mint"], r["trigger"])).fetchone()
        if b and s:
            out.append(net_pct(b[0], s[0]))
    return out


def run(db_path: str, roster_path: str, days: float = 21.0, draws: int = DRAWS, seed: int = 13) -> dict:
    db = sqlite3.connect(db_path)
    try:
        w0 = window(db, days)
        w = Window(w0.start, w0.start, w0.end)               # no formation period: the whole window is the test
        ids = dict(db.execute("SELECT key, id FROM names WHERE kind='wallet'"))
        roster = load_roster(Path(roster_path))
        kol_ids = {ids[k] for k in roster if k in ids}
        elig = eligible(db, w)
        kols = sorted(kol_ids & elig)
        main = copies(db, w, kols)
        s = summary(main)
        others = sorted(elig - kol_ids)
        rng = random.Random(seed)
        pool = rng.sample(others, POOL_CAP) if len(others) > POOL_CAP else others
        per_wallet = {x: copies(db, w, [x]) for x in pool}
        means = []
        k = len(kols)
        if k and len(pool) >= k:
            for _ in range(draws):
                rows = [r for x in rng.sample(pool, k) for r in per_wallet[x]]
                if rows:
                    means.append(sum(r["net_pct"] for r in rows) / len(rows))
        obs = s.get("mean_net_pct")
        p = round(sum(1 for m in means if m >= obs) / len(means), 4) if means and obs is not None else None
        own = own_returns(db, main)
        checks = {"n>=100": s.get("n", 0) >= MIN_N,
                  "ci_wallet_lower>0": bool(s.get("ci95_by_wallet")) and s["ci95_by_wallet"][0] > 0,
                  "random_p<0.05": p is not None and p < 0.05}
        verdict = "PASS" if all(checks.values()) else ("INCONCLUSIVE" if not checks["n>=100"] else "REJECT")
        return {"window": w.__dict__, "roster": len(roster), "kols_seen": len(kol_ids), "kols_eligible": k,
                "test": s, "random_baseline": {"draws": len(means), "pool": len(pool), "pool_all": len(others),
                                               "mean_of_means": round(sum(means) / len(means), 3) if means else None,
                                               "p_random_ge_observed": p},
                "kol_own": {"n": len(own), "mean_net_pct": round(sum(own) / len(own), 3) if own else None},
                "delay_10s": summary(copies(db, w, kols, delay_s=10)), "delay_s": DELAY_S,
                "checks": checks, "verdict": verdict}
    finally:
        db.close()
