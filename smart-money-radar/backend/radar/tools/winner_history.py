"""Winner reverse on PAST data (research phase 2, historical): the last N days at once instead of
waiting for the stream to collect them.

  1. every pump.fun graduation of the period, listed from the pump.fun migration authority with
     Helius getTransactionsForAddress (one call returns 100 migration transactions, the
     CompletePumpAmmMigrationEvent in the logs gives mint, pool, time);
  2. winner test from free GeckoTerminal hourly OHLCV of the PumpSwap pool (closing prices, wicks
     ignored): peak market cap >= winner_hist_min_peak_usd, >= winner_min_multiple x the graduation
     market cap, and >= winner_hold_fraction of the peak one hour after the peak hour;
  3. for each winner, every bonding-curve trade from creation to graduation, read with
     getTransactionsForAddress on the mint (oldest first) and decoded from the program logs;
     early buyers are judged with the same rules as the live worker
     (radar.services.winner_reverse.early_buyers): sells after graduation are not read, so a
     buyer who held past graduation is "not closed" and not counted (conservative);
  4. results are stored like the live worker's (api_sync_status source 'winner'); the winners
     worker then promotes wallets that repeat on >= winner_min_tokens winners.

Credits are counted with the Helius counter and the run stops at --max-credits.
Usage: python -m radar winner-history [--days 14] [--max-credits 80000] [--out report.json]
"""
from __future__ import annotations

import asyncio
import json
import logging
import time

from ..context import create_ctx
from ..decoder.logs import records_from_logs
from ..services.winner_reverse import early_buyers

log = logging.getLogger(__name__)
MIGRATION_AUTHORITY = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"   # signs every pump.fun -> PumpSwap migration
GT = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/hour"
SUPPLY = 1_000_000_000


async def list_graduations(ctx, since: int, budget) -> list[dict]:
    out, token = [], None
    while True:
        await budget()
        page, token = await ctx.rpc.get_transactions_for_address(MIGRATION_AUTHORITY, pagination_token=token, limit=100)
        stop = False
        for tx in page:
            bt = tx.get("blockTime") or 0
            if bt < since:
                stop = True
                break
            if (tx.get("meta") or {}).get("err") is not None:
                continue
            sig = tx["transaction"]["signatures"][0]
            rec = records_from_logs(sig, tx.get("slot") or 0, (tx.get("meta") or {}).get("logMessages") or [])
            for m in rec["migrations"]:
                out.append({"mint": m.mint, "pool": m.pool, "graduated_at": bt, "migration_sig": sig,
                            "migration_sol": m.sol_amount / 1e9})
        if stop or not token or len(page) < 100:
            return out


async def winner_test(ctx, g: dict, s: dict, sol_usd: float) -> dict:
    """Hourly closes of the pool since graduation (free API, 30 calls/min)."""
    for attempt in range(4):
        try:
            d = await ctx.http.request_json("geckoterminal", "GET", GT.format(pool=g["pool"]),
                                            params={"limit": 1000, "currency": "usd"}, cache_ttl=3600)
            break
        except Exception:
            await asyncio.sleep(10 * (attempt + 1))
    else:
        return {"winner": False, "reason": "ohlcv_unavailable"}
    candles = sorted((d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list") or [])
    if len(candles) < 3:
        return {"winner": False, "reason": "too_few_candles"}
    closes = [(c[0], float(c[4])) for c in candles]
    grad_mc = closes[0][1] * SUPPLY
    i_peak = max(range(len(closes)), key=lambda i: closes[i][1])
    peak_mc = closes[i_peak][1] * SUPPLY
    after = closes[i_peak + 1][1] * SUPPLY if i_peak + 1 < len(closes) else 0.0
    out = {"graduation_mc_usd": round(grad_mc), "peak_mc_usd": round(peak_mc), "peak_hour": closes[i_peak][0],
           "mc_1h_after_peak_usd": round(after), "peak_mc_sol": round(peak_mc / sol_usd, 1) if sol_usd else None,
           "winner": False}
    if peak_mc < float(s["winner_hist_min_peak_usd"]):
        out["reason"] = "peak_below_min"
    elif grad_mc <= 0 or peak_mc / grad_mc < float(s["winner_min_multiple"]) / 2:
        # graduation already happens at ~ 13x the launch price; half the live multiple is asked from it
        out["reason"] = "multiple_below_min"
    elif after < float(s["winner_hold_fraction"]) * peak_mc:
        out["reason"] = "did_not_hold_after_peak"
    else:
        out["winner"] = True
    return out


async def curve_trades(ctx, mint: str, until: int, budget, max_pages: int) -> tuple[list[dict], dict]:
    """All bonding-curve trades of a mint from its creation up to graduation (oldest first)."""
    trades, token, pages = [], None, 0
    meta: dict = {"created_slot": None, "creator": None, "created_at": None}
    while pages < max_pages:
        await budget()
        page, token = await ctx.rpc.get_transactions_for_address(mint, pagination_token=token, limit=100, sort="asc")
        pages += 1
        for tx in page:
            bt = tx.get("blockTime") or 0
            if bt > until:
                return trades, {**meta, "pages": pages, "complete": True}
            if (tx.get("meta") or {}).get("err") is not None:
                continue
            sig = tx["transaction"]["signatures"][0]
            rec = records_from_logs(sig, tx.get("slot") or 0, (tx.get("meta") or {}).get("logMessages") or [])
            for c in rec["creates"]:
                if c.mint == mint and meta["created_slot"] is None:
                    meta.update(created_slot=tx.get("slot"), creator=c.creator or c.user, created_at=bt)
            for t in rec["trades"]:
                if t.mint == mint:
                    trades.append({"wallet": t.wallet, "slot": t.slot, "bt": t.block_time or bt, "tx_type": t.tx_type,
                                   "sol": t.sol_amount, "tok": t.token_amount, "fee": t.fee_trading_sol or 0.0,
                                   "mc": t.market_cap_sol, "signature": sig})
        if not token or len(page) < 100:
            return trades, {**meta, "pages": pages, "complete": True}
    return trades, {**meta, "pages": pages, "complete": False}


async def run(days: int = 14, max_credits: int = 80_000, out: str | None = None, max_pages_per_token: int = 150) -> dict:
    ctx = await create_ctx("winner-history", pool_size=2)
    rep: dict = {"days": days, "max_credits": max_credits}
    try:
        s = await ctx.settings.get()
        sol_usd = await ctx.sol_usd() or 0.0
        start = await ctx.rpc.helius_credits_today()

        spent = {"now": 0}

        async def refresh():
            spent["now"] = await ctx.rpc.helius_credits_today() - start

        async def budget():
            # checked before every paid call (Helius counter in Redis, all workers included)
            await refresh()
            if spent["now"] >= max_credits:
                raise RuntimeError("credit budget reached")

        try:
            grads = await list_graduations(ctx, int(time.time()) - days * 86400, budget)
        except RuntimeError:
            rep["stopped"] = "credit budget reached while listing graduations"
            return rep
        await refresh()
        rep["graduations"] = len(grads)
        rep["credits_listing"] = spent["now"]
        done = {r["job"] for r in await ctx.pool.fetch("SELECT job FROM api_sync_status WHERE source = 'winner'")}
        winners, evaluated, tested = 0, 0, 0
        reasons: dict = {}
        for g in grads:
            if g["mint"] in done:
                continue
            w = await winner_test(ctx, g, s, sol_usd)
            tested += 1
            reasons[w.get("reason", "winner")] = reasons.get(w.get("reason", "winner"), 0) + 1
            ev = {"source": "history", **g, **w}
            if w["winner"]:
                await refresh()
                try:
                    trades, meta = await curve_trades(ctx, g["mint"], g["graduated_at"], budget, max_pages_per_token)
                except RuntimeError:
                    rep["stopped"] = "credit budget reached"
                    break
                peak_sol = w["peak_mc_sol"] or 0.0
                buyers, excl = early_buyers(trades, meta["created_slot"], meta["creator"], peak_sol, set(), s)
                ev.update(curve_trades=len(trades), **meta, qualifying_buyers=buyers, early_buyer_funnel=excl)
                winners += 1
            await ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, details, last_success_at, updated_at)
                VALUES ('winner', $1, $2, $3, now(), now()) ON CONFLICT (source, job) DO NOTHING""",
                                   g["mint"], "winner" if w["winner"] else "not_winner", ev)
            evaluated += 1
            await asyncio.sleep(2.1)            # GeckoTerminal free tier: 30 calls / minute
        await refresh()
        rep.update(tested=tested, evaluated=evaluated, winners=winners, reasons=reasons, credits_used=spent["now"])
        if out:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(rep, f, indent=1, default=str)
        return rep
    finally:
        await ctx.close()
