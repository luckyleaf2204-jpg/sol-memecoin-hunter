"""Wallet performance over a period, computed from positions.

Definitions (also in docs/METHODOLOGY.md):
  * Period filter: positions whose first buy (or opening) is inside [now - period, now].
  * Qualifying position: Actual Entry Cost (sum of SOL of all buys of the cycle) >= min_buy_sol.
    ALL Smart Money statistics use qualifying positions only.
  * Win Rate = qualifying closed positions with realised PnL > 0 / qualifying closed positions.
    Open positions are excluded; written-off positions count as closed losses.
  * Average / Median / Largest / Total Buy SOL = entry cost per qualifying POSITION.
  * Average position size = mean entry cost over ALL positions (any size).
  * Realised PnL (after fees), Unrealised PnL (open, marked at the latest known price),
    ROI = realised PnL / entry cost of closed qualifying positions.
  * Profit factor = gross profit / gross loss of closed qualifying positions.
  * Max drawdown = largest peak-to-trough fall of cumulative realised PnL (by close time).
  * Hit rates = share of qualifying positions WITH price data whose validated peak after
    entry reached 2x / 3x / 5x / 10x the wallet's average entry price.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Any, Iterable

from .positions import Position


@dataclass
class Performance:
    period_days: int
    positions_total: int = 0
    positions_qualifying: int = 0
    closed_qualifying: int = 0
    open_qualifying: int = 0
    wins: int = 0
    losses: int = 0
    win_rate: float | None = None
    avg_buy_sol: float | None = None
    median_buy_sol: float | None = None
    largest_buy_sol: float | None = None
    total_buy_sol: float = 0.0
    avg_position_sol: float | None = None
    realized_pnl_sol: float = 0.0
    unrealized_pnl_sol: float = 0.0
    total_pnl_sol: float = 0.0
    roi: float | None = None
    profit_factor: float | None = None
    avg_win_sol: float | None = None
    avg_loss_sol: float | None = None
    max_drawdown_sol: float = 0.0
    largest_win_sol: float | None = None
    largest_loss_sol: float | None = None
    hit_rate_2x: float | None = None
    hit_rate_3x: float | None = None
    hit_rate_5x: float | None = None
    hit_rate_10x: float | None = None
    avg_realized_roi: float | None = None
    median_realized_roi: float | None = None
    avg_mfe: float | None = None
    avg_holding_seconds: int | None = None
    pnl_ex_best_sol: float = 0.0
    top_win_share: float | None = None
    verified_share: float | None = None
    coverage_complete: bool = False
    last_active_at: int | None = None
    metrics: dict[str, Any] = field(default_factory=dict)


def _median(xs: list[float]) -> float | None:
    return statistics.median(xs) if xs else None


def _mean(xs: list[float]) -> float | None:
    return sum(xs) / len(xs) if xs else None


def max_drawdown(pnls_in_close_order: list[float]) -> float:
    peak = cum = 0.0
    dd = 0.0
    for x in pnls_in_close_order:
        cum += x
        peak = max(peak, cum)
        dd = max(dd, peak - cum)
    return dd


def compute_performance(positions: Iterable[Position], period_days: int, now: int, min_buy_sol: float,
                        coverage_complete: bool, orphan_sells: int = 0) -> Performance:
    start = now - period_days * 86400
    ps = [p for p in positions if (p.first_buy_at or p.opened_at) >= start]
    perf = Performance(period_days=period_days, coverage_complete=coverage_complete)
    perf.positions_total = len(ps)
    if ps:
        perf.last_active_at = max(p.last_activity_at for p in ps)
        perf.avg_position_sol = _mean([p.buy_sol for p in ps if p.buy_sol > 0])
    q = [p for p in ps if p.qualifying(min_buy_sol)]
    perf.positions_qualifying = len(q)
    closed = [p for p in q if p.is_closed]
    opened = [p for p in q if not p.is_closed]
    perf.closed_qualifying = len(closed)
    perf.open_qualifying = len(opened)
    entries = [p.buy_sol for p in q]
    if entries:
        perf.avg_buy_sol = _mean(entries)
        perf.median_buy_sol = _median(entries)
        perf.largest_buy_sol = max(entries)
        perf.total_buy_sol = sum(entries)
    tx_sizes = [s for p in q for s in p.buy_tx_sizes]
    perf.metrics["avg_buy_tx_sol"] = _mean(tx_sizes)
    perf.metrics["median_buy_tx_sol"] = _median(tx_sizes)
    perf.metrics["n_buy_txs"] = len(tx_sizes)
    perf.metrics["share_positions_qualifying"] = (len(q) / len(ps)) if ps else None
    perf.metrics["orphan_sells"] = orphan_sells
    perf.metrics["written_off"] = sum(1 for p in closed if p.status == "written_off")
    perf.metrics["positions_with_transfers"] = sum(1 for p in q if "transfer_in" in p.flags or "transfer_out" in p.flags)
    shares = [p.entry_share_of_liquidity for p in q if p.entry_share_of_liquidity is not None]
    perf.metrics["median_entry_share_of_liquidity"] = _median(shares)
    if len(entries) >= 2 and perf.avg_buy_sol:
        perf.metrics["buy_size_cv"] = statistics.pstdev(entries) / perf.avg_buy_sol
        perf.metrics["median_to_mean"] = perf.median_buy_sol / perf.avg_buy_sol

    pnls = [p.realized_pnl_sol for p in closed]
    wins = [x for x in pnls if x > 0]
    losses = [x for x in pnls if x <= 0]
    perf.wins, perf.losses = len(wins), len(losses)
    if closed:
        perf.win_rate = len(wins) / len(closed)
        perf.realized_pnl_sol = sum(pnls)
        cost = sum(p.net_entry_cost for p in closed)
        perf.roi = perf.realized_pnl_sol / cost if cost > 0 else None
        gl = -sum(losses)
        gp = sum(wins)
        perf.profit_factor = (gp / gl) if gl > 0 else (99.0 if gp > 0 else None)
        perf.avg_win_sol = _mean(wins)
        perf.avg_loss_sol = _mean(losses)
        perf.largest_win_sol = max(wins) if wins else None
        perf.largest_loss_sol = min(losses) if losses else None
        order = sorted(closed, key=lambda p: p.closed_at or 0)
        perf.max_drawdown_sol = max_drawdown([p.realized_pnl_sol for p in order])
        rois = [p.roi for p in closed if p.roi is not None]
        perf.avg_realized_roi = _mean(rois)
        perf.median_realized_roi = _median(rois)
        hold = [p.holding_seconds for p in closed if p.holding_seconds is not None]
        perf.avg_holding_seconds = int(_mean(hold)) if hold else None
        best = max(pnls)
        perf.pnl_ex_best_sol = perf.realized_pnl_sol - (best if best > 0 else 0)
        perf.top_win_share = (max(wins) / gp) if gp > 0 else None
    perf.unrealized_pnl_sol = sum(p.unrealized_pnl_sol or 0 for p in opened)
    perf.total_pnl_sol = perf.realized_pnl_sol + perf.unrealized_pnl_sol

    # selling behaviour: SOL received per sell transaction and per position
    sells_pos = [p.sell_sol for p in q if p.n_sells > 0]
    perf.metrics["avg_sell_position_sol"] = _mean(sells_pos)
    n_sell_tx = sum(p.n_sells for p in q)
    perf.metrics["avg_sell_tx_sol"] = (sum(sells_pos) / n_sell_tx) if n_sell_tx else None
    perf.metrics["n_sell_txs"] = n_sell_tx
    # how much of the PnL is already locked in: |realised| / (|realised| + |unrealised|)
    denom = abs(perf.realized_pnl_sol) + abs(perf.unrealized_pnl_sol)
    perf.metrics["realized_share"] = (abs(perf.realized_pnl_sol) / denom) if denom > 0 else None
    perf.metrics["days_since_active"] = ((now - perf.last_active_at) / 86400) if perf.last_active_at else None
    perf.metrics["repeatability"] = repeatability(closed)

    priced = [p for p in q if p.price_metrics.get("price_data")]
    perf.metrics["positions_with_price_data"] = len(priced)
    if priced:
        for m in (2, 3, 5, 10):
            known = [p for p in priced if p.price_metrics["hits"].get(m) is not None]
            setattr(perf, f"hit_rate_{m}x", (sum(1 for p in known if p.price_metrics["hits"][m]) / len(known))
                    if known else None)
        mfes = [p.price_metrics["peak_multiple"] for p in priced if p.price_metrics.get("peak_multiple")]
        perf.avg_mfe = _mean(mfes)
        # entered before the move: share of priced positions whose validated peak came AFTER entry
        # and was >= 2x, AND the wallet's entry MC was below the peak MC by that multiple
        early = [p for p in priced if p.price_metrics.get("peak_multiple") and p.price_metrics["peak_multiple"] >= 2]
        perf.metrics["early_entry_share"] = len(early) / len(priced)
        # realized capture: of tokens that did 5x, how many did the wallet actually exit >= 5x?
        five = [p for p in priced if p.price_metrics["hits"].get(5)]
        perf.metrics["realized_5x_given_token_5x"] = (
            sum(1 for p in five if (p.realized_multiple or 0) >= 5) / len(five)) if five else None

    if q:
        perf.verified_share = sum(1 for p in q if p.verification == "verified") / len(q)
        perf.metrics["partial_share"] = sum(1 for p in q if p.verification == "partially_verified") / len(q)
    return perf


def repeatability(closed: list[Position], min_per_half: int = 3) -> dict[str, Any]:
    """Split closed qualifying positions in time (by close time) into two halves. The record is
    'repeatable' when BOTH halves have a positive ROI and a win rate >= 50 %. A wallet whose
    profit comes from one lucky streak fails this check."""
    order = sorted(closed, key=lambda p: p.closed_at or 0)
    if len(order) < 2 * min_per_half:
        return {"status": "insufficient", "pass": False, "positions": len(order)}
    mid = len(order) // 2
    halves = []
    for part in (order[:mid], order[mid:]):
        pnl = sum(p.realized_pnl_sol for p in part)
        cost = sum(p.net_entry_cost for p in part)
        wins = sum(1 for p in part if p.realized_pnl_sol > 0)
        halves.append({"positions": len(part), "pnl_sol": pnl, "roi": (pnl / cost) if cost > 0 else None,
                       "win_rate": wins / len(part)})
    ok = all(h["roi"] is not None and h["roi"] > 0 and h["win_rate"] >= 0.5 for h in halves)
    return {"status": "pass" if ok else "fail", "pass": ok, "first_half": halves[0], "second_half": halves[1]}
