"""Data of the paper trading bot. None = UNKNOWN / NOT AVAILABLE, never 0."""
from __future__ import annotations

import time
from dataclasses import dataclass, field

PASS, FAIL, UNKNOWN = "PASS", "FAIL", "UNKNOWN"
TRADE, WATCH, REJECT = "TRADE", "WATCH", "REJECT"
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
