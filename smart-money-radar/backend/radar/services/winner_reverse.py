"""Winner-reverse discovery (research phase 2, approved 23/11/2026). Database only: no RPC call.

Idea: Smart Money is found from the outcome backwards, not from trade size.
  1. winner tokens: created while the stream was listening, peak market cap (robust: 95th
     percentile of trade market caps) >= winner_min_peak_mc_sol, >= winner_min_multiple x the
     first 10 minutes, and still >= winner_hold_fraction of the peak one hour after the peak
     (pump-and-dump tokens fail this);
  2. early buyers of each winner: first buy at <= winner_early_mc_fraction of the peak, position
     closed in tokens (sold 90-110 % of what was bought, so the PnL is the wallet's own round trip),
     profitable after pump fees; snipers (<= 2 slots after creation), bundlers (>= 3 wallets in the
     same early slot), the creator and wallets funded by / linked to the creator are excluded;
  3. repetition: a wallet that was a qualifying early buyer of >= winner_min_tokens different
     winners within winner_lookback_days, and trades < winner_max_trades_per_day, becomes a
     candidate (discovery_source = 'winner_reverse') with its evidence (signatures, PnL per token).
Candidates still need their full history (losses included) verified before any Smart Money
conclusion; this module never changes scores or statuses of existing wallets.

Every evaluated token is stored once in api_sync_status (source 'winner', job = mint) with its
figures and qualifying buyers, so the evidence outlives the stream retention of raw trades.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import statistics
import time
from collections import defaultdict
from typing import Any

from ..context import K_WINNER_CAPTURE, Ctx
from ..db import repo

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
SANE_MC = 1_000_000.0   # pump tokens have 1e9 supply; above this a row is mispriced (non-SOL quote)


def _p95(xs: list[float]) -> float:
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(0.95 * (len(xs) - 1) + 0.5))]


def evaluate_token(trades: list[dict], created_at: float, s: dict) -> dict:
    """trades: dicts with bt (unix), mc (SOL); returns figures and whether the token is a winner."""
    pts = [(t["bt"], t["mc"]) for t in trades if t.get("mc") and 0 < t["mc"] <= SANE_MC]
    out: dict[str, Any] = {"trades": len(trades), "winner": False}
    if len(pts) < 20:
        out["reason"] = "too_few_priced_trades"
        return out
    pts.sort()
    early = [mc for bt, mc in pts if bt - created_at <= 600]
    if not early:
        out["reason"] = "not_seen_in_first_10_min"
        return out
    first_mc = statistics.median(early[:5])
    peak = _p95([mc for _, mc in pts])
    peak_t = next(bt for bt, mc in pts if mc >= peak)
    after = [mc for bt, mc in pts if peak_t + 3000 <= bt <= peak_t + 4200]
    later = [mc for bt, mc in pts if bt > peak_t + 3600]
    mc_1h = statistics.median(after) if after else (later[0] if later else 0.0)
    out.update(first_mc=round(first_mc, 2), peak_mc=round(peak, 2), peak_at=int(peak_t),
               mc_1h_after_peak=round(mc_1h, 2), multiple=round(peak / first_mc, 2) if first_mc else None)
    if peak < float(s["winner_min_peak_mc_sol"]):
        out["reason"] = "peak_below_min"
    elif first_mc <= 0 or peak / first_mc < float(s["winner_min_multiple"]):
        out["reason"] = "multiple_below_min"
    elif mc_1h < float(s["winner_hold_fraction"]) * peak:
        out["reason"] = "did_not_hold_after_peak"
    else:
        out["winner"] = True
    return out


def early_buyers(trades: list[dict], created_slot: int | None, creator: str | None, peak_mc: float,
                 linked_to_creator: set[str], s: dict) -> tuple[list[dict], dict]:
    """trades: dicts with wallet, slot, bt, tx_type, sol, tok, fee, mc, signature (time order).
    Returns the qualifying early buyers of a winner and counts of each exclusion."""
    by: dict[str, list[dict]] = defaultdict(list)
    slot_buyers: dict[int, set[str]] = defaultdict(set)
    for t in trades:
        by[t["wallet"]].append(t)
        if t["tx_type"] == "buy":
            slot_buyers[t["slot"]].add(t["wallet"])
    excl: dict[str, int] = defaultdict(int)
    out = []
    early_cap = float(s["winner_early_mc_fraction"]) * peak_mc
    for w, rs in by.items():
        buys = [r for r in rs if r["tx_type"] == "buy"]
        sells = [r for r in rs if r["tx_type"] == "sell"]
        if not buys:
            continue
        first = buys[0]
        if first.get("mc") is None or first["mc"] > early_cap:
            continue                                  # not early
        excl["early_buyers"] += 1
        if w == creator:
            excl["creator"] += 1
            continue
        if w in linked_to_creator:
            excl["linked_to_creator"] += 1
            continue
        if created_slot is not None and first["slot"] - created_slot <= 2:
            excl["sniper"] += 1
            continue
        if created_slot is not None and first["slot"] - created_slot <= 5 and len(slot_buyers[first["slot"]]) >= 3:
            excl["bundle"] += 1
            continue
        cost = sum(r["sol"] for r in buys)
        if cost < float(s["winner_min_buy_sol"]):
            excl["too_small"] += 1
            continue
        bought, sold = sum(r["tok"] for r in buys), sum(r["tok"] for r in sells)
        if not sells or not (0.9 * bought <= sold <= 1.1 * bought):
            excl["not_closed_in_tokens"] += 1
            continue
        pnl = sum(r["sol"] for r in sells) - cost - sum(r.get("fee") or 0 for r in rs)
        if pnl <= 0:
            excl["loss"] += 1
            continue
        out.append({"wallet": w, "cost": round(cost, 4), "pnl": round(pnl, 4), "entry_mc": round(first["mc"], 2),
                    "slots_after_creation": (first["slot"] - created_slot) if created_slot is not None else None,
                    "hold_s": int(sells[-1]["bt"] - first["bt"]), "buy_sig": first["signature"],
                    "sell_sig": sells[-1]["signature"]})
    excl["qualified"] = len(out)
    return out, dict(excl)


def repeated(wins: list[tuple[str, dict]], min_tokens: int) -> dict[str, dict]:
    """wins: (mint, qualifying buyer) over the lookback; wallets profitable early on >= min_tokens winners."""
    acc: dict[str, dict] = {}
    for mint, b in wins:
        a = acc.setdefault(b["wallet"], {"tokens": set(), "pnl": 0.0, "cost": 0.0, "evidence": []})
        if mint in a["tokens"]:
            continue
        a["tokens"].add(mint)
        a["pnl"] += b["pnl"]
        a["cost"] += b["cost"]
        a["evidence"].append({"mint": mint, **{k: b[k] for k in ("cost", "pnl", "entry_mc", "buy_sig", "sell_sig")}})
    return {w: {"tokens": len(a["tokens"]), "pnl": round(a["pnl"], 4), "cost": round(a["cost"], 4),
                "evidence": sorted(a["evidence"], key=lambda e: -e["pnl"])[:10]}
            for w, a in acc.items() if len(a["tokens"]) >= min_tokens}


async def _linked_to_creator(ctx: Ctx, creator: str | None, wallets: list[str]) -> set[str]:
    if not creator or not wallets:
        return set()
    rows = await ctx.pool.fetch("""SELECT address AS w FROM wallets WHERE address = ANY($1::text[]) AND funding_source = $2
        UNION SELECT CASE WHEN wallet_a = $2 THEN wallet_b ELSE wallet_a END FROM wallet_relationships
            WHERE (wallet_a = $2 AND wallet_b = ANY($1::text[])) OR (wallet_b = $2 AND wallet_a = ANY($1::text[]))
        UNION SELECT destination FROM sol_transfers WHERE source = $2 AND destination = ANY($1::text[])""", wallets, creator)
    return {r["w"] for r in rows}


async def evaluate_pending(ctx: Ctx, s: dict, limit: int = 200) -> dict:
    """Evaluate captured tokens old enough to judge, once each."""
    captured = list((await ctx.redis.hgetall(K_WINNER_CAPTURE) or {}).keys())
    captured = [m.decode() if isinstance(m, bytes) else m for m in captured]
    if not captured:
        return {"evaluated": 0}
    rows = await ctx.pool.fetch("""SELECT k.mint, extract(epoch FROM k.created_at)::bigint created, k.created_slot, k.creator
        FROM tokens k WHERE k.mint = ANY($1::text[]) AND k.created_at IS NOT NULL
          AND k.created_at < now() - make_interval(secs => $2::float * 3600)
          AND NOT EXISTS (SELECT 1 FROM api_sync_status a WHERE a.source = 'winner' AND a.job = k.mint)
        ORDER BY k.created_at LIMIT $3""", captured, float(s["winner_eval_after_hours"]), limit)
    n = winners = 0
    for k in rows:
        trades = [dict(r) for r in await ctx.pool.fetch("""SELECT wallet, slot, extract(epoch FROM block_time)::bigint bt, tx_type,
                sol_amount::float sol, token_amount::float tok, COALESCE(fee_trading_sol, 0)::float fee,
                market_cap_sol::float mc, signature
            FROM wallet_transactions WHERE mint = $1 AND tx_type IN ('buy','sell') ORDER BY block_time, slot""", k["mint"])]
        ev = evaluate_token(trades, k["created"], s)
        buyers: list[dict] = []
        if ev["winner"]:
            linked = await _linked_to_creator(ctx, k["creator"], list({t["wallet"] for t in trades}))
            buyers, excl = early_buyers(trades, k["created_slot"], k["creator"], ev["peak_mc"], linked, s)
            ev["early_buyer_funnel"] = excl
            winners += 1
        ev["qualifying_buyers"] = buyers
        await ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, details, last_success_at, updated_at)
            VALUES ('winner', $1, $2, $3, now(), now()) ON CONFLICT (source, job) DO NOTHING""",
                               k["mint"], "winner" if ev["winner"] else "not_winner", ev)
        n += 1
    return {"evaluated": n, "winners": winners}


async def promote_candidates(ctx: Ctx, s: dict) -> dict:
    rows = await ctx.pool.fetch("""SELECT job AS mint, details->'qualifying_buyers' AS buyers FROM api_sync_status
        WHERE source = 'winner' AND status = 'winner' AND updated_at > now() - make_interval(days => $1)""",
                                int(s["winner_lookback_days"]))
    wins = [(r["mint"], b) for r in rows for b in (r["buyers"] or [])]
    cands = repeated(wins, int(s["winner_min_tokens"]))
    if not cands:
        return {"winners_in_lookback": len(rows), "candidates": 0}
    activity = {r["wallet"]: r for r in await ctx.pool.fetch("""SELECT wallet, count(*) n,
            GREATEST(extract(epoch FROM max(block_time) - min(block_time)) / 86400, 1) days
        FROM wallet_transactions WHERE wallet = ANY($1::text[]) AND tx_type IN ('buy','sell')
          AND block_time > now() - make_interval(days => $2) GROUP BY wallet""", list(cands), int(s["winner_lookback_days"]))}
    added = updated = bots = 0
    async with ctx.pool.acquire() as conn:
        for w, c in cands.items():
            a = activity.get(w)
            tpd = (a["n"] / float(a["days"])) if a else 0.0
            c["trades_per_day_seen"] = round(tpd, 1)
            if tpd > float(s["winner_max_trades_per_day"]):
                bots += 1
                continue
            detail = {"winner_reverse": {**c, "at": int(time.time())}}
            res = await conn.execute("""INSERT INTO wallets(address, status, discovery_source, discovery_detail, backfill_status)
                VALUES ($1, 'candidate', 'winner_reverse', $2, 'queued') ON CONFLICT (address) DO NOTHING""", w, detail)
            if res.endswith(" 1"):
                added += 1
            else:   # known wallet: attach the evidence only, never change its status or source
                await conn.execute("UPDATE wallets SET discovery_detail = discovery_detail || $2, updated_at = now() "
                                   "WHERE address = $1", w, detail)
                updated += 1
    return {"winners_in_lookback": len(rows), "candidates": len(cands), "added": added,
            "evidence_attached_to_known": updated, "skipped_high_frequency": bots}


async def main(ctx: Ctx) -> None:
    while True:
        info: dict = {}
        try:
            s = await ctx.settings.get()
            if s.get("winner_reverse_enabled", True):
                info["evaluate"] = await evaluate_pending(ctx, s)
                info["promote"] = await promote_candidates(ctx, s)
                if info["evaluate"].get("evaluated") or info["promote"].get("added"):
                    log.info("winner reverse: %s", info)
        except Exception:
            log.exception("winner reverse cycle failed")
        await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, info)
        await asyncio.sleep(120)
