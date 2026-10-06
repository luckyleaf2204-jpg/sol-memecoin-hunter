"""Smart-money copy test exactly as pre-registered (docs/smart_money_plan.md, amendments 1-2). Reads the recorder's
SQLite database; nothing here is tuned on the data. Research only."""
from __future__ import annotations

import random
import sqlite3
from dataclasses import dataclass

LAMPORTS = 1_000_000_000
BIG = 50_000_000                       # 0.05 SOL
MIN_TOKENS = 5
MIN_AVG_BUY = 500_000_000              # 0.5 SOL
TOP_N = 20
DELAY_S = 3
MAX_HOLD_S = 24 * 3600
GAP_EXCLUDE_S = 300
SIDE_COST = 0.0125                     # 0.25 % pool fee + 1.0 % impact / slippage, per side
FIXED_SOL = 2 * (0.00011 + 0.005)
POSITION_SOL = 0.33
DRAWS = 2000


@dataclass
class Window:
    start: int
    cut: int
    end: int


def window(db: sqlite3.Connection, days: float = 21.0) -> Window:
    t0, t1 = db.execute("SELECT MIN(ts), MAX(ts) FROM trades").fetchone()
    end = min(t1, t0 + int(days * 86400))
    return Window(t0, t0 + (end - t0) // 2, end)


def net_pct(entry: float, exit_: float) -> float:
    return 100 * ((exit_ / entry) * (1 - SIDE_COST) ** 2 - 1 - FIXED_SOL / POSITION_SOL)


def _price_at_or_after(db, mint: int, t: int, limit: int):
    r = db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM trades WHERE mint_id=? AND ts>=? AND ts<=? "
                   "ORDER BY ts LIMIT 1", (mint, t, limit)).fetchone()
    return r


def _last_price_before(db, mint: int, t: int):
    return db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM trades WHERE mint_id=? AND ts<=? "
                      "ORDER BY ts DESC LIMIT 1", (mint, t)).fetchone()


def _completion(db) -> dict:
    return dict(db.execute("SELECT mint_id, ts FROM completes"))


def select_wallets(db: sqlite3.Connection, w: Window) -> dict:
    """Formation period: eligible wallets and the top TOP_N by realized profit (SOL)."""
    elig = {wid: (n, avg) for wid, n, avg in db.execute(
        "SELECT wallet_id, COUNT(DISTINCT mint_id), AVG(sol_lamports) FROM trades "
        "WHERE ts < ? AND is_buy = 1 AND sol_lamports >= ? GROUP BY wallet_id", (w.cut, BIG))
        if n >= MIN_TOKENS and avg >= MIN_AVG_BUY}
    done = _completion(db)
    profit: dict[int, float] = {}
    price_cache: dict[int, float] = {}
    rows = db.execute(
        "SELECT wallet_id, mint_id, SUM(CASE WHEN is_buy=1 THEN sol_lamports ELSE 0 END), "
        "SUM(CASE WHEN is_buy=0 THEN sol_lamports ELSE 0 END), "
        "SUM(CASE WHEN is_buy=1 THEN token_raw ELSE -token_raw END) FROM trades WHERE ts < ? "
        "GROUP BY wallet_id, mint_id", (w.cut,))
    for wid, mint, sol_in, sol_out, held in rows:
        if wid not in elig:
            continue
        value = 0.0
        if held > 0:
            if mint not in price_cache:
                t = min(w.cut - 1, done.get(mint, w.cut))
                r = _last_price_before(db, mint, t)
                price_cache[mint] = r[1] if r else 0.0
            value = held * price_cache[mint]
        profit[wid] = profit.get(wid, 0.0) + (sol_out + value - sol_in) / LAMPORTS
    ranked = sorted(profit, key=lambda k: -profit[k])
    return {"eligible": sorted(elig), "selected": ranked[:TOP_N], "profit_sol": profit}


def copies(db: sqlite3.Connection, w: Window, wallets: list[int], gaps: list[tuple] | None = None,
           delay_s: int = DELAY_S) -> list[dict]:
    """Copy trades of these wallets in the test period, exactly per the plan."""
    done = _completion(db)
    gaps = gaps if gaps is not None else [(a, b) for a, b in db.execute("SELECT start, end FROM gaps")
                                          if b - a > GAP_EXCLUDE_S]
    out = []
    for wid in wallets:
        firsts = db.execute("SELECT mint_id, MIN(ts) FROM trades WHERE wallet_id=? AND is_buy=1 AND sol_lamports>=? "
                            "AND ts>=? AND ts<=? GROUP BY mint_id", (wid, BIG, w.cut, w.end)).fetchall()
        for mint, trig in firsts:
            entry = _price_at_or_after(db, mint, trig + delay_s, w.end)
            if entry is None:
                out.append({"wallet": wid, "mint": mint, "trigger": trig, "kind": "no_trade_after", "net_pct": -100.0,
                            "gross_pct": -100.0, "entry_ts": trig, "exit_ts": trig})
                continue
            sell = db.execute("SELECT MIN(ts) FROM trades WHERE wallet_id=? AND mint_id=? AND is_buy=0 AND ts>=?",
                              (wid, mint, trig)).fetchone()[0]
            cap = min(trig + MAX_HOLD_S, w.end)
            comp = done.get(mint)
            if comp is not None and comp >= entry[0] and (sell is None or comp < sell) and comp <= cap:
                ex, kind = _last_price_before(db, mint, comp), "migrated"
            elif sell is not None and sell + delay_s <= cap:
                ex, kind = _price_at_or_after(db, mint, sell + delay_s, cap) or _last_price_before(db, mint, cap), \
                    "wallet_sold"
            else:
                ex, kind = _last_price_before(db, mint, cap), ("end" if cap == w.end else "max_hold")
            if any(a < ex[0] and b > entry[0] for a, b in gaps):
                continue                                        # holding overlaps a recording gap > 5 min
            out.append({"wallet": wid, "mint": mint, "trigger": trig, "kind": kind, "entry_ts": entry[0],
                        "exit_ts": ex[0], "gross_pct": 100 * (ex[1] / entry[1] - 1),
                        "net_pct": net_pct(entry[1], ex[1])})
    return out


def cluster_ci(rows: list[dict], key: str, iters: int = DRAWS, seed: int = 7):
    g = {}
    for r in rows:
        g.setdefault(r[key], []).append(r["net_pct"])
    groups = list(g.values())
    if len(groups) < 2:
        return None
    rng = random.Random(seed)
    means = []
    for _ in range(iters):
        pick = [rng.choice(groups) for _ in groups]
        means.append(sum(sum(x) for x in pick) / sum(len(x) for x in pick))
    means.sort()
    return round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters) - 1], 3)


def summary(rows: list[dict]) -> dict:
    n = len(rows)
    if not n:
        return {"n": 0}
    nets = [r["net_pct"] for r in rows]
    kinds = {}
    for r in rows:
        kinds[r["kind"]] = kinds.get(r["kind"], 0) + 1
    return {"n": n, "wallets": len({r["wallet"] for r in rows}), "tokens": len({r["mint"] for r in rows}),
            "mean_net_pct": round(sum(nets) / n, 3), "median_net_pct": round(sorted(nets)[n // 2], 3),
            "ci95_by_wallet": cluster_ci(rows, "wallet"), "ci95_by_token": cluster_ci(rows, "mint"),
            "win_rate_pct": round(100 * sum(1 for x in nets if x > 0) / n, 1), "exit_kinds": kinds}


def run(db_path: str, days: float = 21.0, draws: int = DRAWS, seed: int = 11) -> dict:
    db = sqlite3.connect(db_path)
    try:
        w = window(db, days)
        sel = select_wallets(db, w)
        selected = sel["selected"]
        main = copies(db, w, selected)
        s = summary(main)
        others = [x for x in sel["eligible"] if x not in set(selected)]
        per_wallet = {x: copies(db, w, [x]) for x in others}
        rng = random.Random(seed)
        means = []
        if len(others) >= TOP_N:
            for _ in range(draws):
                rows = [r for x in rng.sample(others, TOP_N) for r in per_wallet[x]]
                if rows:
                    means.append(sum(r["net_pct"] for r in rows) / len(rows))
        obs = s.get("mean_net_pct")
        p = round(sum(1 for m in means if m >= obs) / len(means), 4) if means and obs is not None else None
        d10 = summary(copies(db, w, selected, delay_s=10))
        checks = {"n>=100": s.get("n", 0) >= 100,
                  "ci_wallet_lower>0": bool(s.get("ci95_by_wallet")) and s["ci95_by_wallet"][0] > 0,
                  "random_p<0.05": p is not None and p < 0.05}
        return {"window": w.__dict__, "eligible": len(sel["eligible"]), "selected": len(selected),
                "selected_formation_profit_sol": [round(sel["profit_sol"][x], 3) for x in selected],
                "test": s, "random_baseline": {"draws": len(means), "pool": len(others),
                                               "mean_of_means": round(sum(means) / len(means), 3) if means else None,
                                               "p_random_ge_observed": p},
                "delay_10s": d10, "checks": checks, "verdict": "PASS" if all(checks.values()) else "REJECT"}
    finally:
        db.close()
