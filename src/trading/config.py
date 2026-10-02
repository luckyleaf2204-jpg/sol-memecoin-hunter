"""Bot configuration. PAPER is the only mode that can run; CONFIRM / AUTO are refused (no real execution exists)."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, fields
from pathlib import Path

PAPER, CONFIRM, AUTO = "PAPER", "CONFIRM", "AUTO"
ALLOWED_MODES = (PAPER,)          # CONFIRM / AUTO need a real executor + explicit owner approval: not built


class ModeNotAllowed(ValueError):
    pass


@dataclass
class TradingConfig:
    mode: str = PAPER
    enabled: bool = True                     # paper bot runs with the scanner
    starting_balance: float = 1_000.0        # paper USD
    # SIZE
    risk_per_trade_pct: float = 1.0          # % of equity lost if the stop loss is hit
    max_position_pct: float = 5.0            # hard cap per position (% of equity)
    max_liquidity_pct: float = 2.0           # never take more than this % of the pool's liquidity
    min_position_usd: float = 10.0
    # RISK
    max_open_positions: int = 5
    max_total_exposure_pct: float = 25.0
    max_daily_loss_pct: float = 5.0
    max_drawdown_pct: float = 20.0
    max_slippage_pct: float = 3.0            # modelled price impact + slippage allowed per fill
    min_liquidity_usd: float = 10_000.0
    max_data_age_s: float = 30.0             # market data older than this -> no new entries
    kill_switch: bool = False
    # SCORE thresholds
    trade_min_opportunity: int = 65
    trade_min_confidence: int = 60
    watch_min_opportunity: int = 45
    # EXIT
    stop_loss_pct: float = 15.0
    tp1_pct: float = 30.0
    tp1_sell_frac: float = 0.5
    tp2_pct: float = 80.0
    trailing_pct: float = 15.0               # armed after TP1
    max_hold_min: float = 120.0
    cooldown_min: float = 30.0               # no re-entry on the same CA after an exit
    # EXECUTION model
    seed: int = 7
    hard_exit_no_quote_haircut_pct: float = 30.0   # HARD / SL exit without a Jupiter SELL quote: ref price x (1 - this)
    priority_fee_sol: float = 0.005          # priority fee / Jito tip per transaction (buy, sell, failed attempts)
    dump_tail_threshold_pct: float = -20.0   # SELL while the 5m price change is at or below this -> long-tail slippage
    dump_tail_scale: float = 0.10            # mean extra SELL slippage = scale x |5m change| (exponential draw)
    dump_tail_cap_pct: float = 25.0          # cap of that extra slippage
    cost_stress_pct: tuple = (5.0, 7.0, 10.0)   # report P&L at these round-trip costs (gross move - cost)
    # EXPERIMENTAL engine (Implementation Spec Part 3): soft EarlyScore + age thresholds + prior risk decide TRADE;
    # the OLD engine still runs on every token for A/B. Off by default (tests / library); the server turns it on.
    experimental: bool = False
    paper_fill_without_quote: bool = False   # experimental PAPER: fill a NO_ROUTE / no-quote candidate on the model
                                             # (off: such fills are not executable; if on they stay out of the main P&L)
    # PAPER execution calibration (max_slippage_pct unchanged): CURRENT | CONSERVATIVE | EMPIRICAL
    latency_slippage_model: str = "CURRENT"    # AUTO: <50 samples CURRENT · >=50 P90 · >=100 P75 if stable
    latency_probe: bool = False              # (server: on) re-quote after the latency window to measure real drift (quotes only)
    empirical_min_samples: int = 50          # EMPIRICAL stays off (falls back to CURRENT) below this
    # ENTRY risk buffer (experimental NEW BUYs only; Risk Engine, exits and the hard limit 60 unchanged)
    entry_max_risk: int = 55
    # LIFECYCLE engine (Lifecycle-Aware Hunter V1). Conservative initial thresholds, chosen for A/B and later
    # calibration — not to produce BUYs. The server turns the engine on (LIFECYCLE_ENGINE=1).
    lifecycle: bool = False
    pre_migration_shadow: bool = True        # V1.1: PRE-MIGRATION setups are SHADOW only (would_buy logged, no BUY)
    premigration_progress_min: float = 70.0  # Pump.fun curve progress (%) that makes a curve PRE_MIGRATION
    new_setup_threshold: float = 70.0
    premigration_setup_threshold: float = 70.0
    second_wave_setup_threshold: float = 70.0
    min_setup_confidence: float = 0.50
    # second-wave state machine (post-migration pair only)
    second_wave_min_history_s: float = 120.0
    first_pump_min_pct: float = 30.0
    pullback_min_pct: float = 15.0
    pullback_max_pct: float = 60.0
    support_min_s: float = 60.0
    bounce_min_pct: float = 5.0
    reentry_buy_share: float = 0.55
    liquidity_retention_min: float = 0.5
    volume_retention_min: float = 0.15

    def __post_init__(self):
        if self.mode not in ALLOWED_MODES:
            raise ModeNotAllowed(f"mode {self.mode} is not available: only PAPER trading exists")

    @classmethod
    def load(cls, path: Path) -> "TradingConfig":
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return cls()
        known = {f.name: f.type for f in fields(cls)}
        cfg = cls(**{k: v for k, v in raw.items() if k in known and k != "mode"})   # mode is always PAPER
        return cfg

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")

    def set_mode(self, mode: str) -> None:
        if mode not in ALLOWED_MODES:
            raise ModeNotAllowed(f"mode {mode} is not available: only PAPER trading exists")
        self.mode = mode
