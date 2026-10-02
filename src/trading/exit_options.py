"""Optional exit variants for A/B (step 3). trading/exits.py stays byte-identical; these rules are applied to its
result by the bot. Every option is OFF by default, so the production exit behaviour is unchanged.

  wide_stop_pct      stop loss at 20-25 % instead of stop_loss_pct. Sizing uses the same stop
                     (usd = equity x risk_per_trade_pct / stop), so the loss at the stop stays <= 1 % of equity.
  tp2_runner_frac    at TP2 keep this fraction of the position as a runner instead of selling everything; the runner
                     has a break-even stop and leaves on its own trailing stop (runner_trailing_pct, default the
                     normal trailing) or at max hold.
  time_stop_min      leave a position that has made no new high for this many minutes (20-30 to test).
"""
from __future__ import annotations

RUNNER_TRAILING = "runner_trailing_stop"
TIME_STOP = "time_stop"


def stop_pct(cfg) -> float:
    """Stop distance used for the stop price AND for sizing."""
    return cfg.wide_stop_pct if cfg.wide_stop_pct else cfg.stop_loss_pct


def apply_exit_options(p, sig, price: float | None, cfg, now: float):
    """sig = trading.exits.exit_signal(...) -> (fraction, reason) | None. Returns the (possibly changed) signal."""
    if sig is not None:
        frac, reason = sig
        if reason == "take_profit_2" and cfg.tp2_runner_frac > 0:
            if not p.runner_active:
                return 1 - cfg.tp2_runner_frac, "take_profit_2"          # sell all but the runner
            # the runner is above the TP2 level: TP2 is no longer an exit, its own trailing stop is
            if price is not None and price <= p.high_price * (1 - p.trailing_pct / 100):
                return 1.0, RUNNER_TRAILING
            if (now - p.opened_at) / 60 >= cfg.max_hold_min:
                return 1.0, "max_hold_time"
            return None
        if p.runner_active and reason == "trailing_stop":
            return frac, RUNNER_TRAILING
        return sig
    if cfg.time_stop_min and price is not None:
        last_high = p.high_ts if p.high_ts is not None else p.opened_at
        if now - last_high >= cfg.time_stop_min * 60:
            return 1.0, TIME_STOP
    return None


def on_filled_sell(p, reason: str, cfg) -> None:
    """After a filled TP2 sell with a runner configured: arm the runner (break-even stop, its own trailing)."""
    if reason == "take_profit_2" and cfg.tp2_runner_frac > 0 and p.status != "CLOSED" and not p.runner_active:
        p.runner_active = True
        p.tp1_done = True                                   # no second TP1; the stop becomes break-even
        p.stop_price = max(p.stop_price, p.entry_price)
        if cfg.runner_trailing_pct:
            p.trailing_pct = cfg.runner_trailing_pct
