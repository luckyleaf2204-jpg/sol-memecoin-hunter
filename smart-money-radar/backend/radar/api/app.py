"""REST + WebSocket API for the dashboard. Read-only towards the blockchain: no endpoint
signs, sends or prepares a transaction. API keys are never returned (only "configured": bool)."""
from __future__ import annotations

import asyncio
import datetime as dt
import decimal
import json
import logging
import time
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from ..analytics.signals import flow_label
from ..context import EVENTS_CHANNEL, Q_PRICE_BACKFILL, Q_VERIFY, Q_WALLET_SYNC, Ctx, create_ctx
from ..db import repo
from ..settings import DEFAULTS, SettingsError
from ..solana.base58 import is_valid_address
from . import auth

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
CTX: Ctx | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global CTX
    from ..config import get_config
    cfg = get_config()
    if not cfg.dashboard_password and not auth.auth_disabled():
        raise SystemExit("DASHBOARD_PASSWORD is required (or ALLOW_NO_AUTH=1 for local development)")
    CTX = await create_ctx("api", run_migrations=True, pool_size=10)
    yield
    await CTX.close()


app = FastAPI(title="Smart Money Radar API", version="1.0.0", lifespan=lifespan,
              docs_url="/api/docs", openapi_url="/api/openapi.json", redoc_url=None)


def _cors():
    from ..config import get_config
    origins = list(get_config().cors_origins)
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_credentials=True,
                           allow_methods=["GET", "POST", "PUT"], allow_headers=["content-type"])


_cors()


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def ctx() -> Ctx:
    assert CTX is not None
    return CTX


def j(v: Any) -> Any:
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, (dt.datetime, dt.date)):
        return v.isoformat()
    if isinstance(v, dict):
        return {k: j(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [j(x) for x in v]
    return v


def rows(rs) -> list[dict]:
    return [j(dict(r)) for r in rs]


Auth = Depends(auth.require_auth)


# ---------------------------------------------------------------- auth / health
class Login(BaseModel):
    password: str


@app.post("/api/auth/login")
async def login(body: Login, request: Request, response: Response):
    ip = request.headers.get("x-forwarded-for", request.client.host if request.client else "?").split(",")[0].strip()
    key = f"login_fail:{ip}"
    fails = int(await ctx().redis.get(key) or 0)
    if fails >= 10:
        raise HTTPException(429, "too many attempts, try again in 15 minutes")
    if not auth.password_ok(body.password):
        await ctx().redis.set(key, fails + 1, ex=900)
        await asyncio.sleep(0.8)
        raise HTTPException(401, "wrong password")
    await ctx().redis.delete(key)
    secure = request.headers.get("x-forwarded-proto", request.url.scheme) == "https"
    response.set_cookie(auth.COOKIE, auth.make_token(ctx().cfg.session_ttl_hours), httponly=True, samesite="lax",
                        secure=secure, max_age=ctx().cfg.session_ttl_hours * 3600, path="/")
    return {"ok": True}


@app.post("/api/auth/logout")
async def logout(response: Response):
    response.delete_cookie(auth.COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/me")
async def me(request: Request):
    return {"authenticated": auth.auth_disabled() or auth.check_token(request.cookies.get(auth.COOKIE))}


@app.get("/api/health")
async def health():
    try:
        await ctx().pool.fetchval("SELECT 1")
        await ctx().redis.ping()
        return {"ok": True}
    except Exception:
        raise HTTPException(503, "unhealthy")


# ---------------------------------------------------------------- overview
@app.get("/api/overview", dependencies=[Auth])
async def overview(period: int = 30):
    c = ctx()
    w = await c.pool.fetchrow("""SELECT count(*) FILTER (WHERE status IN ('candidate','tracked','smart')) AS tracked,
            count(*) FILTER (WHERE status = 'smart') AS smart,
            count(*) FILTER (WHERE status = 'candidate') AS candidates,
            count(*) FILTER (WHERE backfill_status IN ('queued','running')) AS backfilling FROM wallets""")
    official = await c.pool.fetchval("SELECT count(*) FROM smart_money_scores WHERE period_days = $1 "
                                     "AND official_rank IS NOT NULL", period)
    flow = await c.pool.fetchrow("""SELECT COALESCE(sum(t.sol_amount) FILTER (WHERE t.tx_type='buy'),0) AS buy,
            COALESCE(sum(t.sol_amount) FILTER (WHERE t.tx_type='sell'),0) AS sell, count(DISTINCT t.mint) AS tokens
        FROM wallet_transactions t JOIN wallets w ON w.address = t.wallet AND w.status = 'smart'
        WHERE t.block_time > now() - interval '24 hours'""")
    accum = await c.pool.fetchval("""SELECT count(*) FROM (SELECT t.mint FROM wallet_transactions t
        JOIN wallets w ON w.address = t.wallet AND w.status = 'smart' WHERE t.block_time > now() - interval '24 hours'
        AND t.tx_type IN ('buy','sell') GROUP BY t.mint
        HAVING sum(CASE WHEN t.tx_type='buy' THEN t.sol_amount ELSE -t.sol_amount END) > 0) q""")
    unseen = await c.pool.fetchval("SELECT count(*) FROM alerts WHERE NOT seen")
    sigs = await c.pool.fetch("""SELECT s.*, k.name, k.symbol FROM smart_money_signals s LEFT JOIN tokens k ON k.mint = s.mint
        WHERE s.detected_at > now() - interval '24 hours' AND s.status = 'active'
        ORDER BY CASE s.level WHEN 'VERY_STRONG' THEN 3 WHEN 'STRONG' THEN 2 ELSE 1 END DESC, s.total_buy_sol DESC LIMIT 6""")
    stream = await c.pool.fetchrow("SELECT status, details, updated_at FROM api_sync_status WHERE source='solana_ws'")
    obs = await c.pool.fetchrow("""SELECT count(*) AS trades, count(DISTINCT wallet) AS wallets FROM wallet_transactions
        WHERE block_time > now() - interval '24 hours'""")
    return j({"wallets": dict(w), "official_ranked": official, "smart_buy_sol_24h": flow["buy"],
              "smart_sell_sol_24h": flow["sell"], "smart_tokens_24h": flow["tokens"], "tokens_accumulating": accum,
              "unseen_alerts": unseen, "strongest_signals": rows(sigs), "stream": dict(stream) if stream else None,
              "observed_24h": dict(obs), "sol_usd": await c.sol_usd(), "trading_enabled": False})


# ---------------------------------------------------------------- wallets
SORTS = {"score": "sc.score", "win_rate": "p.win_rate", "median_buy": "p.median_buy_sol",
         "realized_pnl": "p.realized_pnl_sol", "roi": "p.roi", "hit_5x": "p.hit_rate_5x", "hit_10x": "p.hit_rate_10x",
         "last_active": "p.last_active_at", "copy_win_rate": "copy_rank"}

WALLET_SELECT = """SELECT sc.wallet, sc.score, sc.category, sc.confidence, sc.qualifies, sc.official_rank, sc.reasons,
        sc.computed_at, p.win_rate, p.median_buy_sol, p.avg_buy_sol, p.largest_buy_sol, p.total_buy_sol,
        p.realized_pnl_sol, p.unrealized_pnl_sol, p.roi, p.hit_rate_2x, p.hit_rate_3x, p.hit_rate_5x, p.hit_rate_10x,
        p.last_active_at, p.closed_qualifying, p.positions_qualifying, p.wins, p.losses, p.verified_share,
        p.coverage_complete, p.profit_factor, w.status, w.cluster_id, w.cluster_size, w.backfill_status,
        p.avg_holding_seconds, p.metrics->'behavior'->>'label' AS behavior,
        p.metrics->'behavior'->'metrics'->>'median_entry_after_creation_s' AS median_entry_after_creation_s,
        sc.components->'copy' AS copy, (sc.components->'copy'->>'win_rate')::float AS copy_win_rate,
        -- Laplace-smoothed copy win rate for sorting: 1/1 must not outrank 9/10
        (COALESCE((sc.components->'copy'->>'win_rate')::float * (sc.components->'copy'->>'positions')::float, 0) + 1)
          / (COALESCE((sc.components->'copy'->>'positions')::float, 0) + 2) AS copy_rank
    FROM smart_money_scores sc JOIN wallet_performance p ON p.wallet = sc.wallet AND p.period_days = sc.period_days
    JOIN wallets w ON w.address = sc.wallet"""


@app.get("/api/wallets/top", dependencies=[Auth])
async def top_wallets(period: int = Query(30, enum=[7, 30, 90]), sort: str = "score", limit: int = 10,
                      official: bool = True, order: str = "desc", human_only: bool = False):
    col = SORTS.get(sort, "sc.score")
    direction = "ASC" if order == "asc" else "DESC"
    where = "sc.period_days = $1 AND NOT w.is_excluded" + (" AND sc.official_rank IS NOT NULL" if official else "")
    if human_only:
        where += " AND p.metrics->'behavior'->>'label' = 'HUMAN'"
    limit = max(1, min(limit, 500))
    if official:
        # Top N is always the official N by score; sorting re-orders those N rows
        rs = await ctx().pool.fetch(f"""SELECT * FROM ({WALLET_SELECT} WHERE {where} ORDER BY sc.official_rank LIMIT $2) q
            ORDER BY {col.split('.')[-1]} {direction} NULLS LAST""", period, limit)
    else:
        rs = await ctx().pool.fetch(f"{WALLET_SELECT} WHERE {where} ORDER BY {col} {direction} NULLS LAST LIMIT $2",
                                    period, limit)
    return {"period": period, "sort": sort, "official": official, "rows": rows(rs)}


CHECK_LABELS = {
    "mandatory_criteria": "Đạt tiêu chí bắt buộc (WR ≥ 65%, Median ≥ 2 SOL, PnL & ROI > 0, ≥ 5 vị thế đóng)",
    "confidence_high_or_medium": "Độ tin cậy dữ liệu High/Medium",
    "score_at_least_65": "Smart Money Score ≥ 65",
    "repeatable_both_halves": "Hiệu suất lặp lại ở cả hai nửa kỳ",
    "verified_share_ok": "≥ 50% vị thế đã xác minh on-chain",
    "active_last_7_days": "Có giao dịch trong 7 ngày gần nhất",
    "not_single_win_dependent": "Vẫn có lãi khi bỏ lệnh thắng lớn nhất",
    "human_like": "Giao dịch giống người, không phải bot",
    "copyable": "Copy trễ vẫn thắng đủ thường xuyên (mô phỏng theo giá lịch sử)",
}


@app.get("/api/wallets/candidates", dependencies=[Auth])
async def candidate_wallets(period: int = Query(30, enum=[7, 30, 90]), limit: int = 20, human_only: bool = False):
    """Top 20 candidates (wallets whose history has been read) with the review checklist.
    Wallets passing every check, best of each relationship cluster, form the official Top 10."""
    hum = " AND p.metrics->'behavior'->>'label' = 'HUMAN'" if human_only else ""
    rs = await ctx().pool.fetch(f"""{WALLET_SELECT} WHERE sc.period_days = $1 AND NOT w.is_excluded
        AND w.backfill_status IN ('complete','partial'){hum}
        ORDER BY sc.score DESC, p.median_buy_sol DESC NULLS LAST LIMIT $2""", period, max(1, min(limit, 50)))
    comps = {r["wallet"]: r["components"] for r in await ctx().pool.fetch(
        "SELECT wallet, components FROM smart_money_scores WHERE period_days = $1 AND wallet = ANY($2::text[])",
        period, [r["wallet"] for r in rs])}
    out = []
    seen_cluster: dict[str, str] = {}
    for r in rs:
        d = j(dict(r))
        checks = (comps.get(r["wallet"]) or {}).get("checks") or {}
        cl = r["cluster_id"] or r["wallet"]
        dup_of = seen_cluster.get(cl)
        if dup_of is None and all(checks.values()) and checks:
            seen_cluster[cl] = r["wallet"]
        d["checks"] = [{"key": k, "label": CHECK_LABELS.get(k, k), "ok": bool(v)} for k, v in checks.items()]
        if not checks:
            d["checks"] = [{"key": "not_recomputed", "label": "Chưa được tính lại với bảng kiểm (chờ chu kỳ phân tích)",
                            "ok": False}]
        d["checks"].append({"key": "independent", "label": "Không trùng chủ với ví xếp trên",
                            "ok": dup_of is None or dup_of == r["wallet"]})
        d["repeatability"] = ((comps.get(r["wallet"]) or {}).get("checks") or {}).get("repeatable_both_halves")
        out.append(d)
    return {"period": period, "rows": out}


@app.get("/api/wallets", dependencies=[Auth])
async def list_wallets(status: str | None = None, q: str | None = None, period: int = 30, page: int = 1,
                       page_size: int = 50):
    page_size = max(1, min(page_size, 200))
    args: list[Any] = [period]
    cond = ["TRUE"]
    if status:
        args.append(status)
        cond.append(f"w.status = ${len(args)}")
    if q:
        args.append(q.strip() + "%")
        cond.append(f"w.address LIKE ${len(args)}")
    args += [page_size, (page - 1) * page_size]
    rs = await ctx().pool.fetch(f"""SELECT w.address, w.status, w.discovery_source, w.backfill_status, w.cluster_size,
            COALESCE((w.discovery_detail->>'high_frequency')::boolean, false) AS high_frequency,
            w.is_excluded, w.last_synced_at, sc.score, sc.category, sc.confidence, sc.official_rank, p.win_rate,
            p.metrics->'behavior'->>'label' AS behavior, (sc.components->'copy'->>'win_rate')::float AS copy_win_rate,
            p.median_buy_sol, p.realized_pnl_sol, p.closed_qualifying, p.last_active_at
        FROM wallets w LEFT JOIN smart_money_scores sc ON sc.wallet = w.address AND sc.period_days = $1
        LEFT JOIN wallet_performance p ON p.wallet = w.address AND p.period_days = $1
        WHERE {' AND '.join(cond)} ORDER BY sc.score DESC NULLS LAST, w.first_seen_at DESC
        LIMIT ${len(args) - 1} OFFSET ${len(args)}""", *args)
    total = await ctx().pool.fetchval(f"SELECT count(*) FROM wallets w WHERE $1::int IS NOT NULL AND {' AND '.join(cond)}",
                                      *args[:-2])
    return {"total": total, "rows": rows(rs)}


def _addr(a: str) -> str:
    if not is_valid_address(a):
        raise HTTPException(400, "invalid Solana address")
    return a


@app.get("/api/wallets/{address}", dependencies=[Auth])
async def wallet_detail(address: str):
    a = _addr(address)
    c = ctx()
    w = await c.pool.fetchrow("SELECT * FROM wallets WHERE address = $1", a)
    if w is None:
        raise HTTPException(404, "wallet not tracked")
    perf = await c.pool.fetch("SELECT * FROM wallet_performance WHERE wallet = $1 ORDER BY period_days", a)
    sc = await c.pool.fetch("SELECT * FROM smart_money_scores WHERE wallet = $1 ORDER BY period_days", a)
    rel = await c.pool.fetch("""SELECT * FROM wallet_relationships WHERE wallet_a = $1 OR wallet_b = $1
        ORDER BY strength DESC LIMIT 50""", a)
    members = await c.pool.fetch("SELECT address, status FROM wallets WHERE cluster_id = $1 AND address <> $2 LIMIT 50",
                                 w["cluster_id"], a) if w["cluster_id"] else []
    curve = await c.pool.fetch("""SELECT closed_at, realized_pnl_sol, mint FROM wallet_positions WHERE wallet = $1
        AND status IN ('closed','written_off') AND qualifying ORDER BY closed_at""", a)
    cum, pts = 0.0, []
    for r in curve:
        cum += float(r["realized_pnl_sol"])
        pts.append({"t": r["closed_at"].isoformat(), "pnl": float(r["realized_pnl_sol"]), "cum": cum, "mint": r["mint"]})
    failed = await c.pool.fetchval("SELECT count(*) FROM failed_transactions WHERE wallet = $1", a)
    return j({"wallet": dict(w), "performance": rows(perf), "scores": rows(sc), "relationships": rows(rel),
              "cluster_members": rows(members), "pnl_curve": pts, "failed_transactions": failed,
              "solscan": f"https://solscan.io/account/{a}"})


@app.get("/api/wallets/{address}/positions", dependencies=[Auth])
async def wallet_positions(address: str, period: int = 90, status: str | None = None, qualifying: bool | None = None):
    a = _addr(address)
    args: list[Any] = [a, period]
    cond = ["p.wallet = $1", "COALESCE(p.first_buy_at, p.opened_at) >= now() - make_interval(days => $2)"]
    if status:
        args.append(status)
        cond.append(f"p.status = ${len(args)}")
    if qualifying is not None:
        args.append(qualifying)
        cond.append(f"p.qualifying = ${len(args)}")
    rs = await ctx().pool.fetch(f"""SELECT p.*, k.name, k.symbol FROM wallet_positions p
        LEFT JOIN tokens k ON k.mint = p.mint WHERE {' AND '.join(cond)} ORDER BY p.opened_at DESC LIMIT 1000""", *args)
    return {"rows": rows(rs)}


@app.get("/api/wallets/{address}/transactions", dependencies=[Auth])
async def wallet_transactions(address: str, limit: int = 200, before: str | None = None, mint: str | None = None):
    a = _addr(address)
    args: list[Any] = [a]
    cond = ["t.wallet = $1"]
    if before:
        args.append(dt.datetime.fromisoformat(before))
        cond.append(f"t.block_time < ${len(args)}")
    if mint:
        args.append(_addr(mint))
        cond.append(f"t.mint = ${len(args)}")
    args.append(max(1, min(limit, 1000)))
    rs = await ctx().pool.fetch(f"""SELECT t.*, k.name, k.symbol FROM wallet_transactions t LEFT JOIN tokens k ON k.mint = t.mint
        WHERE {' AND '.join(cond)} ORDER BY t.block_time DESC, t.event_key LIMIT ${len(args)}""", *args)
    return {"rows": rows(rs)}


class AddWallet(BaseModel):
    address: str


@app.post("/api/wallets", dependencies=[Auth])
async def add_wallet(body: AddWallet):
    a = _addr(body.address.strip())
    async with ctx().pool.acquire() as conn:
        await repo.ensure_wallets(conn, [(a, "manual", {})])
        await conn.execute("UPDATE wallets SET status = CASE WHEN status = 'observed' THEN 'candidate' ELSE status END, "
                           "backfill_status = CASE WHEN backfill_status IN ('none','failed') THEN 'queued' "
                           "ELSE backfill_status END WHERE address = $1", a)
    await ctx().enqueue(Q_WALLET_SYNC, {"wallet": a, "mode": "backfill"}, dedupe_key=a)
    return {"ok": True, "address": a}


class Exclude(BaseModel):
    excluded: bool
    reason: str | None = None


@app.post("/api/wallets/{address}/exclude", dependencies=[Auth])
async def exclude_wallet(address: str, body: Exclude):
    a = _addr(address)
    s = await ctx().settings.get()
    ex = set(s["excluded_wallets"])
    ex.add(a) if body.excluded else ex.discard(a)
    await ctx().settings.update({"excluded_wallets": sorted(ex)})
    await ctx().pool.execute("UPDATE wallets SET is_excluded = $2, exclude_reason = $3, "
                             "status = CASE WHEN $2 THEN 'excluded' ELSE 'tracked' END WHERE address = $1",
                             a, body.excluded, body.reason)
    await ctx().redis.sadd("set:dirty_wallets", a)
    return {"ok": True}


# ---------------------------------------------------------------- token radar
@app.get("/api/radar", dependencies=[Auth])
async def radar(hours: int = 24, limit: int = 100):
    hours = max(1, min(hours, 168))
    c = ctx()
    rs = await c.pool.fetch("""WITH sm AS (
            SELECT t.* FROM wallet_transactions t JOIN wallets w ON w.address = t.wallet AND w.status = 'smart'
            WHERE t.block_time > now() - make_interval(hours => $1) AND t.tx_type IN ('buy','sell'))
        SELECT sm.mint, count(DISTINCT sm.wallet) FILTER (WHERE tx_type='buy') AS smart_buyers,
            count(DISTINCT w2.cluster_id) FILTER (WHERE tx_type='buy') AS independent_buyers,
            COALESCE(sum(sol_amount) FILTER (WHERE tx_type='buy'),0) AS smart_buy_sol,
            COALESCE(sum(sol_amount) FILTER (WHERE tx_type='sell'),0) AS smart_sell_sol,
            min(block_time) FILTER (WHERE tx_type='buy') AS first_smart_entry,
            max(block_time) AS last_smart_trade,
            bool_and(verification_status = 'verified') AS all_verified
        FROM sm LEFT JOIN wallets w2 ON w2.address = sm.wallet GROUP BY sm.mint
        ORDER BY smart_buy_sol DESC LIMIT $2""", hours, max(1, min(limit, 300)))
    mints = [r["mint"] for r in rs]
    toks = {r["mint"]: r for r in await c.pool.fetch("SELECT * FROM tokens WHERE mint = ANY($1::text[])", mints)}
    risk = {r["mint"]: r for r in await c.pool.fetch("""SELECT DISTINCT ON (mint) mint, risk_level, risk_points, computed_at
        FROM token_risk_reports WHERE mint = ANY($1::text[]) ORDER BY mint, computed_at DESC""", mints)}
    sig = {r["mint"]: r for r in await c.pool.fetch("""SELECT DISTINCT ON (mint) mint, level, detected_at FROM smart_money_signals
        WHERE mint = ANY($1::text[]) AND status = 'active' ORDER BY mint, detected_at DESC""", mints)}
    chg = {r["mint"]: r for r in await c.pool.fetch("""SELECT mint,
            (SELECT close_sol FROM token_prices p WHERE p.mint = m.mint AND source='onchain_trades' ORDER BY bucket_start DESC LIMIT 1) AS now,
            (SELECT close_sol FROM token_prices p WHERE p.mint = m.mint AND source='onchain_trades' AND bucket_start <= now() - interval '5 minutes' ORDER BY bucket_start DESC LIMIT 1) AS m5,
            (SELECT close_sol FROM token_prices p WHERE p.mint = m.mint AND source='onchain_trades' AND bucket_start <= now() - interval '15 minutes' ORDER BY bucket_start DESC LIMIT 1) AS m15,
            (SELECT close_sol FROM token_prices p WHERE p.mint = m.mint AND source='onchain_trades' AND bucket_start <= now() - interval '1 hour' ORDER BY bucket_start DESC LIMIT 1) AS h1,
            (SELECT COALESCE(sum(volume_sol),0) FROM token_prices p WHERE p.mint = m.mint AND source='onchain_trades' AND bucket_start > now() - interval '1 hour') AS vol_1h
        FROM unnest($1::text[]) AS m(mint)""", mints)}
    sol_usd = await c.sol_usd()
    out = []
    for r in rs:
        t = toks.get(r["mint"])
        ch = chg.get(r["mint"]) or {}
        nowp = ch.get("now") or (t["last_price_sol"] if t else None)

        def pct(old):
            return (float(nowp) / float(old) - 1) if (nowp and old) else None
        net = float(r["smart_buy_sol"]) - float(r["smart_sell_sol"])
        gross = float(r["smart_buy_sol"]) + float(r["smart_sell_sol"])
        rk = risk.get(r["mint"])
        out.append(j({
            "mint": r["mint"], "name": t["name"] if t else None, "symbol": t["symbol"] if t else None,
            "creator": t["creator"] if t else None, "market_cap_sol": t["last_mc_sol"] if t else None,
            "market_cap_usd": (t["market_cap_usd"] if t and t["market_cap_usd"] else
                               (float(t["last_mc_sol"]) * sol_usd if t and t["last_mc_sol"] and sol_usd else None)),
            "liquidity_sol": t["last_liquidity_sol"] if t else None, "liquidity_usd": t["liquidity_usd"] if t else None,
            "volume_1h_sol": ch.get("vol_1h"), "volume_24h_usd": t["volume_24h_usd"] if t else None,
            "holder_count": t["holder_count"] if t else None,
            "smart_buyers": r["smart_buyers"], "independent_buyers": r["independent_buyers"],
            "smart_buy_sol": r["smart_buy_sol"], "smart_sell_sol": r["smart_sell_sol"], "net_flow_sol": net,
            "flow": flow_label(net, gross), "first_smart_entry": r["first_smart_entry"],
            "last_smart_trade": r["last_smart_trade"], "current_price_sol": nowp,
            "price_change_5m": pct(ch.get("m5")), "price_change_15m": pct(ch.get("m15")), "price_change_1h": pct(ch.get("h1")),
            "token_age_seconds": (time.time() - t["created_at"].timestamp()) if t and t["created_at"] else None,
            "curve_progress": t["curve_progress"] if t else None, "migrated": bool(t and t["migrated_at"]),
            "risk_level": rk["risk_level"] if rk else None, "signal_level": sig[r["mint"]]["level"] if r["mint"] in sig else None,
            "all_verified": r["all_verified"]}))
    return {"hours": hours, "rows": out}


@app.get("/api/tokens/{mint}", dependencies=[Auth])
async def token_detail(mint: str):
    m = _addr(mint)
    c = ctx()
    t = await c.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", m)
    if t is None:
        raise HTTPException(404, "token not seen")
    risk = await c.pool.fetchrow("SELECT * FROM token_risk_reports WHERE mint = $1 ORDER BY computed_at DESC LIMIT 1", m)
    trades = await c.pool.fetch("""SELECT t.*, w.status AS wallet_status, sc.score FROM wallet_transactions t
        JOIN wallets w ON w.address = t.wallet AND w.status IN ('smart','tracked')
        LEFT JOIN smart_money_scores sc ON sc.wallet = t.wallet AND sc.period_days = 30
        WHERE t.mint = $1 ORDER BY t.block_time DESC LIMIT 300""", m)
    bars = await c.pool.fetch("""SELECT bucket_start, open_sol, high_sol, low_sol, close_sol, volume_sol, liquidity_sol
        FROM token_prices WHERE mint = $1 AND source = 'onchain_trades' AND bucket_start > now() - interval '24 hours'
        ORDER BY bucket_start""", m)
    sigs = await c.pool.fetch("SELECT * FROM smart_money_signals WHERE mint = $1 ORDER BY detected_at DESC LIMIT 20", m)
    holders = await c.pool.fetch("""SELECT * FROM token_holders WHERE mint = $1 AND snapshot_at =
        (SELECT max(snapshot_at) FROM token_holders WHERE mint = $1) ORDER BY rank""", m)
    from ..services.analytics_runner import holder_snapshot
    supply = float(t["total_supply_raw"] or 1e15) / 10 ** (t["decimals"] or 6)
    hs = await holder_snapshot(c, t, supply)
    ages = {r["address"]: r for r in await c.pool.fetch(
        "SELECT * FROM wallet_age WHERE address = ANY($1::text[])", [h["wallet"] for h in hs["holders"]])}
    st = await c.settings.get()
    import datetime as _dt
    for h in hs["holders"]:
        a = ages.get(h["wallet"])
        h["is_creator"] = h["wallet"] == t["creator"]
        h["over_limit"] = (h["pct"] or 0) > float(st["holder_alert_pct"])
        h["funder"] = a["funder"] if a else None
        h["first_activity_at"] = a["first_activity_at"] if a else None
        h["age_known"] = bool(a and a["history_complete"])
        h["fresh"] = bool(a and a["history_complete"] and a["first_activity_at"] and
                          (_dt.datetime.now(UTC) - a["first_activity_at"]).total_seconds() < float(st["fresh_wallet_hours"]) * 3600)
    funder_count: dict[str, int] = {}
    for h in hs["holders"]:
        if h["funder"]:
            funder_count[h["funder"]] = funder_count.get(h["funder"], 0) + 1
    for h in hs["holders"]:
        h["linked"] = bool(h["funder"] and funder_count.get(h["funder"], 0) >= 2)
    fees = await c.pool.fetchrow("""SELECT COALESCE(sum(fees_sol),0) AS fees, COALESCE(sum(volume_sol),0) AS vol
        FROM token_prices WHERE mint = $1 AND source = 'onchain_trades'""", m)
    return j({"token": dict(t), "risk": dict(risk) if risk else None, "smart_trades": rows(trades), "bars": rows(bars),
              "signals": rows(sigs), "holders": rows(holders),
              "holder_estimate": {"source": hs["source"], "complete": hs["complete"], "rows": hs["holders"][:20]},
              "flow_metrics": hs["flow_metrics"],
              "fees": {"fees_sol": fees["fees"], "volume_sol": fees["vol"],
                       "volume_to_mc": (float(fees["vol"]) / float(t["last_mc_sol"])) if t["last_mc_sol"] else None},
              "links": {"pumpfun": f"https://pump.fun/coin/{m}", "dexscreener": f"https://dexscreener.com/solana/{m}",
                        "solscan": f"https://solscan.io/token/{m}"}})


# ---------------------------------------------------------------- signals / alerts
@app.get("/api/signals", dependencies=[Auth])
async def signals(limit: int = 100, level: str | None = None):
    args: list[Any] = [max(1, min(limit, 500))]
    cond = "TRUE"
    if level:
        args.append(level)
        cond = "s.level = $2"
    rs = await ctx().pool.fetch(f"""SELECT s.*, k.name, k.symbol FROM smart_money_signals s LEFT JOIN tokens k ON k.mint = s.mint
        WHERE {cond} ORDER BY s.detected_at DESC LIMIT $1""", *args)
    return {"rows": rows(rs)}


@app.get("/api/alerts", dependencies=[Auth])
async def alerts(unseen_only: bool = False, limit: int = 200):
    rs = await ctx().pool.fetch(f"""SELECT a.id, a.signal_id, a.mint, a.level, a.reason, a.channel, a.status, a.message,
            a.error, a.created_at, a.sent_at, a.seen, a.seen_at, k.name, k.symbol, s.independent_buyers, s.total_buy_sol,
            s.net_flow_sol, s.risk_level, s.price_at_detection_sol
        FROM alerts a LEFT JOIN tokens k ON k.mint = a.mint LEFT JOIN smart_money_signals s ON s.id = a.signal_id
        {'WHERE NOT a.seen' if unseen_only else ''} ORDER BY a.created_at DESC LIMIT $1""", max(1, min(limit, 1000)))
    return {"rows": rows(rs)}


@app.post("/api/alerts/{alert_id}/seen", dependencies=[Auth])
async def alert_seen(alert_id: int, seen: bool = True):
    await ctx().pool.execute("UPDATE alerts SET seen = $2, seen_at = CASE WHEN $2 THEN now() END WHERE id = $1",
                             alert_id, seen)
    return {"ok": True}


@app.post("/api/alerts/seen-all", dependencies=[Auth])
async def alerts_seen_all():
    await ctx().pool.execute("UPDATE alerts SET seen = true, seen_at = now() WHERE NOT seen")
    return {"ok": True}


# ---------------------------------------------------------------- settings / system
def _secret_status() -> dict:
    cfg = ctx().cfg
    return {"helius_api_key": bool(cfg.helius_api_key), "birdeye_api_key": bool(cfg.birdeye_api_key),
            "solscan_api_key": bool(cfg.solscan_api_key), "pumpportal_api_key": bool(cfg.pumpportal_api_key),
            "telegram_bot_token": bool(cfg.telegram_bot_token), "telegram_chat_id_env": bool(cfg.telegram_chat_id),
            "rpc_endpoints": [e.name for e in cfg.rpc_endpoints]}


@app.get("/api/settings", dependencies=[Auth])
async def get_settings():
    return {"settings": await ctx().settings.get(), "defaults": DEFAULTS, "secrets": _secret_status()}


class SettingsUpdate(BaseModel):
    changes: dict[str, Any]


@app.put("/api/settings", dependencies=[Auth])
async def put_settings(body: SettingsUpdate):
    try:
        s = await ctx().settings.update(body.changes)
    except SettingsError as e:
        raise HTTPException(400, str(e))
    if "excluded_wallets" in body.changes:
        ex = s["excluded_wallets"]
        await ctx().pool.execute("UPDATE wallets SET is_excluded = (address = ANY($1::text[])), "
                                 "status = CASE WHEN address = ANY($1::text[]) THEN 'excluded' "
                                 "WHEN status = 'excluded' THEN 'tracked' ELSE status END", ex)
    return {"settings": s}


@app.post("/api/settings/test-telegram", dependencies=[Auth])
async def test_telegram():
    from ..services.telegram import send_message
    s = await ctx().settings.get()
    chat = s.get("telegram_chat_id") or ctx().cfg.telegram_chat_id
    if not ctx().cfg.telegram_bot_token or not chat:
        raise HTTPException(400, "TELEGRAM_BOT_TOKEN (env) and a chat id are required")
    try:
        await send_message(ctx().http, ctx().cfg.telegram_bot_token, chat,
                           "✅ Smart Money Radar: kết nối Telegram hoạt động.")
    except Exception as e:
        raise HTTPException(502, f"telegram error: {str(e)[:200]}")
    return {"ok": True}


@app.get("/api/system/status", dependencies=[Auth])
async def system_status():
    c = ctx()
    src = await c.pool.fetch("SELECT * FROM api_sync_status WHERE source <> 'seed' ORDER BY source, job")
    hb = await c.pool.fetch("SELECT * FROM worker_heartbeats ORDER BY worker")
    q = {}
    for name in (Q_VERIFY, Q_WALLET_SYNC, Q_PRICE_BACKFILL):
        try:
            q[name] = await c.redis.xlen(name)
        except Exception:
            q[name] = None
    counts = await c.pool.fetchrow("""SELECT (SELECT count(*) FROM wallet_transactions) AS trades,
        (SELECT count(*) FROM wallet_transactions WHERE verification_status='verified') AS verified,
        (SELECT count(*) FROM wallet_transactions WHERE verification_status='partially_verified') AS partial,
        (SELECT count(*) FROM tokens) AS tokens, (SELECT count(*) FROM token_prices) AS price_bars,
        (SELECT count(*) FROM raw_transactions) AS raw_txs, (SELECT count(*) FROM failed_transactions) AS failed_txs""")
    return j({"sources": rows(src), "workers": rows(hb), "queues": q, "counts": dict(counts),
              "secrets": _secret_status(), "server_time": dt.datetime.now(UTC)})


@app.get("/api/backtest/latest", dependencies=[Auth])
async def backtest_latest():
    r = await ctx().pool.fetchrow("SELECT id, created_at, params, results FROM backtest_runs ORDER BY id DESC LIMIT 1")
    if r is None:
        return {"run": None}
    res = dict(r["results"])
    res.pop("rows", None)
    return j({"run": {"id": r["id"], "created_at": r["created_at"], "params": r["params"], "results": res}})


# ---------------------------------------------------------------- websocket
@app.websocket("/api/ws")
async def ws(websocket: WebSocket):
    if not (auth.auth_disabled() or auth.check_token(websocket.cookies.get(auth.COOKIE))):
        await websocket.close(code=4401)
        return
    await websocket.accept()
    pubsub = ctx().redis.pubsub()
    await pubsub.subscribe(EVENTS_CHANNEL)
    try:
        while True:
            msg = await pubsub.get_message(ignore_subscribe_messages=True, timeout=20)
            if msg and msg.get("type") == "message":
                await websocket.send_text(msg["data"])
            else:
                await websocket.send_text(json.dumps({"type": "ping", "t": time.time()}))
    except (WebSocketDisconnect, RuntimeError):
        pass
    finally:
        await pubsub.unsubscribe(EVENTS_CHANNEL)
        await pubsub.aclose()
