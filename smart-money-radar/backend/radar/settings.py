"""Runtime settings editable from the dashboard (stored in `system_settings`).

Secrets are NOT settings — they come from the environment only (radar.config).
Every value is validated against SPEC (type + range) before it is saved.
"""
from __future__ import annotations

import copy
import time
from dataclasses import dataclass
from typing import Any

# Default configuration — PHẦN XVII of the specification.
DEFAULTS: dict[str, Any] = {
    "analysis_period_days": 30,
    "min_buy_sol": 2.0,
    "min_win_rate": 0.65,
    "preferred_positions_min": 5,
    "preferred_positions_max": 30,
    "min_closed_positions_official": 5,
    "high_confidence_min_closed": 10,
    "medium_confidence_min_verified_share": 0.5,
    "high_confidence_min_verified_share": 0.8,
    # position bookkeeping
    "dust_fraction": 0.001,              # holdings below 0.1% of peak holdings = exited
    "write_off_after_hours": 72,         # open bag with no wallet activity ...
    "write_off_value_fraction": 0.10,    # ... and worth < 10% of its cost -> written off (loss)
    "peak_window_days": 7,               # MFE / 2x..10x hits measured within 7 days of entry
    # liquidity validation for "token reached Nx"
    "peak_min_bar_volume_sol": 1.0,
    "peak_min_liquidity_sol": 3.0,
    "peak_confirm_points": 2,
    "peak_confirm_window_minutes": 5,
    "peak_min_trade_sol": 0.3,
    # copyable human traders (Top 10 requires both when switched on)
    "require_human": True,               # bot / likely-bot wallets are not ranked
    "require_copyable": True,            # follower copying late must still win often enough
    "copy_delay_seconds": 60,            # how late the follower copies (simulation also runs 30 s / 180 s)
    "copy_cost_pct": 0.03,               # follower round-trip fees + slippage (3 %)
    "min_copy_win_rate": 0.6,
    "min_copy_positions": 5,             # positions with price data needed to judge copyability
    # watched wallets (KOL / callers)
    "kol_alert_enabled": True,           # alert the first buy of every watched wallet in a token
    "kol_alert_min_sol": 0.3,
    "kol_alert_max_age_seconds": 300,    # never alert trades older than this (history, re-checks)
    "kol_exit_alert_enabled": True,      # alert when it has sold >= 50 % / 90 % of that token
    # co-buy signal
    "signal_window_minutes": 5,
    "signal_min_position_sol": 2.0,
    "signal_watch_min_wallets": 2,
    "signal_watch_min_total_sol": 5.0,
    "signal_strong_min_wallets": 3,
    "signal_strong_min_total_sol": 10.0,
    "signal_very_strong_min_wallets": 5,
    "signal_very_strong_min_total_sol": 20.0,
    "signal_max_runup": 2.0,             # price may be at most 2x the first smart entry
    "signal_min_wallet_score": 65.0,     # buyers must be at least Watchlist
    "signal_high_risk_caps_watch": True,
    "signal_require_verified_for_strong": True,
    "relationship_cluster_min_strength": 0.6,
    # alerts
    "telegram_enabled": True,
    "telegram_chat_id": "",
    "alert_min_level": "WATCH",
    "alert_resend_new_buyers": 2,        # resend same level when >= 2 new independent buyers joined
    "alert_resend_flow_multiple": 2.0,   # ... or smart-money buy SOL doubled
    "alert_cooldown_minutes": 10,
    "fresh_wallet_hours": 48,            # holder whose first on-chain activity is younger = "new wallet"
    "holder_alert_pct": 5.0,             # one wallet holding more than this % of supply (curve/pool excluded)
    "exit_alert_enabled": True,          # alert when signal wallets sell what they bought
    "exit_alert_share": 0.5,             # ... once >= 50 % (and again at >= 90 %)
    # discovery / backfill
    "discovery_min_buy_sol": 2.0,
    "discovery_min_distinct_tokens": 2,
    "backfill_days": 30,
    "backfill_max_signatures": 3000,
    "tracked_wallet_limit": 2000,
    "skip_high_frequency_wallets": True,  # >= hf_signatures_per_day -> not backfilled (bot-like)
    "hf_signatures_per_day": 1000,
    "wallet_sync_interval_minutes": 30,
    # Helius backfill (phase 1, 23/11/2026): getTransactionsForAddress, daily credit cap, wallets per day
    "helius_tx_history": False,           # backfill via getTransactionsForAddress (Helius only)
    "helius_daily_credit_cap": 150000,    # backfill pauses when today's Helius credits (all workers) reach this
    "backfill_wallets_per_day": 50,       # new backfills started per UTC day, highest priority first
    # winner reverse (research phases 1-2, 23/11/2026): full capture of rising tokens and early buyers
    "winner_capture_mc_sol": 150.0,       # a token at this market cap gets all its trades stored
    "winner_capture_min_sol": 0.05,       # ... trades below this are dust and never stored
    "winner_capture_hours": 24.0,         # ... for this long after it crossed the market cap
    "winner_buffer_minutes": 120.0,       # stream keeps dropped trades this long in memory
    "winner_reverse_enabled": True,
    "winner_min_peak_mc_sol": 400.0,      # winner: peak market cap (robust p95) at least this
    "winner_min_multiple": 5.0,           # ... at least this multiple of the first 10 minutes
    "winner_hold_fraction": 0.3,          # ... still >= 30 % of the peak one hour after the peak
    "winner_eval_after_hours": 6.0,       # evaluate a token once it is this old (exits happened)
    "winner_early_mc_fraction": 0.3,      # early buyer: first buy at <= 30 % of the peak market cap
    "winner_min_buy_sol": 0.2,            # early buyer: at least this much SOL in
    "winner_min_tokens": 3,               # candidate: profitable early buyer of >= 3 different winners
    "winner_lookback_days": 14,
    "winner_max_trades_per_day": 100,     # candidate: not a high-frequency bot
    "stream_store_min_sol": 1.0,
    "stream_retention_days": 7,
    # backtest
    "backtest_slippage_bps": 100,
    "backtest_detection_latency_seconds": 5,
    "backtest_trade_size_sol": 1.0,
    # wallets excluded by the user (never ranked, never counted in signals)
    "excluded_wallets": [],
    "trading_enabled": False,
}


@dataclass(frozen=True)
class Spec:
    kind: type | tuple
    lo: float | None = None
    hi: float | None = None
    choices: tuple | None = None


SPEC: dict[str, Spec] = {
    "analysis_period_days": Spec(int, 7, 90, (7, 30, 90)),
    "min_buy_sol": Spec((int, float), 0.1, 1000),
    "min_win_rate": Spec((int, float), 0.0, 1.0),
    "preferred_positions_min": Spec(int, 1, 1000),
    "preferred_positions_max": Spec(int, 1, 10000),
    "min_closed_positions_official": Spec(int, 1, 1000),
    "high_confidence_min_closed": Spec(int, 1, 1000),
    "medium_confidence_min_verified_share": Spec((int, float), 0, 1),
    "high_confidence_min_verified_share": Spec((int, float), 0, 1),
    "dust_fraction": Spec((int, float), 0, 0.1),
    "write_off_after_hours": Spec((int, float), 1, 24 * 90),
    "write_off_value_fraction": Spec((int, float), 0, 1),
    "peak_window_days": Spec((int, float), 0.1, 90),
    "peak_min_bar_volume_sol": Spec((int, float), 0, 10000),
    "peak_min_liquidity_sol": Spec((int, float), 0, 100000),
    "peak_confirm_points": Spec(int, 1, 20),
    "peak_confirm_window_minutes": Spec((int, float), 1, 1440),
    "peak_min_trade_sol": Spec((int, float), 0, 1000),
    "require_human": Spec(bool),
    "require_copyable": Spec(bool),
    "copy_delay_seconds": Spec(int, 0, 1800),
    "copy_cost_pct": Spec((int, float), 0, 0.5),
    "min_copy_win_rate": Spec((int, float), 0, 1),
    "min_copy_positions": Spec(int, 1, 1000),
    "kol_alert_enabled": Spec(bool),
    "kol_alert_min_sol": Spec((int, float), 0, 1000),
    "kol_alert_max_age_seconds": Spec((int, float), 10, 3600),
    "kol_exit_alert_enabled": Spec(bool),
    "signal_window_minutes": Spec((int, float), 0.5, 120),
    "signal_min_position_sol": Spec((int, float), 0, 1000),
    "signal_watch_min_wallets": Spec(int, 2, 100),
    "signal_watch_min_total_sol": Spec((int, float), 0, 100000),
    "signal_strong_min_wallets": Spec(int, 2, 100),
    "signal_strong_min_total_sol": Spec((int, float), 0, 100000),
    "signal_very_strong_min_wallets": Spec(int, 2, 100),
    "signal_very_strong_min_total_sol": Spec((int, float), 0, 100000),
    "signal_max_runup": Spec((int, float), 1.0, 100),
    "signal_min_wallet_score": Spec((int, float), 0, 100),
    "signal_high_risk_caps_watch": Spec(bool),
    "signal_require_verified_for_strong": Spec(bool),
    "relationship_cluster_min_strength": Spec((int, float), 0, 1),
    "telegram_enabled": Spec(bool),
    "telegram_chat_id": Spec(str),
    "alert_min_level": Spec(str, choices=("WATCH", "STRONG", "VERY_STRONG")),
    "alert_resend_new_buyers": Spec(int, 1, 100),
    "alert_resend_flow_multiple": Spec((int, float), 1.1, 100),
    "alert_cooldown_minutes": Spec((int, float), 0, 1440),
    "fresh_wallet_hours": Spec((int, float), 1, 24 * 30),
    "holder_alert_pct": Spec((int, float), 0.5, 50),
    "exit_alert_enabled": Spec(bool),
    "exit_alert_share": Spec((int, float), 0.1, 0.95),
    "discovery_min_buy_sol": Spec((int, float), 0.1, 1000),
    "discovery_min_distinct_tokens": Spec(int, 1, 100),
    "backfill_days": Spec(int, 1, 120),
    "backfill_max_signatures": Spec(int, 100, 100000),
    "tracked_wallet_limit": Spec(int, 10, 100000),
    "skip_high_frequency_wallets": Spec(bool),
    "hf_signatures_per_day": Spec(int, 50, 100000),
    "wallet_sync_interval_minutes": Spec((int, float), 1, 1440),
    "helius_tx_history": Spec(bool),
    "helius_daily_credit_cap": Spec(int, 1000, 10_000_000),
    "backfill_wallets_per_day": Spec(int, 0, 100000),
    "winner_capture_mc_sol": Spec((int, float), 10, 100000),
    "winner_capture_min_sol": Spec((int, float), 0, 10),
    "winner_capture_hours": Spec((int, float), 1, 168),
    "winner_buffer_minutes": Spec((int, float), 10, 360),
    "winner_reverse_enabled": Spec(bool),
    "winner_min_peak_mc_sol": Spec((int, float), 10, 1_000_000),
    "winner_min_multiple": Spec((int, float), 1.5, 1000),
    "winner_hold_fraction": Spec((int, float), 0, 1),
    "winner_eval_after_hours": Spec((int, float), 1, 72),
    "winner_early_mc_fraction": Spec((int, float), 0.01, 1),
    "winner_min_buy_sol": Spec((int, float), 0, 100),
    "winner_min_tokens": Spec(int, 1, 50),
    "winner_lookback_days": Spec(int, 1, 60),
    "winner_max_trades_per_day": Spec((int, float), 1, 100000),
    "stream_store_min_sol": Spec((int, float), 0, 1000),
    "stream_retention_days": Spec(int, 1, 365),
    "backtest_slippage_bps": Spec((int, float), 0, 5000),
    "backtest_detection_latency_seconds": Spec((int, float), 0, 600),
    "backtest_trade_size_sol": Spec((int, float), 0.01, 1000),
    "excluded_wallets": Spec(list),
    "trading_enabled": Spec(bool, choices=(False,)),  # cannot be switched on
}


class SettingsError(ValueError):
    pass


def validate(key: str, value: Any) -> Any:
    from .solana.base58 import is_valid_address

    spec = SPEC.get(key)
    if spec is None:
        raise SettingsError(f"unknown setting {key}")
    if spec.kind is bool:
        if not isinstance(value, bool):
            raise SettingsError(f"{key} must be true/false")
    elif spec.kind is int:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
            raise SettingsError(f"{key} must be an integer")
        value = int(value)
    elif spec.kind == (int, float):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise SettingsError(f"{key} must be a number")
        value = float(value)
    elif spec.kind is str:
        if not isinstance(value, str) or len(value) > 200:
            raise SettingsError(f"{key} must be a short string")
        value = value.strip()
    elif spec.kind is list:
        if not isinstance(value, list):
            raise SettingsError(f"{key} must be a list")
        if key == "excluded_wallets":
            clean = []
            for w in value:
                if not isinstance(w, str) or not is_valid_address(w.strip()):
                    raise SettingsError(f"invalid wallet address: {w!r}")
                clean.append(w.strip())
            value = sorted(set(clean))
    if spec.choices is not None and value not in spec.choices:
        raise SettingsError(f"{key} must be one of {spec.choices}")
    if spec.lo is not None and isinstance(value, (int, float)) and value < spec.lo:
        raise SettingsError(f"{key} must be >= {spec.lo}")
    if spec.hi is not None and isinstance(value, (int, float)) and value > spec.hi:
        raise SettingsError(f"{key} must be <= {spec.hi}")
    return value


def merged(overrides: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    for k, v in overrides.items():
        if k in DEFAULTS:
            try:
                out[k] = validate(k, v)
            except SettingsError:
                pass  # keep default for an invalid stored value
    out["trading_enabled"] = False
    return out


class SettingsStore:
    """Reads settings from Postgres with a short in-process cache."""

    def __init__(self, pool, ttl: float = 15.0):
        self.pool = pool
        self.ttl = ttl
        self._cache: dict[str, Any] | None = None
        self._at = 0.0

    async def get(self) -> dict[str, Any]:
        if self._cache is not None and time.monotonic() - self._at < self.ttl:
            return self._cache
        rows = await self.pool.fetch("SELECT key, value FROM system_settings")
        self._cache = merged({r["key"]: r["value"] for r in rows})
        self._at = time.monotonic()
        return self._cache

    async def update(self, changes: dict[str, Any], by: str = "dashboard") -> dict[str, Any]:
        clean = {k: validate(k, v) for k, v in changes.items()}
        if clean.get("trading_enabled"):
            raise SettingsError("trading cannot be enabled")
        async with self.pool.acquire() as conn:
            async with conn.transaction():
                for k, v in clean.items():
                    await conn.execute(
                        """INSERT INTO system_settings(key, value, updated_at, updated_by)
                           VALUES ($1, $2, now(), $3)
                           ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value,
                               updated_at = now(), updated_by = EXCLUDED.updated_by""",
                        k, v, by)
        self._cache = None
        return await self.get()
