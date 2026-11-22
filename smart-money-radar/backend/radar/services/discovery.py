"""Candidate discovery. A wallet becomes a *candidate* (and gets its 30-day history backfilled)
when on-chain data shows one of:

  big_buyer     >= discovery_min_distinct_tokens different tokens bought with a position of
                >= discovery_min_buy_sol in the last 7 days (from the live stream)
  early_buyer   bought >= min_buy SOL of a token at a price <= 1/3 of that token's later
                liquidity-validated peak (bought before a >= 3x move)
  seed          early buyers (>= min_buy SOL) of tokens the operator seeds explicitly, or of
                tokens that migrated to PumpSwap; read from the token's own signature history

Nothing here makes a wallet "smart": only the backfilled history and the score do.
"""
from __future__ import annotations

import asyncio
import logging
import time

from ..context import Ctx
from ..db import repo
from .stream import PROTOCOL_TRADERS
from .txproc import PoolCache, decode, write_decoded

log = logging.getLogger(__name__)


async def _capacity(ctx: Ctx, s: dict) -> int:
    n = await ctx.pool.fetchval("SELECT count(*) FROM wallets WHERE status IN ('candidate','tracked','smart')")
    return max(0, int(s["tracked_wallet_limit"]) - int(n))


async def _filter_new(ctx: Ctx, wallets: list[str], s: dict) -> list[str]:
    if not wallets:
        return []
    excluded = set(s.get("excluded_wallets") or []) | PROTOCOL_TRADERS
    known = {r["address"]: r for r in await ctx.pool.fetch(
        "SELECT address, status, updated_at FROM wallets WHERE address = ANY($1::text[])", wallets)}
    out = []
    for w in wallets:
        if w in excluded:
            continue
        k = known.get(w)
        if k is None or (k["status"] == "observed" and k["updated_at"].timestamp() < time.time() - 7 * 86400):
            out.append(w)
    return out


async def big_buyers(ctx: Ctx, s: dict) -> int:
    rows = await ctx.pool.fetch("""SELECT wallet, count(*) AS tokens, sum(sol) AS total FROM (
            SELECT wallet, mint, sum(sol_amount) AS sol FROM wallet_transactions
            WHERE tx_type = 'buy' AND block_time > now() - interval '7 days'
              AND (user_is_signer IS DISTINCT FROM false)
            GROUP BY wallet, mint HAVING sum(sol_amount) >= $1) q
        GROUP BY wallet HAVING count(*) >= $2 ORDER BY count(*) DESC, sum(sol) DESC LIMIT 300""",
                                float(s["discovery_min_buy_sol"]), int(s["discovery_min_distinct_tokens"]))
    new = await _filter_new(ctx, [r["wallet"] for r in rows], s)
    cap = await _capacity(ctx, s)
    detail = {r["wallet"]: {"tokens_7d": r["tokens"], "buy_sol_7d": float(r["total"])} for r in rows}
    chosen = new[:cap]
    async with ctx.pool.acquire() as conn:
        await repo.ensure_wallets(conn, [(w, "big_buyer", detail[w]) for w in chosen])
    return len(chosen)


async def early_buyers(ctx: Ctx, s: dict) -> int:
    """Buyers before a validated >= 3x move (on-chain bars, last 3 days)."""
    movers = await ctx.pool.fetch("""SELECT mint, max(high_confirmed_sol) AS peak FROM token_prices
        WHERE source = 'onchain_trades' AND bucket_start > now() - interval '3 days'
          AND volume_sol >= $1 AND (liquidity_sol IS NULL OR liquidity_sol >= $2)
        GROUP BY mint HAVING max(high_confirmed_sol) IS NOT NULL LIMIT 2000""",
                                  float(s["peak_min_bar_volume_sol"]), float(s["peak_min_liquidity_sol"]))
    cands: dict[str, dict] = {}
    for m in movers:
        rows = await ctx.pool.fetch("""SELECT wallet, sum(sol_amount) AS sol, min(effective_price_sol) AS px
            FROM wallet_transactions WHERE mint = $1 AND tx_type = 'buy' AND effective_price_sol <= $2 / 3
              AND (user_is_signer IS DISTINCT FROM false)
            GROUP BY wallet HAVING sum(sol_amount) >= $3""", m["mint"], m["peak"], float(s["min_buy_sol"]))
        for r in rows:
            cands.setdefault(r["wallet"], {"tokens": []})["tokens"].append(
                {"mint": m["mint"], "buy_sol": float(r["sol"]), "multiple": float(m["peak"]) / float(r["px"])})
    new = await _filter_new(ctx, list(cands), s)
    cap = await _capacity(ctx, s)
    new.sort(key=lambda w: -len(cands[w]["tokens"]))
    chosen = new[:cap]
    async with ctx.pool.acquire() as conn:
        await repo.ensure_wallets(conn, [(w, "early_buyer", cands[w]) for w in chosen])
    return len(chosen)


async def seed_token(ctx: Ctx, mint: str, max_sigs: int = 6000, early_n: int = 400) -> dict:
    """Read a token's signature history back to its creation and decode its earliest trades."""
    s = await ctx.settings.get()
    before = None
    sigs: list[dict] = []
    while len(sigs) < max_sigs:
        page = await ctx.rpc.get_signatures_for_address(mint, before=before, limit=1000)
        sigs.extend(page)
        if len(page) < 1000:
            break
        before = page[-1]["signature"]
    complete = len(sigs) < max_sigs
    oldest = [x for x in reversed(sigs) if x.get("err") is None][:early_n]
    pools = PoolCache()
    await pools.load(ctx.pool)
    buyers: dict[str, float] = {}
    sem = asyncio.Semaphore(4)
    decoded = []

    async def one(sig):
        async with sem:
            tx = await ctx.rpc.get_transaction(sig)
            if tx:
                async with ctx.pool.acquire() as conn:
                    await repo.save_raw_tx(conn, sig, tx, None)   # audit trail for every decoded tx
                decoded.append(decode(tx, sig, pools))

    await asyncio.gather(*(one(x["signature"]) for x in oldest), return_exceptions=True)
    async with ctx.pool.acquire() as conn:
        for d in decoded:
            await write_decoded(conn, d, keep_wallets=set(), store_min_sol=0.0, sol_usd=await ctx.sol_usd())
            for t in d.trades:
                if t.mint == mint and t.tx_type == "buy" and t.extra.get("user_is_signer", True):
                    buyers[t.wallet] = buyers.get(t.wallet, 0) + t.sol_amount
    big = [w for w, v in buyers.items() if v >= float(s["min_buy_sol"])]
    new = await _filter_new(ctx, big, s)
    chosen = new[:await _capacity(ctx, s)]
    async with ctx.pool.acquire() as conn:
        await repo.ensure_wallets(conn, [(w, "seed", {"mint": mint, "early_buy_sol": buyers[w]}) for w in chosen])
    return {"mint": mint, "signatures": len(sigs), "history_complete": complete, "decoded": len(decoded),
            "early_buyers_2sol": len(big), "new_candidates": len(chosen)}


async def main(ctx: Ctx) -> None:
    n = 0
    while True:
        try:
            s = await ctx.settings.get()
            a = await big_buyers(ctx, s)
            b = await early_buyers(ctx, s) if n % 3 == 0 else 0
            # seed from tokens that migrated recently (on-chain migration events)
            c = 0
            if n % 6 == 0:
                mig = await ctx.pool.fetch("""SELECT mint FROM tokens WHERE migrated_at > now() - interval '2 days'
                    AND NOT EXISTS (SELECT 1 FROM api_sync_status WHERE source = 'seed' AND job = tokens.mint)
                    ORDER BY migrated_at DESC LIMIT 3""")
                for r in mig:
                    res = await seed_token(ctx, r["mint"])
                    c += res["new_candidates"]
                    await ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, details, last_success_at)
                        VALUES ('seed', $1, 'done', $2, now()) ON CONFLICT DO NOTHING""", r["mint"], res)
            await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at,
                                 {"big_buyers": a, "early_buyers": b, "seeded": c, "cycle": n})
            if a or b or c:
                log.info("discovery: %d big buyers, %d early buyers, %d seeded", a, b, c)
        except Exception:
            log.exception("discovery cycle failed")
        n += 1
        await asyncio.sleep(600)
