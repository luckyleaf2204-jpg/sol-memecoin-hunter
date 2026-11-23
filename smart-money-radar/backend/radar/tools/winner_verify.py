"""Verify winner-reverse candidates on their FULL history (losses included) before any conclusion.

For each candidate in <data_dir>/candidates.json (wallets profitable early on >= 3 winners):
  1. read the wallet's 60-day history with the existing backfill (radar.services.wallet_sync,
     getTransactionsForAddress, ~280 credits per wallet), within the remaining approved credits;
  2. rebuild its positions with the existing analytics (radar.services.analytics_runner);
  3. judge it on closed positions that are fully verified and do not sell tokens bought before the
     history window (both shown wrong in the batch-1 investigation), with the criteria of
     docs/research/phuong-an-tim-smart-money.md (C1-C3). Scoring and PnL code are not changed.
Output: <data_dir>/verification.json (per wallet: pass/fail, reasons, figures, evidence).
Usage: python -m radar winner-verify --max-credits <remaining> [--out file]
"""
from __future__ import annotations

import json
import os
import statistics
import time

from ..context import create_ctx

CRIT = {"min_closed": 20, "min_pnl_sol": 10.0, "min_pf": 1.5, "min_wr": 0.45, "max_best_share": 0.4,
        "min_verified_share": 0.95, "max_trades_per_day": 100, "min_buy_sol": 0.5}


def judge(positions: list[dict], check: dict, crit: dict = CRIT) -> dict:
    """positions: closed positions of the wallet (dicts with buy_sol, realized_pnl_sol, verification,
    tokens_bought, tokens_sold, flags, holding_seconds, mint). check: backfill_check."""
    use, excluded = [], {"unverified": 0, "sold_pre_window_tokens": 0, "transfers": 0, "small": 0}
    for p in positions:
        if float(p["buy_sol"] or 0) < crit["min_buy_sol"]:
            excluded["small"] += 1
        elif p["verification"] != "verified":
            excluded["unverified"] += 1
        elif any(f in ("transfer_in", "transfer_out", "opened_by_transfer") for f in (p.get("flags") or [])):
            excluded["transfers"] += 1
        elif float(p["tokens_sold"] or 0) > 1.01 * float(p["tokens_bought"] or 0):
            excluded["sold_pre_window_tokens"] += 1
        else:
            use.append(p)
    pnls = [float(p["realized_pnl_sol"]) for p in use]
    wins = [x for x in pnls if x > 0]
    gl = -sum(x for x in pnls if x < 0)
    tot = sum(pnls)
    f = {"closed_used": len(use), "excluded": excluded, "pnl_sol": round(tot, 3),
         "win_rate": round(len(wins) / len(pnls), 3) if pnls else None,
         "profit_factor": round(sum(wins) / gl, 2) if gl > 0 else (99.0 if wins else None),
         "best_share": round(max(pnls) / tot, 3) if tot > 0 and pnls else None,
         "losses": sum(1 for x in pnls if x < 0), "loss_sol": round(-gl, 3),
         "median_hold_s": int(statistics.median([p["holding_seconds"] or 0 for p in use])) if use else None,
         "verified_share": check.get("verified_share"), "history_complete": check.get("method") is not None
         and check.get("transactions_missing") == 0, "signatures": check.get("signatures")}
    # measured over the span actually read (a 3000-signature history can cover only days)
    f["trades_per_day"] = check.get("trades_per_day_measured", round((check.get("trades") or 0) / 60.0, 1))
    reasons = []
    if not f["history_complete"]:
        reasons.append("history not read completely")
    if (f["verified_share"] or 0) < crit["min_verified_share"]:
        reasons.append(f"verified share {f['verified_share']} < {crit['min_verified_share']}")
    if f["trades_per_day"] > crit["max_trades_per_day"]:
        reasons.append(f"{f['trades_per_day']} trades/day: high-frequency bot")
    if len(use) < crit["min_closed"]:
        reasons.append(f"only {len(use)} usable closed positions (< {crit['min_closed']})")
    if tot < crit["min_pnl_sol"]:
        reasons.append(f"PnL {round(tot, 2)} SOL < {crit['min_pnl_sol']}")
    if f["profit_factor"] is not None and f["profit_factor"] < crit["min_pf"]:
        reasons.append(f"profit factor {f['profit_factor']} < {crit['min_pf']}")
    if f["win_rate"] is not None and f["win_rate"] < crit["min_wr"]:
        reasons.append(f"win rate {f['win_rate']} < {crit['min_wr']}")
    if f["best_share"] is not None and f["best_share"] > crit["max_best_share"]:
        reasons.append(f"best trade {f['best_share']} of PnL > {crit['max_best_share']}")
    return {"pass": not reasons, "reasons": reasons, "figures": f,
            "evidence": sorted(({"mint": p["mint"], "buy_sol": float(p["buy_sol"]), "pnl": float(p["realized_pnl_sol"]),
                                 "signatures": (p.get("signatures") or [])[:6]} for p in use), key=lambda e: -e["pnl"])[:5]
            + sorted(({"mint": p["mint"], "buy_sol": float(p["buy_sol"]), "pnl": float(p["realized_pnl_sol"]),
                       "signatures": (p.get("signatures") or [])[:6]} for p in use if float(p["realized_pnl_sol"]) < 0),
                     key=lambda e: e["pnl"])[:5]}


async def run(max_credits: int, out: str | None = None, data_dir: str = "/app/winner_data") -> dict:
    from ..services import analytics_runner
    from ..services.txproc import PoolCache
    from ..services.wallet_sync import sync_wallet
    ctx = await create_ctx("winner-verify", pool_size=3)
    rep: dict = {"ceiling": max_credits}
    try:
        with open(os.path.join(data_dir, "candidates.json"), encoding="utf-8") as fh:
            cands = json.load(fh)
        s = await ctx.settings.get()
        start = await ctx.rpc.helius_credits_today(ctx.rpc.tag)
        pools = PoolCache()
        await pools.load(ctx.pool)
        tracked = {r["address"] for r in await ctx.pool.fetch("SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")}
        day = time.strftime("%Y%m%d", time.gmtime())
        results, stop = {}, None
        for w, c in sorted(cands.items(), key=lambda kv: (-kv[1]["tokens"], -kv[1]["pnl"])):
            used = await ctx.rpc.helius_credits_today(ctx.rpc.tag) - start
            if used + 310 > max_credits:          # a full 60-day read costs up to ~301 credits
                stop = f"credit ceiling: used {used}, next wallet needs ~310"
                break
            await ctx.pool.execute("""INSERT INTO wallets(address, status, discovery_source, discovery_detail, backfill_status)
                VALUES ($1, 'candidate', 'winner_reverse', $2, 'queued') ON CONFLICT (address) DO NOTHING""",
                                   w, {"winner_reverse": c})
            row = await ctx.pool.fetchrow("SELECT backfill_status FROM wallets WHERE address = $1", w)
            if row["backfill_status"] not in ("complete", "partial"):
                await ctx.redis.sadd(f"bf:allowed:{day}", w)     # admit this approved verification read
                res = await sync_wallet(ctx, w, "backfill", pools, tracked | {w})
            else:
                res = {"already_backfilled": True}
            await analytics_runner.recompute_wallet(ctx, w, s, int(time.time()), 1, {})
            wr = await ctx.pool.fetchrow("SELECT discovery_detail FROM wallets WHERE address = $1", w)
            pos = [dict(p) for p in await ctx.pool.fetch("""SELECT mint, buy_sol, realized_pnl_sol, verification, tokens_bought,
                    tokens_sold, flags, holding_seconds, signatures FROM wallet_positions
                WHERE wallet = $1 AND status IN ('closed','written_off')""", w)]
            act = await ctx.pool.fetchrow("""SELECT count(*) n, GREATEST(extract(epoch FROM max(block_time) - min(block_time)) / 86400, 1) d
                FROM wallet_transactions WHERE wallet = $1 AND tx_type IN ('buy','sell') AND source = 'rpc_tx'""", w)
            chk = dict((wr["discovery_detail"] or {}).get("backfill_check") or {})
            chk["trades_per_day_measured"] = round(act["n"] / float(act["d"]), 1) if act and act["n"] else 0.0
            j = judge(pos, chk)
            j["sync"] = res
            j["winners"] = c
            results[w] = j
            with open(os.path.join(data_dir, "verification.json"), "w", encoding="utf-8") as fh:
                json.dump(results, fh, indent=1, default=str)
        rep.update(candidates=len(cands), verified=len(results), passed=sum(1 for r in results.values() if r["pass"]),
                   credits_used=await ctx.rpc.helius_credits_today(ctx.rpc.tag) - start, stopped=stop)
        if out:
            with open(out, "w", encoding="utf-8") as fh:
                json.dump(rep, fh, indent=1, default=str)
        return rep
    finally:
        await ctx.close()
