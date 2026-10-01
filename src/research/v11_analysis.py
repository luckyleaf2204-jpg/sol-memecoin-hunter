"""V1.1 research analysis helpers (pure functions; used by tools/audit_bottlenecks.py and the tests).
Descriptive statistics only — they never change a decision. Small samples are flagged."""
from __future__ import annotations

import statistics

MIN_N = 30


def slippage_bucket(total_pct: float | None) -> str:
    if total_pct is None:
        return "UNKNOWN"
    return "<1%" if total_pct < 1 else "1-2%" if total_pct < 2 else "2-3%" if total_pct < 3 else \
        "3-4%" if total_pct < 4 else ">4%"


def sim_net(path: list, t0: float, p0: float, cost_pct: float, tp: float = 0.30, sl: float = -0.15,
            horizon_s: float = 3600) -> tuple[float | None, str]:
    """Simplified trade from (t0, p0) net of a round-trip cost: entry p0*(1+c), first of TP / SL on LATER prices,
    else marked at the last observed price within the horizon. Returns (pnl %, exit)."""
    if not path or not p0:
        return None, "no_price"
    c = (cost_pct or 0) / 100
    entry = p0 * (1 + c)
    last = None
    for t, p in sorted(path):
        if t <= t0 or t > t0 + horizon_s:
            continue
        r = p / entry - 1
        last = p
        if r >= tp:
            return round(100 * (p * (1 - c) / entry - 1), 2), "tp"
        if r <= sl:
            return round(100 * (p * (1 - c) / entry - 1), 2), "sl"
    return (round(100 * (last * (1 - c) / entry - 1), 2), "mark") if last else (None, "no_price")


def pattern_effect(trades: list[dict], pred) -> dict:
    """If we had filtered trades where pred(t) is True: how many BUYs removed, fast-SL / rugs / winners removed,
    MFE given up and the expectancy change. trades: dicts with pnl_pct, mfe_pct, fast_sl, rug, win (bool)."""
    known = [t for t in trades if pred(t) is not None]
    hit = [t for t in known if pred(t)]
    keep = [t for t in trades if not (pred(t) is True)]
    mean = lambda xs: round(statistics.fmean(xs), 2) if xs else None  # noqa: E731
    return {"buys": len(trades), "flagged": len(hit), "unknown": len(trades) - len(known),
            "fast_sl_flagged": sum(1 for t in hit if t.get("fast_sl")),
            "fast_sl_total": sum(1 for t in trades if t.get("fast_sl")),
            "winners_flagged": sum(1 for t in hit if t.get("win")),
            "rugs_flagged": sum(1 for t in hit if t.get("rug")),
            "mfe_given_up_pct": [t.get("mfe_pct") for t in hit if t.get("win")],
            "expectancy_all_pct": mean([t["pnl_pct"] for t in trades if t.get("pnl_pct") is not None]),
            "expectancy_filtered_pct": mean([t["pnl_pct"] for t in keep if t.get("pnl_pct") is not None]),
            "sample": "OK" if len(trades) >= MIN_N else "INSUFFICIENT SAMPLE"}


def walk_forward_plan(day_keys: list[str]) -> dict:
    """Time-series split (never random): TRAIN = first days, VALIDATION = next, OUT-OF-SAMPLE = last.
    Needs >= 4 distinct days of data."""
    days = sorted(set(day_keys))
    if len(days) < 4:
        return {"status": f"INSUFFICIENT SAMPLE ({len(days)} day(s) of data, need >= 4)", "days": days}
    return {"status": "ok", "train": days[:-2], "validation": [days[-2]], "out_of_sample": [days[-1]]}
