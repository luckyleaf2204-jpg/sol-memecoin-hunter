"""Wallet history: 30-day backfill and incremental sync from the wallet's own signatures.

backfill: getSignaturesForAddress backwards until the period start (backfill_days) or
          `backfill_max_signatures` (then coverage = partial: high-frequency wallet).
sync:     signatures newer than `last_synced_signature`, every wallet_sync_interval_minutes.

Every successful signature not already decoded is fetched with getTransaction and decoded;
failed signatures are stored in failed_transactions (never trades). Raw transactions already
stored are re-decoded locally instead of fetched again.
Funding source = earliest inbound SOL transfer >= 0.05 SOL; it is the wallet's true first
funding only when the signature history was exhausted (wallet younger than the window).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time

from ..context import K_DIRTY_WALLETS, Q_WALLET_SYNC, Ctx
from ..db import repo
from .txproc import PoolCache, decode, write_decoded

log = logging.getLogger(__name__)
GROUP = "wallet_sync"
UTC = dt.timezone.utc


async def collect_signatures(ctx: Ctx, wallet: str, cutoff: int, until: str | None, max_sigs: int):
    sigs: list[dict] = []
    before = None
    exhausted = reached_cutoff = truncated = False
    while True:
        page = await ctx.rpc.get_signatures_for_address(wallet, before=before, until=until, limit=1000)
        for s in page:
            bt = s.get("blockTime")
            if bt is not None and bt < cutoff:
                reached_cutoff = True
                break
            sigs.append(s)
        if reached_cutoff:
            break
        if len(page) < 1000:
            exhausted = True
            break
        if len(sigs) >= max_sigs:
            truncated = True
            break
        before = page[-1]["signature"]
    return sigs, exhausted, reached_cutoff, truncated


class CreditCapReached(Exception):
    pass


async def collect_via_gtfa(ctx: Ctx, wallet: str, cutoff: int, max_sigs: int, cap: int):
    """Backfill listing + full transactions in one pass with Helius getTransactionsForAddress
    (100 full transactions per call, newest first). Returns the same signature entries as
    getSignaturesForAddress (signature, slot, blockTime, err) plus {signature: tx}.
    Checked 23/11/2026: same 1000 signatures incl. failed ones, and field-identical transactions."""
    sigs: list[dict] = []
    txs: dict[str, dict] = {}
    token = None
    pages = 0
    exhausted = reached_cutoff = truncated = False
    while True:
        if await ctx.rpc.helius_credits_today() >= cap:
            raise CreditCapReached()
        page, token = await ctx.rpc.get_transactions_for_address(wallet, pagination_token=token, limit=100)
        pages += 1
        for tx in page:
            sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
            if not sig:
                continue
            bt = tx.get("blockTime")
            if bt is not None and bt < cutoff:
                reached_cutoff = True
                break
            sigs.append({"signature": sig, "slot": tx.get("slot"), "blockTime": bt,
                         "err": (tx.get("meta") or {}).get("err")})
            txs[sig] = tx
        if reached_cutoff:
            break
        if not token or len(page) < 100:
            exhausted = True
            break
        if len(sigs) >= max_sigs:
            truncated = True
            break
    return sigs, txs, exhausted, reached_cutoff, truncated, pages


def _today() -> str:
    return time.strftime("%Y%m%d", time.gmtime())


async def backfill_gate(ctx: Ctx, s: dict, wallet: str) -> str | None:
    """None when this wallet may start a backfill now, else the reason it waits (status stays queued).
    Only wallets the scheduler admitted today (highest priority first, `backfill_wallets_per_day`)
    are backfilled, and only while today's Helius credits are under `helius_daily_credit_cap`.
    Incremental syncs of wallets already backfilled are never gated."""
    if ctx.rpc.has_helius() and await ctx.rpc.helius_credits_today() >= int(s["helius_daily_credit_cap"]):
        return "daily_credit_cap"
    if not await ctx.redis.sismember(f"bf:allowed:{_today()}", wallet):
        return "not_in_todays_batch"
    return None


async def sync_wallet(ctx: Ctx, wallet: str, mode: str, pools: PoolCache, tracked: set[str]) -> dict:
    s = await ctx.settings.get()
    w = await ctx.pool.fetchrow("SELECT * FROM wallets WHERE address = $1", wallet)
    if w is None or w["is_excluded"]:
        return {"skipped": True}
    if mode == "sync" and w["backfill_status"] not in ("complete", "partial"):
        mode = "backfill"
    elif mode == "backfill" and w["backfill_status"] in ("complete", "partial"):
        # a queued message can outlive the backfill it asked for (duplicate messages in the queue);
        # the history is already read, so only fetch what is new (seen 23/11/2026: 6 backfills per wallet)
        mode = "sync"
    now = int(time.time())
    cutoff = now - int(s["backfill_days"]) * 86400
    probe_calls = 0
    if mode == "backfill":
        reason = await backfill_gate(ctx, s, wallet)
        if reason:
            return {"deferred": reason}
    watched = await ctx.pool.fetchval("SELECT 1 FROM watched_wallets WHERE address = $1 AND confirmed", wallet)
    if mode == "backfill" and s.get("skip_high_frequency_wallets", True) and not watched \
            and w["discovery_source"] not in ("manual", "watchlist"):
        # one cheap call: 1000 newest signatures spanning < 1 day means >= 1000 tx/day (bot-like)
        probe = await ctx.rpc.get_signatures_for_address(wallet, limit=1000)
        probe_calls = 1
        own = await ctx.pool.fetchrow("""SELECT count(*) AS n, extract(epoch FROM max(block_time) - min(block_time)) AS span
            FROM wallet_transactions WHERE wallet = $1 AND tx_type IN ('buy','sell')""", wallet)
        own_per_day = (own["n"] / max(float(own["span"] or 0) / 86400, 1.0)) if own and own["n"] else None
        flooded = own_per_day is not None and own_per_day < 50   # many signatures, few own trades: spam airdrops
        if flooded:
            await ctx.pool.execute("UPDATE wallets SET discovery_detail = discovery_detail || $2 WHERE address = $1",
                                   wallet, {"signature_flood_suspected": True, "own_pump_trades_per_day": round(own_per_day, 1)})
        if not flooded and len(probe) == 1000 and probe[-1].get("blockTime") and \
                (probe[0].get("blockTime") or now) - probe[-1]["blockTime"] < 86400 * 1000 / int(s["hf_signatures_per_day"]):
            await ctx.pool.execute("""UPDATE wallets SET status = 'observed', backfill_status = 'none',
                discovery_detail = discovery_detail || $2, updated_at = now() WHERE address = $1""",
                                   wallet, {"high_frequency": True, "probe_span_s": probe[0]["blockTime"] - probe[-1]["blockTime"]})
            return {"skipped": "high_frequency"}
    until = w["last_synced_signature"] if mode == "sync" else None
    await ctx.pool.execute("UPDATE wallets SET backfill_status = CASE WHEN $2 = 'backfill' THEN 'running' "
                           "ELSE backfill_status END, backfill_sigs_fetched = CASE WHEN $2 = 'backfill' THEN 0 "
                           "ELSE backfill_sigs_fetched END, updated_at = now() WHERE address = $1", wallet, mode)
    prefetched: dict[str, dict] = {}
    method, pages = "rpc", 0
    credits0 = await ctx.rpc.helius_credits_today(ctx.rpc.tag) if ctx.rpc.has_helius() else 0
    if mode == "backfill" and s.get("helius_tx_history") and ctx.rpc.has_helius():
        try:
            sigs, prefetched, exhausted, reached_cutoff, truncated, pages = await collect_via_gtfa(
                ctx, wallet, cutoff, int(s["backfill_max_signatures"]), int(s["helius_daily_credit_cap"]))
            method = "gtfa"
        except CreditCapReached:
            await ctx.pool.execute("UPDATE wallets SET backfill_status = 'queued', backfill_error = $2, updated_at = now() "
                                   "WHERE address = $1", wallet, "daily Helius credit cap reached; resumes next UTC day")
            return {"deferred": "daily_credit_cap"}
        except Exception as e:   # any gTFA problem: fall back to the classic path, nothing written yet
            log.warning("getTransactionsForAddress failed for %s (%s); classic backfill", wallet, type(e).__name__)
            prefetched = {}
    if method == "rpc":
        sigs, exhausted, reached_cutoff, truncated = await collect_signatures(
            ctx, wallet, cutoff, until, int(s["backfill_max_signatures"]))
    failed = [x for x in sigs if x.get("err") is not None]
    ok = [x for x in sigs if x.get("err") is None]
    irr_key = f"bf:irrelevant:{wallet}"
    irrelevant = {x.decode() if isinstance(x, bytes) else x for x in (await ctx.redis.smembers(irr_key) or set())}
    names = [x["signature"] for x in ok if x["signature"] not in irrelevant]
    have_raw = {r["signature"] for r in await ctx.pool.fetch(
        "SELECT signature FROM raw_transactions WHERE signature = ANY($1::text[])", names)}
    async with ctx.pool.acquire() as conn:
        for x in failed:
            await repo.record_failed(conn, x["signature"], wallet, x.get("slot"), x.get("blockTime"), x.get("err"))

    keep = tracked | {wallet}
    sem = asyncio.Semaphore(4)
    fetched = decoded_trades = 0
    earliest_funding: tuple[int, str, int, str] | None = None   # (block_time, source, lamports, signature)
    sol_usd = await ctx.sol_usd()
    store_min = float(s["stream_store_min_sol"])
    traded_mints = {r["mint"] for r in await ctx.pool.fetch(
        "SELECT DISTINCT mint FROM wallet_transactions WHERE wallet = $1 AND tx_type IN ('buy','sell')", wallet)}
    decoded_list = []
    new_irrelevant: list[str] = []
    got_tx: list[str] = []
    missing_tx: list[str] = []

    async def one(sig: str) -> None:
        nonlocal fetched
        async with sem:
            if sig in prefetched:
                tx = prefetched.pop(sig)
                if sig not in have_raw:
                    if _relevant(tx):
                        async with ctx.pool.acquire() as conn:
                            await repo.save_raw_tx(conn, sig, tx, None)
                    else:
                        new_irrelevant.append(sig)
            elif sig in have_raw:
                row = await ctx.pool.fetchrow("SELECT tx FROM raw_transactions WHERE signature = $1", sig)
                tx = row["tx"] if row else None
            else:
                tx = await ctx.rpc.get_transaction(sig)
                fetched += 1
                if tx is not None and _relevant(tx):
                    async with ctx.pool.acquire() as conn:
                        await repo.save_raw_tx(conn, sig, tx, None)
                elif tx is not None:
                    new_irrelevant.append(sig)
            if tx is None:
                missing_tx.append(sig)
                return
            got_tx.append(sig)
            decoded_list.append(decode(tx, sig, pools))

    # Checkpointed in chunks of 200: each chunk is fetched, decoded and WRITTEN before the next one,
    # so an interrupted backfill keeps its work. Writes are upserts on (signature, event_key): a
    # resumed run never duplicates rows, and transactions already stored (raw_transactions) or known
    # to be unrelated to pump.fun (Redis set) are not fetched again.
    for i in range(0, len(names), 200):
        chunk = names[i:i + 200]
        decoded_list.clear()
        new_irrelevant.clear()
        await asyncio.gather(*(one(x) for x in chunk), return_exceptions=True)
        for d in decoded_list:
            for t in d.trades:
                if t.wallet == wallet:
                    traded_mints.add(t.mint)
        async with ctx.pool.acquire() as conn:
            for d in decoded_list:
                res = await write_decoded(conn, d, keep_wallets=keep, store_min_sol=store_min, sol_usd=sol_usd,
                                          transfer_mints=traded_mints, sol_transfer_wallets={wallet})
                decoded_trades += res.get("trades", 0)
                for st in d.sol_transfers:
                    if st.destination == wallet and not st.under_swap and st.lamports >= 50_000_000 and d.block_time:
                        cand = (int(d.block_time), st.source, st.lamports, d.signature)
                        if earliest_funding is None or cand[0] < earliest_funding[0]:
                            earliest_funding = cand
        if new_irrelevant:
            await ctx.redis.sadd(irr_key, *new_irrelevant)
            await ctx.redis.expire(irr_key, 14 * 86400)
        await ctx.pool.execute("UPDATE wallets SET backfill_sigs_fetched = backfill_sigs_fetched + $2, "
                               "backfill_sigs_seen = $3 WHERE address = $1", wallet, len(chunk), len(sigs))

    newest = sigs[0]["signature"] if sigs else w["last_synced_signature"]
    if mode == "backfill":
        status = "partial" if truncated else "complete"
        await ctx.pool.execute("""UPDATE wallets SET backfill_status = $2, backfill_from = $3, backfill_to = now(),
                backfill_sigs_seen = $4, backfill_truncated = $5, last_synced_signature = $6, last_synced_at = now(),
                backfill_error = NULL, updated_at = now() WHERE address = $1""",
                               wallet, status, dt.datetime.fromtimestamp(cutoff, UTC), len(sigs), truncated, newest)
    else:
        await ctx.pool.execute("""UPDATE wallets SET last_synced_signature = $2, last_synced_at = now(),
                backfill_sigs_seen = backfill_sigs_seen + $3, updated_at = now() WHERE address = $1""",
                               wallet, newest, len(sigs))
    if earliest_funding and (w["funding_source"] is None or mode == "backfill"):
        await ctx.pool.execute("""UPDATE wallets SET funding_source = $2, funding_amount_sol = $3, funding_signature = $4,
                funding_checked_at = now(), discovery_detail = discovery_detail || $5 WHERE address = $1""",
                               wallet, earliest_funding[1], earliest_funding[2] / 1e9, earliest_funding[3],
                               {"funding_is_first_ever": bool(exhausted)})
    if mode == "backfill":
        check = await backfill_check(ctx, wallet, sigs, failed, ok, names, got_tx, missing_tx, method, pages)
        # this wallet's own Helius calls at list price (gTFA: 10 per page of <= 100 full tx; probe and any
        # fallback getTransaction: 1 each). The worker-wide counter is kept for the daily cap.
        check["credits_est"] = (pages * 10 + fetched + probe_calls) if method == "gtfa" else \
            ((await ctx.rpc.helius_credits_today(ctx.rpc.tag) - credits0) if ctx.rpc.has_helius() else 0)
        await ctx.pool.execute("UPDATE wallets SET discovery_detail = discovery_detail || $2 WHERE address = $1",
                               wallet, {"backfill_check": check})
    await ctx.redis.sadd(K_DIRTY_WALLETS, wallet)
    return {"mode": mode, "method": method, "signatures": len(sigs), "failed": len(failed), "fetched": fetched,
            "trades": decoded_trades, "truncated": truncated, "exhausted": exhausted}


async def backfill_check(ctx: Ctx, wallet: str, sigs: list, failed: list, ok: list, names: list,
                         got_tx: list, missing_tx: list, method: str, pages: int) -> dict:
    """Completeness of one backfill, stored in wallets.discovery_detail.backfill_check:
    every successful signature must have produced a transaction (none missing), and the wallet's
    decoded trades must reconcile with the on-chain balances: verification 'verified' means the
    decoder matched the wallet's SOL change (incl. network fee, tips, rent) and token balance changes."""
    r = await ctx.pool.fetchrow("""SELECT count(*) FILTER (WHERE tx_type IN ('buy','sell')) AS trades,
            count(*) FILTER (WHERE tx_type IN ('buy','sell') AND verification_status = 'verified') AS verified,
            count(*) FILTER (WHERE tx_type IN ('buy','sell') AND abs(COALESCE(reconciliation_residual_sol, 0)) > 0.0001) AS residual,
            count(*) FILTER (WHERE tx_type IN ('transfer_in','transfer_out')) AS transfers,
            COALESCE(sum(fee_network_sol) FILTER (WHERE tx_type IN ('buy','sell')), 0) AS fees_network
        FROM wallet_transactions WHERE wallet = $1 AND source = 'rpc_tx'""", wallet)
    trades, verified = int(r["trades"] or 0), int(r["verified"] or 0)
    return {"method": method, "pages": pages, "at": int(time.time()),
            "signatures": len(sigs), "failed_onchain": len(failed), "successful": len(ok),
            "skipped_known_irrelevant": len(ok) - len(names), "transactions_obtained": len(got_tx),
            "transactions_missing": len(missing_tx), "complete": not missing_tx,
            "trades": trades, "trades_verified": verified,
            "verified_share": round(verified / trades, 4) if trades else None,
            "trades_sol_residual_over_0.0001": int(r["residual"] or 0),
            "token_transfers": int(r["transfers"] or 0), "network_fees_sol": round(float(r["fees_network"] or 0), 6)}


def _relevant(tx: dict) -> bool:
    """Keep raw JSON only for transactions that touch pump.fun / PumpSwap (audit trail)."""
    from ..decoder.tx import _account_keys
    from ..solana.constants import PUMP_AMM_PROGRAM, PUMP_PROGRAM
    try:
        keys = set(_account_keys(tx))
    except Exception:
        return False
    return PUMP_PROGRAM in keys or PUMP_AMM_PROGRAM in keys


async def scheduler(ctx: Ctx) -> None:
    """Queue backfills for new candidates and periodic incremental syncs for tracked wallets."""
    while True:
        try:
            s = await ctx.settings.get()
            rows = await ctx.pool.fetch("""SELECT address, backfill_status FROM wallets
                LEFT JOIN smart_money_scores sc ON sc.wallet = wallets.address AND sc.period_days = 30
                WHERE status IN ('candidate','tracked','smart') AND NOT is_excluded
                  AND (backfill_status IN ('queued','none','failed')
                       OR last_synced_at IS NULL OR last_synced_at < now() - make_interval(mins => $1::int))
                ORDER BY (status = 'smart') DESC, (discovery_source IN ('manual', 'watchlist')) DESC,
                         EXISTS (SELECT 1 FROM watched_wallets ww WHERE ww.address = wallets.address AND ww.confirmed) DESC,
                         (discovery_source = 'patient_winner' AND backfill_status = 'queued') DESC,
                         -- most evidence first: preliminary (live-stream) score passes the mandatory rules,
                         -- then number of closed >= 2 SOL positions already seen, then the score
                         COALESCE(sc.qualifies, false) DESC,
                         COALESCE((sc.components->'confidence_inputs'->>'closed')::int, 0) DESC,
                         COALESCE(sc.score, 0) DESC, (discovery_source IN ('early_buyer','seed')) DESC,
                         COALESCE((discovery_detail->>'tokens_7d')::int, jsonb_array_length(
                             CASE WHEN jsonb_typeof(discovery_detail->'tokens') = 'array' THEN discovery_detail->'tokens'
                             ELSE '[]'::jsonb END)) DESC,
                         (backfill_status = 'queued') DESC, last_synced_at NULLS FIRST
                LIMIT 200""", int(s["wallet_sync_interval_minutes"]))
            allowed_key = f"bf:allowed:{_today()}"
            limit = int(s["backfill_wallets_per_day"])
            capped = ctx.rpc.has_helius() and await ctx.rpc.helius_credits_today() >= int(s["helius_daily_credit_cap"])
            for r in rows:   # rows are in priority order
                mode = "backfill" if r["backfill_status"] in ("queued", "none", "failed") else "sync"
                if mode == "backfill":
                    if capped:
                        continue
                    if not await ctx.redis.sismember(allowed_key, r["address"]):
                        if await ctx.redis.scard(allowed_key) >= limit:
                            continue
                        await ctx.redis.sadd(allowed_key, r["address"])
                        await ctx.redis.expire(allowed_key, 3 * 86400)
                await ctx.enqueue(Q_WALLET_SYNC, {"wallet": r["address"], "mode": mode},
                                  dedupe_key=r["address"], dedupe_ttl=int(s["wallet_sync_interval_minutes"]) * 60)
        except Exception:
            log.exception("sync scheduler failed")
        await asyncio.sleep(60)


async def worker(ctx: Ctx, concurrency: int = 2) -> None:
    pools = PoolCache()
    await pools.load(ctx.pool)
    sem = asyncio.Semaphore(concurrency)
    counts = {"done": 0, "error": 0}
    in_flight: dict[str, float] = {}   # wallet -> start time, shown in the heartbeat

    async def beat() -> None:
        # one backfill of a 3000-signature wallet takes ~10 min on public RPC; beat every minute so
        # the container health check sees a live worker, and expose what it is working on (a stuck
        # wallet shows as a growing running_s instead of a silent worker)
        while True:
            try:
                info = dict(counts, in_flight=[{"wallet": w, "running_s": round(time.time() - t0)}
                                               for w, t0 in in_flight.items()])
                await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, info)
            except Exception:
                log.exception("sync heartbeat failed")
            await asyncio.sleep(60)

    async def handle(mid, p):
        async with sem:
            wallet = p["wallet"]
            in_flight[wallet] = time.time()
            try:
                tracked = {r["address"] for r in await ctx.pool.fetch(
                    "SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")}
                res = await sync_wallet(ctx, wallet, p.get("mode", "sync"), pools, tracked)
                counts["done"] += 1
                log.info("wallet %s %s", wallet, res)
            except Exception as e:
                counts["error"] += 1
                log.warning("wallet sync %s failed: %s", wallet, e)
                await ctx.pool.execute("UPDATE wallets SET backfill_status = CASE WHEN backfill_status = 'running' "
                                       "THEN 'failed' ELSE backfill_status END, backfill_error = $2 WHERE address = $1",
                                       wallet, str(e)[:300])
            in_flight.pop(wallet, None)
            await ctx.ack(Q_WALLET_SYNC, GROUP, [mid])
            await ctx.redis.delete(f"dd:{Q_WALLET_SYNC}:{wallet}")

    beat_task = asyncio.create_task(beat())  # noqa: F841  (kept referenced so it is not collected)
    while True:
        msgs = await ctx.consume(Q_WALLET_SYNC, GROUP, count=concurrency)
        if msgs:
            await asyncio.gather(*(handle(mid, p) for mid, p in msgs))
            await pools.load(ctx.pool)


async def main(ctx: Ctx) -> None:
    await asyncio.gather(scheduler(ctx), worker(ctx))
