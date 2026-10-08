"""Smart-money copy test exactly as pre-registered (docs/smart_money_plan.md, amendments 1-6). Reads the recorder's
SQLite database; nothing here is tuned on the data. Research only.

Amendment 4: a copy is excluded when a recording gap > 5 min overlaps ANY part of it — signal -> entry -> exit — and a
"no trade after the signal" copy counts -100 % only when no gap overlaps [signal, window end].
Amendment 5: a token that completes its curve (migrates to PumpSwap) while a copy is open is valued on PumpSwap
trades (`amm_trades`, filled after the window by a separate fetcher). Without that data the copy is UNRESOLVED and
the run is BLOCKED: the last curve price is never used as a post-migration exit.
Amendment 6: the window is fixed (first valid trade + 21 days); analysis refuses to run before its end."""
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
    """Amendment 6: fixed window = first valid trade + `days`; downtime or an early stop does NOT shorten it."""
    t0 = db.execute("SELECT MIN(ts) FROM trades").fetchone()[0]
    end = t0 + int(days * 86400)
    return Window(t0, t0 + (end - t0) // 2, end)


def analysis_allowed(db: sqlite3.Connection, days: float = 21.0, now: float | None = None) -> tuple[bool, int]:
    """Amendment 6 stopping rule: no B / C analysis before the pre-registered window end (no peeking, no early stop)."""
    import time
    end = window(db, days).end
    return (time.time() if now is None else now) >= end, end


def net_pct(entry: float, exit_: float) -> float:
    return 100 * ((exit_ / entry) * (1 - SIDE_COST) ** 2 - 1 - FIXED_SOL / POSITION_SOL)


AMM_SCHEMA = """
CREATE TABLE IF NOT EXISTS amm_trades (ts INTEGER NOT NULL, mint_id INTEGER NOT NULL, wallet_id INTEGER NOT NULL,
                                       is_buy INTEGER NOT NULL, sol_lamports INTEGER NOT NULL, token_raw INTEGER NOT NULL,
                                       source TEXT);
CREATE TABLE IF NOT EXISTS amm_fetch (mint_id INTEGER PRIMARY KEY, from_ts INTEGER, to_ts INTEGER, source TEXT,
                                      fetched_at REAL);
CREATE TABLE IF NOT EXISTS amm_wallet_fetch (mint_id INTEGER NOT NULL, wallet_id INTEGER NOT NULL, from_ts INTEGER,
                                             to_ts INTEGER, source TEXT, PRIMARY KEY (mint_id, wallet_id));
CREATE INDEX IF NOT EXISTS ix_amm_mint_ts ON amm_trades(mint_id, ts);
"""
# amm_trades: PumpSwap trades after a curve completed (wallet_id 0 = price-only row, wallet unknown).
# amm_fetch: the token's PumpSwap PRICE path is complete for [from_ts, to_ts].
# amm_wallet_fetch: that wallet's PumpSwap trades of that token are complete for [from_ts, to_ts].


def _has_amm(db) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='amm_trades'").fetchone() is not None


def _price_at_or_after(db, mint: int, t: int, limit: int):
    """First trade price at or after t (curve, and PumpSwap when recorded)."""
    best = db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM trades WHERE mint_id=? AND ts>=? AND ts<=? "
                      "ORDER BY ts LIMIT 1", (mint, t, limit)).fetchone()
    if _has_amm(db):
        a = db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM amm_trades WHERE mint_id=? AND ts>=? AND ts<=? "
                       "ORDER BY ts LIMIT 1", (mint, t, limit)).fetchone()
        if a is not None and (best is None or a[0] < best[0]):
            best = a
    return best


def _last_price_before(db, mint: int, t: int):
    """Last trade price at or before t (curve, and PumpSwap when recorded)."""
    best = db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM trades WHERE mint_id=? AND ts<=? "
                      "ORDER BY ts DESC LIMIT 1", (mint, t)).fetchone()
    if _has_amm(db):
        a = db.execute("SELECT ts, sol_lamports * 1.0 / token_raw FROM amm_trades WHERE mint_id=? AND ts<=? "
                       "ORDER BY ts DESC LIMIT 1", (mint, t)).fetchone()
        if a is not None and (best is None or a[0] > best[0]):
            best = a
    return best


def _first_sell(db, wallet: int, mint: int, t: int):
    s = db.execute("SELECT MIN(ts) FROM trades WHERE wallet_id=? AND mint_id=? AND is_buy=0 AND ts>=?",
                   (wallet, mint, t)).fetchone()[0]
    if _has_amm(db):
        a = db.execute("SELECT MIN(ts) FROM amm_trades WHERE wallet_id=? AND mint_id=? AND is_buy=0 AND ts>=?",
                       (wallet, mint, t)).fetchone()[0]
        if a is not None and (s is None or a < s):
            s = a
    return s


def _amm_resolved(db, mint: int, wallet: int, start: int, end: int) -> bool:
    """PumpSwap price path AND this wallet's PumpSwap activity are both complete over [start, end]."""
    if not _has_amm(db):
        return False
    p = db.execute("SELECT 1 FROM amm_fetch WHERE mint_id=? AND from_ts<=? AND to_ts>=?", (mint, start, end)).fetchone()
    w = db.execute("SELECT 1 FROM amm_wallet_fetch WHERE mint_id=? AND wallet_id=? AND from_ts<=? AND to_ts>=?",
                   (mint, wallet, start, end)).fetchone()
    return p is not None and w is not None


def _overlaps(gaps: list[tuple], start: float, end: float) -> bool:
    return any(a < end and b > start for a, b in gaps)


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


def copies_with_status(db: sqlite3.Connection, w: Window, wallets: list[int], gaps: list[tuple] | None = None,
                       delay_s: int = DELAY_S) -> list[dict]:
    """Every copy of these wallets in the test period with its status: 'ok' (counted), 'gap' (excluded: a recording
    gap > 5 min overlaps signal -> exit, amendment 4) or 'unresolved' (the curve completed while the copy was open and
    PumpSwap data is missing, amendment 5)."""
    done = _completion(db)
    gaps = gaps if gaps is not None else [(a, b) for a, b in db.execute("SELECT start, end FROM gaps")
                                          if b - a > GAP_EXCLUDE_S]
    out = []
    for wid in wallets:
        firsts = db.execute("SELECT mint_id, MIN(ts) FROM trades WHERE wallet_id=? AND is_buy=1 AND sol_lamports>=? "
                            "AND ts>=? AND ts<=? GROUP BY mint_id", (wid, BIG, w.cut, w.end)).fetchall()
        for mint, trig in firsts:
            base = {"wallet": wid, "mint": mint, "trigger": trig}
            comp = done.get(mint)
            entry = _price_at_or_after(db, mint, trig + delay_s, w.end)
            if entry is None:                                   # nothing traded after the signal
                if _overlaps(gaps, trig, w.end):
                    out.append({**base, "status": "gap", "kind": "no_trade_after", "net_pct": None})
                elif comp is not None and trig <= comp <= w.end and not _amm_resolved(db, mint, wid, comp, w.end):
                    out.append({**base, "status": "unresolved", "kind": "no_trade_after", "net_pct": None})
                else:
                    out.append({**base, "status": "ok", "kind": "no_trade_after", "net_pct": -100.0,
                                "gross_pct": -100.0, "entry_ts": trig, "exit_ts": trig, "span_end": w.end})
                continue
            cap = min(trig + MAX_HOLD_S, w.end)
            sell = _first_sell(db, wid, mint, trig)
            if sell is not None and sell + delay_s <= cap:
                decide, kind = sell + delay_s, "wallet_sold"
                ex = _price_at_or_after(db, mint, decide, cap) or _last_price_before(db, mint, cap)
            else:
                decide, kind = cap, ("end" if cap == w.end else "max_hold")
                ex = _last_price_before(db, mint, cap)
            span_end = max(decide, ex[0])
            migrated = comp is not None and trig <= comp <= span_end
            if _overlaps(gaps, trig, span_end):
                out.append({**base, "status": "gap", "kind": kind, "net_pct": None})
                continue
            if migrated and not _amm_resolved(db, mint, wid, comp, span_end):
                out.append({**base, "status": "unresolved", "kind": kind + "+migrated", "net_pct": None})
                continue
            out.append({**base, "status": "ok", "kind": kind + ("+migrated" if migrated else ""),
                        "entry_ts": entry[0], "exit_ts": ex[0], "span_end": span_end,
                        "gross_pct": 100 * (ex[1] / entry[1] - 1), "net_pct": net_pct(entry[1], ex[1])})
    return out


def copies(db: sqlite3.Connection, w: Window, wallets: list[int], gaps: list[tuple] | None = None,
           delay_s: int = DELAY_S) -> list[dict]:
    """The counted copies (status 'ok') — the input of every metric."""
    return [r for r in copies_with_status(db, w, wallets, gaps, delay_s) if r["status"] == "ok"]


def status_counts(rows: list[dict]) -> dict:
    out = {}
    for r in rows:
        out[r["status"]] = out.get(r["status"], 0) + 1
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
        main_all = copies_with_status(db, w, selected)
        main = [r for r in main_all if r["status"] == "ok"]
        s = summary(main)
        others = [x for x in sel["eligible"] if x not in set(selected)]
        per_all = {x: copies_with_status(db, w, [x]) for x in others}
        per_wallet = {x: [r for r in rows if r["status"] == "ok"] for x, rows in per_all.items()}
        unresolved = sum(1 for r in main_all if r["status"] == "unresolved") + \
            sum(1 for rows in per_all.values() for r in rows if r["status"] == "unresolved")
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
        verdict = "PASS" if all(checks.values()) else "REJECT"
        if unresolved:                                          # amendment 5: never decide on curve-price exits
            verdict = "BLOCKED_MIGRATION_DATA"
        return {"window": w.__dict__, "eligible": len(sel["eligible"]), "selected": len(selected),
                "copy_status_selected": status_counts(main_all), "unresolved_total": unresolved,
                "selected_formation_profit_sol": [round(sel["profit_sol"][x], 3) for x in selected],
                "test": s, "random_baseline": {"draws": len(means), "pool": len(others),
                                               "mean_of_means": round(sum(means) / len(means), 3) if means else None,
                                               "p_random_ge_observed": p},
                "delay_10s": d10, "checks": checks, "verdict": verdict}
    finally:
        db.close()
