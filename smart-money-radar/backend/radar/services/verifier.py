"""Verifier: confirms stream-decoded trades with the full transaction (getTransaction).

Consumes `q:verify`. A transaction that is not yet available on the RPC node is retried with
increasing delay (up to 6 attempts). After writing, the trade is re-published to the signal
engine so a WATCH that was waiting for verification can be upgraded.
A sweeper re-queues partially verified trades of tracked wallets every minute.
"""
from __future__ import annotations

import asyncio
import json
import logging

from ..context import K_DIRTY_WALLETS, Q_VERIFY, S_SMART_TRADES, Ctx
from ..db import repo
from .txproc import PoolCache, decode, write_decoded

log = logging.getLogger(__name__)
GROUP = "verifier"


async def verify_one(ctx: Ctx, sig: str, pools: PoolCache, tracked: set[str], store_min: float) -> str:
    tx = await ctx.rpc.get_transaction(sig)
    if tx is None:
        return "not_found"
    d = decode(tx, sig, pools)
    async with ctx.pool.acquire() as conn:
        await repo.save_raw_tx(conn, sig, tx, None)
        await write_decoded(conn, d, keep_wallets=tracked, store_min_sol=store_min, sol_usd=await ctx.sol_usd())
    for t in d.trades:
        if t.wallet in tracked:
            await ctx.redis.sadd(K_DIRTY_WALLETS, t.wallet)
            await ctx.redis.xadd(S_SMART_TRADES, {"p": json.dumps({
                "signature": t.signature, "event_key": t.event_key, "wallet": t.wallet, "mint": t.mint,
                "tx_type": t.tx_type, "sol": t.sol_amount, "ts": t.block_time,
                "verification": t.verification_status, "verified_event": True})}, maxlen=100_000, approximate=True)
    return "failed_tx" if d.failed else "ok"


async def _tracked(ctx: Ctx) -> set[str]:
    rows = await ctx.pool.fetch("SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")
    return {r["address"] for r in rows}


async def sweeper(ctx: Ctx) -> None:
    while True:
        try:
            rows = await ctx.pool.fetch("""SELECT DISTINCT t.signature FROM wallet_transactions t
                JOIN wallets w ON w.address = t.wallet AND w.status IN ('tracked','smart')
                WHERE t.source <> 'rpc_tx' AND t.ingested_at < now() - interval '20 seconds'
                  AND t.ingested_at > now() - interval '3 days' LIMIT 500""")
            for r in rows:
                await ctx.enqueue(Q_VERIFY, {"signature": r["signature"], "reason": "sweep"},
                                  dedupe_key=r["signature"], dedupe_ttl=900)
        except Exception:
            log.exception("sweeper failed")
        await asyncio.sleep(60)


async def worker(ctx: Ctx, concurrency: int = 4) -> None:
    pools = PoolCache()
    await pools.load(ctx.pool)
    tracked = await _tracked(ctx)
    s = await ctx.settings.get()
    sem = asyncio.Semaphore(concurrency)
    n = 0
    counts = {"ok": 0, "not_found": 0, "failed_tx": 0, "error": 0}

    async def handle(mid, p):
        async with sem:
            sig = p.get("signature")
            attempt = int(p.get("attempt", 0))
            try:
                res = await verify_one(ctx, sig, pools, tracked, float(s["stream_store_min_sol"]))
            except Exception as e:
                log.warning("verify %s failed: %s", sig, e)
                res = "error"
            counts[res] = counts.get(res, 0) + 1
            if res in ("not_found", "error") and attempt < 6:
                await asyncio.sleep(min(2 ** attempt, 30))
                await ctx.redis.xadd(Q_VERIFY, {"p": json.dumps({"signature": sig, "attempt": attempt + 1})})
            await ctx.ack(Q_VERIFY, GROUP, [mid])

    while True:
        msgs = await ctx.consume(Q_VERIFY, GROUP, count=concurrency * 4)
        if msgs:
            await asyncio.gather(*(handle(mid, p) for mid, p in msgs))
        n += 1
        if n % 20 == 0:
            tracked = await _tracked(ctx)
            s = await ctx.settings.get()
            await pools.load(ctx.pool)
            await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, counts)


async def main(ctx: Ctx) -> None:
    await asyncio.gather(worker(ctx), sweeper(ctx))
