"""SQL writes shared by the workers. All writes are idempotent (keyed by signature)."""
from __future__ import annotations

import datetime as dt
from typing import Any, Iterable

import asyncpg

from ..decoder.models import TokenTransfer, TradeRecord

UTC = dt.timezone.utc


def ts(x: int | float | None) -> dt.datetime | None:
    return dt.datetime.fromtimestamp(int(x), UTC) if x else None


TRADE_COLS = ("signature", "event_key", "wallet", "mint", "slot", "block_time", "tx_type", "venue", "sol_amount",
              "token_amount", "token_amount_raw", "fee_trading_sol", "fee_network_sol", "tip_sol", "other_cost_sol",
              "rent_sol", "wallet_sol_delta", "reconciliation_residual_sol", "price_sol", "effective_price_sol",
              "market_cap_sol", "sol_usd", "liquidity_sol", "counterparty", "fee_payer", "source",
              "verification_status", "verification_note", "verified_at", "user_is_signer")


def _trade_tuple(r: TradeRecord, sol_usd: float | None, verified_at: dt.datetime | None) -> tuple:
    return (r.signature, r.event_key, r.wallet, r.mint, r.slot, ts(r.block_time), r.tx_type, r.venue, r.sol_amount,
            r.token_amount, r.token_amount_raw, r.fee_trading_sol, r.fee_network_sol, r.tip_sol, r.other_cost_sol,
            r.rent_sol, r.wallet_sol_delta, r.reconciliation_residual_sol, r.price_sol, r.effective_price_sol,
            r.market_cap_sol, sol_usd, r.liquidity_sol, None, r.fee_payer, r.source, r.verification_status,
            r.verification_note, verified_at, r.extra.get("user_is_signer"))


def _transfer_tuple(t: TokenTransfer, verified_at: dt.datetime) -> tuple:
    return (t.signature, t.event_key, t.wallet, t.mint, t.slot, ts(t.block_time), t.direction, "transfer", 0.0,
            t.token_amount, t.token_amount_raw, 0, 0, 0, 0, 0, None, None, None, None, None, None, None,
            t.counterparty, None, "rpc_tx", "verified", "token transfer decoded from transaction", verified_at, None)


_PLACE = ", ".join(f"${i + 1}" for i in range(len(TRADE_COLS)))
_INSERT = f"INSERT INTO wallet_transactions ({', '.join(TRADE_COLS)}) VALUES ({_PLACE})"
_UPDATE_SET = ", ".join(f"{c} = EXCLUDED.{c}" for c in TRADE_COLS if c not in ("signature", "event_key"))


async def insert_stream_trades(conn: asyncpg.Connection, recs: Iterable[TradeRecord], sol_usd: float | None) -> int:
    rows = [_trade_tuple(r, sol_usd, None) for r in recs]
    if not rows:
        return 0
    # never overwrite a row the verifier already wrote
    await conn.executemany(_INSERT + " ON CONFLICT (signature, event_key) DO NOTHING", rows)
    return len(rows)


async def upsert_verified(conn: asyncpg.Connection, signature: str, trades: list[TradeRecord],
                          transfers: list[TokenTransfer], sol_usd: float | None,
                          all_keys: list[str] | None = None) -> None:
    """Write the verifier's decode of one tx. Stream rows of the same tx whose key the full decode
    does not contain are removed (all_keys = every key the full decode produced)."""
    now = dt.datetime.now(UTC)
    rows = [_trade_tuple(r, sol_usd, now) for r in trades] + [_transfer_tuple(t, now) for t in transfers]
    async with conn.transaction():
        keep = list(all_keys) if all_keys is not None else [r[1] for r in rows]
        # remove stream rows of this tx that the full decode does not confirm
        # remove rows of this tx (stream rows or an older decoder version) that the current
        # full decode does not produce any more
        await conn.execute("DELETE FROM wallet_transactions WHERE signature = $1 "
                           "AND NOT (event_key = ANY($2::text[]))", signature, keep)
        if rows:
            await conn.executemany(_INSERT + f" ON CONFLICT (signature, event_key) DO UPDATE SET {_UPDATE_SET}", rows)


async def upsert_sol_transfers(conn: asyncpg.Connection, rows: list[tuple]) -> None:
    if rows:
        await conn.executemany("""INSERT INTO sol_transfers(signature, source, destination, lamports, block_time, slot)
            VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING""", rows)


async def record_failed(conn: asyncpg.Connection, signature: str, wallet: str | None, slot: int | None,
                        block_time: int | None, err: Any) -> None:
    await conn.execute("""INSERT INTO failed_transactions(signature, wallet, slot, block_time, error)
        VALUES ($1,$2,$3,$4,$5) ON CONFLICT (signature) DO NOTHING""", signature, wallet, slot, ts(block_time), err)


async def save_raw_tx(conn: asyncpg.Connection, signature: str, tx: dict, endpoint: str | None) -> None:
    await conn.execute("""INSERT INTO raw_transactions(signature, slot, block_time, tx, fetched_from)
        VALUES ($1,$2,$3,$4,$5) ON CONFLICT (signature) DO NOTHING""",
                       signature, tx.get("slot"), ts(tx.get("blockTime")), tx, endpoint)


async def upsert_created_tokens(conn: asyncpg.Connection, creates: list, signature: str, slot: int) -> None:
    for c in creates:
        await conn.execute("""INSERT INTO tokens(mint, name, symbol, uri, creator, created_at, created_signature,
                created_slot, bonding_curve, token_program, is_mayhem, metadata_source, decimals, total_supply_raw)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,'pump_create_event',6,1000000000000000)
            ON CONFLICT (mint) DO UPDATE SET name = COALESCE(tokens.name, EXCLUDED.name),
                symbol = COALESCE(tokens.symbol, EXCLUDED.symbol), uri = COALESCE(tokens.uri, EXCLUDED.uri),
                creator = COALESCE(tokens.creator, EXCLUDED.creator),
                created_at = COALESCE(tokens.created_at, EXCLUDED.created_at),
                created_signature = COALESCE(tokens.created_signature, EXCLUDED.created_signature),
                created_slot = COALESCE(tokens.created_slot, EXCLUDED.created_slot),
                bonding_curve = COALESCE(tokens.bonding_curve, EXCLUDED.bonding_curve),
                token_program = COALESCE(tokens.token_program, EXCLUDED.token_program),
                is_mayhem = COALESCE(tokens.is_mayhem, EXCLUDED.is_mayhem),
                metadata_source = 'pump_create_event', updated_at = now()""",
                           c.mint, c.name[:100], c.symbol[:40], c.uri[:300], c.creator or c.user, ts(c.timestamp),
                           signature, slot, c.bonding_curve, c.token_program, c.is_mayhem)


async def mark_completes(conn: asyncpg.Connection, completes: list) -> None:
    for c in completes:
        await conn.execute("""INSERT INTO tokens(mint, curve_complete, curve_completed_at, curve_progress, bonding_curve)
            VALUES ($1, true, $2, 1, $3) ON CONFLICT (mint) DO UPDATE SET curve_complete = true,
            curve_completed_at = COALESCE(tokens.curve_completed_at, EXCLUDED.curve_completed_at),
            curve_progress = 1, updated_at = now()""", c.mint, ts(c.timestamp), c.bonding_curve)


async def mark_migrations(conn: asyncpg.Connection, migrations: list, signature: str) -> None:
    for m in migrations:
        await conn.execute("""INSERT INTO tokens(mint, curve_complete, migrated_at, migration_signature, amm_pool)
            VALUES ($1, true, $2, $3, $4) ON CONFLICT (mint) DO UPDATE SET curve_complete = true,
            migrated_at = COALESCE(tokens.migrated_at, EXCLUDED.migrated_at),
            migration_signature = COALESCE(tokens.migration_signature, EXCLUDED.migration_signature),
            amm_pool = EXCLUDED.amm_pool, updated_at = now()""", m.mint, ts(m.timestamp), signature, m.pool)
        await upsert_pool(conn, m.pool, m.mint, "So11111111111111111111111111111111111111112", "migration_event")


async def upsert_pool(conn: asyncpg.Connection, pool: str, base: str, quote: str, source: str) -> None:
    await conn.execute("""INSERT INTO amm_pools(pool, base_mint, quote_mint, source) VALUES ($1,$2,$3,$4)
        ON CONFLICT (pool) DO NOTHING""", pool, base, quote, source)


async def upsert_price_bars(conn: asyncpg.Connection, bars: list[dict]) -> None:
    if not bars:
        return
    await conn.executemany("""INSERT INTO token_prices(mint, bucket_start, source, open_sol, high_sol, low_sol,
            close_sol, high_confirmed_sol, volume_sol, volume_usd, buy_sol, sell_sol, trades, liquidity_sol, market_cap_sol)
        VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15)
        ON CONFLICT (mint, bucket_start, source) DO UPDATE SET
            open_sol = COALESCE(token_prices.open_sol, EXCLUDED.open_sol),
            high_sol = GREATEST(token_prices.high_sol, EXCLUDED.high_sol),
            low_sol = LEAST(token_prices.low_sol, EXCLUDED.low_sol),
            close_sol = EXCLUDED.close_sol,
            high_confirmed_sol = CASE WHEN EXCLUDED.source = 'onchain_trades'
                THEN GREATEST(token_prices.high_confirmed_sol, EXCLUDED.high_confirmed_sol)
                ELSE EXCLUDED.high_confirmed_sol END,
            volume_sol = CASE WHEN EXCLUDED.source = 'onchain_trades'
                THEN token_prices.volume_sol + EXCLUDED.volume_sol ELSE EXCLUDED.volume_sol END,
            volume_usd = COALESCE(EXCLUDED.volume_usd, token_prices.volume_usd),
            buy_sol = token_prices.buy_sol + EXCLUDED.buy_sol,
            sell_sol = token_prices.sell_sol + EXCLUDED.sell_sol,
            trades = CASE WHEN EXCLUDED.source = 'onchain_trades'
                THEN token_prices.trades + EXCLUDED.trades ELSE EXCLUDED.trades END,
            liquidity_sol = COALESCE(EXCLUDED.liquidity_sol, token_prices.liquidity_sol),
            market_cap_sol = COALESCE(EXCLUDED.market_cap_sol, token_prices.market_cap_sol)""",
        [(b["mint"], b["bucket_start"], b["source"], b.get("open"), b.get("high"), b.get("low"), b.get("close"),
          b.get("high_confirmed"), b.get("volume_sol", 0), b.get("volume_usd"), b.get("buy_sol", 0),
          b.get("sell_sol", 0), b.get("trades", 0), b.get("liquidity_sol"), b.get("mc_sol")) for b in bars])


async def update_token_market(conn: asyncpg.Connection, rows: list[tuple]) -> None:
    """rows: (mint, last_trade_at, price_sol, mc_sol, liquidity_sol, progress)"""
    if rows:
        await conn.executemany("""INSERT INTO tokens(mint, last_trade_at, last_price_sol, last_mc_sol,
                last_liquidity_sol, curve_progress) VALUES ($1,$2,$3,$4,$5,$6)
            ON CONFLICT (mint) DO UPDATE SET last_trade_at = GREATEST(tokens.last_trade_at, EXCLUDED.last_trade_at),
                last_price_sol = EXCLUDED.last_price_sol, last_mc_sol = EXCLUDED.last_mc_sol,
                last_liquidity_sol = EXCLUDED.last_liquidity_sol,
                curve_progress = COALESCE(EXCLUDED.curve_progress, tokens.curve_progress), updated_at = now()""", rows)


async def ensure_wallets(conn: asyncpg.Connection, wallets: list[tuple[str, str, dict]]) -> None:
    """(address, discovery_source, detail) — creates rows, promotes 'observed' to 'candidate'."""
    if wallets:
        await conn.executemany("""INSERT INTO wallets(address, status, discovery_source, discovery_detail, backfill_status)
            VALUES ($1, 'candidate', $2, $3, 'queued')
            ON CONFLICT (address) DO UPDATE SET status = CASE WHEN wallets.status = 'observed'
                AND wallets.backfill_status = 'none' THEN 'candidate' ELSE wallets.status END,
                updated_at = now()""", wallets)


async def heartbeat(pool: asyncpg.Pool, worker: str, started_at: dt.datetime, info: dict) -> None:
    await pool.execute("""INSERT INTO worker_heartbeats(worker, last_beat, started_at, info) VALUES ($1, now(), $2, $3)
        ON CONFLICT (worker) DO UPDATE SET last_beat = now(), started_at = EXCLUDED.started_at, info = EXCLUDED.info""",
                       worker, started_at, info)
