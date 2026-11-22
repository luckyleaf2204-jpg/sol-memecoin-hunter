"""Signal engine + alert dispatcher.

Consumes trades of tracked wallets (stream + verifier), re-evaluates the co-buy signal of the
token, stores signals, and sends alerts with de-duplication:
  * one alert per (token, level) — `alerts.dedupe_key` is unique,
  * a higher level (upgrade) always alerts,
  * the same level alerts again only on an important change: >= alert_resend_new_buyers new
    independent buyers or smart-money buy SOL x alert_resend_flow_multiple, after the cooldown,
  * every decision (sent / failed / suppressed / dashboard_only) is stored in `alerts`.
"""
from __future__ import annotations

import asyncio
import datetime as dt
import logging
import os
import time

from ..analytics.positions import build_positions, group_events
from ..analytics.signals import LEVEL_RANK, SmartBuy, SmartSell, evaluate, exit_progress
from ..context import S_SMART_TRADES, Ctx
from ..db import repo
from . import telegram
from .analytics_runner import risk_for_token

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
GROUP = "signals"


async def smart_profiles(ctx: Ctx, period: int) -> dict[str, dict]:
    rows = await ctx.pool.fetch("""SELECT w.address, w.cluster_id, w.funding_source, sc.score, p.win_rate
        FROM wallets w JOIN smart_money_scores sc ON sc.wallet = w.address AND sc.period_days = $1
        LEFT JOIN wallet_performance p ON p.wallet = w.address AND p.period_days = $1
        WHERE w.status = 'smart' AND NOT w.is_excluded""", period)
    return {r["address"]: {"cluster": r["cluster_id"] or r["address"], "score": float(r["score"]),
                           "win_rate": float(r["win_rate"]) if r["win_rate"] is not None else None,
                           "funder": r["funding_source"]} for r in rows}


async def evaluate_token(ctx: Ctx, mint: str, s: dict, profiles: dict[str, dict], now: int) -> dict | None:
    window = int(float(s["signal_window_minutes"]) * 60)
    smart = list(profiles)
    if not smart:
        return None
    token = await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", mint)
    creator = token["creator"] if token else None
    rows = await ctx.pool.fetch("""SELECT * FROM wallet_transactions WHERE mint = $1 AND wallet = ANY($2::text[])
        AND block_time >= now() - interval '7 days' ORDER BY slot""", mint, smart)
    by_wallet: dict[str, list] = {}
    for r in rows:
        by_wallet.setdefault(r["wallet"], []).append(dict(r))
    buys: list[SmartBuy] = []
    sells: list[SmartSell] = []
    excluded_creator_links = []
    for w, lst in by_wallet.items():
        prof = profiles[w]
        if creator and (w == creator or prof.get("funder") == creator):
            excluded_creator_links.append(w)
            continue
        positions, _ = build_positions(w, mint, group_events(lst)[mint], float(s["dust_fraction"]))
        cur = positions[-1] if positions and not positions[-1].is_closed else None
        for r in lst:
            t = int(r["block_time"].timestamp())
            if t < now - window:
                continue
            if r["tx_type"] == "buy":
                buys.append(SmartBuy(w, t, float(r["sol_amount"]), cur.buy_sol if cur else float(r["sol_amount"]),
                                     float(r["effective_price_sol"]) if r["effective_price_sol"] else None,
                                     r["verification_status"], prof["cluster"], prof["score"], prof["win_rate"],
                                     r["signature"],
                                     float(r["market_cap_sol"]) if r["market_cap_sol"] is not None else None))
            elif r["tx_type"] == "sell":
                sells.append(SmartSell(w, t, float(r["sol_amount"])))
    if not buys:
        return None
    risk = await ctx.pool.fetchrow("SELECT risk_level, computed_at FROM token_risk_reports WHERE mint = $1 "
                                   "ORDER BY computed_at DESC LIMIT 1", mint)
    if risk is None or risk["computed_at"] < dt.datetime.now(UTC) - dt.timedelta(minutes=3):
        try:
            risk_level = (await risk_for_token(ctx, mint)).get("level")
        except Exception:
            risk_level = risk["risk_level"] if risk else None
    else:
        risk_level = risk["risk_level"]
    price = float(token["last_price_sol"]) if token and token["last_price_sol"] else None
    res = evaluate(buys, sells, now, s, price, risk_level)
    if excluded_creator_links:
        res.downgrade_reasons.append(f"{len(excluded_creator_links)} smart wallet(s) linked to the token creator ignored")
    if not res.level:
        return None
    return {"mint": mint, "level": res.level, "raw_buyers": res.raw_buyers, "independent_buyers": res.independent_buyers,
            "total_buy_sol": res.total_buy_sol, "total_sell_sol": res.total_sell_sol, "net_flow_sol": res.net_flow_sol,
            "window_start": repo.ts(res.window_start), "window_end": repo.ts(res.window_end),
            "first_entry_price_sol": res.first_entry_price, "price_at_detection_sol": price,
            "runup_multiple": res.runup, "market_cap_sol": float(token["last_mc_sol"]) if token and token["last_mc_sol"] else None,
            "liquidity_sol": float(token["last_liquidity_sol"]) if token and token["last_liquidity_sol"] else None,
            "risk_level": risk_level, "avg_buyer_score": res.avg_buyer_score, "verification": res.verification,
            "buyers": res.buyers, "downgrade_reasons": res.downgrade_reasons}


async def store_and_alert(ctx: Ctx, sig: dict, s: dict) -> None:
    prev = await ctx.pool.fetchrow("""SELECT * FROM smart_money_signals WHERE mint = $1 AND status = 'active'
        AND detected_at > now() - interval '6 hours' ORDER BY detected_at DESC LIMIT 1""", sig["mint"])
    upgrade = prev is None or LEVEL_RANK[sig["level"]] > LEVEL_RANK[prev["level"]]
    if prev is not None and not upgrade:
        await ctx.pool.execute("""UPDATE smart_money_signals SET raw_buyers = $2, independent_buyers = $3,
            total_buy_sol = $4, total_sell_sol = $5, net_flow_sol = $6, buyers = $7, window_end = $8,
            verification = $9, downgrade_reasons = $10 WHERE id = $1""", prev["id"], sig["raw_buyers"],
                               sig["independent_buyers"], sig["total_buy_sol"], sig["total_sell_sol"], sig["net_flow_sol"],
                               sig["buyers"], sig["window_end"], sig["verification"], sig["downgrade_reasons"])
        sig_id = prev["id"]
    else:
        if prev is not None:
            await ctx.pool.execute("UPDATE smart_money_signals SET status = 'superseded' WHERE id = $1", prev["id"])
        sig_id = await ctx.pool.fetchval("""INSERT INTO smart_money_signals(mint, level, window_start, window_end,
                raw_buyers, independent_buyers, total_buy_sol, total_sell_sol, net_flow_sol, first_entry_price_sol,
                price_at_detection_sol, runup_multiple, market_cap_sol, liquidity_sol, risk_level, avg_buyer_score,
                verification, buyers, downgrade_reasons)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) RETURNING id""",
            sig["mint"], sig["level"], sig["window_start"], sig["window_end"], sig["raw_buyers"], sig["independent_buyers"],
            sig["total_buy_sol"], sig["total_sell_sol"], sig["net_flow_sol"], sig["first_entry_price_sol"],
            sig["price_at_detection_sol"], sig["runup_multiple"], sig["market_cap_sol"], sig["liquidity_sol"],
            sig["risk_level"], sig["avg_buyer_score"], sig["verification"], sig["buyers"], sig["downgrade_reasons"])
        await ctx.publish("signal", {"id": sig_id, "mint": sig["mint"], "level": sig["level"],
                                     "independent_buyers": sig["independent_buyers"], "total_buy_sol": sig["total_buy_sol"]})

    # ---- alert decision ----
    if LEVEL_RANK[sig["level"]] < LEVEL_RANK.get(s["alert_min_level"], 1):
        return
    last = await ctx.pool.fetchrow("""SELECT a.*, sg.independent_buyers AS ib, sg.total_buy_sol AS tb
        FROM alerts a LEFT JOIN smart_money_signals sg ON sg.id = a.signal_id
        WHERE a.mint = $1 AND a.level = $2 AND a.status IN ('sent','dashboard_only')
        ORDER BY a.created_at DESC LIMIT 1""", sig["mint"], sig["level"])
    if last is None:
        reason = "upgrade" if upgrade and prev is not None else "new"
        key = f"{sig['mint']}:{sig['level']}"
    else:
        cooldown_ok = last["created_at"] < dt.datetime.now(UTC) - dt.timedelta(minutes=float(s["alert_cooldown_minutes"]))
        last_ib = int(last["ib"] or 0)
        last_tb = float(last["tb"] or 0)
        more = sig["independent_buyers"] - last_ib >= int(s["alert_resend_new_buyers"])
        bigger = last_tb > 0 and sig["total_buy_sol"] >= last_tb * float(s["alert_resend_flow_multiple"])
        if not (cooldown_ok and (more or bigger)):
            return
        reason = "important_change"
        key = f"{sig['mint']}:{sig['level']}:chg:{sig['independent_buyers']}:{int(sig['total_buy_sol'])}"
    token = dict(await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", sig["mint"]) or {"mint": sig["mint"]})
    sig_row = dict(await ctx.pool.fetchrow("SELECT * FROM smart_money_signals WHERE id = $1", sig_id))
    sig_row["buyers"] = sig["buyers"]
    text = telegram.format_alert(sig_row, token, await ctx.sol_usd(), os.environ.get("PUBLIC_URL"))
    await _deliver(ctx, s, sig_id, sig["mint"], sig["level"], reason, key, text)


async def bot_handler(ctx: Ctx, cmd: str) -> str:
    s = await ctx.settings.get()
    if cmd == "/top":
        rows = await ctx.pool.fetch("""SELECT sc.wallet, sc.score, p.win_rate, p.median_buy_sol FROM smart_money_scores sc
            JOIN wallet_performance p ON p.wallet = sc.wallet AND p.period_days = sc.period_days
            WHERE sc.period_days = $1 AND sc.official_rank IS NOT NULL ORDER BY sc.official_rank LIMIT 10""",
                                    int(s["analysis_period_days"]))
        if not rows:
            return "Chưa có ví nào đủ điều kiện Top 10 chính thức."
        return "\n".join(f"{i}. <code>{r['wallet']}</code> · {telegram.fmt_num(float(r['score']), 1)} điểm · "
                         f"WR {telegram.fmt_num(100 * float(r['win_rate'] or 0))}% · median "
                         f"{telegram.fmt_sol(float(r['median_buy_sol'] or 0))}" for i, r in enumerate(rows, 1))
    if cmd == "/status":
        n = await ctx.pool.fetchrow("""SELECT count(*) FILTER (WHERE status='smart') AS smart,
            count(*) FILTER (WHERE status IN ('candidate','tracked','smart')) AS tracked FROM wallets""")
        a = await ctx.pool.fetchval("SELECT count(*) FROM alerts WHERE created_at > now() - interval '24 hours'")
        return f"Ví theo dõi: {n['tracked']} · Smart Money: {n['smart']} · Cảnh báo 24h: {a}"
    return "Lệnh: /top, /status"


EXIT_LEVELS = (("EXIT50", 0.5), ("EXIT90", 0.9))


async def check_exits(ctx: Ctx, mint: str, s: dict) -> None:
    """Exit / distribution alert: the wallets of an active signal are selling what they bought."""
    if not s.get("exit_alert_enabled", True):
        return
    sig = await ctx.pool.fetchrow("""SELECT * FROM smart_money_signals WHERE mint = $1 AND status = 'active'
        AND detected_at > now() - interval '48 hours' ORDER BY detected_at DESC LIMIT 1""", mint)
    if sig is None:
        return
    wallets = [b["wallet"] for b in sig["buyers"]]
    rows = await ctx.pool.fetch("""SELECT wallet, extract(epoch from block_time)::bigint AS ts, tx_type, token_amount,
            sol_amount FROM wallet_transactions WHERE mint = $1 AND wallet = ANY($2::text[])
            AND tx_type IN ('buy','sell') AND verification_status <> 'unverified'
            AND block_time > $3::timestamptz - interval '7 days'""", mint, wallets, sig["detected_at"])
    by_w: dict[str, list] = {}
    for r in rows:
        by_w.setdefault(r["wallet"], []).append((int(r["ts"]), r["tx_type"], float(r["token_amount"]),
                                                 float(r["sol_amount"])))
    prog = exit_progress(by_w, int(sig["detected_at"].timestamp()))
    threshold = float(s.get("exit_alert_share", 0.5))
    for level, share in EXIT_LEVELS:
        share = max(share, threshold) if level == "EXIT50" else share
        if prog["exited_share"] < share:
            continue
        key = f"{mint}:{level}:{sig['id']}"
        if await ctx.pool.fetchval("SELECT 1 FROM alerts WHERE dedupe_key = $1", key):
            continue
        token = dict(await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", mint) or {"mint": mint})
        text = telegram.format_exit_alert(dict(sig), token, prog, level, os.environ.get("PUBLIC_URL"))
        await _deliver(ctx, s, sig["id"], mint, level, "exit", key, text)


async def _deliver(ctx: Ctx, s: dict, sig_id, mint: str, level: str, reason: str, key: str, text: str) -> None:
    chat = s.get("telegram_chat_id") or ctx.cfg.telegram_chat_id
    status, err, sent_at = "dashboard_only", None, None
    if s.get("telegram_enabled") and ctx.cfg.telegram_bot_token and chat:
        try:
            await telegram.send_message(ctx.http, ctx.cfg.telegram_bot_token, chat, text)
            status, sent_at = "sent", dt.datetime.now(UTC)
        except Exception as e:
            status, err = "failed", str(e)[:300]
    inserted = await ctx.pool.fetchval("""INSERT INTO alerts(signal_id, mint, level, reason, dedupe_key, channel, status,
            message, error, sent_at) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
        ON CONFLICT (dedupe_key, channel) DO NOTHING RETURNING id""",
        sig_id, mint, level, reason, key, "telegram" if status in ("sent", "failed") else "dashboard",
        status, text, err, sent_at)
    if inserted:
        await ctx.publish("alert", {"id": inserted, "mint": mint, "level": level, "status": status})
        log.info("alert %s %s %s (%s)", mint, level, reason, status)


async def consumer(ctx: Ctx) -> None:
    s = await ctx.settings.get()
    profiles = await smart_profiles(ctx, int(s["analysis_period_days"]))
    last_refresh = time.time()
    pending: dict[str, float] = {}
    exits: dict[str, float] = {}
    while True:
        msgs = await ctx.consume(S_SMART_TRADES, GROUP, count=200, block_ms=2000)
        ids = []
        for mid, p in msgs:
            ids.append(mid)
            if p.get("wallet") in profiles:
                pending[p["mint"]] = time.time()
                if p.get("tx_type") == "sell":
                    exits[p["mint"]] = time.time()
        await ctx.ack(S_SMART_TRADES, GROUP, ids)
        if time.time() - last_refresh > 30:
            s = await ctx.settings.get()
            profiles = await smart_profiles(ctx, int(s["analysis_period_days"]))
            last_refresh = time.time()
        for mint in list(pending):
            pending.pop(mint, None)
            try:
                sig = await evaluate_token(ctx, mint, s, profiles, int(time.time()))
                if sig:
                    await store_and_alert(ctx, sig, s)
            except Exception:
                log.exception("signal evaluation %s failed", mint)
        for mint in list(exits):
            exits.pop(mint, None)
            try:
                await check_exits(ctx, mint, s)
            except Exception:
                log.exception("exit check %s failed", mint)
        await repo.heartbeat(ctx.pool, ctx.worker, ctx.started_at, {"smart_wallets": len(profiles)})


async def main(ctx: Ctx) -> None:
    tasks = [consumer(ctx)]
    if ctx.cfg.telegram_bot_token:
        s = await ctx.settings.get()

        async def handler(cmd):
            return await bot_handler(ctx, cmd)
        tasks.append(telegram.poll_commands(ctx.http, ctx.cfg.telegram_bot_token,
                                            s.get("telegram_chat_id") or ctx.cfg.telegram_chat_id, handler))
    await asyncio.gather(*tasks)
