"""Backtest of stored signals (basic version; full Phase 3 comes later).

No look-ahead: the signal is the FIRST snapshot where the score stored at that moment
reached the threshold AND that snapshot's data quality was VALID (legacy rows without a
data-quality status, scored by the old V1 formula, are ignored as signals). Only prices recorded AFTER the signal are used for outcomes.
A window counts only if the token was still being recorded near the end of it;
otherwise that signal is "insufficient data" for that window.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass

WINDOWS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "24h": 86400}
TARGETS = {"+20%": 1.2, "+50%": 1.5, "2x": 2.0, "5x": 5.0, "10x": 10.0}
DRAWDOWN = 0.5  # counts signals that fell ≥50% within the window


@dataclass
class BacktestRow:
    threshold: int
    window: str
    signals: int
    evaluated: int
    hits: dict[str, float]      # target -> % of evaluated signals
    drop50_pct: float
    median_max_gain_pct: float | None


SIGNAL_COLUMNS = ("score", "early_signal")


def load_series(conn: sqlite3.Connection, column: str = "score") -> dict[str, list[tuple[float, float, int | None]]]:
    if column not in SIGNAL_COLUMNS:
        raise ValueError(column)
    series: dict[str, list] = {}
    for mint, ts, price, score, dq_status in conn.execute(
            f"SELECT mint, ts, price, {column}, dq_status FROM snapshots WHERE price IS NOT NULL AND price>0 "
            "AND (dq_status IS NULL OR dq_status != 'INVALID') ORDER BY mint, ts"):
        # outcomes: any non-INVALID price; signals: only VALID rows
        series.setdefault(mint, []).append((ts, price, score if dq_status == "VALID" else None))
    return series


def run_backtest(conn: sqlite3.Connection, thresholds=(70, 80, 90), column: str = "score") -> list[BacktestRow]:
    """column = "score" (Opportunity) or "early_signal" (Early Signal strength)."""
    series = load_series(conn, column)
    out: list[BacktestRow] = []
    for thr in thresholds:
        signals = []
        for pts in series.values():
            idx = next((k for k, p in enumerate(pts) if p[2] is not None and p[2] >= thr), None)
            if idx is not None:
                signals.append((pts, idx))
        for wname, wsec in WINDOWS.items():
            evaluated, hits, drops, gains = 0, {t: 0 for t in TARGETS}, 0, []
            for pts, idx in signals:
                t0, p0, _ = pts[idx]
                after = [p for p in pts[idx + 1:] if p[0] <= t0 + wsec]
                if not after or after[-1][0] < t0 + wsec * 0.8:
                    continue  # not enough recorded data to judge this window
                evaluated += 1
                mx = max(p[1] for p in after) / p0
                mn = min(p[1] for p in after) / p0
                gains.append((mx - 1) * 100)
                for t, mult in TARGETS.items():
                    if mx >= mult:
                        hits[t] += 1
                if mn <= DRAWDOWN:
                    drops += 1
            gains.sort()
            out.append(BacktestRow(
                threshold=thr, window=wname, signals=len(signals), evaluated=evaluated,
                hits={t: (100 * n / evaluated if evaluated else 0.0) for t, n in hits.items()},
                drop50_pct=100 * drops / evaluated if evaluated else 0.0,
                median_max_gain_pct=gains[len(gains) // 2] if gains else None))
    return out
