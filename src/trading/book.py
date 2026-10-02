"""PAPER BOOK — cash, positions, executions and NET P&L.

NET P&L = gross P&L − route fees − network fees (incl. failed transactions) − slippage/price-impact cost.
  gross        what the trades would have made at the validated reference prices (no costs)
  slippage     (fill − reference) × tokens on buys, (reference − fill) × tokens on sells
  win rate     closed positions with net P&L > 0 / closed positions
  profit factor  Σ net wins / |Σ net losses|   (None while there is no loss)
  max drawdown   largest peak-to-trough fall of the equity curve (%)
Equity values open positions at their last validated price; a position without one is flagged STALE.
"""
from __future__ import annotations

import json
import time
from dataclasses import asdict
from pathlib import Path

from trading.models import Execution, Position


def _day(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(ts))


def mid_price(ex: Execution) -> float | None:
    """Mid price behind a fill: the fill without its modelled price impact and latency slippage
    (BUY fill = mid x (1 + impact) x (1 + slip); SELL fill = mid x (1 - impact) x (1 - slip); a haircut fill is
    recorded as slippage). Pool fees inside a Jupiter quote stay in (not separable)."""
    if ex.status != "FILLED" or not ex.fill_price:
        return None
    imp, slip = (ex.price_impact_pct or 0.0) / 100, (ex.slippage_pct or 0.0) / 100
    if ex.side == "BUY":
        return ex.fill_price / ((1 + imp) * (1 + slip))
    d = (1 - imp) * (1 - slip)
    return ex.fill_price / d if d > 0 else None


def cost_stress(closed: list, levels) -> dict:
    """P&L of the closed (executable) trades if every round trip cost exactly c % instead of the modelled costs:
    net_c = gross price move (mid in -> mid out) - c."""
    rows = [(p.gross_move_pct, p.cost_usd, p.realized_usd - p.cost_usd) for p in closed if p.gross_move_pct is not None]
    out = {"n": len(rows), "gross_move_mean_pct": round(sum(r[0] for r in rows) / len(rows), 2) if rows else None,
           "modelled_cost_mean_pct": round(sum(r[0] - 100 * r[2] / r[1] for r in rows) / len(rows), 2)
           if rows else None, "levels": {}}
    for c in levels:
        v = sorted(r[0] - c for r in rows)
        out["levels"][f"{c:g}%"] = {
            "mean_pct": round(sum(v) / len(v), 2) if v else None,
            "median_pct": round((v[len(v) // 2] + v[(len(v) - 1) // 2]) / 2, 2) if v else None,
            "win_rate": round(100 * sum(1 for x in v if x > 0) / len(v), 1) if v else None,
            "total_usd": round(sum(r[1] * (r[0] - c) / 100 for r in rows), 2) if rows else 0.0}
    return out


class PaperBook:
    def __init__(self, starting_balance: float):
        self.starting = starting_balance
        self.cash = starting_balance
        self.positions: dict[str, Position] = {}
        self.closed: list[Position] = []
        self.executions: list[Execution] = []
        self.equity_history: list[tuple[float, float]] = []
        self.peak = starting_balance
        self.max_dd = 0.0
        self.day = _day(time.time())
        self.day_start = starting_balance
        self.next_id = 1
        self.last_exit: dict[str, float] = {}
        self.fees = self.network_fees = self.slippage = self.failed_fees = 0.0
        self.failed = 0

    # ---------------------------------------------------------------- values
    def exposure(self) -> float:
        return sum(p.value() or p.cost_usd for p in self.positions.values())

    def equity(self) -> float:
        return self.cash + sum((p.value() if p.value() is not None else 0.0) for p in self.positions.values())

    def mark(self, now: float) -> float:
        eq = self.equity()
        d = _day(now)
        if d != self.day:
            self.day, self.day_start = d, eq
        self.peak = max(self.peak, eq)
        if self.peak > 0:
            self.max_dd = max(self.max_dd, 100 * (self.peak - eq) / self.peak)
        if not self.equity_history or now - self.equity_history[-1][0] >= 30:
            self.equity_history.append((now, round(eq, 4)))
            del self.equity_history[:-2000]
        return eq

    # ---------------------------------------------------------------- fills
    def record(self, ex: Execution) -> None:
        self.executions.append(ex)
        del self.executions[:-2000]
        self.network_fees += ex.network_fee_usd
        self.cash -= ex.network_fee_usd
        if ex.status != "FILLED":
            if ex.status == "FAILED":
                self.failed += 1
                self.failed_fees += ex.network_fee_usd
            return
        self.fees += ex.fee_usd
        if ex.side == "BUY":
            self.slippage += (ex.fill_price - ex.ref_price) * ex.tokens
            self.cash -= ex.usd_in
        else:
            self.slippage += (ex.ref_price - ex.fill_price) * ex.tokens
            self.cash += ex.usd_in

    cost_levels: tuple = (5.0, 7.0, 10.0)        # round-trip costs (%) for the P&L stress report (bot sets it)

    def open(self, ex: Execution, cfg, now: float, entry_score=None, why=None, entry_liq=None, entry_vol=None,
             setup: str = "") -> Position:
        self.record(ex)
        p = Position(id=self.next_id, mint=ex.mint, symbol=ex.symbol, opened_at=now, entry_price=ex.fill_price,
                     tokens=ex.tokens, cost_usd=ex.usd_in + ex.network_fee_usd, initial_tokens=ex.tokens,
                     stop_price=ex.fill_price * (1 - cfg.stop_loss_pct / 100),
                     tp1_price=ex.fill_price * (1 + cfg.tp1_pct / 100), tp2_price=ex.fill_price * (1 + cfg.tp2_pct / 100),
                     trailing_pct=cfg.trailing_pct, high_price=ex.fill_price, last_price=ex.fill_price, last_price_ts=now,
                     fees_usd=ex.fee_usd + ex.network_fee_usd,
                     slippage_usd=(ex.fill_price - ex.ref_price) * ex.tokens,
                     entry_score=entry_score, entry_why=why or [], entry_liq=entry_liq, entry_vol=entry_vol,
                     setup=setup)
        p.entry_mid = mid_price(ex)
        self.next_id += 1
        self.positions[p.mint] = p
        return p

    def reduce(self, p: Position, ex: Execution, now: float, reason: str) -> None:
        self.record(ex)
        p.fees_usd += ex.network_fee_usd
        p.realized_usd -= ex.network_fee_usd          # every sell attempt's network fee belongs to this position
        if ex.status != "FILLED":
            return
        p.fees_usd += ex.fee_usd
        p.slippage_usd += (ex.ref_price - ex.fill_price) * ex.tokens
        p.tokens -= ex.tokens
        p.realized_usd += ex.usd_in
        p.exit_mid_value += ex.tokens * (mid_price(ex) or 0.0)
        if p.tokens <= p.initial_tokens * 1e-6:
            p.tokens, p.status, p.exit_reason, p.closed_at = 0.0, "CLOSED", reason, now
            self.closed.append(self.positions.pop(p.mint))
            self.last_exit[p.mint] = now

    # ---------------------------------------------------------------- stats
    def stats(self, now: float | None = None) -> dict:
        now = now or time.time()
        eq = self.equity()
        net_all = eq - self.starting
        # main P&L: executable fills only. Positions opened on a SIMULATED (no-quote) fill are reported apart.
        nq_closed = [p.realized_usd - p.cost_usd for p in self.closed if p.noquote]
        nq_open = [p.pnl_usd() or 0.0 for p in self.positions.values() if p.noquote]
        nq_net = sum(nq_closed) + sum(nq_open)
        net = net_all - nq_net
        main = [p for p in self.closed if not p.noquote]
        closed_net = [p.realized_usd - p.cost_usd for p in main]
        wins = [x for x in closed_net if x > 0]
        losses = [x for x in closed_net if x <= 0]
        gross = net_all + self.fees + self.network_fees + self.slippage
        return {
            "equity": round(eq, 2), "cash": round(self.cash, 2), "starting": self.starting,
            "net_pnl": round(net, 2), "net_pnl_pct": round(100 * net / self.starting, 2) if self.starting else None,
            "net_pnl_all": round(net_all, 2),
            "noquote": {"closed": len(nq_closed), "open": len(nq_open), "net": round(nq_net, 2),
                        "note": "SIMULATED fills (no Jupiter route): excluded from the main P&L"},
            "cost_stress": cost_stress(main, self.cost_levels),
            "today_pnl": round(eq - self.day_start, 2),
            "gross_pnl": round(gross, 2), "fees": round(self.fees, 2), "network_fees": round(self.network_fees, 2),
            "slippage_cost": round(self.slippage, 2), "failed_trades": self.failed,
            "failed_fees": round(self.failed_fees, 2),
            "closed": len(main), "open": len(self.positions),
            "win_rate": round(100 * len(wins) / len(closed_net), 1) if closed_net else None,
            "wins": len(wins), "losses": len(losses),
            "avg_win": round(sum(wins) / len(wins), 2) if wins else None,
            "avg_loss": round(sum(losses) / len(losses), 2) if losses else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) != 0 else None,
            "max_drawdown_pct": round(self.max_dd, 2),
            "exposure": round(self.exposure(), 2),
            # long-term edge, not trade count: expected NET $ per closed trade (None until a trade closed)
            "expectancy": round(sum(closed_net) / len(closed_net), 2) if closed_net else None,
            "by_setup": self.by_setup(),
            "sample_note": "insufficient" if len(closed_net) < 30 else "ok",   # < 30 closed trades: no conclusion
        }

    def by_setup(self) -> list[dict]:
        groups: dict[str, list[float]] = {}
        for p in self.closed:
            groups.setdefault(p.setup or "unknown", []).append(p.realized_usd - p.cost_usd)
        out = []
        for setup, nets in groups.items():
            w = [x for x in nets if x > 0]
            l_ = [x for x in nets if x <= 0]
            out.append({"setup": setup, "trades": len(nets), "net": round(sum(nets), 2),
                        "win_rate": round(100 * len(w) / len(nets), 1),
                        "expectancy": round(sum(nets) / len(nets), 2),
                        "avg_win": round(sum(w) / len(w), 2) if w else None,
                        "avg_loss": round(sum(l_) / len(l_), 2) if l_ else None,
                        "profit_factor": round(sum(w) / abs(sum(l_)), 2) if l_ and sum(l_) != 0 else None})
        out.sort(key=lambda x: -x["net"])
        return out

    # ---------------------------------------------------------------- persistence (paper data only, no secrets)
    def save(self, path: Path) -> None:
        data = {k: getattr(self, k) for k in ("starting", "cash", "peak", "max_dd", "day", "day_start", "next_id",
                                              "fees", "network_fees", "slippage", "failed_fees", "failed")}
        data["positions"] = [asdict(p) for p in self.positions.values()]
        data["closed"] = [asdict(p) for p in self.closed[-500:]]
        data["executions"] = [asdict(e) for e in self.executions[-500:]]
        data["equity_history"] = self.equity_history[-2000:]
        data["last_exit"] = self.last_exit
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data), encoding="utf-8")
        tmp.replace(path)

    @classmethod
    def load(cls, path: Path, starting_balance: float) -> "PaperBook":
        b = cls(starting_balance)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return b
        for k in ("starting", "cash", "peak", "max_dd", "day", "day_start", "next_id", "fees", "network_fees",
                  "slippage", "failed_fees", "failed"):
            if k in data:
                setattr(b, k, data[k])
        b.positions = {p["mint"]: Position(**p) for p in data.get("positions", [])}
        b.closed = [Position(**p) for p in data.get("closed", [])]
        b.executions = [Execution(**e) for e in data.get("executions", [])]
        b.equity_history = [tuple(x) for x in data.get("equity_history", [])]
        b.last_exit = data.get("last_exit", {})
        return b
