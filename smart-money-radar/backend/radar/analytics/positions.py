"""Build positions (holding cycles) from a wallet's decoded events for one token.

Rules (documented in docs/METHODOLOGY.md):

* A position = one holding cycle of one token. It opens on the first buy (or token receipt)
  while the wallet holds nothing, and closes when holdings fall to dust
  (<= dust_fraction of the cycle's peak holdings). A buy after that opens a NEW position.
* Several buys inside a cycle are ONE position (their SOL is summed = Actual Entry Cost);
  several sells (partial exits) are part of the same position.
* Cost accounting uses the average-cost method. Selling k tokens realises
  proceeds − k × average cost. Fees (trading + network + tips + bot fees) of every trade in
  the position are subtracted from realised PnL. Rent is excluded (refundable).
* Token transfers are NOT buys/sells: a transfer in adds tokens at zero cost and flags the
  position; a transfer out removes tokens together with their share of cost basis (the cost
  moved, it was not lost) and flags the position.
* A sell with no open position (tokens bought before the data window or obtained in an
  unknown way) is an "orphan sell" — no PnL is invented for it; it lowers data coverage.
* An open position whose wallet has been inactive on it for `write_off_after_hours` and whose
  remaining tokens are worth < `write_off_value_fraction` of their cost (or cannot be priced)
  is `written_off`: it counts as a closed LOSS (remaining value 0). Without this rule wallets
  that never sell dead bags would show inflated win rates.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable

TYPE_RANK = {"buy": 0, "transfer_in": 1, "sell": 2, "transfer_out": 3}
VERIF_RANK = {"verified": 0, "partially_verified": 1, "unverified": 2}


@dataclass
class Event:
    signature: str
    event_key: str
    slot: int
    block_time: int
    tx_type: str
    sol_amount: float = 0.0
    token_amount: float = 0.0
    fees_sol: float = 0.0          # trading + network + tip + other costs
    verification_status: str = "verified"
    market_cap_sol: float | None = None
    price_sol: float | None = None
    liquidity_sol: float | None = None   # curve real SOL / pool SOL reserves at execution

    @classmethod
    def from_row(cls, r: dict[str, Any]) -> "Event":
        bt = r["block_time"]
        if hasattr(bt, "timestamp"):
            bt = int(bt.timestamp())
        fees = sum(float(r.get(k) or 0) for k in ("fee_trading_sol", "fee_network_sol", "tip_sol",
                                                   "other_cost_sol"))
        return cls(signature=r["signature"], event_key=r["event_key"], slot=int(r["slot"]),
                   block_time=int(bt), tx_type=r["tx_type"], sol_amount=float(r.get("sol_amount") or 0),
                   token_amount=float(r.get("token_amount") or 0), fees_sol=max(fees, 0.0),
                   verification_status=r.get("verification_status") or "unverified",
                   market_cap_sol=float(r["market_cap_sol"]) if r.get("market_cap_sol") is not None else None,
                   price_sol=float(r["effective_price_sol"]) if r.get("effective_price_sol") is not None else None,
                   liquidity_sol=float(r["liquidity_sol"]) if r.get("liquidity_sol") is not None else None)


@dataclass
class Position:
    wallet: str
    mint: str
    cycle_no: int
    status: str = "open"
    opened_at: int = 0
    closed_at: int | None = None
    first_buy_at: int | None = None
    last_buy_at: int | None = None
    first_sell_at: int | None = None
    last_activity_at: int = 0
    n_buys: int = 0
    n_sells: int = 0
    buy_sol: float = 0.0
    sell_sol: float = 0.0
    fees_sol: float = 0.0
    tokens_bought: float = 0.0
    tokens_sold: float = 0.0
    tokens_in: float = 0.0
    tokens_out: float = 0.0
    remaining_tokens: float = 0.0
    cost_basis_remaining: float = 0.0
    transferred_out_cost: float = 0.0
    realized_gross: float = 0.0     # proceeds - cost of sold tokens (before fees)
    peak_holdings: float = 0.0
    entry_mc_weighted: float = 0.0
    exit_mc_weighted: float = 0.0
    buy_tx_sizes: list[float] = field(default_factory=list)
    entry_liquidity_sol: float | None = None
    flags: list[str] = field(default_factory=list)
    signatures: list[str] = field(default_factory=list)
    verification: str = "verified"
    # filled by mark_to_market / price analytics
    current_price_sol: float | None = None
    unrealized_pnl_sol: float | None = None
    price_metrics: dict[str, Any] = field(default_factory=dict)

    # ---- derived -------------------------------------------------------------------
    @property
    def is_closed(self) -> bool:
        return self.status in ("closed", "written_off")

    @property
    def net_entry_cost(self) -> float:
        """Entry cost of the tokens that were sold or are still held (excludes cost moved out)."""
        return self.buy_sol - self.transferred_out_cost

    @property
    def realized_pnl_sol(self) -> float:
        if self.status == "written_off":
            return self.sell_sol - self.net_entry_cost - self.fees_sol
        return self.realized_gross - self.fees_sol

    @property
    def roi(self) -> float | None:
        if self.buy_sol <= 0 or not self.is_closed:
            return None
        return self.realized_pnl_sol / self.net_entry_cost if self.net_entry_cost > 0 else None

    @property
    def avg_entry_price(self) -> float | None:
        return self.buy_sol / self.tokens_bought if self.tokens_bought > 0 else None

    @property
    def avg_exit_price(self) -> float | None:
        return self.sell_sol / self.tokens_sold if self.tokens_sold > 0 else None

    @property
    def realized_multiple(self) -> float | None:
        if self.net_entry_cost <= 0 or not self.is_closed:
            return None
        return self.sell_sol / self.net_entry_cost

    @property
    def entry_share_of_liquidity(self) -> float | None:
        if self.entry_liquidity_sol is None or self.buy_sol <= 0:
            return None
        return self.buy_sol / max(self.entry_liquidity_sol + self.buy_sol, 1e-9)

    @property
    def entry_mc_sol(self) -> float | None:
        return self.entry_mc_weighted / self.buy_sol if self.buy_sol > 0 and self.entry_mc_weighted else None

    @property
    def exit_mc_sol(self) -> float | None:
        return self.exit_mc_weighted / self.sell_sol if self.sell_sol > 0 and self.exit_mc_weighted else None

    @property
    def holding_seconds(self) -> int | None:
        if self.first_buy_at is None:
            return None
        end = self.closed_at if self.closed_at is not None else None
        return (end - self.first_buy_at) if end is not None else None

    def qualifying(self, min_buy_sol: float) -> bool:
        return self.buy_sol >= min_buy_sol and self.n_buys > 0

    @property
    def is_win(self) -> bool | None:
        if not self.is_closed:
            return None
        return self.realized_pnl_sol > 0


def _add_flag(p: Position, f: str) -> None:
    if f not in p.flags:
        p.flags.append(f)


def build_positions(wallet: str, mint: str, events: Iterable[Event], dust_fraction: float = 0.001,
                    ) -> tuple[list[Position], list[Event]]:
    """Return (positions, orphan_sells). Events may come in any order; duplicates (same
    signature+event_key) are ignored."""
    seen: set[tuple[str, str]] = set()
    evs = []
    for e in events:
        k = (e.signature, e.event_key)
        if k in seen:
            continue
        seen.add(k)
        evs.append(e)
    evs.sort(key=lambda e: (e.slot, TYPE_RANK.get(e.tx_type, 9), e.signature, e.event_key))

    positions: list[Position] = []
    orphans: list[Event] = []
    cur: Position | None = None
    cycle = 0

    def open_new(e: Event) -> Position:
        nonlocal cycle
        cycle += 1
        p = Position(wallet=wallet, mint=mint, cycle_no=cycle, opened_at=e.block_time,
                     last_activity_at=e.block_time)
        positions.append(p)
        return p

    for e in evs:
        t = e.tx_type
        if t in ("buy", "transfer_in"):
            if e.token_amount <= 0 and t == "transfer_in":
                continue
            if cur is None:
                cur = open_new(e)
                if t == "transfer_in":
                    _add_flag(cur, "opened_by_transfer")
            p = cur
            p.last_activity_at = e.block_time
            p.signatures.append(e.signature)
            if VERIF_RANK.get(e.verification_status, 2) > VERIF_RANK.get(p.verification, 0):
                p.verification = e.verification_status
            if t == "buy":
                p.n_buys += 1
                p.buy_sol += e.sol_amount
                p.tokens_bought += e.token_amount
                p.cost_basis_remaining += e.sol_amount
                p.fees_sol += e.fees_sol
                p.buy_tx_sizes.append(e.sol_amount)
                if e.market_cap_sol:
                    p.entry_mc_weighted += e.market_cap_sol * e.sol_amount
                if p.first_buy_at is None and e.liquidity_sol is not None:
                    # liquidity before the first buy = reserves after it minus what it added
                    p.entry_liquidity_sol = max(e.liquidity_sol - e.sol_amount, 0.0)
                p.first_buy_at = p.first_buy_at or e.block_time
                p.last_buy_at = e.block_time
            else:
                p.tokens_in += e.token_amount
                _add_flag(p, "transfer_in")
            p.remaining_tokens += e.token_amount
            p.peak_holdings = max(p.peak_holdings, p.remaining_tokens)
            continue

        # sell / transfer_out
        if cur is None:
            if t == "sell":
                orphans.append(e)
            continue
        p = cur
        p.last_activity_at = e.block_time
        p.signatures.append(e.signature)
        if VERIF_RANK.get(e.verification_status, 2) > VERIF_RANK.get(p.verification, 0):
            p.verification = e.verification_status
        qty = e.token_amount
        if qty > p.remaining_tokens * 1.000001 + 1e-9:
            _add_flag(p, "sold_more_than_known_holdings")
            qty_used = p.remaining_tokens
        else:
            qty_used = min(qty, p.remaining_tokens)
        frac = (qty_used / p.remaining_tokens) if p.remaining_tokens > 0 else 1.0
        cost_removed = p.cost_basis_remaining * frac
        if t == "sell":
            p.n_sells += 1
            p.sell_sol += e.sol_amount
            p.tokens_sold += qty
            p.fees_sol += e.fees_sol
            p.realized_gross += e.sol_amount - cost_removed
            if e.market_cap_sol:
                p.exit_mc_weighted += e.market_cap_sol * e.sol_amount
            p.first_sell_at = p.first_sell_at or e.block_time
        else:
            p.tokens_out += qty
            p.transferred_out_cost += cost_removed
            _add_flag(p, "transfer_out")
        p.cost_basis_remaining -= cost_removed
        p.remaining_tokens = max(0.0, p.remaining_tokens - qty_used)
        if p.remaining_tokens <= max(p.peak_holdings * dust_fraction, 1e-6):
            p.status = "closed"
            p.closed_at = e.block_time
            p.remaining_tokens = 0.0
            p.cost_basis_remaining = 0.0
            if p.tokens_out > 0 and p.tokens_out >= 0.5 * (p.tokens_bought + p.tokens_in):
                _add_flag(p, "exited_mostly_by_transfer")
            cur = None
    return positions, orphans


def mark_to_market(p: Position, price_sol: float | None, now: int, write_off_after_hours: float = 72,
                   write_off_value_fraction: float = 0.10) -> None:
    """Value an open position at `price_sol` and apply the write-off rule."""
    if p.is_closed:
        return
    p.current_price_sol = price_sol
    value = p.remaining_tokens * price_sol if price_sol is not None else None
    p.unrealized_pnl_sol = (value - p.cost_basis_remaining) if value is not None else None
    idle_h = (now - p.last_activity_at) / 3600
    if idle_h >= write_off_after_hours:
        worthless = value is None or (p.cost_basis_remaining > 0 and value < write_off_value_fraction * p.cost_basis_remaining)
        if worthless:
            p.status = "written_off"
            p.closed_at = p.last_activity_at
            _add_flag(p, "written_off_unpriceable" if value is None else "written_off_illiquid")


def group_events(rows: Iterable[dict[str, Any]]) -> dict[str, list[Event]]:
    by_mint: dict[str, list[Event]] = {}
    for r in rows:
        by_mint.setdefault(r["mint"], []).append(Event.from_row(r))
    return by_mint
