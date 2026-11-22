"""Watched wallets (KOL / callers): instant buy alerts, sell alerts, side-wallet suggestions.

Callers usually buy (often with several wallets) BEFORE they post the call. The stream sees each
buy in the program logs ~1–3 s after it is confirmed; the signal engine sends one Telegram alert
per (wallet, token) for the FIRST buy, saying how many watched wallets bought the token before
(KOL #1, #2 …) and how long after the token's creation. When a watched wallet has sold >= 50 % and
>= 90 % of what it bought, a sell alert follows.

Side wallets: a wallet that received SOL directly from a watched wallet and then traded on
pump.fun is suggested (unconfirmed, not alerted) with the transfer as evidence. The operator
confirms or deletes it. Funding through exchanges or several hops is not detected.

Labels (name, X handle) are entered by the operator — the system never invents who owns a wallet.
"""
from __future__ import annotations

import datetime as dt
import html
import logging
import os
import time
from typing import Any

from ..context import Ctx
from ..solana.base58 import is_valid_address
from . import telegram

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
VN = dt.timezone(dt.timedelta(hours=7))


# ------------------------------------------------------------------ watchlist management

def parse_lines(text: str) -> tuple[list[dict], list[str]]:
    """One wallet per line: `<address> [name ...] [@handle]`; commas/tabs also separate.
    Returns (entries, errors)."""
    out, errors, seen = [], [], set()
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.replace(",", " ").replace("\t", " ").strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split()
        addr = parts[0]
        if not is_valid_address(addr):
            errors.append(f"dòng {n}: địa chỉ không hợp lệ: {addr[:50]}")
            continue
        if addr in seen:
            continue
        seen.add(addr)
        handle = next((p.lstrip("@") for p in parts[1:] if p.startswith("@")), None)
        if handle is None:
            for p in parts[1:]:
                if "x.com/" in p or "twitter.com/" in p:
                    handle = p.rstrip("/").split("/")[-1]
        name = " ".join(p for p in parts[1:] if not p.startswith("@") and "x.com/" not in p
                        and "twitter.com/" not in p).strip()
        out.append({"address": addr, "label": (name or handle or f"Ví {addr[:4]}…{addr[-4:]}")[:80],
                    "x_handle": (handle or None) and handle[:40]})
    return out, errors


async def add_watched(ctx: Ctx, entries: list[dict], kind: str = "kol") -> int:
    async with ctx.pool.acquire() as conn:
        async with conn.transaction():
            for e in entries:
                await conn.execute("""INSERT INTO watched_wallets(address, label, x_handle, kind, source, confirmed)
                    VALUES ($1, $2, $3, $4, 'manual', true)
                    ON CONFLICT (address) DO UPDATE SET label = EXCLUDED.label,
                        x_handle = COALESCE(EXCLUDED.x_handle, watched_wallets.x_handle), confirmed = true""",
                                   e["address"], e["label"], e.get("x_handle"), kind)
                await _track(conn, e["address"])
    return len(entries)


async def _track(conn, address: str) -> None:
    # watched wallets are tracked by the stream and get their history read like candidates
    await conn.execute("""INSERT INTO wallets(address, status, discovery_source, discovery_detail, backfill_status)
        VALUES ($1, 'tracked', 'watchlist', '{}'::jsonb, 'queued')
        ON CONFLICT (address) DO UPDATE SET status = CASE WHEN wallets.status = 'smart' THEN 'smart'
            ELSE 'tracked' END, is_excluded = false, updated_at = now()""", address)


async def watched_map(ctx: Ctx) -> dict[str, dict]:
    return {r["address"]: dict(r) for r in await ctx.pool.fetch(
        "SELECT * FROM watched_wallets WHERE confirmed")}


# ------------------------------------------------------------------ alerts

async def handle_trade(ctx: Ctx, p: dict, s: dict, watched: dict[str, dict], deliver) -> None:
    w = watched.get(p.get("wallet"))
    if w is None or not s.get("kol_alert_enabled", True):
        return
    # only live trades: history read later (backfill) or re-published after verification must
    # never produce a "buy now" alert
    if time.time() - float(p.get("ts") or 0) > float(s.get("kol_alert_max_age_seconds", 300)):
        return
    if p.get("tx_type") == "buy":
        if float(p.get("sol") or 0) < float(s.get("kol_alert_min_sol", 0.3)):
            return
        key = f"kol:{p['wallet']}:{p['mint']}"
        if await ctx.pool.fetchval("SELECT 1 FROM alerts WHERE dedupe_key = $1", key):
            return
        text = await _buy_text(ctx, p, w, watched)
        await deliver(ctx, s, None, p["mint"], "KOL", "kol_buy", key, text)
    elif p.get("tx_type") == "sell" and s.get("kol_exit_alert_enabled", True):
        if not await ctx.pool.fetchval("SELECT 1 FROM alerts WHERE dedupe_key = $1", f"kol:{p['wallet']}:{p['mint']}"):
            return          # only for tokens we alerted the buy of
        r = await ctx.pool.fetchrow("""SELECT
                COALESCE(sum(token_amount) FILTER (WHERE tx_type = 'buy'), 0) AS bought,
                COALESCE(sum(token_amount) FILTER (WHERE tx_type = 'sell'), 0) AS sold,
                COALESCE(sum(sol_amount) FILTER (WHERE tx_type = 'buy'), 0) AS buy_sol,
                COALESCE(sum(sol_amount) FILTER (WHERE tx_type = 'sell'), 0) AS sell_sol
            FROM wallet_transactions WHERE wallet = $1 AND mint = $2""", p["wallet"], p["mint"])
        if not r or float(r["bought"]) <= 0:
            return
        share = float(r["sold"]) / float(r["bought"])
        for lvl in (0.9, 0.5):
            if share >= lvl:
                key = f"kolsell:{p['wallet']}:{p['mint']}:{int(lvl * 100)}"
                if not await ctx.pool.fetchval("SELECT 1 FROM alerts WHERE dedupe_key = $1", key):
                    text = await _sell_text(ctx, p, w, share, r)
                    await deliver(ctx, s, None, p["mint"], "KOL_EXIT", "kol_sell", key, text)
                break


def _who(w: dict, watched: dict[str, dict]) -> str:
    e = html.escape
    name = f"<b>{e(w['label'])}</b>" + (f" (@{e(w['x_handle'])})" if w.get("x_handle") else "")
    if w.get("kind") == "side_wallet" and w.get("parent") in watched:
        par = watched[w["parent"]]
        name += f" — ví phụ của {e(par['label'])}"
    return name


async def _buy_text(ctx: Ctx, p: dict, w: dict, watched: dict[str, dict]) -> str:
    e = html.escape
    mint = p["mint"]
    tok = dict(await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", mint) or {"mint": mint})
    # watched wallets that bought this token, in order of their first buy
    prior = await ctx.pool.fetch("""SELECT wallet, min(block_time) AS t, sum(sol_amount) AS sol FROM wallet_transactions
        WHERE mint = $1 AND tx_type = 'buy' AND wallet = ANY($2::text[]) GROUP BY wallet ORDER BY min(block_time)""",
                                 mint, list(watched))
    order = [r["wallet"] for r in prior]
    if p["wallet"] not in order:
        order.append(p["wallet"])
    rank = order.index(p["wallet"]) + 1
    owners = {watched[x].get("parent") or x for x in order if x in watched}
    ts = int(p.get("ts") or time.time())
    when = dt.datetime.fromtimestamp(ts, UTC).astimezone(VN)
    age = None
    if tok.get("created_at"):
        age = ts - int(tok["created_at"].timestamp())
    head = "🟣 <b>KOL MUA</b>" if rank == 1 else f"🔥 <b>KOL thứ {rank} mua cùng token</b>"
    lines = [head, "", f"Ví: {_who(w, watched)}", f"<code>{e(p['wallet'])}</code>", "",
             f"Token: <b>{e(tok.get('name') or '?')}</b> ({e(tok.get('symbol') or '?')})",
             f"Mint: <code>{e(mint)}</code>",
             f"Mua: {telegram.fmt_sol(float(p.get('sol') or 0))} lúc {when.strftime('%H:%M:%S %d/%m/%Y')} (GMT+7), "
             f"báo sau {max(0, int(time.time()) - ts)} giây"]
    if age is not None:
        lines.append(f"Token tạo trước đó: {_dur(age)}")
    mc = tok.get("last_mc_sol") or tok.get("market_cap_sol")
    if mc:
        lines.append(f"Market cap hiện tại: {telegram.fmt_sol(float(mc), 0)}")
    if len(order) > 1:
        lines.append("")
        lines.append(f"Ví theo dõi đã mua token này ({len(order)} ví, {len(owners)} người):")
        for i, x in enumerate(order[:8], 1):
            lab = watched.get(x, {}).get("label", x[:6])
            lines.append(f"{i}. {e(lab)} – <code>{e(x[:6])}…{e(x[-4:])}</code>")
    perf = await ctx.pool.fetchrow("""SELECT win_rate, closed_qualifying, metrics->'copy'->'60'->>'win_rate' AS copy_wr
        FROM wallet_performance WHERE wallet = $1 AND period_days = 30""", p["wallet"])
    if perf and perf["closed_qualifying"]:
        lines.append("")
        cw = f", copy trễ 1 phút thắng {telegram.fmt_num(100 * float(perf['copy_wr']), 0)}%" if perf["copy_wr"] else ""
        lines.append(f"Lịch sử 30 ngày: thắng {telegram.fmt_num(100 * float(perf['win_rate'] or 0), 0)}% "
                     f"trên {perf['closed_qualifying']} vị thế ≥ 2 SOL{cw}")
    lines += ["", f"Pump.fun: https://pump.fun/coin/{mint}", f"DexScreener: https://dexscreener.com/solana/{mint}",
              f"Giao dịch: https://solscan.io/tx/{p['signature']}"]
    pub = os.environ.get("PUBLIC_URL")
    if pub:
        lines.append(f"Dashboard: {pub.rstrip('/')}/tokens/{mint}")
    lines.append("Dữ liệu từ log on-chain (đang xác minh đầy đủ). Không phải khuyến nghị; hệ thống không tự giao dịch.")
    return "\n".join(lines)


async def _sell_text(ctx: Ctx, p: dict, w: dict, share: float, r) -> str:
    e = html.escape
    watched = await watched_map(ctx)
    tok = dict(await ctx.pool.fetchrow("SELECT name, symbol FROM tokens WHERE mint = $1", p["mint"]) or {})
    pnl = float(r["sell_sol"]) - float(r["buy_sol"])
    return "\n".join([
        f"⚠️ <b>KOL ĐANG BÁN — đã bán {telegram.fmt_num(100 * min(share, 1), 0)}%</b>", "",
        f"Ví: {_who(w, watched)}", f"<code>{e(p['wallet'])}</code>",
        f"Token: <b>{e(tok.get('name') or '?')}</b> ({e(tok.get('symbol') or '?')})",
        f"Mint: <code>{e(p['mint'])}</code>",
        f"Đã mua {telegram.fmt_sol(float(r['buy_sol']))}, đã bán {telegram.fmt_sol(float(r['sell_sol']))} "
        f"(chênh lệch {telegram.fmt_sol(pnl)})",
        f"DexScreener: https://dexscreener.com/solana/{p['mint']}",
        "Không phải khuyến nghị; hệ thống không tự giao dịch."])


def _dur(sec: int) -> str:
    if sec < 90:
        return f"{sec} giây"
    if sec < 5400:
        return f"{sec // 60} phút"
    if sec < 2 * 86400:
        return f"{sec / 3600:.1f} giờ".replace(".", ",")
    return f"{sec // 86400} ngày"


# ------------------------------------------------------------------ side wallets

async def suggest_side_wallets(ctx: Ctx, min_sol: float = 0.5, days: int = 30) -> int:
    """Wallets funded directly by a watched wallet (>= min_sol) that then traded on pump.fun."""
    rows = await ctx.pool.fetch("""SELECT t.source AS parent, t.destination AS address, sum(t.lamports) / 1e9 AS sol,
            min(t.block_time) AS first_at, (array_agg(t.signature ORDER BY t.block_time))[1] AS signature
        FROM sol_transfers t JOIN watched_wallets p ON p.address = t.source AND p.confirmed
        WHERE t.block_time > now() - make_interval(days => $2)
          AND NOT EXISTS (SELECT 1 FROM watched_wallets x WHERE x.address = t.destination)
          AND EXISTS (SELECT 1 FROM wallet_transactions wt WHERE wt.wallet = t.destination
                      AND wt.tx_type IN ('buy','sell') AND wt.block_time >= t.block_time)
        GROUP BY t.source, t.destination HAVING sum(t.lamports) / 1e9 >= $1""", min_sol, days)
    n = 0
    for r in rows:
        par = await ctx.pool.fetchrow("SELECT label FROM watched_wallets WHERE address = $1", r["parent"])
        await ctx.pool.execute("""INSERT INTO watched_wallets(address, label, kind, parent, source, confirmed, evidence)
            VALUES ($1, $2, 'side_wallet', $3, 'funded_by_watched', false, $4) ON CONFLICT (address) DO NOTHING""",
                               r["address"], f"Ví phụ? của {par['label']}", r["parent"],
                               {"funded_sol": float(r["sol"]), "first_funding_at": r["first_at"].isoformat(),
                                "signature": r["signature"]})
        n += 1
    if n:
        log.info("suggested %d side wallets", n)
    return n
