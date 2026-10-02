"""SAMPLE REPORT — the decision metric for the current sample epoch (paper trading, read-only).

PRIMARY metric: net expectancy per trade = mean(gross mid-to-mid move - round-trip cost) for costs of 5, 7 and 10 %,
with a 95 % bootstrap confidence interval. Next to it: win rate, realised R:R (mean win / |mean loss|), max drawdown
(trades in exit order, $ = position cost x net %), and a STOP-GAP scenario where every stop-loss exit fills at -20 %
(or worse if it already was).
TP/(TP+SL) is shown for reference only: it mostly reflects volatility, not edge.
Only trades of the current epoch count (trading/sample_epoch.py); LEGACY and NO_ROUTE trades are excluded.
"""
from __future__ import annotations

import random

STOP_REASONS = ("stop_loss",)
TP_REASONS = ("take_profit_1", "take_profit_2", "runner_trailing_stop")
SL_GAP_PCT = -20.0
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
    if sl_gap and r.get("exit_reason") in STOP_REASONS:
        return min(g, SL_GAP_PCT)
    return g


def cost_table(rows: list[dict], levels, starting: float, sl_gap: bool = False) -> dict:
    return {f"{c:g}%": metrics(rows, [gross_of(r, sl_gap) - c for r in rows], starting) for c in levels}


def report(journal: list[dict], epoch, levels=(5.0, 7.0, 10.0), starting: float = 1000.0) -> dict:
    counted = [r for r in journal if epoch is not None and epoch.counts(r) and r.get("gross_move_pct") is not None]
    legacy = sum(1 for r in journal if epoch is None or not epoch.counts(r))
    tp = sum(1 for r in counted if r.get("exit_reason") in TP_REASONS)
    sl = sum(1 for r in counted if r.get("exit_reason") in STOP_REASONS)
    return {
        "epoch": epoch.as_dict() if epoch is not None else None,
        "n": len(counted), "excluded_legacy_or_noquote": legacy,
        "primary_metric": "net expectancy per trade after a fixed round-trip cost (95 % bootstrap CI)",
        "by_cost": cost_table(counted, levels, starting),
        "modelled_cost": metrics(counted, [r["net_pnl_pct"] for r in counted if r.get("net_pnl_pct") is not None],
                                 starting) if all(r.get("net_pnl_pct") is not None for r in counted) else None,
        "sl_gap_scenario": {"assumption": f"every stop-loss exit fills at {SL_GAP_PCT:.0f}% (or worse)",
                            "stop_exits": sl, "by_cost": cost_table(counted, levels, starting, sl_gap=True)},
        "reference_only": {"tp_share": round(tp / (tp + sl), 4) if tp + sl else None, "tp_exits": tp, "sl_exits": sl,
                           "note": "TP/(TP+SL) mostly reflects volatility; NOT a decision metric"},
    }
