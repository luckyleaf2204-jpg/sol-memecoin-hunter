"""Price behaviour after a wallet's entry, from HISTORICAL price bars only.

* Price at horizon h (5m … 7d) = close of the last bar at or before entry+h, provided the bar
  is not older than `max_staleness(h)`; otherwise unknown. The current price is never used
  for a past horizon.
* Validated peak ("token reached Nx"): a bar is eligible only if its traded volume >=
  `min_bar_volume_sol` and (when known) its liquidity >= `min_liquidity_sol`. Its level is the
  bar high when the bar has >= 2 trades (onchain: high from trades >= min trade size),
  otherwise the close. A level counts only if at least `confirm_points` eligible bars inside
  `confirm_window` reach it (the support of bar i = the confirm_points-th highest level in
  [t_i, t_i + window]). This rejects single-print wicks on thin liquidity.
"""
from __future__ import annotations

import bisect
from dataclasses import dataclass
from typing import Iterable, Sequence

HORIZONS = {"5m": 300, "15m": 900, "30m": 1800, "1h": 3600, "6h": 21600, "24h": 86400, "7d": 604800}
MULTIPLES = (2, 3, 5, 10)


@dataclass
class Bar:
    ts: int                     # bucket start (unix seconds)
    close: float
    high: float | None = None
    high_confirmed: float | None = None   # onchain bars: high of trades >= min trade size
    volume_sol: float = 0.0
    trades: int = 0
    liquidity_sol: float | None = None
    interval: int = 60
    open: float | None = None


@dataclass
class PeakConfig:
    min_bar_volume_sol: float = 1.0
    min_liquidity_sol: float = 3.0
    confirm_points: int = 2
    confirm_window: int = 300
    window_seconds: int = 7 * 86400


def max_staleness(h: int) -> int:
    return max(120, min(h // 2, 6 * 3600))


def price_at(bars: Sequence[Bar], ts_index: list[int], t: int, staleness: int) -> float | None:
    i = bisect.bisect_right(ts_index, t) - 1
    if i < 0:
        return None
    b = bars[i]
    if t - (b.ts + b.interval) > staleness:
        return None
    return b.close


def horizon_returns(bars: Sequence[Bar], entry_time: int, entry_price: float, now: int) -> dict[str, float | None]:
    out: dict[str, float | None] = {}
    if not bars or not entry_price:
        return {k: None for k in HORIZONS}
    idx = [b.ts for b in bars]
    for k, h in HORIZONS.items():
        t = entry_time + h
        if t > now:
            out[k] = None
            continue
        p = price_at(bars, idx, t, max_staleness(h))
        out[k] = (p / entry_price - 1.0) if p else None
    return out


def _level(b: Bar) -> float:
    if b.high_confirmed is not None:
        return b.high_confirmed
    if b.high is not None and b.trades >= 2:
        return b.high
    return b.close


def validated_peak(bars: Sequence[Bar], start: int, cfg: PeakConfig) -> tuple[float | None, int | None, int]:
    """Return (peak_price, peak_ts, eligible_bar_count) within [start, start + window]."""
    end = start + cfg.window_seconds
    elig = [b for b in bars if start <= b.ts <= end and b.volume_sol >= cfg.min_bar_volume_sol
            and (b.liquidity_sol is None or b.liquidity_sol >= cfg.min_liquidity_sol)]
    if not elig:
        return None, None, 0
    if cfg.confirm_points <= 1:
        best = max(elig, key=_level)
        return _level(best), best.ts, len(elig)
    best_p, best_t = None, None
    j = 0
    levels = [_level(b) for b in elig]
    for i, b in enumerate(elig):
        window_levels = []
        j = i
        while j < len(elig) and elig[j].ts <= b.ts + cfg.confirm_window:
            window_levels.append(levels[j])
            j += 1
        if len(window_levels) < cfg.confirm_points:
            continue
        window_levels.sort(reverse=True)
        support = window_levels[cfg.confirm_points - 1]
        if best_p is None or support > best_p:
            best_p, best_t = support, b.ts
    return best_p, best_t, len(elig)


def analyse_entry(bars: Sequence[Bar], entry_time: int, entry_price: float | None, now: int,
                  cfg: PeakConfig) -> dict:
    """Everything the position table needs about price after entry."""
    if not entry_price or not bars:
        return {"price_data": False, "peak_multiple": None, "peak_at": None, "peak_validated": None,
                "hits": {m: None for m in MULTIPLES}, "horizons": {k: None for k in HORIZONS}}
    peak, peak_t, n = validated_peak(bars, entry_time, cfg)
    mult = (peak / entry_price) if peak else None
    hits = {m: (mult is not None and mult >= m) if peak is not None else None for m in MULTIPLES}
    return {"price_data": True, "peak_price": peak, "peak_multiple": mult, "peak_at": peak_t,
            "peak_validated": peak is not None, "eligible_bars": n, "hits": hits,
            "horizons": horizon_returns(bars, entry_time, entry_price, now)}


def bars_from_rows(rows: Iterable[dict]) -> list[Bar]:
    out = []
    for r in rows:
        ts = r["bucket_start"]
        ts = int(ts.timestamp()) if hasattr(ts, "timestamp") else int(ts)
        f = lambda k: float(r[k]) if r.get(k) is not None else None  # noqa: E731
        out.append(Bar(ts=ts, close=f("close_sol") or 0.0, high=f("high_sol"),
                       high_confirmed=f("high_confirmed_sol"), volume_sol=f("volume_sol") or 0.0,
                       trades=int(r.get("trades") or 0), liquidity_sol=f("liquidity_sol"),
                       interval=int(r.get("interval") or 60), open=f("open_sol")))
    out.sort(key=lambda b: b.ts)
    return [b for b in out if b.close and b.close > 0]
