"""Price data: SOL/USD (Jupiter), market snapshot of radar tokens (DexScreener), and historical
candles (GeckoTerminal) for positions that our own on-chain bars do not cover.

Historical candles: minute candles for the first ~16 h after entry and hourly candles for
7 days. Volumes are USD in GeckoTerminal and are converted to SOL with the SOL/USD price
recorded at fetch time (approximation, labelled). The bar high is only trusted as
`high_confirmed` when the candle traded >= 3 SOL; otherwise the close is used for peaks.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from ..context import K_SOL_USD, Q_PRICE_BACKFILL, Ctx
from ..db import repo
from ..sources import dexscreener, geckoterminal, jupiter

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
GROUP = "prices"


async def sol_price_loop(ctx: Ctx) -> None:
    while True:
        try:
            p = await jupiter.sol_usd(ctx.http)
            if p:
                await ctx.redis.set(K_SOL_USD, p, ex=600)
                minute = dt.datetime.fromtimestamp((int(time.time()) // 60) * 60, UTC)
                await ctx.pool.execute("INSERT INTO sol_usd_prices(ts, source, price) VALUES ($1,'jupiter',$2) "
                                       "ON CONFLICT DO NOTHING", minute, p)
        except Exception as e:
            log.warning("SOL price failed: %s", e)
        await asyncio.sleep(60)


async def dex_loop(ctx: Ctx) -> None:
    while True:
        try:
            mints = [r["mint"] for r in await ctx.pool.fetch("""SELECT t.mint FROM wallet_transactions t
                JOIN wallets w ON w.address = t.wallet AND w.status IN ('smart','tracked')
                WHERE t.block_time > now() - interval '24 hours' GROUP BY t.mint
                ORDER BY max(t.block_time) DESC LIMIT 90""")]
            if mints:
                pairs = await dexscreener.token_pairs(ctx.http, mints)
                rows = []
                for m, p in pairs.items():
                    rows.append((m, _f(p.get("marketCap") or p.get("fdv")), _f((p.get("liquidity") or {}).get("usd")),
                                 _f((p.get("volume") or {}).get("h24")), p.get("url"),
                                 (p.get("baseToken") or {}).get("name"), (p.get("baseToken") or {}).get("symbol")))
                await ctx.pool.executemany("""UPDATE tokens SET market_cap_usd = $2, liquidity_usd = $3,
                        volume_24h_usd = $4, dex_url = $5, dex_updated_at = now(),
                        name = COALESCE(name, $6), symbol = COALESCE(symbol, $7),
                        metadata_source = COALESCE(metadata_source, 'dexscreener') WHERE mint = $1""", rows)
        except Exception as e:
            log.warning("dexscreener loop failed: %s", e)
        await asyncio.sleep(30)


def _f(x):
    try:
        return float(x) if x is not None else None
    except (TypeError, ValueError):
        return None


async def backfill_token(ctx: Ctx, mint: str, start: int) -> int:
    sol_usd = await ctx.sol_usd() or 0
    pools = await geckoterminal.token_pools(ctx.http, mint)
    n = 0
    bars = []
    for pool in pools:
        # minute candles from entry - 1 h to entry + ~16 h
        mins = await geckoterminal.minute_candles(ctx.http, pool["pool"], before_ts=start + 1000 * 60 - 3600,
                                                  limit=1000)
        for c in mins:
            bars.append(_gt_bar(mint, c, "geckoterminal", sol_usd))
        # hourly candles for 7 days (GeckoTerminal /ohlcv/hour)
        data = await ctx.http.request_json(
            "geckoterminal", "GET", f"{geckoterminal.BASE}/pools/{pool['pool']}/ohlcv/hour", headers=geckoterminal.HEADERS,
            params={"aggregate": "1", "limit": "200", "currency": "token", "before_timestamp": str(start + 7 * 86400 + 3600)})
        for c in (((data or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []:
            bars.append(_gt_bar(mint, c, "geckoterminal_1h", sol_usd))
    bars = [b for b in bars if b]
    async with ctx.pool.acquire() as conn:
        await repo.upsert_price_bars(conn, bars)
        await conn.execute("UPDATE tokens SET price_backfill_until = $2, price_backfill_status = $3 WHERE mint = $1",
                           mint, repo.ts(start + 7 * 86400), "ok" if bars else "no_data")
    n += len(bars)
    return n


def _gt_bar(mint: str, c: list, source: str, sol_usd: float) -> dict | None:
    try:
        t, o, h, lo, cl, vol_usd = c[:6]
    except ValueError:
        return None
    vol_sol = (float(vol_usd) / sol_usd) if sol_usd else 0.0
    return {"mint": mint, "bucket_start": dt.datetime.fromtimestamp(int(t), UTC), "source": source,
            "open": float(o), "high": float(h), "low": float(lo), "close": float(cl),
            "high_confirmed": float(h) if vol_sol >= 3.0 else None, "volume_sol": vol_sol,
            "volume_usd": float(vol_usd), "trades": 0}


async def backfill_worker(ctx: Ctx) -> None:
    while True:
        msgs = await ctx.consume(Q_PRICE_BACKFILL, GROUP, count=2)
        for mid, p in msgs:
            try:
                n = await backfill_token(ctx, p["mint"], int(p["start"]))
                log.info("price backfill %s: %d bars", p["mint"], n)
                await ctx.redis.sadd("set:dirty_wallets", *[r["wallet"] for r in await ctx.pool.fetch(
                    "SELECT DISTINCT wallet FROM wallet_positions WHERE mint = $1", p["mint"])] or ["_"])
            except Exception as e:
                log.warning("price backfill %s failed: %s", p.get("mint"), e)
            await ctx.ack(Q_PRICE_BACKFILL, GROUP, [mid])
        await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, {"queue": "price_backfill"})


async def main(ctx: Ctx) -> None:
    await asyncio.gather(sol_price_loop(ctx), dex_loop(ctx), backfill_worker(ctx))
