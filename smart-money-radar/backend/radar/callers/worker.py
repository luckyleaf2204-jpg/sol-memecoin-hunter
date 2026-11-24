"""Worker role `callers`: reads the followed channels, records calls, takes real prices at the marks,
finalises 24 h outcomes, checks smart money around each call and sends alerts.

Loops (one process, independent of the other roles; a failure here never touches them):
  poll      every channel in turn (web preview by default), new / edited / deleted posts -> calls
  marks     prices due at 0/5/15/30/60/120/360/720/1440 min (DexScreener now; never back-filled
            with a later price - a mark missed by > 10 min is filled from the 1-minute candle of its
            own minute at finalisation, or left 'missing')
  watch     live calls < 24 h: 2x / 3x / rug alerts
  finalise  24 h after the call: candles call-6h..call+24h -> ATH, drawdown, 2x/3x/5x, rug, pre-call
            run; smart-money check
History: `python -m radar tg-backfill <channel> --days N` reads older pages (calls marked live=false,
priced only from the 1-minute candles of their own minute).
"""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import os

from ..context import Ctx
from ..services import telegram
from . import outcomes, prices, smartmoney
from .parse import extract, text_hash
from .sources import ClientSource, SourceError, WebSource

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
POLL_GAP_S = float(os.environ.get("CALLERS_POLL_GAP_S", "45"))
SESSION = os.environ.get("TELEGRAM_SESSION_PATH", "/app/tg_session/radar")
MARK_GRACE_S = 600


def _now() -> dt.datetime:
    return dt.datetime.now(UTC)


# ------------------------------------------------------------------ posts -> messages -> calls
async def store_posts(ctx: Ctx, ch: dict, posts: list[dict], source: str, live_after: dt.datetime | None) -> list[tuple]:
    """Upsert posts; returns [(post, is_new_or_edited, live)] for posts whose text must be parsed."""
    todo = []
    async with ctx.pool.acquire() as conn:
        for p in posts:
            h = text_hash(p["text"])
            row = await conn.fetchrow("SELECT text_hash, text, deleted_at FROM tg_messages WHERE channel_id=$1 AND msg_id=$2",
                                      ch["id"], p["msg_id"])
            live = live_after is not None and p["posted_at"] >= live_after
            if row is None:
                await conn.execute("""INSERT INTO tg_messages(channel_id, msg_id, posted_at, text, text_hash, edited, views, source)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING""",
                                   ch["id"], p["msg_id"], p["posted_at"], p["text"], h, p.get("edited", False), p.get("views"), source)
                todo.append((p, True, live))
            elif row["text_hash"] != h:
                await conn.execute("""UPDATE tg_messages SET text=$3, text_hash=$4, edited=true, edit_count=edit_count+1,
                        previous_texts = previous_texts || jsonb_build_array(jsonb_build_object('text', $5::text, 'replaced_at', now())),
                        last_seen_at=now(), views=$6, deleted_at=NULL WHERE channel_id=$1 AND msg_id=$2""",
                                   ch["id"], p["msg_id"], p["text"], h, row["text"], p.get("views"))
                todo.append((p, True, live))
            else:
                await conn.execute("UPDATE tg_messages SET last_seen_at=now(), views=$3, deleted_at=NULL WHERE channel_id=$1 AND msg_id=$2",
                                   ch["id"], p["msg_id"], p.get("views"))
        if posts:
            lo, hi = posts[0]["msg_id"], posts[-1]["msg_id"]
            seen = [p["msg_id"] for p in posts]
            # a stored post inside the range just read that is no longer there was deleted
            await conn.execute("""UPDATE tg_messages SET deleted_at = now() WHERE channel_id=$1 AND msg_id BETWEEN $2 AND $3
                AND NOT (msg_id = ANY($4::bigint[])) AND deleted_at IS NULL""", ch["id"], lo, hi, seen)
    return todo


async def record_calls(ctx: Ctx, ch: dict, todo: list[tuple]) -> list[int]:
    """Parse posts, validate addresses, insert calls (unique per channel/post/mint). Returns new live call ids."""
    cand = {}
    for p, _, live in todo:
        ex = extract(p["text"], p.get("links"))
        for a in ex["mints"]:
            cand.setdefault(a, []).append((p, ex, live))
    if not cand:
        return []
    try:
        resolved = await prices.resolve_addresses(ctx.http, list(cand))
    except Exception as e:  # noqa: BLE001 - keep the posts; addresses are retried on the next edit/backfill
        log.warning("address resolution failed: %s", e)
        return []
    new_live = []
    async with ctx.pool.acquire() as conn:
        for addr, items in cand.items():
            mint = resolved.get(addr)
            for p, ex, live in items:
                status, reason = ("tracking", None) if mint else ("invalid", "không phải token mint / không có cặp giao dịch")
                m = mint or addr
                prev = await conn.fetchrow("""SELECT id FROM tg_calls WHERE channel_id=$1 AND mint=$2 AND called_at < $3
                    AND status <> 'invalid' ORDER BY called_at LIMIT 1""", ch["id"], m, p["posted_at"])
                cid = await conn.fetchval("""INSERT INTO tg_calls(channel_id, msg_id, mint, called_at, live, ticker, name, stated_mc_usd,
                        stated_price_usd, is_repeat, first_call_id, status, invalid_reason)
                    VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13) ON CONFLICT (channel_id, msg_id, mint) DO NOTHING RETURNING id""",
                    ch["id"], p["msg_id"], m, p["posted_at"], live, ex["ticker"], ex["name"], ex["stated_mc_usd"],
                    ex["stated_price_usd"], prev is not None, prev["id"] if prev else None, status, reason)
                if cid and status == "tracking":
                    due = [(cid, o, p["posted_at"] + dt.timedelta(minutes=o)) for o in outcomes.MARKS_MIN]
                    await conn.executemany("INSERT INTO tg_call_marks(call_id, offset_min, due_at) VALUES ($1,$2,$3) ON CONFLICT DO NOTHING", due)
                    if live:
                        new_live.append(cid)
    return new_live


async def read_channel(ctx: Ctx, ch: dict, web: WebSource, client: ClientSource | None, before: int | None = None) -> dict:
    if ch["source"] == "client":
        if client is None:
            raise SourceError("client source not configured (TELEGRAM_API_ID / TELEGRAM_API_HASH)")
        return await client.page(ch["username"], before=before)
    return await web.page(ch["username"], before=before)


async def poll_once(ctx: Ctx, ch: dict, web: WebSource, client: ClientSource | None) -> list[int]:
    try:
        page = await read_channel(ctx, ch, web, client)
    except Exception as e:  # noqa: BLE001
        await ctx.pool.execute("""UPDATE tg_channels SET last_poll_at=now(), last_error=$2, error_count=error_count+1 WHERE id=$1""",
                               ch["id"], str(e)[:300])
        return []
    posts = page["posts"]
    live_after = ch["added_at"] - dt.timedelta(minutes=2)
    todo = await store_posts(ctx, ch, posts, ch["source"], live_after)
    new_live = await record_calls(ctx, ch, todo)
    await ctx.pool.execute("""UPDATE tg_channels SET last_poll_at=now(), last_ok_at=now(), last_error=NULL, error_count=0,
            title=COALESCE($2, title), last_msg_id=GREATEST(COALESCE(last_msg_id,0), $3),
            oldest_msg_id=LEAST(COALESCE(oldest_msg_id, $4), $4) WHERE id=$1""",
                           ch["id"], page.get("title"), posts[-1]["msg_id"] if posts else 0, posts[0]["msg_id"] if posts else 2 ** 62)
    return new_live


# ------------------------------------------------------------------ marks / watch / finalise
async def take_marks(ctx: Ctx) -> int:
    rows = await ctx.pool.fetch("""SELECT m.call_id, m.offset_min, m.due_at, c.mint, c.live FROM tg_call_marks m
        JOIN tg_calls c ON c.id = m.call_id WHERE m.status='pending' AND m.due_at <= now() AND c.live
        AND m.due_at > now() - interval '10 minutes' ORDER BY m.due_at LIMIT 120""")
    if not rows:
        return 0
    snap = await prices.snapshot(ctx.http, sorted({r["mint"] for r in rows}))
    n = 0
    for r in rows:
        s = snap.get(r["mint"])
        if not s or s["price_usd"] is None:
            continue
        await ctx.pool.execute("""UPDATE tg_call_marks SET observed_at=$3, price_usd=$4, price_sol=$5, mc_usd=$6, liquidity_usd=$7,
                source='dexscreener', lag_s=$8, status='ok' WHERE call_id=$1 AND offset_min=$2 AND status='pending'""",
                               r["call_id"], r["offset_min"], s["observed_at"], s["price_usd"], s["price_sol"], s["mc_usd"],
                               s["liquidity_usd"], (s["observed_at"] - r["due_at"]).total_seconds())
        n += 1
        if r["offset_min"] == 0:
            await ctx.pool.execute("UPDATE tg_calls SET venue=$2 WHERE id=$1", r["call_id"], s.get("dex"))
    return n


async def watch(ctx: Ctx, s: dict) -> None:
    rows = await ctx.pool.fetch("""SELECT c.id, c.mint, c.channel_id, ch.username, m.price_usd entry, m.liquidity_usd liq FROM tg_calls c
        JOIN tg_channels ch ON ch.id=c.channel_id JOIN tg_call_marks m ON m.call_id=c.id AND m.offset_min=0 AND m.status='ok'
        WHERE c.live AND c.status='tracking' AND c.called_at > now() - interval '24 hours' AND NOT c.is_repeat""")
    if not rows:
        return
    snap = await prices.snapshot(ctx.http, sorted({r["mint"] for r in rows}))
    for r in rows:
        p = snap.get(r["mint"])
        if not p or not p["price_usd"] or not r["entry"]:
            continue
        mult = p["price_usd"] / float(r["entry"])
        for k in (3, 2):
            if mult >= k:
                await alert(ctx, s, r["mint"], f"x{k}", f"callx{k}:{r['id']}",
                            f"📈 Kèo @{r['username']} đạt x{k}\nCA: {r['mint']}\nGiá lúc gọi ${float(r['entry']):.8g} → ${p['price_usd']:.8g} ({mult:.2f}x)")
                break
        liq_drop = r["liq"] and p["liquidity_usd"] is not None and p["liquidity_usd"] <= outcomes.RUG_LIQUIDITY * float(r["liq"])
        if mult <= outcomes.RUG_PRICE or liq_drop:
            await alert(ctx, s, r["mint"], "rug", f"callrug:{r['id']}",
                        f"⚠️ Dấu hiệu rug sau kèo @{r['username']}\nCA: {r['mint']}\nGiá còn {mult:.0%} giá lúc gọi; thanh khoản "
                        f"${(p['liquidity_usd'] or 0):,.0f} (lúc gọi ${float(r['liq'] or 0):,.0f})")


async def finalise(ctx: Ctx, limit: int = 20) -> int:
    rows = await ctx.pool.fetch("""SELECT c.id, c.mint, c.called_at, c.live FROM tg_calls c
        LEFT JOIN tg_call_outcomes o ON o.call_id=c.id
        WHERE c.status='tracking' AND o.call_id IS NULL AND c.called_at < now() - interval '24 hours 10 minutes'
        ORDER BY c.called_at LIMIT $1""", limit)
    n = 0
    for r in rows:
        ts = int(r["called_at"].timestamp())
        try:
            candles, pools = await prices.candles_for_mint(ctx.http, r["mint"], ts - 6 * 3600, ts + 86400)
        except Exception as e:  # noqa: BLE001
            log.warning("candles failed for %s: %s", r["mint"], e)
            continue
        marks = {m["offset_min"]: m for m in await ctx.pool.fetch("SELECT * FROM tg_call_marks WHERE call_id=$1", r["id"])}
        m0 = marks.get(0)
        entry, entry_src = None, None
        if r["live"] and m0 and m0["status"] == "ok":
            entry, entry_src = float(m0["price_usd"]), "live_snapshot"
        else:
            c = outcomes.candle_at(candles, ts)
            if c:
                entry, entry_src = c[4], "geckoterminal_1m_close"
        # marks never filled live: the candle of their own minute, else 'missing'
        for off, m in marks.items():
            if m["status"] != "pending":
                continue
            c = outcomes.candle_at(candles, ts + off * 60)
            if c:
                # observed_at = the candle's own minute (<= due time): the last traded price in force then
                await ctx.pool.execute("""UPDATE tg_call_marks SET observed_at=to_timestamp($3), price_usd=$4, source='geckoterminal_1m',
                        lag_s=$5, status='ok' WHERE call_id=$1 AND offset_min=$2""", r["id"], off, c[0], c[4], c[0] - (ts + off * 60))
            else:
                await ctx.pool.execute("UPDATE tg_call_marks SET status='missing' WHERE call_id=$1 AND offset_min=$2", r["id"], off)
        if entry is None:
            await ctx.pool.execute("UPDATE tg_calls SET status='unpriced' WHERE id=$1", r["id"])
            n += 1
            continue
        liq0 = float(m0["liquidity_usd"]) if m0 and m0["liquidity_usd"] is not None else None
        m_last = marks.get(1440)
        liq_last = float(m_last["liquidity_usd"]) if m_last and m_last["liquidity_usd"] is not None else None
        out = outcomes.compute(entry, ts, candles, liq0, liq_last)
        async with ctx.pool.acquire() as conn:
            await conn.execute("""INSERT INTO tg_call_outcomes(call_id, entry_price_usd, entry_source, ath_price_usd, ath_at, ath_multiple,
                    min_price_usd, max_drawdown, roi_24h, x2_at, x3_at, x5_at, rugged, rug_reason, pre_call_run, pre_call_6h_mult,
                    candles, coverage, detail)
                VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16,$17,$18,$19) ON CONFLICT (call_id) DO NOTHING""",
                r["id"], entry, entry_src, out.get("ath_price_usd"), out.get("ath_at"), out.get("ath_multiple"), out.get("min_price_usd"),
                out.get("max_drawdown"), out.get("roi_24h"), out.get("x2_at"), out.get("x3_at"), out.get("x5_at"),
                out.get("rugged", False), out.get("rug_reason"), out.get("pre_call_run"), out.get("pre_call_6h_mult"),
                out.get("candles"), out.get("coverage"), {"pools": pools})
            await smartmoney.check(conn, r["id"], r["mint"], r["called_at"])
            await conn.execute("UPDATE tg_calls SET status='done' WHERE id=$1", r["id"])
        n += 1
    return n


# ------------------------------------------------------------------ alerts
async def alert(ctx: Ctx, s: dict, mint: str, level: str, key: str, text: str) -> None:
    if await ctx.pool.fetchval("SELECT 1 FROM alerts WHERE dedupe_key=$1", key):
        return
    sent = await ctx.redis.incr("callers:alerts:hour:" + _now().strftime("%Y%m%d%H"))
    await ctx.redis.expire("callers:alerts:hour:" + _now().strftime("%Y%m%d%H"), 7200)
    chat = s.get("telegram_chat_id") or ctx.cfg.telegram_chat_id
    status, err, sent_at = "dashboard_only", None, None
    if sent <= int(os.environ.get("CALLERS_MAX_ALERTS_PER_HOUR", "30")) and s.get("telegram_enabled") and ctx.cfg.telegram_bot_token and chat:
        try:
            await telegram.send_message(ctx.http, ctx.cfg.telegram_bot_token, chat, text)
            status, sent_at = "sent", _now()
        except Exception as e:  # noqa: BLE001
            status, err = "failed", str(e)[:300]
    await ctx.pool.execute("""INSERT INTO alerts(mint, level, reason, dedupe_key, channel, status, message, error, sent_at)
        VALUES ($1,$2,'caller',$3,$4,$5,$6,$7,$8) ON CONFLICT (dedupe_key, channel) DO NOTHING""",
                           mint, level, key, "telegram" if status in ("sent", "failed") else "dashboard", status, text, err, sent_at)


async def call_alerts(ctx: Ctx, s: dict, call_ids: list[int]) -> None:
    for cid in call_ids:
        c = await ctx.pool.fetchrow("""SELECT c.*, ch.username FROM tg_calls c JOIN tg_channels ch ON ch.id=c.channel_id WHERE c.id=$1""", cid)
        if c is None or c["is_repeat"]:
            continue
        await alert(ctx, s, c["mint"], "call", f"call:{cid}",
                    f"📣 @{c['username']} gọi kèo mới\n{c['ticker'] or c['name'] or ''}\nCA: {c['mint']}\n"
                    f"Thời điểm: {c['called_at']:%H:%M:%S} UTC")
        others = await ctx.pool.fetch("""SELECT DISTINCT ch.username FROM tg_calls c JOIN tg_channels ch ON ch.id=c.channel_id
            WHERE c.mint=$1 AND c.channel_id<>$2 AND c.called_at BETWEEN $3::timestamptz - interval '60 minutes' AND $3::timestamptz AND c.status<>'invalid'""",
                                      c["mint"], c["channel_id"], c["called_at"])
        if others:
            names = ", ".join("@" + o["username"] for o in others)
            await alert(ctx, s, c["mint"], "multi", f"multi:{c['mint']}:{c['called_at']:%Y%m%d%H}",
                        f"🔁 {len(others) + 1} kênh cùng gọi một token trong 60 phút\n@{c['username']}, {names}\nCA: {c['mint']}")


async def sm_alerts(ctx: Ctx, s: dict) -> None:
    """30 min after a live call: smart money buying around it / selling right after it."""
    rows = await ctx.pool.fetch("""SELECT c.id, c.mint, c.called_at, ch.username FROM tg_calls c JOIN tg_channels ch ON ch.id=c.channel_id
        WHERE c.live AND NOT c.is_repeat AND c.status='tracking' AND c.called_at BETWEEN now() - interval '40 minutes' AND now() - interval '30 minutes'""")
    for r in rows:
        async with ctx.pool.acquire() as conn:
            res = await smartmoney.check(conn, r["id"], r["mint"], r["called_at"])
        near = [t for t in res["detail"]["trades"] if t["side"] == "buy" and abs(t["min_from_call"]) <= 15]
        if near:
            await alert(ctx, s, r["mint"], "sm_buy", f"smbuy:{r['id']}",
                        f"🧠 Smart Money mua quanh kèo @{r['username']}\nCA: {r['mint']}\n{len({t['wallet'] for t in near})} ví, "
                        f"{sum(t['sol'] for t in near):.2f} SOL trong ±15 phút")
        if res["sellers_30m"]:
            await alert(ctx, s, r["mint"], "sm_sell", f"smsell:{r['id']}",
                        f"🔻 Smart Money bán sau kèo @{r['username']}\nCA: {r['mint']}\n{res['sellers_30m']} ví bán "
                        f"{res['sol_sold_30m']:.2f} SOL trong 30 phút sau khi gọi")


# ------------------------------------------------------------------ main loops
async def poll_loop(ctx: Ctx) -> None:
    web = WebSource()
    client = None
    if ClientSource.configured():
        client = ClientSource(SESSION)
    try:
        while True:
            s = await ctx.settings.get()
            chans = [dict(r) for r in await ctx.pool.fetch("SELECT * FROM tg_channels WHERE enabled ORDER BY id")]
            for ch in chans:
                new_live = await poll_once(ctx, ch, web, client)
                if new_live:
                    await take_marks(ctx)
                    await call_alerts(ctx, s, new_live)
            await asyncio.sleep(POLL_GAP_S if chans else 30)
    finally:
        await web.close()
        if client:
            await client.close()


async def price_loop(ctx: Ctx) -> None:
    i = 0
    while True:
        try:
            s = await ctx.settings.get()
            await take_marks(ctx)
            if i % 6 == 0:
                await watch(ctx, s)
                await sm_alerts(ctx, s)
            if i % 30 == 0:
                await finalise(ctx)
        except Exception:  # noqa: BLE001
            log.exception("callers price loop error")
        i += 1
        await asyncio.sleep(20)


async def main(ctx: Ctx) -> None:
    log.info("callers worker started (read-only; no trading)")
    await asyncio.gather(poll_loop(ctx), price_loop(ctx))


async def backfill(ctx: Ctx, username: str, days: float, max_pages: int = 200) -> dict:
    """Read older pages of a channel: calls are stored with live=false and priced at finalisation
    from the candle of their own minute."""
    ch = dict(await ctx.pool.fetchrow("SELECT * FROM tg_channels WHERE username=$1", username))
    web = WebSource()
    client = ClientSource(SESSION) if ch["source"] == "client" else None
    stop = _now() - dt.timedelta(days=days)
    before = ch["oldest_msg_id"]
    pages = posts_n = calls_n = empty = 0
    try:
        while pages < max_pages:
            page = await read_channel(ctx, ch, web, client, before=before)
            pages += 1
            posts = page["posts"]
            if not posts:
                # message ids are not contiguous (deleted ranges): step further back a few times
                empty += 1
                if not before or empty > 12:
                    break
                before = max(1, before - 50 * 2 ** empty)
                continue
            empty = 0
            todo = await store_posts(ctx, ch, posts, ch["source"], None)
            calls_n += len(await record_calls(ctx, ch, [(p, n, False) for p, n, _ in todo]))
            posts_n += len(posts)
            before = posts[0]["msg_id"]
            await ctx.pool.execute("UPDATE tg_channels SET oldest_msg_id=LEAST(COALESCE(oldest_msg_id,$2),$2) WHERE id=$1", ch["id"], before)
            if posts[0]["posted_at"] < stop or before <= 1:
                break
        await ctx.pool.execute("UPDATE tg_channels SET backfill_done=true WHERE id=$1", ch["id"])
    finally:
        await web.close()
        if client:
            await client.close()
    n_calls = await ctx.pool.fetchval("SELECT count(*) FROM tg_calls WHERE channel_id=$1", ch["id"])
    return {"pages": pages, "posts": posts_n, "calls_in_channel": n_calls}
