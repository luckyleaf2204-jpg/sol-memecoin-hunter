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
import gzip
import json
import logging
import math
import os
import time

from ..context import create_ctx
from ..decoder.logs import records_from_logs
from ..services.winner_reverse import early_buyers

log = logging.getLogger(__name__)
MIGRATION_AUTHORITY = "39azUYFWPz3VHgKCf3VChUwbpURdCHRxjWVowf5jUJjg"   # signs every pump.fun -> PumpSwap migration
GT = "https://api.geckoterminal.com/api/v2/networks/solana/pools/{pool}/ohlcv/hour"
SUPPLY = 1_000_000_000


from ..decoder.migration import graduation_from_tx  # noqa: E402  (shared with the stream)


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
            g = graduation_from_tx(tx)
            if g:
                out.append({**g, "graduated_at": bt})
        if stop or not token or len(page) < 100:
            return out


async def find_pool(ctx, mint: str) -> str | None:
    """PumpSwap pool of a mint from GeckoTerminal (free)."""
    try:
        d = await ctx.http.request_json("geckoterminal", "GET",
                                        f"https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools",
                                        cache_ttl=3600)
    except Exception:
        return None
    for x in (d or {}).get("data") or []:
        if ((x.get("relationships") or {}).get("dex") or {}).get("data", {}).get("id") == "pumpswap":
            return (x.get("attributes") or {}).get("address")
    return None


async def winner_test(ctx, g: dict, s: dict, sol_usd: float) -> dict:
    """Hourly closes of the pool since graduation (free API, 30 calls/min)."""
    if not g.get("pool"):
        g["pool"] = await find_pool(ctx, g["mint"])
        await asyncio.sleep(2.1)
        if not g["pool"]:
            return {"winner": False, "reason": "pool_not_found"}
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
    vol24 = sum(float(c[5] or 0) for c in candles[:24])
    out = {"graduation_mc_usd": round(grad_mc), "peak_mc_usd": round(peak_mc), "peak_hour": closes[i_peak][0],
           "mc_1h_after_peak_usd": round(after), "peak_mc_sol": round(peak_mc / sol_usd, 1) if sol_usd else None,
           "hold_ratio": round(after / peak_mc, 3) if peak_mc else 0.0, "volume_first_24h_usd": round(vol24),
           "candles": len(candles), "pool": g.get("pool"), "winner": False}
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
            if meta["created_slot"] is None and pages == 1 and not trades:
                # the creation transaction is the mint's first; if its logs were truncated the create
                # event is missing: its slot and fee payer (the launcher) are used instead
                keys = (tx.get("transaction") or {}).get("message", {}).get("accountKeys") or []
                payer = keys[0].get("pubkey") if keys and isinstance(keys[0], dict) else (keys[0] if keys else None)
                meta.update(created_slot=tx.get("slot"), creator=payer, created_at=bt, creation_from="first_tx")
            for t in rec["trades"]:
                if t.mint == mint:
                    trades.append({"wallet": t.wallet, "slot": t.slot, "bt": t.block_time or bt, "tx_type": t.tx_type,
                                   "sol": t.sol_amount, "tok": t.token_amount, "fee": t.fee_trading_sol or 0.0,
                                   "mc": t.market_cap_sol, "signature": sig})
        if not token or len(page) < 100:
            return trades, {**meta, "pages": pages, "complete": True}
    return trades, {**meta, "pages": pages, "complete": False}


def priority(w: dict) -> float:
    """Ranking of winners before any paid call: growth after graduation x how well it held, x
    trading depth (first-day volume, capped), x data reliability (enough hourly candles)."""
    grow = math.log10(max(w["peak_mc_usd"], 1) / max(w["graduation_mc_usd"], 1))
    depth = min(1.0, math.log10(max(w.get("volume_first_24h_usd", 0), 1)) / 7)     # 10M USD/day = 1
    reliab = 1.0 if w.get("candles", 0) >= 24 else 0.5
    return round(grow * w["hold_ratio"] * depth * reliab, 4)


async def sig_count(ctx, mint: str, until: int) -> dict:
    """10 credits: oldest 1000 signatures of the mint -> creation time and curve-phase size."""
    page, _ = await ctx.rpc.get_transactions_for_address(mint, limit=1000, full=False, sort="asc")
    if not page:
        return {"curve_sigs": 0, "created_at": None}
    created = page[0].get("blockTime") or 0
    n = sum(1 for x in page if (x.get("blockTime") or 0) <= until)
    return {"curve_sigs": n, "curve_sigs_capped": n >= 1000, "created_at": created,
            "curve_seconds": max(0, until - created)}


class Budget:
    """Counts this run's Helius credits (Redis counter of this worker tag); raises at the ceiling."""

    def __init__(self, ctx, ceiling: int, already_spent: int):
        self.ctx, self.ceiling, self.already = ctx, ceiling, already_spent
        self.start = None

    async def used(self) -> int:
        now = await self.ctx.rpc.helius_credits_today(self.ctx.rpc.tag)
        if self.start is None:
            self.start = now
        return self.already + now - self.start

    async def check(self, need: int = 0) -> int:
        u = await self.used()
        if u + need > self.ceiling:
            raise RuntimeError(f"credit ceiling: used {u} + needed {need} > {self.ceiling}")
        return u


def _dump(path: str, obj) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    if path.endswith(".gz"):
        with gzip.open(tmp, "wt", encoding="utf-8") as f:
            json.dump(obj, f, default=str)
    else:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=1, default=str)
    os.replace(tmp, path)


def _load(path: str):
    if not os.path.exists(path):
        return None
    if path.endswith(".gz"):
        with gzip.open(path, "rt", encoding="utf-8") as f:
            return json.load(f)
    with open(path, encoding="utf-8") as f:
        return json.load(f)


async def run(days: int = 14, max_credits: int = 77_760, out: str | None = None, max_pages_per_token: int = 150,
              data_dir: str = "/app/winner_data", already_spent: int = 0, verify_reserve: int = 15_000,
              group: int = 10, prefilter_fdv: float = 100_000.0) -> dict:
    """Staged and resumable; every stage writes its inputs and results under data_dir:
      graduations.json        listing (signature, slot, time, mint, pool) - paid once
      ranking.json            free winner test + priority of every graduation
      estimates.json          10-credit signature counts of the winners read next
      trades/<mint>.json.gz   decoded curve trades (signature, slot, wallet, side, SOL, tokens, MC)
      results.json            early-buyer funnel and qualifying buyers per token
      progress.jsonl          credits before / after every group
    already_spent: credits of this approval spent by earlier runs (counted against max_credits)."""
    ctx = await create_ctx("winner-history", pool_size=2)
    rep: dict = {"days": days, "ceiling": max_credits, "already_spent_before_this_run": already_spent}
    prog_path = os.path.join(data_dir, "progress.jsonl")

    def progress(**kw):
        os.makedirs(data_dir, exist_ok=True)
        with open(prog_path, "a", encoding="utf-8") as f:
            f.write(json.dumps({"at": int(time.time()), **kw}, default=str) + "\n")

    try:
        s = await ctx.settings.get()
        sol_usd = await ctx.sol_usd() or 0.0
        budget = Budget(ctx, max_credits, already_spent)

        async def guard():
            await budget.check()

        # 1. listing (paid once, cached)
        gpath = os.path.join(data_dir, "graduations.json")
        grads = _load(gpath)
        if grads is None:
            u0 = await budget.check()
            grads = await list_graduations(ctx, int(time.time()) - days * 86400, guard)
            grads = list({g["mint"]: g for g in grads}.values())
            _dump(gpath, grads)
            progress(stage="listing", graduations=len(grads), credits_before=u0, credits_after=await budget.used())
        rep["graduations"] = len(grads)

        # 2a. free snapshot of every graduated token (GeckoTerminal, 30 tokens per call): current
        # market cap and PumpSwap pool. Tokens worth less than `prefilter_fdv` today are not tested
        # further (a winner that held its gains is still worth something; bias noted in the report).
        spath = os.path.join(data_dir, "snapshot.json")
        snap = _load(spath) or {}
        todo = [g["mint"] for g in grads if g["mint"] not in snap]
        for i in range(0, len(todo), 30):
            chunk = todo[i:i + 30]
            try:
                d = await ctx.http.request_json("geckoterminal", "GET",
                                                "https://api.geckoterminal.com/api/v2/networks/solana/tokens/multi/" + ",".join(chunk),
                                                params={"include": "top_pools"}, cache_ttl=3600)
            except Exception:
                await asyncio.sleep(15)
                continue
            dexpool = {x["id"]: ((x.get("relationships") or {}).get("dex") or {}).get("data", {}).get("id")
                       for x in (d or {}).get("included") or []}
            for x in (d or {}).get("data") or []:
                a = x.get("attributes") or {}
                pools = [p["id"] for p in ((x.get("relationships") or {}).get("top_pools") or {}).get("data") or []]
                pump = next((p for p in pools if dexpool.get(p) == "pumpswap"), None)
                snap[a.get("address")] = {"fdv_usd": float(a.get("fdv_usd") or 0),
                                          "pool": pump.replace("solana_", "") if pump else None}
            for m in chunk:
                snap.setdefault(m, {"fdv_usd": 0.0, "pool": None, "missing": True})
            if (i // 30) % 20 == 0:
                _dump(spath, snap)
            await asyncio.sleep(2.1)
        _dump(spath, snap)
        rep["snapshot"] = {"tokens": len(snap), "pass_prefilter": sum(1 for v in snap.values() if v["fdv_usd"] >= prefilter_fdv),
                           "prefilter_fdv_usd": prefilter_fdv}

        # 2. free winner test + ranking (GeckoTerminal; earlier evaluations reused)
        rpath = os.path.join(data_dir, "ranking.json")
        ranking = _load(rpath) or {}
        prior = {r["job"]: r["details"] for r in await ctx.pool.fetch(
            "SELECT job, details FROM api_sync_status WHERE source = 'winner'")}
        for g in grads:
            if g["mint"] in ranking:
                continue
            sn = snap.get(g["mint"]) or {}
            if sn.get("fdv_usd", 0) < prefilter_fdv:
                ranking[g["mint"]] = {**g, "winner": False, "reason": "prefilter_current_fdv_below",
                                      "current_fdv_usd": sn.get("fdv_usd", 0)}
                continue
            g = {**g, "pool": g.get("pool") or sn.get("pool")}
            d = prior.get(g["mint"])
            if d and "peak_mc_usd" in d and "hold_ratio" in d:
                w = {k: d.get(k) for k in ("graduation_mc_usd", "peak_mc_usd", "peak_hour", "mc_1h_after_peak_usd",
                                           "peak_mc_sol", "hold_ratio", "volume_first_24h_usd", "candles", "pool")}
                w["winner"] = None
            else:
                w = await winner_test(ctx, g, s, sol_usd)
                await asyncio.sleep(2.1)
            ranking[g["mint"]] = {**g, **w}
            if len(ranking) % 50 == 0:
                _dump(rpath, ranking)
        # winner rule applied uniformly (also to reused evaluations)
        for m, w in ranking.items():
            if "peak_mc_usd" not in w or not w.get("peak_mc_usd"):
                w["winner"], w["priority"] = False, 0.0
                continue
            w["hold_ratio"] = w.get("hold_ratio") or ((w.get("mc_1h_after_peak_usd") or 0) / w["peak_mc_usd"])
            w["winner"] = (w["peak_mc_usd"] >= float(s["winner_hist_min_peak_usd"])
                           and w["peak_mc_usd"] >= float(s["winner_min_multiple"]) / 2 * max(w.get("graduation_mc_usd") or 0, 1)
                           and w["hold_ratio"] >= float(s["winner_hold_fraction"]))
            w["priority"] = priority(w) if w["winner"] else 0.0
        _dump(rpath, ranking)
        winners = sorted((w for w in ranking.values() if w["winner"]), key=lambda w: -w["priority"])
        rep["free_filter"] = {"tested": len(ranking), "winners": len(winners),
                              "not_winner": len(ranking) - len(winners)}

        # 3+4. estimate and read in priority order, in groups, within the ceiling minus the verification reserve
        epath, respath = os.path.join(data_dir, "estimates.json"), os.path.join(data_dir, "results.json")
        estimates, results = _load(epath) or {}, _load(respath) or {}
        read_ceiling = max_credits - verify_reserve
        stop = None
        for gi in range(0, len(winners), group):
            batch = winners[gi:gi + group]
            u_before = await budget.used()
            done_in_group = []
            for w in batch:
                m = w["mint"]
                if m in results:
                    continue
                try:
                    if m not in estimates:
                        await budget.check(10)
                        estimates[m] = await sig_count(ctx, m, w["graduated_at"])
                        _dump(epath, estimates)
                    e = estimates[m]
                    if (e.get("curve_seconds") or 0) < 60:
                        results[m] = {"skipped": "graduated within 60 s of launch (bundled launch, no organic curve phase)"}
                        continue
                    need = min(max_pages_per_token, (e["curve_sigs"] + 99) // 100) * 10
                    if await budget.used() + need > read_ceiling:
                        stop = f"next token needs ~{need} credits; read ceiling {read_ceiling} reached"
                        break
                    trades, meta = await curve_trades(ctx, m, w["graduated_at"], guard, max_pages_per_token)
                except RuntimeError as ex:
                    stop = str(ex)
                    break
                _dump(os.path.join(data_dir, "trades", f"{m}.json.gz"), {"mint": m, "meta": meta, "trades": trades})
                buyers, excl = early_buyers(trades, meta["created_slot"], meta["creator"], w.get("peak_mc_sol") or 0.0, set(), s)
                results[m] = {"curve_trades": len(trades), **meta, "qualifying_buyers": buyers, "early_buyer_funnel": excl,
                              "priority": w["priority"], "peak_mc_usd": w["peak_mc_usd"], "hold_ratio": w["hold_ratio"]}
                await ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, details, last_success_at, updated_at)
                    VALUES ('winner', $1, 'winner', $2, now(), now())
                    ON CONFLICT (source, job) DO UPDATE SET status = 'winner', details = EXCLUDED.details, updated_at = now()""",
                                       m, {"source": "history", **w, **results[m]})
                done_in_group.append(m)
            _dump(respath, results)
            progress(stage="read", group=gi // group + 1, tokens=done_in_group, credits_before=u_before,
                     credits_after=await budget.used())
            if stop:
                break
        rep["read"] = {"tokens_read": sum(1 for r in results.values() if "curve_trades" in r),
                       "skipped_bundled_launch": sum(1 for r in results.values() if "skipped" in r),
                       "winners_not_reached": len(winners) - len(results), "stopped": stop}

        # 5. candidates: profitable early buyer on >= winner_min_tokens different winners
        wins = [(m, b) for m, r in results.items() for b in r.get("qualifying_buyers", [])]
        from ..services.winner_reverse import repeated
        cands = repeated(wins, int(s["winner_min_tokens"]))
        _dump(os.path.join(data_dir, "candidates.json"), cands)
        rep["candidates"] = len(cands)
        rep["credits_used_total"] = await budget.used()
        if out:
            _dump(out, rep)
        return rep
    finally:
        await ctx.close()
