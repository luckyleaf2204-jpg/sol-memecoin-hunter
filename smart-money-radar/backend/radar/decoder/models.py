"""Normalised decoder output shared by the log-stream decoder and the full-transaction decoder."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

LAMPORTS = 1_000_000_000

VERIFIED = "verified"
PARTIAL = "partially_verified"
UNVERIFIED = "unverified"


@dataclass
class TradeRecord:
    signature: str
    event_key: str            # 'trade:<n>' — n-th trade event in execution order
    wallet: str
    mint: str
    slot: int
    block_time: int           # unix seconds
    tx_type: str              # buy | sell
    venue: str                # pump_curve | pumpswap
    sol_amount: float         # SOL swapped before fees (= actual entry cost for buys)
    token_amount: float       # UI units
    token_amount_raw: int
    decimals: int
    fee_trading_sol: float
    fee_network_sol: float = 0.0
    tip_sol: float = 0.0
    other_cost_sol: float = 0.0
    rent_sol: float = 0.0
    wallet_sol_delta: float | None = None
    reconciliation_residual_sol: float | None = None
    price_sol: float | None = None           # spot price after the trade (reserves)
    effective_price_sol: float | None = None  # sol_amount / token_amount
    market_cap_sol: float | None = None
    liquidity_sol: float | None = None
    fee_payer: str | None = None
    source: str = "rpc_tx"
    verification_status: str = UNVERIFIED
    verification_note: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_row(self) -> dict[str, Any]:
        d = asdict(self)
        d.pop("decimals")
        d.pop("extra")
        return d


@dataclass
class TokenTransfer:
    signature: str
    event_key: str
    wallet: str
    counterparty: str | None
    mint: str
    slot: int
    block_time: int
    direction: str            # transfer_in | transfer_out
    token_amount: float
    token_amount_raw: int
    decimals: int


@dataclass
class SolTransfer:
    source: str
    destination: str
    lamports: int
    under_swap: bool


@dataclass
class DecodedTx:
    signature: str
    slot: int
    block_time: int | None
    fee_payer: str | None
    fee_lamports: int
    failed: bool
    error: Any = None
    trades: list[TradeRecord] = field(default_factory=list)
    token_transfers: list[TokenTransfer] = field(default_factory=list)
    sol_transfers: list[SolTransfer] = field(default_factory=list)
    creates: list = field(default_factory=list)
    completes: list = field(default_factory=list)
    migrations: list = field(default_factory=list)
    amm_pools: dict[str, tuple[str, str]] = field(default_factory=dict)  # pool -> (base_mint, quote_mint)
    notes: list[str] = field(default_factory=list)
    logs_truncated: bool = False
