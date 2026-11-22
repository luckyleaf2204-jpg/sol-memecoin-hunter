"""Walk-forward backtest of the co-buy signal (PHẦN XV).

No look-ahead:
  * Which wallets are "Smart Money" at time t is decided from positions CLOSED before the
    most recent checkpoint <= t (checkpoints every `checkpoint_hours`), using the same score
    and mandatory criteria as the live system.
  * A signal can only fire on a buy that already happened; detection time = buy time +
    `detection_latency_seconds`.
  * Entry price = the first observed price AFTER detection (open of the next bar), plus
    slippage, trading fee and a price-impact estimate for `trade_size_sol`.
  * Exits use prices observed at/before each horizon (last close), minus slippage, fee and
    impact. A token that stops trading is exited at its last observed price with an extra
    liquidity haircut; if liquidity is gone (< 1 SOL) the position is a total loss.
Data split: signals ordered by detection time -> train 60 % | validation 20 % | test 20 %.
Thresholds are chosen on train, checked on validation, and test is reported once.
"""
from __future__ import annotations

import bisect
import statistics
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

from .performance import compute_performance
from .positions import Event, build_positions, mark_to_market
from .price_hits import Bar, PeakConfig, analyse_entry, validated_peak
from .scoring import score_wallet
from .signals import LEVEL_RANK, SmartBuy, evaluate

HORIZONS = {"1h": 3600, "6h": 21600, "24h": 86400}


@dataclass
class BtTrade:
    wallet: str
    mint: str
    ts: int
    slot: int
    tx_type: str
    sol: float
    tokens: float
    fees: float
    price: float | None
    liquidity: float | None
    verification: str
    signature: str
    event_key: str
    venue: str = "pump_curve"


@dataclass
class BtSignal:
    mint: str
    level: str
    detect_ts: int
    independent: int
    total_sol: float
    entry_price: float | None = None
    outcome: dict[str, Any] = field(default_factory=dict)


def peak_config(settings: dict[str, Any], window_days: float | None = None) -> PeakConfig:
    return PeakConfig(min_bar_volume_sol=float(settings["peak_min_bar_volume_sol"]),
                      min_liquidity_sol=float(settings["peak_min_liquidity_sol"]),
                      confirm_points=int(settings["peak_confirm_points"]),
                      confirm_window=int(float(settings["peak_confirm_window_minutes"]) * 60),
                      window_seconds=int(float(window_days or settings["peak_window_days"]) * 86400))


def _bars_index(bars: list[Bar]) -> list[int]:
    return [b.ts for b in bars]


def first_price_after(bars: list[Bar], idx: list[int], t: int) -> tuple[float | None, Bar | None]:
    i = bisect.bisect_right(idx, t)   # first bar starting strictly after t
    if i >= len(bars):
        return None, None
    b = bars[i]
    return (b.open if b.open else b.close), b


def last_price_at(bars: list[Bar], idx: list[int], t: int) -> tuple[float | None, Bar | None]:
    i = bisect.bisect_right(idx, t) - 1
    if i < 0:
        return None, None
    return bars[i].close, bars[i]


def _impact(size: float, liquidity: float | None, venue: str) -> float:
    if not liquidity or liquidity <= 0:
        return 0.05
    depth = liquidity + (30.0 if venue == "pump_curve" else 0.0)  # curve virtual SOL offset
    return min(0.5, size / max(depth, 1e-9))


class SmartSetOracle:
    """Smart-money membership as known at each checkpoint (closed positions only)."""

    def __init__(self, trades_by_wallet: dict[str, list[BtTrade]], settings: dict[str, Any],
                 checkpoint_hours: float = 6.0, bars_by_mint: dict[str, list[Bar]] | None = None):
        self.tbw = trades_by_wallet
        self.bars = bars_by_mint or {}
        self.s = settings
        self.step = int(checkpoint_hours * 3600)
        self.cache: dict[int, dict[str, tuple[float, float | None]]] = {}
        self.peak_cfg = peak_config(settings)

    def at(self, t: int) -> dict[str, tuple[float, float | None]]:
        cp = (t // self.step) * self.step
        if cp in self.cache:
            return self.cache[cp]
        out: dict[str, tuple[float, float | None]] = {}
        period = int(self.s["analysis_period_days"])
        for w, trades in self.tbw.items():
            known = [x for x in trades if x.ts < cp]
            if not known:
                continue
            by_mint: dict[str, list[Event]] = defaultdict(list)
            for x in known:
                by_mint[x.mint].append(Event(x.signature, x.event_key, x.slot, x.ts, x.tx_type, x.sol, x.tokens,
                                             x.fees, x.verification, None, x.price, x.liquidity))
            positions = []
            for m, evs in by_mint.items():
                ps, _ = build_positions(w, m, evs, float(self.s["dust_fraction"]))
                for p in ps:
                    mark_to_market(p, None, cp, float(self.s["write_off_after_hours"]),
                                   float(self.s["write_off_value_fraction"]))
                bars = [b for b in self.bars.get(m, []) if b.ts + b.interval <= cp]  # only the past
                for p in ps:
                    if bars and p.first_buy_at and p.avg_entry_price:
                        p.price_metrics = analyse_entry(bars, p.first_buy_at, p.avg_entry_price, cp, self.peak_cfg)
                positions.extend(p for p in ps if p.closed_at is None or p.closed_at < cp)
            perf = compute_performance(positions, period, cp, float(self.s["min_buy_sol"]), True)
            r = score_wallet(perf, self.s)
            if r.qualifies:
                out[w] = (r.score, perf.win_rate)
        self.cache[cp] = out
        return out


def detect_signals(trades: list[BtTrade], settings: dict[str, Any], oracle: SmartSetOracle,
                   min_level: str = "WATCH") -> list[BtSignal]:
    """Replay buys chronologically; emit the first signal per (mint, level)."""
    lat = int(float(settings["backtest_detection_latency_seconds"]))
    window = int(float(settings["signal_window_minutes"]) * 60)
    by_mint: dict[str, list[BtTrade]] = defaultdict(list)
    for x in sorted(trades, key=lambda x: (x.ts, x.slot)):
        by_mint[x.mint].append(x)
    out: list[BtSignal] = []
    for mint, lst in by_mint.items():
        fired: set[str] = set()
        pos_sol: dict[str, float] = defaultdict(float)
        holding: dict[str, float] = defaultdict(float)
        buys: list[SmartBuy] = []
        for x in lst:
            if x.tx_type == "buy":
                if holding[x.wallet] <= 1e-9:
                    pos_sol[x.wallet] = 0.0
                pos_sol[x.wallet] += x.sol
                holding[x.wallet] += x.tokens
            elif x.tx_type in ("sell", "transfer_out"):
                holding[x.wallet] = max(0.0, holding[x.wallet] - x.tokens)
                continue
            else:
                continue
            smart = oracle.at(x.ts)
            if x.wallet not in smart:
                continue
            sc, wr = smart[x.wallet]
            buys.append(SmartBuy(x.wallet, x.ts, x.sol, pos_sol[x.wallet], x.price, x.verification, x.wallet,
                                 sc, wr, x.signature))
            buys = [b for b in buys if b.ts >= x.ts - window]
            res = evaluate(buys, [], x.ts, settings, x.price, None)
            if res.level and LEVEL_RANK[res.level] >= LEVEL_RANK[min_level] and res.level not in fired:
                for lv in ("WATCH", "STRONG", "VERY_STRONG"):
                    if LEVEL_RANK[lv] <= LEVEL_RANK[res.level]:
                        fired.add(lv)
                out.append(BtSignal(mint, res.level, x.ts + lat, res.independent_buyers, res.total_buy_sol))
    out.sort(key=lambda s: s.detect_ts)
    return out


def simulate(sig: BtSignal, bars: list[Bar], settings: dict[str, Any], fee_rate: float = 0.0125,
             venue: str = "pump_curve") -> dict[str, Any]:
    size = float(settings["backtest_trade_size_sol"])
    slip = float(settings["backtest_slippage_bps"]) / 1e4
    idx = _bars_index(bars)
    p0, b0 = first_price_after(bars, idx, sig.detect_ts)
    if p0 is None:
        return {"tradable": False, "reason": "no price observed after detection"}
    imp_in = _impact(size, b0.liquidity_sol, venue)
    entry = p0 * (1 + slip + imp_in) * (1 + fee_rate)
    res: dict[str, Any] = {"tradable": True, "entry_price": entry, "entry_bar_ts": b0.ts, "raw_price": p0}
    cfg = peak_config(settings, window_days=7)
    peak, peak_t, _ = validated_peak(bars, b0.ts, cfg)
    m = (peak / p0) if peak else None
    res["peak_multiple_7d"] = m
    for k in (2, 5, 10):
        res[f"hit_{k}x"] = bool(m and m >= k)
    later = [b for b in bars if b0.ts <= b.ts <= b0.ts + 7 * 86400]
    lows = [b.close for b in later]
    res["drawdown_50"] = bool(lows and min(lows) <= 0.5 * p0)
    last = later[-1] if later else b0
    res["liquidity_lost"] = bool((last.liquidity_sol is not None and last.liquidity_sol < 1.0)
                                 or (last.ts < b0.ts + 3 * 86400 and last.close < 0.2 * p0))
    for name, h in HORIZONS.items():
        px, bx = last_price_at(bars, idx, b0.ts + h)
        if px is None or bx is None:
            res[f"ret_{name}"] = None
            continue
        if bx.liquidity_sol is not None and bx.liquidity_sol < 1.0:
            res[f"ret_{name}"] = -1.0
            continue
        imp_out = _impact(size * px / p0, bx.liquidity_sol, venue)
        exitp = px * (1 - slip - imp_out) * (1 - fee_rate)
        res[f"ret_{name}"] = exitp / entry - 1
    # rule: TP 2x / SL -50 % / else exit at 24 h
    ret = None
    for b in later:
        if b.ts > b0.ts + 86400:
            break
        lvl = b.high_confirmed if b.high_confirmed is not None else b.close
        if lvl >= 2 * p0:
            ret = (2 * p0 * (1 - slip) * (1 - fee_rate)) / entry - 1
            break
        if b.close <= 0.5 * p0:
            ret = (b.close * (1 - slip) * (1 - fee_rate)) / entry - 1
            break
    res["ret_tp2x_sl50"] = ret if ret is not None else res.get("ret_24h")
    return res


def summarize(rows: list[dict[str, Any]]) -> dict[str, Any]:
    tr = [r for r in rows if r.get("tradable")]
    n = len(tr)

    def share(key):
        return (sum(1 for r in tr if r.get(key)) / n) if n else None

    def stats(key):
        xs = [r[key] for r in tr if r.get(key) is not None]
        if not xs:
            return {"n": 0, "mean": None, "median": None, "win_share": None}
        return {"n": len(xs), "mean": sum(xs) / len(xs), "median": statistics.median(xs),
                "win_share": sum(1 for x in xs if x > 0) / len(xs)}

    return {"signals": len(rows), "tradable": n,
            "hit_2x": share("hit_2x"), "hit_5x": share("hit_5x"), "hit_10x": share("hit_10x"),
            "drawdown_50": share("drawdown_50"), "liquidity_lost": share("liquidity_lost"),
            "ret_1h": stats("ret_1h"), "ret_6h": stats("ret_6h"), "ret_24h": stats("ret_24h"),
            "ret_tp2x_sl50": stats("ret_tp2x_sl50"),
            "hit_2x_count": sum(1 for r in tr if r.get("hit_2x")),
            "hit_5x_count": sum(1 for r in tr if r.get("hit_5x")),
            "hit_10x_count": sum(1 for r in tr if r.get("hit_10x")),
            "drawdown_50_count": sum(1 for r in tr if r.get("drawdown_50")),
            "liquidity_lost_count": sum(1 for r in tr if r.get("liquidity_lost"))}


def split_chrono(items: list, train: float = 0.6, val: float = 0.2) -> tuple[list, list, list]:
    n = len(items)
    a = int(n * train)
    b = int(n * (train + val))
    return items[:a], items[a:b], items[b:]


def run_backtest(trades: list[BtTrade], bars_by_mint: dict[str, list[Bar]], settings: dict[str, Any],
                 grid: list[dict[str, Any]] | None = None, min_level: str = "WATCH") -> dict[str, Any]:
    tbw: dict[str, list[BtTrade]] = defaultdict(list)
    for x in trades:
        tbw[x.wallet].append(x)
    venue_of = {x.mint: x.venue for x in trades}
    grid = grid or [{}]
    results = []
    for g in grid:
        s = dict(settings)
        s.update(g)
        oracle = SmartSetOracle(tbw, s, bars_by_mint=bars_by_mint)
        sigs = detect_signals(trades, s, oracle, min_level)
        rows = []
        for sg in sigs:
            r = simulate(sg, bars_by_mint.get(sg.mint, []), s,
                         fee_rate=0.0125 if venue_of.get(sg.mint) == "pump_curve" else 0.004,
                         venue=venue_of.get(sg.mint, "pump_curve"))
            r.update({"mint": sg.mint, "level": sg.level, "detect_ts": sg.detect_ts,
                      "independent": sg.independent, "total_sol": sg.total_sol})
            rows.append(r)
        tr, va, te = split_chrono(rows)
        results.append({"params": g, "train": summarize(tr), "validation": summarize(va), "test": summarize(te),
                        "all": summarize(rows), "rows": rows})

    def key(res):
        st = res["train"]["ret_6h"]
        return (st["n"] >= 5, st["mean"] if st["mean"] is not None else -9)

    best = max(results, key=key) if results else None
    return {"grid": [{k: v for k, v in r.items() if k != "rows"} for r in results],
            "selected_params": best["params"] if best else None,
            "selected": {k: v for k, v in best.items() if k != "rows"} if best else None,
            "rows": best["rows"] if best else []}
