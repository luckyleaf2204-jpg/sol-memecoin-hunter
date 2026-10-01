"""RISK ENGINE — fail-closed: any error inside it means NO new position.

Entry is allowed only if ALL hold:
  mode PAPER · kill switch off · open positions < max · no position / cooldown on this CA ·
  today's P&L > -max_daily_loss % of the day's starting equity · drawdown from peak < max_drawdown %
  (a breach also engages the kill switch) · position <= max_position % · exposure after the fill
  <= max_total_exposure % · estimated price impact <= max_slippage % · feeds healthy (DexScreener not in
  cooldown, market data fresh) — an API outage can never make the bot buy.
Exits are never blocked by the Risk Engine.
"""
from __future__ import annotations

from trading.config import PAPER, TradingConfig
from trading.models import RiskDecision


class RiskEngine:
    def __init__(self, cfg: TradingConfig):
        self.cfg = cfg
        self.last_error = ""

    def drawdown_pct(self, equity: float, peak: float) -> float:
        return 0.0 if peak <= 0 else 100 * (peak - equity) / peak

    def check_entry(self, *, mint: str, usd: float, equity: float, peak: float, day_start: float,
                    open_positions: int, holding: bool, in_cooldown: bool, exposure: float,
                    est_impact: float | None, feeds_ok: bool, feeds_reason: str = "") -> RiskDecision:
        try:
            return self._check(mint, usd, equity, peak, day_start, open_positions, holding, in_cooldown,
                               exposure, est_impact, feeds_ok, feeds_reason)
        except Exception as e:  # fail closed
            self.last_error = f"{type(e).__name__}: {e}"
            return RiskDecision(False, [f"risk engine error ({self.last_error}) -> no entry"])

    def _check(self, mint, usd, equity, peak, day_start, open_positions, holding, in_cooldown, exposure,
               est_impact, feeds_ok, feeds_reason) -> RiskDecision:
        c, r = self.cfg, []
        if c.mode != PAPER:
            r.append(f"mode {c.mode} not allowed")
        if c.kill_switch:
            r.append("kill switch engaged")
        if open_positions >= c.max_open_positions:
            r.append(f"max open positions {c.max_open_positions}")
        if holding:
            r.append("already holding this CA")
        if in_cooldown:
            r.append(f"cooldown {c.cooldown_min:.0f} min after the last exit")
        if day_start > 0 and 100 * (equity - day_start) / day_start <= -c.max_daily_loss_pct:
            r.append(f"daily loss limit -{c.max_daily_loss_pct}% reached")
        dd = self.drawdown_pct(equity, peak)
        if dd >= c.max_drawdown_pct:
            c.kill_switch = True
            r.append(f"max drawdown {dd:.1f}% >= {c.max_drawdown_pct}% -> kill switch engaged")
        if usd <= 0:
            r.append("size 0")
        if usd > equity * c.max_position_pct / 100 + 1e-9:
            r.append(f"position > {c.max_position_pct}% of equity")
        if exposure + usd > equity * c.max_total_exposure_pct / 100 + 1e-9:
            r.append(f"total exposure would exceed {c.max_total_exposure_pct}%")
        if est_impact is None:
            r.append("price impact unknown (no liquidity data)")
        elif 100 * est_impact > c.max_slippage_pct:
            r.append(f"estimated price impact {100 * est_impact:.2f}% > {c.max_slippage_pct}%")
        if not feeds_ok:
            r.append(f"data feed unhealthy ({feeds_reason}) -> no entries")
        return RiskDecision(not r, r or ["all limits OK"])
