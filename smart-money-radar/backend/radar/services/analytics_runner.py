"""Periodic analytics: positions -> performance -> score -> official ranking, wallet status,
relationships / clusters and token risk. Everything is recomputed from stored on-chain rows,
so results are reproducible and change automatically when new data arrives."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import time
from collections import defaultdict
from typing import Any

from ..analytics import relationships as rel
from ..analytics.backtest import peak_config
from ..analytics.behavior import classify_behavior, simulate_copy
from ..analytics.performance import COPY_DELAYS, compute_performance
from ..analytics.positions import build_positions, group_events, mark_to_market
from ..analytics.price_hits import bars_from_rows, analyse_entry
from ..analytics.risk import RiskInputs, assess, wash_share
from ..analytics.scoring import rank_official, score_wallet
from ..context import K_DIRTY_WALLETS, K_SMART, K_TRACKED, Q_PRICE_BACKFILL, Ctx
from ..db import repo

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
PERIODS = (7, 30, 90)


async def load_bars(ctx: Ctx, mint: str, start: int, end: int) -> list:
    rows = await ctx.pool.fetch("""SELECT bucket_start, source, open_sol, high_sol, low_sol, close_sol,
            high_confirmed_sol, volume_sol, trades, liquidity_sol FROM token_prices
        WHERE mint = $1 AND bucket_start BETWEEN $2 AND $3 ORDER BY bucket_start""",
                                mint, repo.ts(start - 3600), repo.ts(end))
    onchain = [dict(r) for r in rows if r["source"] == "onchain_trades"]
    gecko = [dict(r) for r in rows if r["source"] == "geckoterminal"]
    hourly = [dict(r, interval=3600) for r in rows if r["source"] == "geckoterminal_1h"]
    # prefer exact on-chain bars; fill minutes they do not cover with GeckoTerminal minute
    # candles, and hours with no minute data at all with hourly candles
    have = {r["bucket_start"] for r in onchain}
    merged = onchain + [r for r in gecko if r["bucket_start"] not in have]
    hours = {r["bucket_start"].replace(minute=0, second=0, microsecond=0) for r in merged}
    merged += [r for r in hourly if r["bucket_start"] not in hours]
    return bars_from_rows(merged)


async def recompute_wallet(ctx: Ctx, wallet: str, s: dict[str, Any], now: int, cluster_size: int,
                           bar_cache: dict) -> dict[int, Any]:
    w = await ctx.pool.fetchrow("SELECT * FROM wallets WHERE address = $1", wallet)
    if w is None:
        return {}
    rows = await ctx.pool.fetch("""SELECT * FROM wallet_transactions WHERE wallet = $1
        AND block_time >= now() - interval '100 days' ORDER BY slot""", wallet)
    signer_known = [r["user_is_signer"] for r in rows if r["user_is_signer"] is not None and r["tx_type"] in ("buy", "sell")]
    signer_share = (sum(1 for x in signer_known if x) / len(signer_known)) if signer_known else None
    by_mint = group_events([dict(r) for r in rows])
    prices = {r["mint"]: (float(r["last_price_sol"]) if r["last_price_sol"] is not None else None, r["last_trade_at"])
              for r in await ctx.pool.fetch("SELECT mint, last_price_sol, last_trade_at FROM tokens WHERE mint = ANY($1::text[])",
                                            list(by_mint))}
    cfg = peak_config(s)
    copy_delays = sorted(set(COPY_DELAYS) | {int(s["copy_delay_seconds"])})
    positions, orphans = [], 0
    need_prices = []
    for mint, evs in by_mint.items():
        ps, orph = build_positions(wallet, mint, evs, float(s["dust_fraction"]))
        orphans += len(orph)
        for p in ps:
            if p.first_buy_at and p.avg_entry_price:
                key = (mint, p.first_buy_at)
                if key not in bar_cache:
                    bar_cache[key] = await load_bars(ctx, mint, p.first_buy_at, p.first_buy_at + cfg.window_seconds)
                bars = bar_cache[key]
                p.price_metrics = analyse_entry(bars, p.first_buy_at, p.avg_entry_price, now, cfg)
                if not bars and p.buy_sol >= float(s["min_buy_sol"]):
                    need_prices.append((mint, p.first_buy_at))
            # last executed on-chain price; None when the token was never seen trading
            price, _last_trade = prices.get(mint, (None, None))
            mark_to_market(p, price, now, float(s["write_off_after_hours"]), float(s["write_off_value_fraction"]))
            # after the write-off rule, so a dead bag is a loss for the follower too
            bars = bar_cache.get((mint, p.first_buy_at)) if p.first_buy_at else None
            if p.is_closed and bars:
                p.price_metrics["copy"] = {
                    str(d): simulate_copy(p.buy_events, p.sell_events, p.status == "written_off", bars, d,
                                          float(s["copy_cost_pct"])) for d in copy_delays}
        positions.extend(ps)
    for mint, t0 in need_prices[:50]:
        await ctx.enqueue(Q_PRICE_BACKFILL, {"mint": mint, "start": t0}, dedupe_key=f"{mint}", dedupe_ttl=6 * 3600)

    await _write_positions(ctx, wallet, positions, float(s["min_buy_sol"]))
    out = {}
    backfill_ok = w["backfill_status"] == "complete"
    backfill_from = w["backfill_from"].timestamp() if w["backfill_from"] else None
    excluded = w["is_excluded"] or wallet in set(s.get("excluded_wallets") or [])
    created = {r["mint"]: (int(r["created_at"].timestamp()) if r["created_at"] else None)
               for r in await ctx.pool.fetch("SELECT mint, created_at FROM tokens WHERE mint = ANY($1::text[])",
                                             list(by_mint))}
    for period in PERIODS:
        start = now - period * 86400
        trades = [r for r in rows if r["tx_type"] in ("buy", "sell") and r["block_time"].timestamp() >= start]
        holds = [p.holding_seconds for p in positions if p.is_closed and (p.first_buy_at or p.opened_at) >= start]
        behavior = classify_behavior(trades, created, holds).as_dict()
        covered = backfill_ok and backfill_from is not None and backfill_from <= now - period * 86400 + 86400
        perf = compute_performance(positions, period, now, float(s["min_buy_sol"]), covered, orphans)
        perf.metrics["behavior"] = behavior
        res = score_wallet(perf, s, cluster_size=cluster_size, excluded=excluded, signer_share=signer_share,
                           history_read=w["backfill_status"] in ("complete", "partial"), behavior=behavior)
        await _write_perf_score(ctx, wallet, perf, res)
        out[period] = (perf, res)
    return out


async def _write_positions(ctx: Ctx, wallet: str, positions: list, min_buy: float) -> None:
    rows = []
    for p in positions:
        pm = p.price_metrics or {}
        hits = pm.get("hits") or {}
        rows.append((p.wallet, p.mint, p.cycle_no, p.status, repo.ts(p.opened_at), repo.ts(p.closed_at),
                     repo.ts(p.first_buy_at), repo.ts(p.last_buy_at), repo.ts(p.first_sell_at), p.n_buys, p.n_sells,
                     p.buy_sol, p.sell_sol, p.fees_sol, p.tokens_bought, p.tokens_sold, p.tokens_in, p.tokens_out,
                     p.remaining_tokens, p.cost_basis_remaining, p.realized_pnl_sol, p.unrealized_pnl_sol, p.roi,
                     p.avg_entry_price, p.avg_exit_price, p.entry_mc_sol, p.exit_mc_sol, p.current_price_sol,
                     pm.get("peak_price"), pm.get("peak_multiple"), repo.ts(pm.get("peak_at")), pm.get("peak_validated"),
                     hits.get(2), hits.get(3), hits.get(5), hits.get(10), p.realized_multiple,
                     pm.get("horizons") or {}, p.holding_seconds, p.qualifying(min_buy), p.verification, p.flags,
                     p.signatures[:200]))
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("DELETE FROM wallet_positions WHERE wallet = $1", wallet)
            if rows:
                await conn.executemany("""INSERT INTO wallet_positions(wallet, mint, cycle_no, status, opened_at, closed_at,
                    first_buy_at, last_buy_at, first_sell_at, n_buys, n_sells, buy_sol, sell_sol, fees_sol, tokens_bought,
                    tokens_sold, tokens_in, tokens_out, remaining_tokens, cost_basis_remaining, realized_pnl_sol,
                    unrealized_pnl_sol, roi, avg_entry_price_sol, avg_exit_price_sol, entry_mc_sol, exit_mc_sol,
                    current_price_sol, peak_price_sol, peak_multiple, peak_at, peak_validated, hit_2x, hit_3x, hit_5x,
                    hit_10x, realized_multiple, price_roi, holding_seconds, qualifying, verification, flags, signatures)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,
                            $26,$27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37,$38,$39,$40,$41,$42,$43)""", rows)


async def _write_perf_score(ctx: Ctx, wallet: str, perf, res) -> None:
    f = perf
    await ctx.pool.execute("""INSERT INTO wallet_performance(wallet, period_days, computed_at, positions_total,
            positions_qualifying, closed_qualifying, open_qualifying, wins, losses, win_rate, avg_buy_sol, median_buy_sol,
            largest_buy_sol, total_buy_sol, avg_position_sol, realized_pnl_sol, unrealized_pnl_sol, total_pnl_sol, roi,
            profit_factor, avg_win_sol, avg_loss_sol, max_drawdown_sol, largest_win_sol, largest_loss_sol, hit_rate_2x,
            hit_rate_3x, hit_rate_5x, hit_rate_10x, avg_realized_roi, median_realized_roi, avg_mfe, avg_holding_seconds,
            pnl_ex_best_sol, top_win_share, verified_share, coverage_complete, last_active_at, metrics)
        VALUES ($1,$2,now(),$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19,$20,$21,$22,$23,$24,$25,$26,
                $27,$28,$29,$30,$31,$32,$33,$34,$35,$36,$37,$38)
        ON CONFLICT (wallet, period_days) DO UPDATE SET computed_at = now(), positions_total = EXCLUDED.positions_total,
            positions_qualifying = EXCLUDED.positions_qualifying, closed_qualifying = EXCLUDED.closed_qualifying,
            open_qualifying = EXCLUDED.open_qualifying, wins = EXCLUDED.wins, losses = EXCLUDED.losses,
            win_rate = EXCLUDED.win_rate, avg_buy_sol = EXCLUDED.avg_buy_sol, median_buy_sol = EXCLUDED.median_buy_sol,
            largest_buy_sol = EXCLUDED.largest_buy_sol, total_buy_sol = EXCLUDED.total_buy_sol,
            avg_position_sol = EXCLUDED.avg_position_sol, realized_pnl_sol = EXCLUDED.realized_pnl_sol,
            unrealized_pnl_sol = EXCLUDED.unrealized_pnl_sol, total_pnl_sol = EXCLUDED.total_pnl_sol, roi = EXCLUDED.roi,
            profit_factor = EXCLUDED.profit_factor, avg_win_sol = EXCLUDED.avg_win_sol, avg_loss_sol = EXCLUDED.avg_loss_sol,
            max_drawdown_sol = EXCLUDED.max_drawdown_sol, largest_win_sol = EXCLUDED.largest_win_sol,
            largest_loss_sol = EXCLUDED.largest_loss_sol, hit_rate_2x = EXCLUDED.hit_rate_2x,
            hit_rate_3x = EXCLUDED.hit_rate_3x, hit_rate_5x = EXCLUDED.hit_rate_5x, hit_rate_10x = EXCLUDED.hit_rate_10x,
            avg_realized_roi = EXCLUDED.avg_realized_roi, median_realized_roi = EXCLUDED.median_realized_roi,
            avg_mfe = EXCLUDED.avg_mfe, avg_holding_seconds = EXCLUDED.avg_holding_seconds,
            pnl_ex_best_sol = EXCLUDED.pnl_ex_best_sol, top_win_share = EXCLUDED.top_win_share,
            verified_share = EXCLUDED.verified_share, coverage_complete = EXCLUDED.coverage_complete,
            last_active_at = EXCLUDED.last_active_at, metrics = EXCLUDED.metrics""",
        wallet, f.period_days, f.positions_total, f.positions_qualifying, f.closed_qualifying, f.open_qualifying, f.wins,
        f.losses, f.win_rate, f.avg_buy_sol, f.median_buy_sol, f.largest_buy_sol, f.total_buy_sol, f.avg_position_sol,
        f.realized_pnl_sol, f.unrealized_pnl_sol, f.total_pnl_sol, f.roi, f.profit_factor, f.avg_win_sol, f.avg_loss_sol,
        f.max_drawdown_sol, f.largest_win_sol, f.largest_loss_sol, f.hit_rate_2x, f.hit_rate_3x, f.hit_rate_5x,
        f.hit_rate_10x, f.avg_realized_roi, f.median_realized_roi, f.avg_mfe, f.avg_holding_seconds, f.pnl_ex_best_sol,
        f.top_win_share, f.verified_share, f.coverage_complete, repo.ts(f.last_active_at), f.metrics)
    await ctx.pool.execute("""INSERT INTO smart_money_scores(wallet, period_days, computed_at, score, category, confidence,
            qualifies, official_rank, components, reasons) VALUES ($1,$2,now(),$3,$4,$5,$6,NULL,$7,$8)
        ON CONFLICT (wallet, period_days) DO UPDATE SET computed_at = now(), score = EXCLUDED.score,
            category = EXCLUDED.category, confidence = EXCLUDED.confidence, qualifies = EXCLUDED.qualifies,
            components = EXCLUDED.components, reasons = EXCLUDED.reasons""",
        wallet, f.period_days, res.score, res.category, res.confidence, res.qualifies, res.components, res.reasons)


async def rank_and_status(ctx: Ctx, s: dict[str, Any]) -> None:
    from ..analytics.scoring import ScoreResult
    now = dt.datetime.now(UTC)
    for period in PERIODS:
        rows = await ctx.pool.fetch("SELECT * FROM smart_money_scores WHERE period_days = $1", period)
        results = {r["wallet"]: ScoreResult(float(r["score"]), r["category"], r["confidence"], r["qualifies"],
                                            r["components"], r["reasons"]) for r in rows}
        clusters = {r["address"]: r["cluster_id"] for r in await ctx.pool.fetch(
            "SELECT address, cluster_id FROM wallets WHERE address = ANY($1::text[])", list(results))}
        ranked = rank_official(results, limit=100000, clusters=clusters)
        async with ctx.pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("UPDATE smart_money_scores SET official_rank = NULL WHERE period_days = $1", period)
                await conn.executemany("UPDATE smart_money_scores SET official_rank = $3 WHERE wallet = $1 AND period_days = $2",
                                       [(w, period, i + 1) for i, (w, _) in enumerate(ranked)])
                if ranked:
                    await conn.executemany("""INSERT INTO top_wallet_snapshots(period_days, computed_at, rank, wallet, score)
                        VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING""",
                                           [(period, now, i + 1, w, r.score) for i, (w, r) in enumerate(ranked[:10])])
    # wallet status from the analysis period
    period = int(s["analysis_period_days"])
    min_closed = int(s["min_closed_positions_official"])
    rows = await ctx.pool.fetch("""SELECT w.address, w.status, w.backfill_status, sc.qualifies, sc.confidence, sc.score,
            sc.components, p.closed_qualifying, p.positions_qualifying FROM wallets w
        LEFT JOIN smart_money_scores sc ON sc.wallet = w.address AND sc.period_days = $1
        LEFT JOIN wallet_performance p ON p.wallet = w.address AND p.period_days = $1
        WHERE w.status IN ('candidate','tracked','smart')""", period)
    updates = []
    for r in rows:
        if r["backfill_status"] in ("queued", "running", "none"):
            continue
        checks = (r["components"] or {}).get("checks") or {}
        # signal wallets must also be human-like and copyable when those rules are on
        copy_ok = checks.get("human_like", True) and checks.get("copyable", True)
        if (r["qualifies"] and r["confidence"] in ("HIGH", "MEDIUM") and copy_ok
                and float(r["score"] or 0) >= float(s["signal_min_wallet_score"])):
            new = "smart"
        elif (r["closed_qualifying"] or 0) < min_closed and (r["positions_qualifying"] or 0) > 0:
            new = "tracked"
        elif r["qualifies"]:
            new = "tracked"
        else:
            new = "observed"
        if new != r["status"]:
            updates.append((r["address"], new))
    if updates:
        await ctx.pool.executemany("UPDATE wallets SET status = $2, updated_at = now() WHERE address = $1", updates)
    smart = [r["address"] for r in await ctx.pool.fetch("SELECT address FROM wallets WHERE status = 'smart'")]
    tracked = [r["address"] for r in await ctx.pool.fetch(
        "SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")]
    pipe = ctx.redis.pipeline()
    pipe.delete(K_SMART, K_TRACKED)
    if smart:
        pipe.sadd(K_SMART, *smart)
    if tracked:
        pipe.sadd(K_TRACKED, *tracked)
    await pipe.execute()


async def relationships_pass(ctx: Ctx, s: dict[str, Any]) -> None:
    wallets = [r["address"] for r in await ctx.pool.fetch(
        "SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart')")]
    if not wallets:
        return
    funders = {r["address"]: (r["funding_source"], r["funding_signature"]) for r in await ctx.pool.fetch(
        "SELECT address, funding_source, funding_signature FROM wallets WHERE address = ANY($1::text[]) "
        "AND funding_source IS NOT NULL", wallets)}
    edges = rel.funding_edges(funders)
    tr = [(r["source"], r["destination"], "sol", r["lamports"] / 1e9, r["signature"]) for r in await ctx.pool.fetch(
        "SELECT * FROM sol_transfers WHERE source = ANY($1::text[]) AND destination = ANY($1::text[])", wallets)]
    tr += [(r["wallet"], r["counterparty"], "token", float(r["token_amount"]), r["signature"]) for r in await ctx.pool.fetch(
        """SELECT wallet, counterparty, token_amount, signature FROM wallet_transactions WHERE tx_type = 'transfer_out'
           AND wallet = ANY($1::text[]) AND counterparty = ANY($1::text[])""", wallets)]
    edges += rel.transfer_edges(tr)
    buys: dict[str, list] = defaultdict(list)
    for r in await ctx.pool.fetch("""SELECT wallet, mint, min(slot) AS slot FROM wallet_transactions
            WHERE tx_type = 'buy' AND wallet = ANY($1::text[]) GROUP BY wallet, mint""", wallets):
        buys[r["wallet"]].append((r["mint"], r["slot"]))
    edges += rel.sync_buy_edges(buys)
    creators = {r["creator"] for r in await ctx.pool.fetch(
        "SELECT DISTINCT creator FROM tokens WHERE creator IS NOT NULL AND creator = ANY($1::text[]) "
        "UNION SELECT DISTINCT t.creator FROM tokens t JOIN wallets w ON w.funding_source = t.creator "
        "WHERE w.address = ANY($1::text[])", wallets)}
    links: dict[str, set] = defaultdict(set)
    for w in wallets:
        if w in creators:
            links[w].add(w)
        f = funders.get(w, (None,))[0]
        if f in creators:
            links[w].add(f)
    edges += rel.creator_edges(links)
    assign = rel.clusters(wallets, edges, float(s["relationship_cluster_min_strength"]))
    sizes = rel.cluster_sizes(assign)
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            await conn.executemany("""INSERT INTO wallet_relationships(wallet_a, wallet_b, relation_type, strength, evidence)
                VALUES ($1,$2,$3,$4,$5) ON CONFLICT (wallet_a, wallet_b, relation_type) DO UPDATE SET
                strength = EXCLUDED.strength, evidence = EXCLUDED.evidence, last_seen_at = now()""",
                                   [(e.a, e.b, e.relation, e.strength, e.evidence) for e in edges])
            await conn.executemany("UPDATE wallets SET cluster_id = $2, cluster_size = $3, is_token_creator = $4 WHERE address = $1",
                                   [(w, assign.get(w, w), sizes.get(w, 1), w in creators) for w in wallets])
    log.info("relationships: %d edges, %d clusters", len(edges), len(set(assign.values())))


async def risk_for_token(ctx: Ctx, mint: str) -> dict:
    t = await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", mint)
    if t is None:
        return {}
    i = RiskInputs(liquidity_sol=float(t["last_liquidity_sol"]) if t["last_liquidity_sol"] is not None else None,
                   on_curve=not t["curve_complete"])
    supply = float(t["total_supply_raw"] or 1e15) / 10 ** (t["decimals"] or 6)
    # mint / freeze authority (on-chain mint account)
    if t["authorities_checked_at"] is None:
        try:
            acc = await ctx.rpc.get_account_info(mint, encoding="jsonParsed")
            info = ((acc or {}).get("data") or {}).get("parsed", {}).get("info", {}) if acc else {}
            if info:
                await ctx.pool.execute("""UPDATE tokens SET mint_authority = $2, freeze_authority = $3,
                    authorities_checked_at = now(), decimals = COALESCE($4, decimals),
                    total_supply_raw = COALESCE($5::numeric, total_supply_raw) WHERE mint = $1""",
                                       mint, info.get("mintAuthority"), info.get("freezeAuthority"), info.get("decimals"),
                                       info.get("supply"))
                i.mint_authority, i.freeze_authority = info.get("mintAuthority"), info.get("freezeAuthority")
        except Exception as e:
            log.debug("mint read failed %s: %s", mint, e)
    else:
        i.mint_authority, i.freeze_authority = t["mint_authority"], t["freeze_authority"]
    creator = t["creator"]
    if creator:
        try:
            accs = await ctx.rpc.get_token_accounts_by_owner(creator, mint)
            amt = sum(float(a["account"]["data"]["parsed"]["info"]["tokenAmount"]["uiAmount"] or 0) for a in accs)
            i.dev_holding_pct = 100 * amt / supply
        except Exception:
            pass
        dev = await ctx.pool.fetchrow("""SELECT COALESCE(sum(token_amount) FILTER (WHERE tx_type='buy'),0) AS b,
                COALESCE(sum(token_amount) FILTER (WHERE tx_type='sell'),0) AS s, count(*) FILTER (WHERE tx_type='sell') AS n
            FROM wallet_transactions WHERE mint = $1 AND wallet = $2""", mint, creator)
        if dev and float(dev["b"]) > 0:
            i.dev_sold_share = float(dev["s"]) / float(dev["b"])
            i.dev_sells = int(dev["n"])
    if t["created_slot"]:
        b = await ctx.pool.fetchrow("""SELECT count(DISTINCT wallet) AS n, COALESCE(sum(token_amount),0) AS tok
            FROM wallet_transactions WHERE mint = $1 AND slot = $2 AND tx_type = 'buy' AND wallet <> COALESCE($3,'')""",
                                    mint, t["created_slot"], creator)
        i.bundle_wallets = int(b["n"])
        i.bundle_supply_pct = 100 * float(b["tok"]) / supply
    if ctx.cfg.has_helius:
        try:
            largest = await ctx.rpc.get_token_largest_accounts(mint)
            accts = [a["address"] for a in largest[:20]]
            infos = await ctx.rpc.get_multiple_accounts(accts, encoding="jsonParsed")
            excl = {t["bonding_curve"], t["amm_pool"]}
            owners = []
            for a, inf in zip(largest[:20], infos):
                owner = ((inf or {}).get("data") or {}).get("parsed", {}).get("info", {}).get("owner")
                owners.append((owner, float(a.get("uiAmount") or 0), a["address"]))
            top = [x for x in owners if x[0] not in excl][:10]
            i.top10_pct = 100 * sum(x[1] for x in top) / supply
            i.holders_source = "rpc_largest_accounts"
            snap = dt.datetime.now(UTC)
            await ctx.pool.executemany("""INSERT INTO token_holders(mint, snapshot_at, rank, owner, token_account, amount,
                    pct_supply, is_curve_or_pool, is_creator, source) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,'rpc_largest_accounts')
                ON CONFLICT DO NOTHING""", [(mint, snap, k + 1, o, acct, amt, 100 * amt / supply, o in excl, o == creator)
                                            for k, (o, amt, acct) in enumerate(owners)])
        except Exception as e:
            log.debug("holders %s: %s", mint, e)
    await _holder_structure(ctx, t, i, supply)
    early = await ctx.pool.fetch("""SELECT DISTINCT ON (t.wallet) t.wallet, w.funding_source FROM wallet_transactions t
        LEFT JOIN wallets w ON w.address = t.wallet WHERE t.mint = $1 AND t.tx_type = 'buy'
        ORDER BY t.wallet, t.slot LIMIT 40""", mint)
    fs = [r["funding_source"] for r in early if r["funding_source"]]
    if len(fs) >= 5:
        common = max(fs.count(f) for f in set(fs))
        i.early_buyers_common_funder_share = common / len(fs) if common > 1 else 0.0
    v = await ctx.pool.fetchval("""SELECT COALESCE(sum(volume_sol),0) FROM token_prices WHERE mint = $1
        AND source = 'onchain_trades' AND bucket_start >= now() - interval '5 minutes'""", mint)
    i.volume_5m_sol = float(v) if v is not None else None
    tr = await ctx.pool.fetch("""SELECT wallet, extract(epoch from block_time)::bigint AS ts, tx_type, sol_amount
        FROM wallet_transactions WHERE mint = $1 AND tx_type IN ('buy','sell')
        AND block_time >= now() - interval '2 hours'""", mint)
    i.wash_volume_share = wash_share([(r["wallet"], r["ts"], r["tx_type"], float(r["sol_amount"])) for r in tr])
    if t["curve_complete"]:
        try:
            from ..sources.jupiter import sell_quote
            q = await sell_quote(ctx.http, mint, float(t["last_price_sol"] or 0))
            if q is not None:
                i.sell_route, i.sell_price_impact = q
        except Exception:
            pass
    r = assess(i)
    await ctx.pool.execute("INSERT INTO token_risk_reports(mint, computed_at, risk_level, risk_points, checks) "
                           "VALUES ($1, now(), $2, $3, $4)", mint, r.level, int(round(r.points)),
                           {"checks": r.checks, "disclaimer": r.disclaimer, "inputs": i.__dict__})
    return {"level": r.level, "points": r.points}


async def holder_snapshot(ctx: Ctx, t, supply: float) -> dict:
    """Top holders: RPC (Helius) snapshot when recent, else the trade-flow estimate."""
    from ..analytics.holders import Flow, estimate_holders, flow_metrics
    excl = {x for x in (t["bonding_curve"], t["amm_pool"]) if x}
    snap = await ctx.pool.fetch("""SELECT owner AS wallet, amount AS tokens, pct_supply AS pct FROM token_holders
        WHERE mint = $1 AND NOT is_curve_or_pool AND snapshot_at = (SELECT max(snapshot_at) FROM token_holders
        WHERE mint = $1 AND snapshot_at > now() - interval '30 minutes') ORDER BY rank""", t["mint"])
    dec = 10 ** (t["decimals"] or 6)
    flows = [Flow(r["wallet"], float(r["bought_raw"]) / dec, float(r["sold_raw"]) / dec, float(r["buy_sol"]),
                  float(r["sell_sol"]), float(r["fees_sol"]), r["n_buys"], r["n_sells"])
             for r in await ctx.pool.fetch("SELECT * FROM token_wallet_flows WHERE mint = $1", t["mint"])]
    metrics = flow_metrics(flows) if flows else {}
    if snap:
        holders = [{"wallet": r["wallet"], "tokens": float(r["tokens"]), "pct": float(r["pct"] or 0)} for r in snap]
        source, complete = "rpc_largest_accounts", True
    else:
        holders = estimate_holders(flows, supply, exclude=excl)
        source, complete = "trade_estimate", bool(t["stream_from_creation"])
    return {"holders": holders, "source": source, "complete": complete, "flow_metrics": metrics}


async def _holder_structure(ctx: Ctx, t, i: RiskInputs, supply: float) -> None:
    from ..analytics.holders import linked_groups, unnatural_volume
    from .holder_probe import is_fresh, probe_wallets
    s = await ctx.settings.get()
    i.holder_alert_pct = float(s["holder_alert_pct"])
    i.fresh_hours = float(s["fresh_wallet_hours"])
    hs = await holder_snapshot(ctx, t, supply)
    m = hs["flow_metrics"]
    mc = float(t["last_mc_sol"]) if t["last_mc_sol"] else None
    if m.get("buyers"):
        i.unnatural_volume, i.unnatural_reasons = unnatural_volume(m, mc)
        i.fees_sol = m.get("fees_sol")
        i.volume_to_mc = (m["volume_sol"] / mc) if mc else None
    if not hs["holders"] or not hs["complete"]:
        return   # an incomplete estimate is never used to judge concentration
    top = hs["holders"][:10]
    i.max_holder_pct = top[0]["pct"]
    i.max_holder_wallet = top[0]["wallet"]
    if i.top10_pct is None:
        i.top10_pct = sum(h["pct"] or 0 for h in top)
        i.holders_source = hs["source"]
    ages = await probe_wallets(ctx, [h["wallet"] for h in top])
    known = [h for h in top if h["wallet"] in ages]
    if len(known) >= max(3, len(top) // 2):
        i.fresh_top_holders = sum(1 for h in known if is_fresh(ages[h["wallet"]], i.fresh_hours))
        funders = {h["wallet"]: ages[h["wallet"]].get("funder") for h in known}
        fan = {r["funder"]: r["n"] for r in await ctx.pool.fetch(
            "SELECT funder, count(*) AS n FROM wallet_age WHERE funder = ANY($1::text[]) GROUP BY funder",
            [f for f in funders.values() if f])}
        groups = linked_groups(known, funders, funder_children=fan)
        i.linked_groups = len(groups)
        i.linked_holders_pct = sum(g["pct"] for g in groups)


async def risk_pass(ctx: Ctx) -> None:
    mints = [r["mint"] for r in await ctx.pool.fetch("""SELECT DISTINCT t.mint FROM wallet_transactions t
        JOIN wallets w ON w.address = t.wallet AND w.status = 'smart'
        WHERE t.block_time >= now() - interval '24 hours' AND t.tx_type = 'buy'
        AND NOT EXISTS (SELECT 1 FROM token_risk_reports r WHERE r.mint = t.mint AND r.computed_at > now() - interval '3 minutes')
        LIMIT 40""")]
    for m in mints:
        try:
            await risk_for_token(ctx, m)
        except Exception:
            log.exception("risk %s failed", m)


async def retention_pass(ctx: Ctx, s: dict[str, Any]) -> dict:
    """Stream rows of wallets that are not tracked are kept `stream_retention_days` (they only
    serve discovery / token risk). Tracked wallets' history and price bars are kept."""
    days = int(s["stream_retention_days"])
    a = await ctx.pool.execute("""DELETE FROM wallet_transactions t WHERE t.block_time < now() - make_interval(days => $1)
        AND NOT EXISTS (SELECT 1 FROM wallets w WHERE w.address = t.wallet AND w.status IN ('candidate','tracked','smart'))""",
                               days)
    b = await ctx.pool.execute("""DELETE FROM raw_transactions r WHERE r.block_time < now() - make_interval(days => $1)
        AND NOT EXISTS (SELECT 1 FROM wallet_transactions t WHERE t.signature = r.signature)""", days)
    c = await ctx.pool.execute("DELETE FROM token_wallet_flows WHERE last_ts < now() - make_interval(days => $1)", days)
    return {"trades_deleted": a, "raw_deleted": b, "flows_deleted": c}


async def run_cycle(ctx: Ctx, full: bool) -> dict:
    s = await ctx.settings.get()
    now = int(time.time())
    if full:
        wallets = [r["address"] for r in await ctx.pool.fetch(
            "SELECT address FROM wallets WHERE status IN ('candidate','tracked','smart') "
            "OR (backfill_status IN ('complete','partial') AND NOT is_excluded)")]
        await ctx.redis.delete(K_DIRTY_WALLETS)
    else:
        wallets = list(await ctx.redis.spop(K_DIRTY_WALLETS, 500) or [])
    sizes = {r["address"]: r["cluster_size"] for r in await ctx.pool.fetch(
        "SELECT address, cluster_size FROM wallets WHERE address = ANY($1::text[])", wallets)}
    bar_cache: dict = {}
    for w in wallets:
        try:
            await recompute_wallet(ctx, w, s, now, int(sizes.get(w) or 1), bar_cache)
        except Exception:
            log.exception("recompute %s failed", w)
    if wallets:
        await rank_and_status(ctx, s)
        await ctx.publish("rankings_updated", {"wallets": len(wallets)})
    return {"wallets": len(wallets)}


async def main(ctx: Ctx) -> None:
    n = 0
    while True:
        t0 = time.time()
        try:
            full = n % 30 == 0           # full recompute every ~30 cycles (~1 h)
            if n % 10 == 0:
                await relationships_pass(ctx, await ctx.settings.get())
            if n % 30 == 15:
                log.info("retention: %s", await retention_pass(ctx, await ctx.settings.get()))
            res = await run_cycle(ctx, full)
            await risk_pass(ctx)
            await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, {**res, "full": full, "cycle": n,
                                                                         "seconds": round(time.time() - t0, 1)})
        except Exception:
            log.exception("analytics cycle failed")
        n += 1
        await asyncio.sleep(max(5.0, 120 - (time.time() - t0)))
