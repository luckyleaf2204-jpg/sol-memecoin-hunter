"""Is this wallet a person you can copy?

Two independent questions, both answered from on-chain trades only:

1. HUMAN or BOT (`classify_behavior`). A person using a trading app (Axiom, Photon, Phantom…)
   cannot do the things below for long; an automated program does them all the time:
     * many trades inside one minute                (max_trades_per_minute)
     * hundreds of trades per active day             (trades_per_active_day)
     * trades on DIFFERENT tokens a few seconds apart (multi_token_burst_share)
     * buying within seconds of the token's creation  (snipe_share)
     * selling within seconds of buying               (quick_flip_share)
     * busy days with no 4-hour rest at all            (no_rest_day_share)
   Each test has a "likely bot" and a "bot" threshold. BOT = one hard hit or three soft hits,
   LIKELY_BOT = one or two soft hits, HUMAN = none, UNKNOWN = fewer than 10 trades.

2. Could a follower who sees the trade LATE still win? (`simulate_copy`). The follower mirrors
   the wallet: every buy is copied `delay` seconds later with the same SOL, every sell (same
   fraction of holdings) `delay` seconds later. The follower's price = the wallet's own executed
   price x (market price at t + delay / market price at t). The market price curve is
   interpolated from historical 1-minute bars (open at the bar start, close at the bar end;
   on-chain trades, GeckoTerminal as fallback), so a 0-second delay reproduces the wallet's real
   result exactly and a longer delay shows how far the price ran while the follower was late.
   A written-off bag is a total loss for the follower too. Round-trip costs (fees + slippage)
   are subtracted. No price for any needed moment -> "unpriced", never guessed.
   A sell that the wallet makes before the follower could even be in (less than max(delay, 60 s)
   after the wallet's first buy) earns the follower nothing: that part is sold at the follower's
   own entry price (flat, minus costs). This removes profits that only a sniper's instant
   execution could capture, and moves inside one 1-minute bar that the data cannot resolve.
"""
from __future__ import annotations

import bisect
import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

from .price_hits import Bar

LABELS = ("HUMAN", "LIKELY_BOT", "BOT", "UNKNOWN")

# (metric, likely-bot threshold, bot threshold, minimum sample needed for the test)
THRESHOLDS = {
    "max_trades_per_minute": (6, 12),
    "trades_per_active_day": (120, 400),
    "multi_token_burst_share": (0.05, 0.20),
    "snipe_share": (0.25, 0.60),
    "quick_flip_share": (0.25, 0.60),
    "no_rest_day_share": (0.5, 0.9),
}
MIN_TRADES = 10
BURST_SECONDS = 5          # two different tokens traded within 5 s
SNIPE_SECONDS = 5          # buy within 5 s of token creation
QUICK_FLIP_SECONDS = 20    # position closed within 20 s of the first buy
BUSY_DAY_TRADES = 20       # a UTC day with >= 20 trades is "busy"
REST_SECONDS = 4 * 3600    # a person rests (sleeps) >= 4 h a day


@dataclass
class Behavior:
    label: str = "UNKNOWN"
    n_trades: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)
    hits: dict[str, str] = field(default_factory=dict)      # metric -> "likely" | "bot"
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"label": self.label, "n_trades": self.n_trades, "metrics": self.metrics,
                "hits": self.hits, "reasons": self.reasons}


def _ts(x) -> int:
    return int(x.timestamp()) if hasattr(x, "timestamp") else int(x)


def classify_behavior(trades: Iterable[dict[str, Any]], token_created: dict[str, int | None],
                      holding_seconds: Sequence[int]) -> Behavior:
    """`trades`: the wallet's buy/sell rows (block_time, mint, tx_type) in the period, any size.
    `token_created`: mint -> creation unix time when known. `holding_seconds`: closed positions."""
    rows = sorted(((_ts(r["block_time"]), r["mint"], r["tx_type"]) for r in trades
                   if r.get("tx_type") in ("buy", "sell")), key=lambda x: x[0])
    b = Behavior(n_trades=len(rows))
    if not rows:
        b.reasons.append("no trades in the period")
        return b
    m = b.metrics
    times = [t for t, _, _ in rows]

    # busiest 60-second window
    best, j = 0, 0
    for i in range(len(times)):
        while times[i] - times[j] >= 60:
            j += 1
        best = max(best, i - j + 1)
    m["max_trades_per_minute"] = best

    days = {t // 86400 for t in times}
    m["active_days"] = len(days)
    m["trades_per_active_day"] = round(len(rows) / len(days), 1)

    # different tokens within a few seconds of each other
    burst = sum(1 for (t0, m0, _), (t1, m1, _) in zip(rows, rows[1:]) if m1 != m0 and t1 - t0 <= BURST_SECONDS)
    m["multi_token_burst_share"] = round(burst / (len(rows) - 1), 3) if len(rows) > 1 else 0.0

    # first buy of each token vs the token's creation time
    first_buy: dict[str, int] = {}
    for t, mint, kind in rows:
        if kind == "buy" and mint not in first_buy:
            first_buy[mint] = t
    delays = [t - token_created[mint] for mint, t in first_buy.items()
              if token_created.get(mint) is not None and t >= token_created[mint]]
    m["tokens_with_creation_time"] = len(delays)
    m["snipe_share"] = round(sum(1 for d in delays if d <= SNIPE_SECONDS) / len(delays), 3) if len(delays) >= 5 else None
    m["median_entry_after_creation_s"] = int(statistics.median(delays)) if delays else None

    holds = [h for h in holding_seconds if h is not None]
    m["closed_positions"] = len(holds)
    m["quick_flip_share"] = (round(sum(1 for h in holds if h <= QUICK_FLIP_SECONDS) / len(holds), 3)
                             if len(holds) >= 5 else None)
    m["median_holding_s"] = int(statistics.median(holds)) if holds else None

    # busy days on which the wallet never paused for 4 hours. Gaps are taken on the whole timeline
    # (a night's rest may cross midnight), and a day counts as rested if any >= 4 h gap overlaps it.
    per_day: dict[int, int] = {}
    for t in times:
        per_day[t // 86400] = per_day.get(t // 86400, 0) + 1
    busy = [d for d, n in per_day.items() if n >= BUSY_DAY_TRADES]
    long_gaps = [(a, b) for a, b in zip(times, times[1:]) if b - a >= REST_SECONDS]
    no_rest = 0
    for d in busy:
        d0, d1 = d * 86400, d * 86400 + 86400
        if not any(a < d1 and b > d0 for a, b in long_gaps):
            no_rest += 1
    m["busy_days"] = len(busy)
    m["no_rest_day_share"] = round(no_rest / len(busy), 3) if len(busy) >= 3 else None

    if len(rows) < MIN_TRADES:
        b.reasons.append(f"only {len(rows)} trades: too few to judge")
        return b

    for k, (soft, hard) in THRESHOLDS.items():
        v = m.get(k)
        if v is None:
            continue
        if v >= hard:
            b.hits[k] = "bot"
        elif v >= soft:
            b.hits[k] = "likely"
    hard_hits = [k for k, v in b.hits.items() if v == "bot"]
    soft_hits = [k for k, v in b.hits.items() if v == "likely"]
    if hard_hits or len(soft_hits) >= 3:
        b.label = "BOT"
    elif soft_hits:
        b.label = "LIKELY_BOT"
    else:
        b.label = "HUMAN"
    for k, lvl in b.hits.items():
        b.reasons.append(f"{k} = {m[k]} ({'bot' if lvl == 'bot' else 'likely bot'} level)")
    return b


# ----------------------------------------------------------------- copy-trade simulation

def price_curve(bars: Sequence[Bar], staleness: int = 180) -> tuple[list[int], list[float], int]:
    """Points (time, price): bar open at its start (when known) and close at its end."""
    pts: dict[int, float] = {}
    for b in bars:
        if b.open and b.open > 0:
            pts.setdefault(b.ts, b.open)
        pts[b.ts + b.interval] = b.close
    ts = sorted(pts)
    return ts, [pts[t] for t in ts], staleness


def curve_at(curve: tuple[list[int], list[float], int], t: int) -> float | None:
    ts, ps, stale = curve
    i = bisect.bisect_right(ts, t)
    if i == 0:
        return None
    t0, p0 = ts[i - 1], ps[i - 1]
    if i < len(ts) and ts[i] - t0 <= stale:
        t1, p1 = ts[i], ps[i]
        return p0 + (p1 - p0) * (t - t0) / (t1 - t0) if t1 > t0 else p1
    return p0 if t - t0 <= stale else None


def simulate_copy(buys: Sequence[tuple], sells: Sequence[tuple], written_off: bool,
                  bars: Sequence[Bar], delay: int, cost_pct: float) -> dict[str, Any] | None:
    """Mirror one position `delay` seconds late. `buys` = (time, SOL, executed price), `sells` =
    (time, fraction of the wallet's holdings sold, executed price). Returns {"multiple", "win"}
    or None when a price is missing or the follower would still hold tokens."""
    if not buys or not bars:
        return None
    curve = price_curve(bars)
    tokens = bought = cost = proceeds = 0.0
    first_buy = min(e[0] for e in buys)
    min_hold = max(delay, 60) if delay else 0
    entry_px = None
    events = sorted([(e[0], 0, e[1], e[2]) for e in buys] + [(e[0], 1, e[1], e[2]) for e in sells])
    for t, kind, v, px in events:
        if not px or px <= 0:
            return None
        if delay:
            m0, m1 = curve_at(curve, t), curve_at(curve, t + delay)
            if not m0 or not m1:
                return None
            px = px * m1 / m0
        if kind == 1 and t - first_buy < min_hold and entry_px:
            px = entry_px                       # the follower was not in yet: flat
        if kind == 0:
            entry_px = px if entry_px is None else entry_px
            tokens += v / px
            bought += v / px
            cost += v
        else:
            q = tokens * min(max(v, 0.0), 1.0)
            proceeds += q * px
            tokens -= q
    if cost <= 0:
        return None
    if tokens > 0.002 * bought and not written_off:
        return None          # still open for the follower: no outcome yet
    net = proceeds * (1 - cost_pct / 2) - cost * (cost_pct / 2)
    mult = net / cost
    return {"multiple": round(mult, 4), "win": mult > 1.0}


def copy_summary(results: Sequence[dict[str, Any] | None], closed_total: int) -> dict[str, Any]:
    done = [r for r in results if r is not None]
    out: dict[str, Any] = {"positions": len(done), "closed_total": closed_total,
                           "coverage": round(len(done) / closed_total, 3) if closed_total else None}
    if done:
        mults = [r["multiple"] for r in done]
        out["win_rate"] = round(sum(1 for r in done if r["win"]) / len(done), 3)
        out["median_multiple"] = round(statistics.median(mults), 4)
        out["avg_return"] = round(sum(mults) / len(mults) - 1.0, 4)
    else:
        out["win_rate"] = out["median_multiple"] = out["avg_return"] = None
    return out
