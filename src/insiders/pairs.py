"""Wallet quality per (wallet, coin) pair, from the priced trades of the PnL run (result['pnl']['rows']).

A pair is a WIN when the wallet held the coin >= HOLD_S before half of what it got was gone (sold or sent) AND ended
up in profit on that coin (sales + the remainder marked to market - buys > WIN_MIN_SOL). A position never half-sold
counts too, held until the end of the data. The main list = wallets with >= 2 different winning coins (repeatable),
the watch list = exactly one.

Coins received for free (transfers in, no SOL spent) have no cost basis: their pnl counts, their multiple does not."""
from __future__ import annotations

import os
from collections import Counter, defaultdict

HOLD_S = int(os.environ.get("INSIDER_WIN_HOLD_S") or 600)
WIN_MIN_SOL = float(os.environ.get("INSIDER_WIN_MIN_SOL") or 0.5)     # a 0.1 SOL "win" is noise, not information
WIN_MIN_MULT = float(os.environ.get("INSIDER_WIN_MIN_MULT") or 1.2)   # and at least +20 % on what was spent


def pair_result(trades: list, end_ts: float, mark_sol_per_token: float = 0.0, hold_s_min: int = HOLD_S) -> dict | None:
    """trades: [ts, side, sol, tokens] with side in buy / in / sell / out. The remainder is marked at
    mark_sol_per_token (0 = worthless, the conservative default for coins without a live price)."""
    inn = out = 0.0
    sol_in = sol_out = 0.0
    t0 = t_half = None
    for ts, side, sol, tk in sorted(trades, key=lambda x: x[0]):
        if side in ("buy", "in"):
            inn += tk
            sol_in += sol if side == "buy" else 0
            t0 = ts if t0 is None else t0
        elif side in ("sell", "out") and t0 is not None:
            out += tk
            sol_out += sol if side == "sell" else 0
            if t_half is None and inn and out >= inn / 2:
                t_half = ts
    if t0 is None:
        return None
    unsold = t_half is None
    hold_s = (t_half if t_half is not None else end_ts) - t0
    remain = max(0.0, inn - out)
    value = sol_out + remain * mark_sol_per_token
    pnl = value - sol_in
    return {"t0": t0, "t_half": t_half, "hold_s": hold_s, "unsold": unsold, "sol_in": round(sol_in, 4),
            "pnl_sol": round(pnl, 4), "pnl_mult": round(value / sol_in, 4) if sol_in > 0 else None,
            "cost": "buy" if sol_in > 0 else "transfer",
            "win": hold_s >= hold_s_min and pnl > WIN_MIN_SOL and (sol_in <= 0 or value / sol_in >= WIN_MIN_MULT)}


def pairs_from(result: dict, end_ts: float, marks: dict | None = None, upto: float | None = None,
               hold_s_min: int = HOLD_S) -> dict:
    """{(wallet, mint): pair_result}. upto: only trades before this time (for the look-ahead-free backtest)."""
    marks = marks or {}
    out = {}
    for x in (result.get("pnl") or {}).get("rows", []):
        if x.get("status") != "ok" or not x.get("trades"):
            continue
        tr = [t for t in x["trades"] if upto is None or t[0] < upto]
        r = pair_result(tr, end_ts if upto is None else min(end_ts, upto), marks.get(x["mint"], 0.0), hold_s_min)
        if r:
            out[(x["wallet"], x["mint"])] = r
    return out


def build_lists(pairs: dict, exclude: set | None = None) -> tuple[set, set, Counter]:
    """(main, watch, wins per wallet). exclude: exchanges / deposits / known bots."""
    exclude = exclude or set()
    wins = Counter(w for (w, _m), r in pairs.items() if r and r["win"] and w not in exclude)
    return {w for w, n in wins.items() if n >= 2}, {w for w, n in wins.items() if n == 1}, wins


def tiers(result: dict, now: float, exclude: set | None = None) -> dict[str, str]:
    """{wallet: 'main' | 'watch'} from the whole PnL history (for live signals: the past is all known)."""
    main, watch, _ = build_lists(pairs_from(result, now), exclude)
    return {**{w: "watch" for w in watch}, **{w: "main" for w in main}}


def summary(pairs: dict, main: set, watch: set, drop: set | None = None) -> dict:
    """Totals for the report, optionally without some wallets (e.g. the CR7 deployer that dominates the profit)."""
    drop = drop or set()
    per = defaultdict(float)
    for (w, _m), r in pairs.items():
        if w not in drop:
            per[w] += r["pnl_sol"]
    return {"main": len(main - drop), "watch": len(watch - drop),
            "win_pairs": sum(1 for (w, _m), r in pairs.items() if r["win"] and w not in drop),
            "pnl_main": round(sum(per[w] for w in main - drop), 1),
            "pnl_watch": round(sum(per[w] for w in watch - drop), 1)}
