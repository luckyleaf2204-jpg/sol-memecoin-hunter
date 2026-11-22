"""Command line entry point.

    python -m radar migrate                 apply database migrations
    python -m radar api                     REST + WebSocket API (uvicorn)
    python -m radar stream|verifier|sync|discovery|prices|analytics|signals   one worker role
    python -m radar all                     every worker role in one process (small servers / dev)
    python -m radar seed <MINT> [...]       backfill early buyers of tokens as candidates
    python -m radar add-wallet <ADDR> [...] track wallets manually (still scored from on-chain data)
    python -m radar recompute               full analytics recompute now
    python -m radar redecode                re-decode stored raw transactions (after a decoder upgrade)
    python -m radar backtest [--out FILE]   walk-forward backtest on stored data
    python -m radar crosscheck [--n 25]     re-fetch stored trades from a second RPC and compare
    python -m radar history --days 60       sample graduated tokens of the last N days, seed their
                                            early buyers and backfill N days of their history
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys

log = logging.getLogger("radar")

ROLES = ("stream", "verifier", "sync", "discovery", "prices", "analytics", "signals")


async def _run_role(role: str) -> None:
    from .context import create_ctx
    from .services import (analytics_runner, discovery, health, prices, signal_engine, stream, verifier,
                           wallet_sync)
    mains = {"stream": stream.main, "verifier": verifier.main, "sync": wallet_sync.main, "discovery": discovery.main,
             "prices": prices.main, "analytics": analytics_runner.main, "signals": signal_engine.main}
    roles = list(ROLES) if role == "all" else [role]
    ctx = await create_ctx(role, run_migrations=True, pool_size=4 + 3 * len(roles))
    log.info("starting roles %s (trading disabled: read-only analytics)", roles)
    try:
        await asyncio.gather(health.loop(ctx), *(mains[r](ctx) for r in roles))
    finally:
        await ctx.close()


async def _migrate() -> None:
    from .context import create_ctx
    ctx = await create_ctx("migrate", run_migrations=True, pool_size=2)
    await ctx.close()


async def _seed(mints: list[str]) -> None:
    from .context import create_ctx
    from .services.discovery import seed_token
    from .solana.base58 import is_valid_address
    ctx = await create_ctx("seed", run_migrations=True)
    try:
        for m in mints:
            if not is_valid_address(m):
                print("invalid mint", m)
                continue
            print(json.dumps(await seed_token(ctx, m), default=str))
    finally:
        await ctx.close()


async def _add_wallets(addrs: list[str]) -> None:
    from .context import create_ctx
    from .db import repo
    from .solana.base58 import is_valid_address
    ctx = await create_ctx("cli", run_migrations=True)
    try:
        ok = [a for a in addrs if is_valid_address(a)]
        async with ctx.pool.acquire() as conn:
            await repo.ensure_wallets(conn, [(a, "manual", {}) for a in ok])
            await conn.execute("UPDATE wallets SET status = 'candidate', backfill_status = CASE WHEN backfill_status "
                               "IN ('none','failed') THEN 'queued' ELSE backfill_status END WHERE address = ANY($1::text[])",
                               ok)
        print(f"queued {len(ok)} wallet(s)")
    finally:
        await ctx.close()


async def _redecode() -> None:
    """Re-decode every stored raw transaction with the current decoder (no RPC calls)."""
    from .context import create_ctx
    from .services.txproc import PoolCache, decode, write_decoded
    ctx = await create_ctx("redecode", run_migrations=True)
    try:
        pools = PoolCache()
        await pools.load(ctx.pool)
        s = await ctx.settings.get()
        tracked = {r["address"] for r in await ctx.pool.fetch(
            "SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")}
        n = 0
        rows = await ctx.pool.fetch("SELECT signature, tx FROM raw_transactions")
        async with ctx.pool.acquire() as conn:
            for r in rows:
                d = decode(r["tx"], r["signature"], pools)
                keep = tracked | {t.wallet for t in d.trades if await conn.fetchval(
                    "SELECT 1 FROM wallet_transactions WHERE signature = $1 AND wallet = $2 LIMIT 1", r["signature"], t.wallet)}
                await write_decoded(conn, d, keep_wallets=keep, store_min_sol=float(s["stream_store_min_sol"]),
                                    sol_usd=await ctx.sol_usd())
                n += 1
        await ctx.redis.sadd("set:dirty_wallets", *tracked) if tracked else None
        print(f"re-decoded {n} transactions")
    finally:
        await ctx.close()


async def _recompute() -> None:
    from .context import create_ctx
    from .services.analytics_runner import relationships_pass, run_cycle
    ctx = await create_ctx("recompute", run_migrations=True)
    try:
        await relationships_pass(ctx, await ctx.settings.get())
        print(await run_cycle(ctx, full=True))
    finally:
        await ctx.close()


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(prog="radar")
    ap.add_argument("command")
    ap.add_argument("args", nargs="*")
    ap.add_argument("--host", default=os.environ.get("API_HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("API_PORT", "8000")))
    ap.add_argument("--out", default=None)
    ap.add_argument("--n", type=int, default=25)
    ap.add_argument("--days", type=int, default=60)
    ap.add_argument("--per-day", dest="per_day", type=int, default=2)
    ap.add_argument("--early", type=int, default=120)
    ap.add_argument("--promote", type=int, default=150)
    a = ap.parse_args(argv)
    if a.command == "api":
        import uvicorn
        uvicorn.run("radar.api.app:app", host=a.host, port=a.port, proxy_headers=True, forwarded_allow_ips="*",
                    log_level="info", access_log=False)
    elif a.command == "migrate":
        asyncio.run(_migrate())
    elif a.command in ROLES or a.command == "all":
        asyncio.run(_run_role(a.command))
    elif a.command == "seed":
        asyncio.run(_seed(a.args))
    elif a.command == "add-wallet":
        asyncio.run(_add_wallets(a.args))
    elif a.command == "redecode":
        asyncio.run(_redecode())
    elif a.command == "recompute":
        asyncio.run(_recompute())
    elif a.command == "backtest":
        from .tools.backtest_cli import run
        asyncio.run(run(a.out))
    elif a.command == "history":
        from .tools.history import run as hist
        asyncio.run(hist(a.days, a.per_day, a.early, a.promote, a.out))
    elif a.command == "crosscheck":
        from .tools.crosscheck import run as cc
        asyncio.run(cc(a.n, a.out))
    else:
        ap.print_help()
        sys.exit(2)


if __name__ == "__main__":
    main()
