"""Smart-money module — PHASE 2 (interface only, returns no data).

Planned design:
  * wallet table: address, total_trades, wins, win_rate, realized_pnl, avg_entry_mc, avg_exit_mc,
    n_5x, n_10x, avg_hold_minutes  (built from the snapshots table + Helius enhanced transactions)
  * on a new token: check early buyers (Helius getSignaturesForAddress on the bonding curve)
    against the table; 1 smart wallet = signal, 3-5 = strong signal. Never a price prediction.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class SmartMoneyActivity:
    wallets: list[str] = field(default_factory=list)
    available: bool = False
    note: str = "Phase 2 — not implemented"


class SmartMoneyTracker:
    async def activity(self, mint: str) -> SmartMoneyActivity:
        return SmartMoneyActivity()
