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

ROLES = ("stream", "verifier", "sync", "discovery", "prices", "analytics", "signals", "winners")


async def _run_role(role: str) -> None:
    from .context import create_ctx
    from .services import (analytics_runner, discovery, health, prices, signal_engine, stream, verifier,
                           wallet_sync, winner_reverse)
    mains = {"stream": stream.main, "verifier": verifier.main, "sync": wallet_sync.main, "discovery": discovery.main,
             "prices": prices.main, "analytics": analytics_runner.main, "signals": signal_engine.main,
             "winners": winner_reverse.main}
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
    try:
        # first install: load the bundled list of public KOL wallets (only while the watchlist is empty)
        if not await ctx.pool.fetchval("SELECT 1 FROM watched_wallets LIMIT 1"):
            print("kol seed:", await _kol_import(ctx, str(KOL_SEED)))
    finally:
        await ctx.close()


KOL_SEED = __import__("pathlib").Path(__file__).parent / "data" / "kol_seed.txt"


async def _kol_import(ctx, path: str) -> dict:
    from .services.kol import add_watched, parse_lines
    with open(path, encoding="utf-8") as f:
        entries, errors = parse_lines(f.read())
    return {"added": await add_watched(ctx, entries), "errors": errors}


async def _kol_import_cmd(paths: list[str]) -> None:
    from .context import create_ctx
    ctx = await create_ctx("kol-import", run_migrations=True, pool_size=2)
    try:
        for p in paths or [str(KOL_SEED)]:
            print(p, await _kol_import(ctx, p))
    finally:
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


async def _healthcheck() -> int:
    """Container healthcheck for worker roles: healthy when this role wrote a heartbeat in the
    last 5 minutes. The role is read from the container's main process (PID 1) command line."""
    import asyncpg
    try:
        with open("/proc/1/cmdline", "rb") as f:
            role = f.read().split(b"\0")[-2].decode() if f else ""
    except OSError:
        role = ""
    conn = await asyncpg.connect(os.environ["DATABASE_URL"], timeout=5)
    try:
        ok = await conn.fetchval("SELECT count(*) FROM worker_heartbeats WHERE worker = $1 "
                                 "AND last_beat > now() - interval '5 minutes'", role)
    finally:
        await conn.close()
    return 0 if ok else 1


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
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--minutes", type=int, default=30)
    ap.add_argument("--max-credits", dest="max_credits", type=int, default=77760)
    ap.add_argument("--already-spent", dest="already_spent", type=int, default=0)
    ap.add_argument("--verify-reserve", dest="verify_reserve", type=int, default=15000)
    ap.add_argument("--since", default=None)   # verify-pnl: only wallets backfilled since this ISO time
    a = ap.parse_args(argv)
    if a.command == "api":
        import uvicorn
        uvicorn.run("radar.api.app:app", host=a.host, port=a.port, proxy_headers=True, forwarded_allow_ips="*",
                    log_level="info", access_log=False)
    elif a.command == "healthcheck":
        sys.exit(asyncio.run(_healthcheck()))
    elif a.command == "migrate":
        asyncio.run(_migrate())
    elif a.command in ROLES or a.command == "all":
        asyncio.run(_run_role(a.command))
    elif a.command == "seed":
        asyncio.run(_seed(a.args))
    elif a.command == "verify-pnl":
        from .tools.verify_pnl import run as vp
        import json as _json
        print(_json.dumps(asyncio.run(vp(a.out, a.since)), indent=1, default=str))
    elif a.command == "winner-history":
        from .tools.winner_history import run as wh
        import json as _json
        print(_json.dumps(asyncio.run(wh(a.days, a.max_credits, a.out, already_spent=a.already_spent,
                                         verify_reserve=a.verify_reserve)), indent=1, default=str))
    elif a.command == "latency":
        from .tools.latency import run as lat
        import json as _json
        print(_json.dumps(asyncio.run(lat(a.minutes, a.out)), indent=1, default=str))
    elif a.command == "backfill-audit":
        from .tools.backfill_audit import run as bfa
        r = asyncio.run(bfa("--apply" in (a.args or []) or a.apply, a.out))
        print({k: (v if not isinstance(v, list) else len(v)) for k, v in r.items()})
    elif a.command == "kol-import":
        asyncio.run(_kol_import_cmd(a.args))
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
