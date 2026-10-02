"""SAMPLE REPORT — the decision metric for the current sample epoch (paper trading, read-only).

PRIMARY metric: net expectancy per trade = mean(gross mid-to-mid move - round-trip cost); an exit filled with the
no-quote haircut counts at its haircut price (the haircut is part of the gross move, not a removable cost) for costs of 5, 7 and 10 %,
with a 95 % bootstrap confidence interval. Next to it: win rate, realised R:R (mean win / |mean loss|), max drawdown
(trades in exit order, $ = position cost x net %), and a STOP-GAP scenario where every HARD exit (stop loss, risk,
liquidity, whale, holder, identity) fills at -20 % (or worse if it already was).
TP/(TP+SL) is shown for reference only: it mostly reflects volatility, not edge.
Only trades of the current epoch count (trading/sample_epoch.py); LEGACY and NO_ROUTE trades are excluded.
"""
from __future__ import annotations

import random

from trading.exits import HARD

STOP_REASONS = ("stop_loss",)
GAP_REASONS = HARD               # stop gap floor applies to every HARD exit (stop, risk, liquidity, whale, ...)
TP_REASONS = ("take_profit_1", "take_profit_2", "runner_trailing_stop")
SL_GAP_PCT = -20.0
MIN_PRELIMINARY_N = 30          # per sample: preliminary check only (docs/sample_plan.md)
MIN_AB_N = 200                  # per arm: needed to compare exit variants
BOOT_ITERS = 2000


def bootstrap_ci(values: list[float], iters: int = BOOT_ITERS, seed: int = 7) -> tuple[float, float] | None:
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    n = len(values)
    means = sorted(sum(rng.choices(values, k=n)) / n for _ in range(iters))
    return round(means[int(0.025 * iters)], 3), round(means[int(0.975 * iters) - 1], 3)


def max_drawdown(rows: list[dict], nets: list[float], starting: float) -> dict:
    eq = peak = dd = 0.0
    for r, n in sorted(zip(rows, nets), key=lambda x: x[0].get("exit_ts") or 0):
        eq += (r.get("cost_usd") or 0.0) * n / 100
        peak = max(peak, eq)
        dd = max(dd, peak - eq)
    return {"usd": round(dd, 2), "pct_of_start": round(100 * dd / starting, 2) if starting else None}


def metrics(rows: list[dict], nets: list[float], starting: float) -> dict:
    n = len(nets)
    wins, losses = [x for x in nets if x > 0], [x for x in nets if x <= 0]
    return {"n": n, "expectancy_pct": round(sum(nets) / n, 3) if n else None, "ci95_pct": bootstrap_ci(nets),
            "win_rate_pct": round(100 * len(wins) / n, 1) if n else None,
            "avg_win_pct": round(sum(wins) / len(wins), 3) if wins else None,
            "avg_loss_pct": round(sum(losses) / len(losses), 3) if losses else None,
            "rr_realised": round((sum(wins) / len(wins)) / abs(sum(losses) / len(losses)), 3)
            if wins and losses and sum(losses) else None,
            "max_drawdown": max_drawdown(rows, nets, starting),
            "total_usd": round(sum((r.get("cost_usd") or 0) * x / 100 for r, x in zip(rows, nets)), 2)}


def gross_of(r: dict, sl_gap: bool = False) -> float:
    g = r["gross_move_pct"]
    if sl_gap and r.get("exit_reason") in GAP_REASONS:
        return min(g, SL_GAP_PCT)
    return g


def cost_table(rows: list[dict], levels, starting: float, sl_gap: bool = False) -> dict:
    return {f"{c:g}%": metrics(rows, [gross_of(r, sl_gap) - c for r in rows], starting) for c in levels}


def haircut_split(counted: list[dict], levels, starting: float) -> dict:
    """Step D: exits that had NO Jupiter SELL quote and were filled with the haircut (default 30 %) — reported
    apart, and the sample without them."""
    hc = [r for r in counted if r.get("haircut")]
    rest = [r for r in counted if not r.get("haircut")]
    return {"n_haircut_trades": len(hc), "share_pct": round(100 * len(hc) / len(counted), 1) if counted else None,
            "haircut_trades": cost_table(hc, levels, starting), "without_haircut_trades": cost_table(rest, levels, starting),
            "note": "haircut fills are an assumption (no executable quote existed); judge the strategy on both"}


def warnings(n: int, by_cost: dict) -> tuple[str, list[str]]:
    w = []
    if n < MIN_PRELIMINARY_N:
        status = "INSUFFICIENT"
        w.append(f"n = {n} < {MIN_PRELIMINARY_N}: not even a preliminary check — no conclusion")
    elif n < MIN_AB_N:
        status = "PRELIMINARY"
        w.append(f"n = {n}: preliminary only; comparing exit variants (A/B) needs >= {MIN_AB_N} trades per arm")
    else:
        status = "OK"
    for k, m in by_cost.items():
        ci = m.get("ci95_pct")
        if ci and ci[0] <= 0 <= ci[1]:
            w.append(f"at {k} cost the 95 % CI [{ci[0]}, {ci[1]}] includes 0: no evidence of a positive or negative edge")
    return status, w


def report(journal: list[dict], epoch, levels=(5.0, 7.0, 10.0), starting: float = 1000.0,
           gaps: list[dict] | None = None, now: float | None = None) -> dict:
    import time
    from trading.gaps import overlaps
    now = now or time.time()
    gaps = gaps or []
    in_epoch = [r for r in journal if epoch is not None and epoch.counts(r) and r.get("gross_move_pct") is not None]
    legacy = sum(1 for r in journal if epoch is None or not epoch.counts(r))
    start = epoch.started_at if epoch is not None and epoch.started_at else 0.0
    epoch_gaps = [g for g in gaps if g["end"] >= start]
    in_gap = [r for r in in_epoch if overlaps(r, epoch_gaps, now)]
    counted = [r for r in in_epoch if not overlaps(r, epoch_gaps, now)]
    tp = sum(1 for r in counted if r.get("exit_reason") in TP_REASONS)
    sl = sum(1 for r in counted if r.get("exit_reason") in STOP_REASONS)
    by_cost = cost_table(counted, levels, starting)
    status, warns = warnings(len(counted), by_cost)
    return {
        "epoch": epoch.as_dict() if epoch is not None else None,
        "sample_status": status, "warnings": warns,
        "n": len(counted), "excluded_legacy_or_noquote": legacy,
        "gaps": {"count": len(epoch_gaps), "minutes": round(sum(g["minutes"] for g in epoch_gaps), 1),
                 "excluded_trades": len(in_gap), "list": epoch_gaps[-20:],
                 "rule": "trades whose holding period overlaps a gap (bot stopped / feed down > 5 min) are excluded"},
        "primary_metric": "net expectancy per trade after a fixed round-trip cost (95 % bootstrap CI)",
        "by_cost": by_cost,
        "modelled_cost": metrics(counted, [r["net_pnl_pct"] for r in counted if r.get("net_pnl_pct") is not None],
                                 starting) if all(r.get("net_pnl_pct") is not None for r in counted) else None,
        "sl_gap_scenario": {"assumption": f"every HARD exit ({', '.join(GAP_REASONS)}) fills at "
                                          f"{SL_GAP_PCT:.0f}% (or worse)",
                            "stop_exits": sl, "hard_exits": sum(1 for r in counted if r.get("exit_reason") in GAP_REASONS),
                            "by_cost": cost_table(counted, levels, starting, sl_gap=True)},
        "haircut": haircut_split(counted, levels, starting),
        "reference_only": {"tp_share": round(tp / (tp + sl), 4) if tp + sl else None, "tp_exits": tp, "sl_exits": sl,
                           "note": "TP/(TP+SL) mostly reflects volatility; NOT a decision metric"},
    }


def summary_line(r: dict) -> str:
    """One line printed by every report: n, status, net expectancy with its 95 % CI at each cost, gaps."""
    parts = []
    for k, m in (r.get("by_cost") or {}).items():
        ci = m.get("ci95_pct")
        parts.append(f"@{k} {m['expectancy_pct'] if m['expectancy_pct'] is not None else '-'}%"
                     f"{f' [{ci[0]}, {ci[1]}]' if ci else ' [CI n/a]'}")
    ep = r.get("epoch") or {}
    g = r.get("gaps") or {}
    return (f"SAMPLE {r.get('sample_status')} n={r.get('n')} (thresholds 30 preliminary / 200 A/B) · "
            f"net expectancy {' · '.join(parts)} · gaps {g.get('count', 0)} ({g.get('excluded_trades', 0)} trades "
            f"excluded) · epoch since {ep.get('started_at_utc')} params {ep.get('fingerprint')}")
