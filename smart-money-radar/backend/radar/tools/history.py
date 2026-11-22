"""Historical research over the last N days (default 60) from on-chain data only.

1. Graduated tokens: every pump.fun -> PumpSwap migration writes to the pump migration account
   `39azUYFW…` (system-owned, verified on mainnet 2026-11-22). Its signature history is paged back
   N days; for each day a sample of migration transactions is decoded (CompletePumpAmmMigrationEvent
   gives mint, bonding curve, pool, time). A graduated token rose ~13x from its launch market cap,
   so it is a confirmed "winner" — but nothing about a wallet is concluded from that alone.
2. Early buyers: the bonding curve's own signature history is paged back to the token's
   creation; its first `early_n` successful transactions are fetched and decoded. Signing wallets
   whose position reached >= min_buy_sol there are recorded.
3. Wallets that bought early in SEVERAL different winners are queued as candidates; their full
   N-day history (including every losing trade) is then backfilled and scored by the normal
   pipeline. Being early in winners only selects who is examined, never the score.

    python -m radar history --days 60 --per-day 2 --early 120 --promote 150
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import time
from collections import defaultdict

from ..context import create_ctx
from ..db import repo
from ..services.txproc import PoolCache, decode, write_decoded

log = logging.getLogger(__name__)
MIGRATION_ACCOUNT = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"
UTC = dt.timezone.utc


async def _tx(ctx, sig: str):
    """Stored raw transaction first (resumable runs), RPC otherwise."""
    raw = await ctx.pool.fetchval("SELECT tx FROM raw_transactions WHERE signature = $1", sig)
    if raw:
        return raw, True
    return await ctx.rpc.get_transaction(sig), False


async def list_migrations(ctx, days: int, per_day: int) -> list[dict]:
    cutoff = int(time.time()) - days * 86400
    by_day: dict[str, list[dict]] = defaultdict(list)
    before = None
    pages = 0
    while True:
        page = await ctx.rpc.get_signatures_for_address(MIGRATION_ACCOUNT, before=before, limit=1000)
        pages += 1
        if not page:
            break
        for s in page:
            bt = s.get("blockTime")
            if bt and bt >= cutoff and s.get("err") is None:
                by_day[dt.datetime.fromtimestamp(bt, UTC).strftime("%Y-%m-%d")].append(s)
        oldest = page[-1].get("blockTime") or 0
        if oldest < cutoff or len(page) < 1000:
            break
        before = page[-1]["signature"]
    log.info("migration account: %d pages, %d days", pages, len(by_day))
    pools = PoolCache()
    out: list[dict] = []
    for day in sorted(by_day):
        sigs = by_day[day]
        step = max(1, len(sigs) // (per_day * 4))
        found = 0
        for s in sigs[::step]:
            if found >= per_day:
                break
            tx, _cached = await _tx(ctx, s["signature"])
            if not tx:
                continue
            d = decode(tx, s["signature"], pools)
            if not d.migrations:
                async with ctx.pool.acquire() as conn:   # remember it was not a migration
                    await repo.save_raw_tx(conn, s["signature"], tx, None)
                continue
            async with ctx.pool.acquire() as conn:
                await repo.save_raw_tx(conn, s["signature"], tx, None)
                await write_decoded(conn, d, keep_wallets=set(), store_min_sol=1e9, sol_usd=None)
            for m in d.migrations:
                out.append({"day": day, "mint": m.mint, "curve": m.bonding_curve, "pool": m.pool,
                            "migrated_at": m.timestamp, "signature": s["signature"]})
                found += 1
    return out


async def early_buyers(ctx, mint: str, curve: str, early_n: int, min_buy: float, max_pages: int = 12) -> dict:
    sigs: list[dict] = []
    before = None
    complete = False
    for _ in range(max_pages):
        page = await ctx.rpc.get_signatures_for_address(curve, before=before, limit=1000)
        sigs.extend(page)
        if len(page) < 1000:
            complete = True
            break
        before = page[-1]["signature"]
    if not complete:
        return {"mint": mint, "history_complete": False, "buyers": {}}
    oldest = [x for x in reversed(sigs) if x.get("err") is None][:early_n]
    pools = PoolCache()
    decoded = []
    sem = asyncio.Semaphore(4)

    async def one(sig):
        async with sem:
            tx, _cached = await _tx(ctx, sig)
            if tx:
                decoded.append((sig, tx, decode(tx, sig, pools)))

    await asyncio.gather(*(one(x["signature"]) for x in oldest), return_exceptions=True)
    buyers: dict[str, dict] = {}
    async with ctx.pool.acquire() as conn:
        for sig, tx, d in decoded:
            await repo.save_raw_tx(conn, sig, tx, None)
            await write_decoded(conn, d, keep_wallets=set(), store_min_sol=1.0, sol_usd=None)
            for t in d.trades:
                if t.mint == mint and t.tx_type == "buy" and t.extra.get("user_is_signer", True):
                    b = buyers.setdefault(t.wallet, {"sol": 0.0, "first_mc_sol": t.market_cap_sol, "first_ts": t.block_time})
                    b["sol"] += t.sol_amount
                    if t.block_time < b["first_ts"]:
                        b["first_ts"], b["first_mc_sol"] = t.block_time, t.market_cap_sol
    big = {w: b for w, b in buyers.items() if b["sol"] >= min_buy}
    return {"mint": mint, "history_complete": True, "curve_signatures": len(sigs), "decoded": len(decoded),
            "buyers": big}


async def run(days: int = 60, per_day: int = 2, early_n: int = 120, promote: int = 150, out: str | None = None) -> dict:
    ctx = await create_ctx("history", run_migrations=True)
    try:
        s = await ctx.settings.get()
        if int(s["backfill_days"]) < days:
            await ctx.settings.update({"backfill_days": days})   # candidates get N days of history
        migs = await list_migrations(ctx, days, per_day)
        log.info("graduated tokens sampled: %d", len(migs))
        appearances: dict[str, list[dict]] = defaultdict(list)
        seeded = []
        done = {r["job"]: r["details"] for r in await ctx.pool.fetch(
            "SELECT job, details FROM api_sync_status WHERE source = 'history_seed'")}
        for i, m in enumerate(migs):
            if m["mint"] in done:
                r = done[m["mint"]]
            else:
                try:
                    r = await early_buyers(ctx, m["mint"], m["curve"], early_n, float(s["min_buy_sol"]))
                except Exception as e:
                    log.warning("early buyers %s failed: %s", m["mint"], e)
                    continue
                await ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, details, last_success_at)
                    VALUES ('history_seed', $1, 'done', $2, now()) ON CONFLICT (source, job) DO UPDATE
                    SET details = EXCLUDED.details, last_success_at = now()""", m["mint"], r)
            seeded.append({**m, "history_complete": r["history_complete"], "early_buyers_2sol": len(r["buyers"])})
            for w, b in r["buyers"].items():
                appearances[w].append({"mint": m["mint"], "day": m["day"], "buy_sol": round(b["sol"], 3),
                                       "entry_mc_sol": b["first_mc_sol"]})
            if (i + 1) % 10 == 0:
                log.info("history: %d/%d tokens seeded, %d distinct early buyers", i + 1, len(migs), len(appearances))
        ranked = sorted(appearances.items(), key=lambda kv: (-len(kv[1]), -sum(x["buy_sol"] for x in kv[1])))
        existing = {r["address"] for r in await ctx.pool.fetch(
            "SELECT address FROM wallets WHERE address = ANY($1::text[])", [w for w, _ in ranked])}
        chosen = [(w, a) for w, a in ranked if w not in existing][:promote]
        async with ctx.pool.acquire() as conn:
            await repo.ensure_wallets(conn, [(w, "history_seed", {"early_in_graduates": a[:20], "graduates": len(a)})
                                             for w, a in chosen])
            # wallets already known get their history re-read over the longer window
            await conn.execute("""UPDATE wallets SET backfill_status = 'queued', status = CASE WHEN status = 'observed'
                AND NOT (discovery_detail ? 'high_frequency') THEN 'candidate' ELSE status END
                WHERE address = ANY($1::text[]) AND backfill_status IN ('complete','partial')
                AND (backfill_from IS NULL OR backfill_from > now() - make_interval(days => $2))""",
                               [w for w, _ in ranked[:promote] if w in existing], days)
        report = {"generated_at": dt.datetime.now(UTC).isoformat(), "days": days,
                  "graduated_sampled": len(migs), "seeded": seeded,
                  "distinct_early_buyers_2sol": len(appearances),
                  "multi_graduate_buyers": sum(1 for _, a in ranked if len(a) >= 2),
                  "promoted": len(chosen),
                  "top_early_buyers": [{"wallet": w, "graduates": len(a), "tokens": a[:10]} for w, a in ranked[:50]]}
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(report, f, indent=1, default=str)
        print(json.dumps({k: v for k, v in report.items() if k not in ("seeded", "top_early_buyers")}, indent=1))
        return report
    finally:
        await ctx.close()
