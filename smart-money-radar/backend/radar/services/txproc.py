"""Write a fully decoded transaction to the database (shared by the verifier and wallet sync)."""
from __future__ import annotations

from typing import Iterable

import asyncpg

from ..db import repo
from ..decoder.models import DecodedTx
from ..decoder.tx import decode_transaction

MIN_SOL_TRANSFER_LAMPORTS = 10_000_000  # 0.01 SOL


class PoolCache:
    def __init__(self):
        self.pools: dict[str, tuple[str, str]] = {}
        self.loaded = False

    async def load(self, pool: asyncpg.Pool) -> None:
        for r in await pool.fetch("SELECT pool, base_mint, quote_mint FROM amm_pools"):
            self.pools[r["pool"]] = (r["base_mint"], r["quote_mint"])
        self.loaded = True

    def get(self, pool: str):
        return self.pools.get(pool)


def decode(tx: dict, signature: str, pools: PoolCache) -> DecodedTx:
    return decode_transaction(tx, signature, pool_resolver=pools.get)


async def write_decoded(conn: asyncpg.Connection, d: DecodedTx, *, keep_wallets: set[str] | Iterable[str],
                        store_min_sol: float, sol_usd: float | None, transfer_mints: set[str] | None = None,
                        sol_transfer_wallets: set[str] | None = None) -> dict:
    keep_wallets = set(keep_wallets)
    if d.failed:
        for w in keep_wallets:
            await repo.record_failed(conn, d.signature, w, d.slot, d.block_time, d.error)
        return {"failed": True, "trades": 0}
    trades = [t for t in d.trades if t.wallet in keep_wallets or t.sol_amount >= store_min_sol]
    transfers = [t for t in d.token_transfers if t.wallet in keep_wallets
                 and (transfer_mints is None or t.mint in transfer_mints)]
    all_keys = [t.event_key for t in d.trades] + [t.event_key for t in d.token_transfers]
    await repo.upsert_verified(conn, d.signature, trades, transfers, sol_usd, all_keys=all_keys)
    if d.creates:
        await repo.upsert_created_tokens(conn, d.creates, d.signature, d.slot)
    if d.completes:
        await repo.mark_completes(conn, d.completes)
    if d.migrations:
        await repo.mark_migrations(conn, d.migrations, d.signature)
    for pool, (b, q) in d.amm_pools.items():
        await repo.upsert_pool(conn, pool, b, q, "transaction")
    sw = sol_transfer_wallets if sol_transfer_wallets is not None else keep_wallets
    rows = [(d.signature, s.source, s.destination, s.lamports, repo.ts(d.block_time), d.slot)
            for s in d.sol_transfers
            if not s.under_swap and s.lamports >= MIN_SOL_TRANSFER_LAMPORTS and (s.source in sw or s.destination in sw)]
    await repo.upsert_sol_transfers(conn, rows)
    return {"failed": False, "trades": len(trades), "transfers": len(transfers)}
