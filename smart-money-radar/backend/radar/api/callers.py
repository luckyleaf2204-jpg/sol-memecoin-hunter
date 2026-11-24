"""REST API of the Telegram Caller Tracker (read + channel management). Mounted by app.py."""
from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel

from ..callers import stats as cstats
from ..callers.sources import clean_username

router = APIRouter(prefix="/api/callers")
UTC = dt.timezone.utc

CALL_COLS = """c.id, c.channel_id, ch.username, ch.title, c.msg_id, c.mint, c.called_at, c.detected_at, c.live, c.ticker, c.name,
    c.stated_mc_usd, c.is_repeat, c.status, c.invalid_reason, c.venue,
    o.entry_price_usd, o.entry_source, o.ath_multiple, o.ath_at, o.max_drawdown, o.roi_24h, o.x2_at, o.x3_at, o.x5_at,
    o.rugged, o.rug_reason, o.pre_call_run, o.pre_call_6h_mult, o.coverage, (o.call_id IS NOT NULL) AS finalized,
    s.wallets_known, s.buyers_before, s.buyers_after, s.sellers_30m, s.sol_before, s.sol_after, s.sol_sold_30m,
    m0.price_usd AS entry_live_usd, m0.mc_usd AS entry_mc_usd, m0.liquidity_usd AS entry_liq_usd"""
CALL_FROM = """FROM tg_calls c JOIN tg_channels ch ON ch.id = c.channel_id
    LEFT JOIN tg_call_outcomes o ON o.call_id = c.id LEFT JOIN tg_call_sm s ON s.call_id = c.id
    LEFT JOIN tg_call_marks m0 ON m0.call_id = c.id AND m0.offset_min = 0 AND m0.status = 'ok'"""


class ChannelIn(BaseModel):
    username: str
    source: str = "web"
    note: str | None = None


def install(app, ctx, rows, j, Auth) -> None:   # noqa: N803 - mirrors app.py names
    @router.get("/channels", dependencies=[Auth])
    async def channels():
        rs = await ctx().pool.fetch("""SELECT ch.*, (SELECT count(*) FROM tg_calls c WHERE c.channel_id = ch.id) calls,
            (SELECT count(*) FROM tg_messages m WHERE m.channel_id = ch.id) messages,
            (SELECT max(called_at) FROM tg_calls c WHERE c.channel_id = ch.id) last_call_at
            FROM tg_channels ch ORDER BY ch.added_at""")
        return rows(rs)

    @router.post("/channels", dependencies=[Auth])
    async def add_channel(body: ChannelIn):
        try:
            u = clean_username(body.username)
        except ValueError as e:
            raise HTTPException(400, str(e))
        if body.source not in ("web", "client"):
            raise HTTPException(400, "source must be web or client")
        r = await ctx().pool.fetchrow("""INSERT INTO tg_channels(username, source, note) VALUES ($1,$2,$3)
            ON CONFLICT (username) DO UPDATE SET enabled = true, source = EXCLUDED.source RETURNING *""", u, body.source, body.note)
        return j(dict(r))

    @router.post("/channels/{cid}/toggle", dependencies=[Auth])
    async def toggle(cid: int):
        r = await ctx().pool.fetchrow("UPDATE tg_channels SET enabled = NOT enabled WHERE id=$1 RETURNING id, enabled", cid)
        if not r:
            raise HTTPException(404, "channel not found")
        return j(dict(r))

    @router.delete("/channels/{cid}", dependencies=[Auth])
    async def delete_channel(cid: int, keep_history: bool = True):
        if keep_history:   # stop following, keep its calls for the statistics
            await ctx().pool.execute("UPDATE tg_channels SET enabled = false WHERE id=$1", cid)
        else:
            await ctx().pool.execute("DELETE FROM tg_channels WHERE id=$1", cid)
        return {"ok": True}

    def _filters(channel: int | None, mint: str | None, since_h: float | None, min_mult: float | None, only: str | None):
        w, a = ["TRUE"], []
        if channel:
            a.append(channel); w.append(f"c.channel_id = ${len(a)}")
        if mint:
            a.append(mint.strip()); w.append(f"(c.mint = ${len(a)} OR upper(c.ticker) = upper(${len(a)}))")
        if since_h:
            a.append(dt.datetime.now(UTC) - dt.timedelta(hours=since_h)); w.append(f"c.called_at >= ${len(a)}")
        if min_mult:
            a.append(min_mult); w.append(f"o.ath_multiple >= ${len(a)}")
        if only == "rug":
            w.append("o.rugged")
        elif only == "pumping":
            w.append("o.call_id IS NULL AND c.live AND c.called_at > now() - interval '24 hours'")
        elif only == "valid":
            w.append("c.status <> 'invalid'")
        return " AND ".join(w), a

    @router.get("/calls", dependencies=[Auth])
    async def calls(channel: int | None = None, mint: str | None = None, since_h: float | None = None,
                    min_mult: float | None = None, only: str | None = None, limit: int = Query(100, le=500)):
        w, a = _filters(channel, mint, since_h, min_mult, only)
        rs = await ctx().pool.fetch(f"SELECT {CALL_COLS} {CALL_FROM} WHERE {w} ORDER BY c.called_at DESC LIMIT {int(limit)}", *a)
        return rows(rs)

    @router.get("/pumping", dependencies=[Auth])
    async def pumping():
        """Live calls of the last 24 h with their latest real mark (multiple vs the entry)."""
        rs = await ctx().pool.fetch(f"""SELECT {CALL_COLS}, lm.price_usd last_price_usd, lm.offset_min last_offset, lm.observed_at last_observed
            {CALL_FROM} LEFT JOIN LATERAL (SELECT price_usd, offset_min, observed_at FROM tg_call_marks WHERE call_id = c.id AND status='ok'
            ORDER BY offset_min DESC LIMIT 1) lm ON TRUE
            WHERE c.live AND c.status = 'tracking' AND c.called_at > now() - interval '24 hours' ORDER BY c.called_at DESC LIMIT 200""")
        out = rows(rs)
        for r in out:
            r["multiple_now"] = (r["last_price_usd"] / r["entry_live_usd"]) if r.get("last_price_usd") and r.get("entry_live_usd") else None
        return sorted(out, key=lambda r: -(r["multiple_now"] or 0))

    @router.get("/calls/{call_id}", dependencies=[Auth])
    async def call_detail(call_id: int):
        c = await ctx().pool.fetchrow(f"SELECT {CALL_COLS}, s.detail sm_detail, o.detail outcome_detail {CALL_FROM} WHERE c.id=$1", call_id)
        if not c:
            raise HTTPException(404, "call not found")
        marks = await ctx().pool.fetch("SELECT * FROM tg_call_marks WHERE call_id=$1 ORDER BY offset_min", call_id)
        msg = await ctx().pool.fetchrow("SELECT * FROM tg_messages WHERE channel_id=$1 AND msg_id=$2", c["channel_id"], c["msg_id"])
        other = await ctx().pool.fetch("""SELECT c.id, ch.username, c.called_at FROM tg_calls c JOIN tg_channels ch ON ch.id=c.channel_id
            WHERE c.mint=$1 AND c.id<>$2 ORDER BY c.called_at""", c["mint"], call_id)
        return {"call": j(dict(c)), "marks": rows(marks), "message": j(dict(msg)) if msg else None, "other_calls": rows(other),
                "link": f"https://t.me/{c['username']}/{c['msg_id']}"}

    async def _all_calls(since_h: float | None):
        w, a = _filters(None, None, since_h, None, None)
        return [dict(r) for r in await ctx().pool.fetch(f"SELECT {CALL_COLS} {CALL_FROM} WHERE {w}", *a)]

    @router.get("/leaderboard", dependencies=[Auth])
    async def leaderboard(since_h: float | None = None):
        calls_ = await _all_calls(since_h)
        chans = {r["id"]: dict(r) for r in await ctx().pool.fetch("SELECT id, username, title, enabled, added_at, backfill_done FROM tg_channels")}
        by: dict[int, list] = {cid: [] for cid in chans}
        for c in calls_:
            by.setdefault(c["channel_id"], []).append(c)
        out = []
        for cid, cs in by.items():
            if cid not in chans:
                continue
            out.append({**j(chans[cid]), **cstats.channel_stats(cs)})
        order = {"A": 0, "B": 1, "C": 2, "D": 3}
        out.sort(key=lambda r: (order[r["grade"]], -(r["scored_calls"] or 0), -(r["median_roi_24h"] or -9)))
        return {"min_sample": cstats.MIN_SAMPLE, "win_multiple": cstats.WIN_MULTIPLE, "channels": out}

    @router.get("/roi-series", dependencies=[Auth])
    async def roi_series(channel: int | None = None, since_h: float | None = 24 * 30):
        w, a = _filters(channel, None, since_h, None, None)
        rs = await ctx().pool.fetch(f"""SELECT c.id, ch.username, c.called_at, o.roi_24h, o.ath_multiple, o.rugged {CALL_FROM}
            WHERE {w} AND o.call_id IS NOT NULL AND NOT c.is_repeat ORDER BY c.called_at""", *a)
        return rows(rs)

    @router.get("/smart-money", dependencies=[Auth])
    async def smart_money(since_h: float | None = None):
        """Per channel: how often smart/tracked/KOL wallets bought before / after its calls, sold right after,
        and the wallets seen most often around its calls."""
        w, a = _filters(None, None, since_h, None, None)
        rs = await ctx().pool.fetch(f"""SELECT c.channel_id, ch.username, s.detail FROM tg_calls c JOIN tg_channels ch ON ch.id=c.channel_id
            JOIN tg_call_sm s ON s.call_id = c.id LEFT JOIN tg_call_outcomes o ON o.call_id = c.id WHERE {w} AND NOT c.is_repeat""", *a)
        agg: dict = {}
        for r in rs:
            g = agg.setdefault(r["channel_id"], {"username": r["username"], "calls_checked": 0, "wallets": {}})
            g["calls_checked"] += 1
            seen = set()
            for t in (r["detail"] or {}).get("trades", []) if isinstance(r["detail"], dict) else []:
                k = (t["wallet"], t["side"], "before" if t["min_from_call"] < 0 else "after")
                if k in seen:
                    continue
                seen.add(k)
                wv = g["wallets"].setdefault(t["wallet"], {"wallet": t["wallet"], "role": t["role"], "buy_before": 0, "buy_after": 0, "sell_after": 0})
                wv[("buy_before" if k[2] == "before" else "buy_after") if t["side"] == "buy" else "sell_after"] += 1
        out = []
        for cid, g in agg.items():
            ws = sorted(g["wallets"].values(), key=lambda x: -(x["buy_before"] + x["buy_after"] + x["sell_after"]))[:15]
            out.append({"channel_id": cid, "username": g["username"], "calls_checked": g["calls_checked"], "top_wallets": ws})
        known = await ctx().pool.fetchval("""SELECT count(*) FROM (SELECT address FROM wallets WHERE status IN ('smart','tracked','candidate')
            AND NOT is_excluded UNION SELECT address FROM watched_wallets WHERE confirmed) s""")
        return {"wallets_known": known, "channels": out}

    app.include_router(router)
