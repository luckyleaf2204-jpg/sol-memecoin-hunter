"""Post-graduation (PumpSwap) survey of the winner tokens: who bought in the 100k-300k USD
market-cap zone, and does it repeat? (research step 4, approved 23/11/2026)

Stages, each saving its inputs/results under <data_dir>/pumpswap/ (resumable, no repeat calls):
  zones      free: hourly closes (GeckoTerminal) -> the window after graduation (max 24 h) while the
             market cap stayed in [zone_lo, zone_hi] USD; tokens never in the zone are skipped
  estimate   10 credits per 1000 signatures: the pool's transactions inside the window
             (getTransactionsForAddress, signatures only, oldest first) -> credit estimate
  read       only if the estimate fits the budget: full pool transactions of the window, decoded
             from the logs (PumpSwap buy/sell events); stops at the ceiling
  buyers     wallets that bought >= min_buy SOL inside the zone (per token)
Exits, losses and fees are NOT taken from the pool: they come from each candidate wallet's full
history (radar.tools.winner_verify), which is cheaper and includes every other trade of the wallet.
Usage: python -m radar pumpswap-zone --stage zones|estimate|read --max-credits N
"""
from __future__ import annotations

import asyncio
import json
import os
import time

from ..context import create_ctx
from ..decoder.logs import records_from_logs
from .winner_history import Budget, _dump, _load

GT = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/minute"
WSOL = "So11111111111111111111111111111111111111112"
SUPPLY = 1_000_000_000


async def zone_window(ctx, pool: str, graduated_at: int, lo: float, hi: float, max_hours: int = 24) -> dict:
    """15-minute closes (free) for the first `max_hours` after graduation; the zone window is from
    the first close inside [lo, hi] to the first close above hi (or the end)."""
    d = await ctx.http.request_json("geckoterminal", "GET", GT.format(pool=pool),
                                    params={"aggregate": 15, "limit": 1000, "currency": "usd",
                                            "before_timestamp": graduated_at + max_hours * 3600}, cache_ttl=86400)
    candles = sorted(c for c in ((d or {}).get("data", {}).get("attributes", {}).get("ohlcv_list") or [])
                     if graduated_at - 900 <= c[0] <= graduated_at + max_hours * 3600)
    if not candles:
        return {"in_zone": False, "reason": "no_candles"}
    start = end = None
    for t, _o, _h, _l, c, _v in candles:
        mc = float(c) * SUPPLY
        if start is None and lo <= mc <= hi:
            start = t
        if start is not None and mc > hi:
            end = t + 900
            break
    if start is None:
        return {"in_zone": False, "reason": "never_in_zone", "first_mc": float(candles[0][4]) * SUPPLY}
    return {"in_zone": True, "start": int(start), "end": int(end or candles[-1][0] + 900),
            "minutes": round(((end or candles[-1][0] + 900) - start) / 60)}


async def count_window(ctx, pool: str, start: int, end: int, budget) -> dict:
    n, token, calls = 0, None, 0
    while True:
        await budget()
        page, token = await ctx.rpc.get_transactions_for_address(pool, pagination_token=token, limit=1000,
                                                                 full=False, sort="asc")
        calls += 1
        inside = [x for x in page if start <= (x.get("blockTime") or 0) <= end]
        n += len(inside)
        if not token or len(page) < 1000 or (page and (page[-1].get("blockTime") or 0) > end):
            return {"signatures": n, "count_calls": calls}


async def read_window(ctx, mint: str, pool: str, start: int, end: int, budget, sol_usd: float) -> list[dict]:
    trades, token = [], None
    resolver = lambda p: (mint, WSOL) if p == pool else None   # noqa: E731
    while True:
        await budget()
        page, token = await ctx.rpc.get_transactions_for_address(pool, pagination_token=token, limit=100, sort="asc")
        for tx in page:
            bt = tx.get("blockTime") or 0
            if bt > end:
                return trades
            if bt < start or (tx.get("meta") or {}).get("err") is not None:
                continue
            sig = tx["transaction"]["signatures"][0]
            rec = records_from_logs(sig, tx.get("slot") or 0, (tx.get("meta") or {}).get("logMessages") or [], resolver)
            for t in rec["trades"]:
                if t.mint == mint:
                    trades.append({"wallet": t.wallet, "slot": t.slot, "bt": t.block_time or bt, "tx_type": t.tx_type,
                                   "sol": t.sol_amount, "tok": t.token_amount,
                                   # pump tokens: 1e9 supply; price from the pool event (SOL per token)
                                   "mc_usd": (t.market_cap_sol or (t.price_sol or t.effective_price_sol or 0) * SUPPLY) * sol_usd,
                                   "signature": sig, "venue": t.venue})
        if not token or len(page) < 100:
            return trades


async def run(stage: str, max_credits: int, already_spent: int = 0, data_dir: str = "/app/winner_data",
              zone_lo: float = 100_000, zone_hi: float = 300_000, min_buy: float = 0.5) -> dict:
    ctx = await create_ctx("pumpswap-zone", pool_size=2)
    base = os.path.join(data_dir, "pumpswap")
    rep: dict = {"stage": stage, "ceiling": max_credits, "already_spent": already_spent}
    try:
        rk = _load(os.path.join(data_dir, "ranking.json")) or {}
        winners = sorted((w for w in rk.values() if w.get("winner")), key=lambda w: -(w.get("priority") or 0))
        budget_obj = Budget(ctx, max_credits, already_spent)

        async def budget():
            await budget_obj.check()

        zpath = os.path.join(base, "zones.json")
        zones = _load(zpath) or {}
        for w in winners:
            if w["mint"] in zones or not w.get("pool"):
                continue
            try:
                zones[w["mint"]] = {"pool": w["pool"], "graduated_at": w["graduated_at"],
                                    **await zone_window(ctx, w["pool"], w["graduated_at"], zone_lo, zone_hi)}
            except Exception as e:
                zones[w["mint"]] = {"in_zone": False, "reason": f"ohlcv_error {type(e).__name__}"}
            await asyncio.sleep(2.1)
        _dump(zpath, zones)
        rep["zones"] = {"winners": len(winners), "in_zone": sum(1 for z in zones.values() if z.get("in_zone"))}
        if stage == "zones":
            return rep

        epath = os.path.join(base, "estimates.json")
        est = _load(epath) or {}
        for m, z in zones.items():
            if not z.get("in_zone") or m in est:
                continue
            u0 = await budget_obj.used()
            try:
                est[m] = await count_window(ctx, z["pool"], z["start"], z["end"], budget)
            except RuntimeError as ex:
                rep["stopped"] = str(ex)
                break
            est[m]["read_cost_estimate"] = 10 * ((est[m]["signatures"] + 99) // 100)
            est[m]["credits_for_count"] = await budget_obj.used() - u0
            _dump(epath, est)
        rep["estimate"] = {"tokens": len(est), "signatures": sum(e["signatures"] for e in est.values()),
                           "read_cost_estimate": sum(e["read_cost_estimate"] for e in est.values()),
                           "credits_used_so_far": await budget_obj.used()}
        if stage == "estimate":
            return rep

        # read: cheapest first never exceeds the ceiling; every token read is saved
        sol_usd = await ctx.sol_usd() or 0.0
        rdir = os.path.join(base, "trades")
        buyers_all: dict = _load(os.path.join(base, "zone_buyers.json")) or {}
        for m, e in sorted(est.items(), key=lambda kv: kv[1]["read_cost_estimate"]):
            if os.path.exists(os.path.join(rdir, f"{m}.json.gz")):
                continue
            if await budget_obj.used() + e["read_cost_estimate"] > max_credits:
                rep.setdefault("skipped_over_budget", []).append(m)
                continue
            z = zones[m]
            u0 = await budget_obj.used()
            try:
                trades = await read_window(ctx, m, z["pool"], z["start"], z["end"], budget, sol_usd)
            except RuntimeError as ex:
                rep["stopped"] = str(ex)
                break
            _dump(os.path.join(rdir, f"{m}.json.gz"), {"mint": m, "zone": z, "sol_usd_used": sol_usd, "trades": trades})
            by: dict = {}
            for t in trades:
                if t["tx_type"] == "buy" and zone_lo <= t["mc_usd"] <= zone_hi:
                    b = by.setdefault(t["wallet"], {"sol": 0.0, "first": t["signature"], "mc_usd": t["mc_usd"]})
                    b["sol"] += t["sol"]
            buyers_all[m] = {w: b for w, b in by.items() if b["sol"] >= min_buy}
            _dump(os.path.join(base, "zone_buyers.json"), buyers_all)
            with open(os.path.join(base, "progress.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": int(time.time()), "mint": m, "trades": len(trades), "zone_buyers": len(buyers_all[m]),
                                    "credits_before": u0, "credits_after": await budget_obj.used()}) + "\n")
        rep["read"] = {"tokens_read": len(buyers_all), "credits_used_total": await budget_obj.used()}
        return rep
    finally:
        await ctx.close()
