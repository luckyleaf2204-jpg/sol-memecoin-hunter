"""Data of the paper trading bot. None = UNKNOWN / NOT AVAILABLE, never 0."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
TRADE, WATCH, REJECT = "TRADE", "WATCH", "REJECT"
PENDING_IDENTITY = "PENDING_IDENTITY"      # identity not verified yet (no conflict): never REJECT, never BUY
RUN, READY, BLOCKED, ERROR = "RUN", "READY", "BLOCKED", "ERROR"


@dataclass
class Check:
    key: str
    result: str                  # PASS | FAIL | UNKNOWN (UNKNOWN blocks a trade, it is never a pass)
    value: str = ""
    rule: str = ""


@dataclass
class Vet:
    mint: str
    checks: list[Check] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return bool(self.checks) and all(c.result == PASS for c in self.checks)

    @property
    def failed(self) -> list[Check]:
        return [c for c in self.checks if c.result != PASS]


@dataclass
class Score:
    mint: str
    components: dict[str, int | None]        # x_alpha, momentum, onchain, liquidity, smart_money, risk -> 0-100/None
    opportunity: int | None                  # 0-100 (None = not enough data)
    confidence: int                          # 0-100: share of the score that is backed by data
    decision: str                            # TRADE | WATCH | REJECT
    why: list[str] = field(default_factory=list)
    invalidate: list[str] = field(default_factory=list)
    waiting: list[str] = field(default_factory=list)   # WATCH: what is still missing before a trade (never a reject)
    rejected: list[str] = field(default_factory=list)  # REJECT: the real reasons (never "data missing")


@dataclass
class Size:
    usd: float
    reasons: list[str] = field(default_factory=list)
    capped_by: str = ""


@dataclass
class RiskDecision:
    allowed: bool
    reasons: list[str] = field(default_factory=list)


@dataclass
class Execution:
    ts: float
    mint: str
    symbol: str
    side: str                    # BUY | SELL
    status: str                  # FILLED | FAILED | REJECTED
    route: str
    usd_in: float = 0.0          # USD spent (BUY) or position value sold at mark (SELL)
    tokens: float = 0.0
    ref_price: float | None = None   # validated market price at decision time
    fill_price: float | None = None
    price_impact_pct: float = 0.0
    slippage_pct: float = 0.0
    fee_usd: float = 0.0
    network_fee_usd: float = 0.0
    latency_ms: int = 0
    reason: str = ""
    model: str = "PAPER (modelled execution, no transaction sent)"


@dataclass
class Position:
    id: int
    mint: str
    symbol: str
    opened_at: float
    entry_price: float
    tokens: float
    cost_usd: float              # USD spent incl. fees
    initial_tokens: float
    stop_price: float
    tp1_price: float
    tp2_price: float
    trailing_pct: float
    high_price: float
    tp1_done: bool = False
    status: str = "OPEN"         # OPEN | CLOSED
    last_price: float | None = None
    last_price_ts: float | None = None
    realized_usd: float = 0.0    # proceeds of partial sells (net)
    fees_usd: float = 0.0
    slippage_usd: float = 0.0
    entry_score: int | None = None
    entry_why: list[str] = field(default_factory=list)
    entry_liq: float | None = None
    entry_vol: float | None = None
    stale: bool = False          # no validated price right now -> nothing is sold on a guess
    setup: str = ""              # how the bot found it (scan reasons), for P&L per setup
    exit_reason: str = ""
    closed_at: float | None = None
    # path diagnostics (logging only — the Exit Engine does not read them)
    low_price: float | None = None
    tp1_hit_ts: float | None = None        # first time the observed price reached the TP1 level
    tp2_hit_ts: float | None = None
    sl_hit_ts: float | None = None         # first time the observed price reached the initial stop level
    initial_stop: float | None = None
    entry_lifecycle: str | None = None     # NEW | PRE_MIGRATION | POST_MIGRATION (lifecycle engine)
    entry_setup: str | None = None         # NEW | PRE_MIGRATION | SECOND_WAVE
    entry_setup_score: float | None = None
    # cost-free price move (step 1: P&L at fixed round-trip costs): mid prices backed out of every fill
    entry_mid: float | None = None
    high_ts: float | None = None           # last time the observed price made a new high (step 3 time stop)
    runner_active: bool = False            # step 3: TP2 runner armed
    entry_engine: str = ""                 # step 4: engine that gave the BUY signal (lifecycle / experimental / old)
    sample_id: str = ""                    # step 4: TradingConfig.sample_id() when opened
    sample_epoch: str = ""                 # SampleEpoch.id when opened ("" = no epoch -> LEGACY)
    haircut_exits: int = 0                 # step D: sells filled with the no-quote haircut (no Jupiter SELL quote)
    tx_count: int = 0                      # fix 6: transactions of this trade (the buy + every sell attempt)
    fixed_fees_usd: float = 0.0            # fix 6: network + priority fees of those transactions
    exit_mid_value: float = 0.0            # sum(tokens sold x mid at that sell)

    @property
    def noquote(self) -> bool:
        """Opened on a SIMULATED fill (no executable Jupiter route): never part of the main P&L."""
        return "+noquote" in (self.setup or "")

    @property
    def gross_move_pct(self) -> float | None:
        """Closed trade's price move before any modelled cost (impact, latency, fees): mid in vs mid out."""
        if self.status != "CLOSED" or not self.entry_mid or not self.initial_tokens:
            return None
        return 100 * (self.exit_mid_value / (self.initial_tokens * self.entry_mid) - 1)

    @property
    def mfe_pct(self) -> float | None:
        return 100 * (self.high_price / self.entry_price - 1) if self.entry_price else None

    @property
    def mae_pct(self) -> float | None:
        return 100 * (self.low_price / self.entry_price - 1) if self.entry_price and self.low_price else None

    def path_log(self) -> dict:
        rel = lambda t: None if t is None else round(t - self.opened_at)  # noqa: E731
        return {"mfe_pct": None if self.mfe_pct is None else round(self.mfe_pct, 2),
                "mae_pct": None if self.mae_pct is None else round(self.mae_pct, 2),
                "time_to_tp1_s": rel(self.tp1_hit_ts), "time_to_tp2_s": rel(self.tp2_hit_ts),
                "time_to_sl_s": rel(self.sl_hit_ts), "time_to_exit_s": rel(self.closed_at)}

    def value(self, price: float | None = None) -> float | None:
        p = price if price is not None else self.last_price
        return None if p is None else self.tokens * p

    def pnl_usd(self) -> float | None:
        v = self.value()
        return None if v is None else self.realized_usd + v - self.cost_usd

    def pnl_pct(self) -> float | None:
        p = self.pnl_usd()
        return None if p is None or not self.cost_usd else 100 * p / self.cost_usd


@dataclass
class Activity:
    ts: float
    kind: str                    # BUY | SELL | FAILED | REJECT | BLOCK | WATCH | INFO | KILL
    mint: str = ""
    symbol: str = ""
    text: str = ""
    usd: float | None = None
    price: float | None = None


@dataclass
class ModuleState:
    key: str                     # scan | vet | size | risk | fills | book
    status: str = READY
    detail: str = ""
    updated: float = field(default_factory=time.time)
    items: list[dict] = field(default_factory=list)
