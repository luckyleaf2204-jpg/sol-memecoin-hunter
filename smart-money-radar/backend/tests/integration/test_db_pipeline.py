"""Integration tests against a real PostgreSQL + Redis (TEST_DATABASE_URL / TEST_REDIS_URL).
Data = the real mainnet transactions in tests/fixtures/tx."""
import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tx"


@pytest.fixture(scope="session")
async def ctx():
    os.environ["DATABASE_URL"] = os.environ["TEST_DATABASE_URL"]
    os.environ["REDIS_URL"] = os.environ["TEST_REDIS_URL"]
    os.environ.setdefault("DASHBOARD_PASSWORD", "integration-pass")
    os.environ.setdefault("SESSION_SECRET", "integration-secret")
    os.environ["LOG_JSON"] = "false"
    from radar.config import reset_config_for_tests
    reset_config_for_tests()
    import asyncpg
    conn = await asyncpg.connect(os.environ["TEST_DATABASE_URL"])
    await conn.execute("DROP SCHEMA public CASCADE; CREATE SCHEMA public;")
    await conn.close()
    from radar.context import create_ctx
    c = await create_ctx("test", run_migrations=True)
    await c.redis.flushdb()
    yield c
    await c.close()


def fixtures():
    return [(f.stem, json.loads(f.read_text())) for f in sorted(FIX.glob("*.json"))]


async def _write_all(ctx, keep):
    from radar.services.txproc import PoolCache, decode, write_decoded
    pools = PoolCache()
    async with ctx.pool.acquire() as conn:
        for sig, tx in fixtures():
            await write_decoded(conn, decode(tx, sig, pools), keep_wallets=keep, store_min_sol=0.0, sol_usd=140.0)


async def test_migrations_are_idempotent(ctx):
    from radar.db.migrate import migrate
    async with ctx.pool.acquire() as conn:
        assert await migrate(conn) == []      # already applied, nothing to do
        n = await conn.fetchval("SELECT count(*) FROM schema_migrations")
    assert n >= 2


async def test_writing_the_same_transactions_twice_creates_no_duplicates(ctx):
    await _write_all(ctx, set())
    n1 = await ctx.pool.fetchval("SELECT count(*) FROM wallet_transactions")
    await _write_all(ctx, set())
    n2 = await ctx.pool.fetchval("SELECT count(*) FROM wallet_transactions")
    assert n1 == n2 and n1 >= 30
    dup = await ctx.pool.fetchval("""SELECT count(*) FROM (SELECT signature, event_key FROM wallet_transactions
        GROUP BY 1, 2 HAVING count(*) > 1) q""")
    assert dup == 0


async def test_failed_transactions_are_stored_but_never_traded(ctx):
    from radar.services.txproc import PoolCache, decode, write_decoded
    failed = [(s, t) for s, t in fixtures() if t["meta"]["err"] is not None]
    assert failed
    async with ctx.pool.acquire() as conn:
        for sig, tx in failed:
            await write_decoded(conn, decode(tx, sig, PoolCache()), keep_wallets={tx["transaction"]["message"]["accountKeys"][0]["pubkey"]},
                                store_min_sol=0, sol_usd=None)
    sigs = [s for s, _ in failed]
    assert await ctx.pool.fetchval("SELECT count(*) FROM failed_transactions WHERE signature = ANY($1::text[])", sigs) == len(sigs)
    assert await ctx.pool.fetchval("SELECT count(*) FROM wallet_transactions WHERE signature = ANY($1::text[])", sigs) == 0


async def test_stream_row_is_upgraded_by_verifier(ctx):
    from radar.db import repo
    from radar.decoder.logs import records_from_logs
    from radar.services.txproc import PoolCache, decode, write_decoded
    sig, tx = next((s, t) for s, t in fixtures() if s.startswith("6acaFRzs"))
    async with ctx.pool.acquire() as conn:
        await conn.execute("DELETE FROM wallet_transactions WHERE signature = $1", sig)
        stream = records_from_logs(sig, tx["slot"], tx["meta"]["logMessages"])
        await repo.insert_stream_trades(conn, stream["trades"], None)
        row = await conn.fetchrow("SELECT source, verification_status FROM wallet_transactions WHERE signature = $1", sig)
        assert row["source"] == "rpc_logs" and row["verification_status"] == "partially_verified"
        await write_decoded(conn, decode(tx, sig, PoolCache()), keep_wallets=set(), store_min_sol=0, sol_usd=None)
        rows = await conn.fetch("SELECT source, verification_status FROM wallet_transactions WHERE signature = $1", sig)
    assert len(rows) == 1 and rows[0]["source"] == "rpc_tx" and rows[0]["verification_status"] == "verified"
    # a later stream insert must not downgrade the verified row
    async with ctx.pool.acquire() as conn:
        await repo.insert_stream_trades(conn, stream["trades"], None)
        assert await conn.fetchval("SELECT verification_status FROM wallet_transactions WHERE signature = $1", sig) == "verified"


async def test_positions_recomputed_from_stored_rows(ctx):
    from radar.services.analytics_runner import recompute_wallet
    wallet = "5XCbWZyQT3Hy3i6LUkD2yEoo3mduAEvsnEw3huEPJYwo"   # dev of 2iDdpR…pump (create + 3.46 SOL buy)
    await ctx.pool.execute("INSERT INTO wallets(address, status, backfill_status) VALUES ($1,'tracked','complete') "
                           "ON CONFLICT (address) DO NOTHING", wallet)
    s = await ctx.settings.get()
    import time
    out = await recompute_wallet(ctx, wallet, s, int(time.time()), 1, {})
    pos = await ctx.pool.fetch("SELECT * FROM wallet_positions WHERE wallet = $1", wallet)
    assert len(pos) == 1
    p = pos[0]
    assert p["mint"] == "2iDdpRNvUywMpcL6aKT75brjm9ehko4z9ss6DnE9pump"
    assert float(p["buy_sol"]) == pytest.approx(3.45679, abs=1e-5) and p["qualifying"]
    assert set(out) == {7, 30, 90}
    sc = await ctx.pool.fetchrow("SELECT * FROM smart_money_scores WHERE wallet = $1 AND period_days = 30", wallet)
    assert sc["confidence"] == "LOW" and not sc["qualifies"]   # one position is never enough


async def test_wallets_of_one_owner_end_in_one_cluster(ctx):
    from radar.services.analytics_runner import relationships_pass
    ws = ["Cw1111111111111111111111111111111111111111a", "Cw1111111111111111111111111111111111111111b",
          "Cw1111111111111111111111111111111111111111c", "Cw1111111111111111111111111111111111111111d"]
    for w in ws[:3]:
        await ctx.pool.execute("""INSERT INTO wallets(address, status, funding_source, funding_signature)
            VALUES ($1, 'tracked', 'FunderAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA', 'sig-' || $1)""", w)
    await ctx.pool.execute("INSERT INTO wallets(address, status) VALUES ($1, 'tracked')", ws[3])
    await relationships_pass(ctx, await ctx.settings.get())
    rows = {r["address"]: r for r in await ctx.pool.fetch("SELECT address, cluster_id, cluster_size FROM wallets "
                                                         "WHERE address = ANY($1::text[])", ws)}
    assert rows[ws[0]]["cluster_id"] == rows[ws[1]]["cluster_id"] == rows[ws[2]]["cluster_id"]
    assert rows[ws[0]]["cluster_size"] == 3 and rows[ws[3]]["cluster_size"] == 1
    edges = await ctx.pool.fetchval("SELECT count(*) FROM wallet_relationships WHERE relation_type = 'common_funder'")
    assert edges == 3


async def test_api_requires_login_and_validates_settings(ctx):
    import httpx
    from radar.api import app as appmod
    async with appmod.app.router.lifespan_context(appmod.app):
        transport = httpx.ASGITransport(app=appmod.app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            assert (await c.get("/api/overview")).status_code == 401
            assert (await c.post("/api/auth/login", json={"password": "nope"})).status_code == 401
            r = await c.post("/api/auth/login", json={"password": os.environ["DASHBOARD_PASSWORD"]})
            assert r.status_code == 200
            assert (await c.get("/api/overview")).status_code == 200
            assert (await c.get("/api/wallets/top?period=30")).status_code == 200
            assert (await c.get("/api/radar")).status_code == 200
            bad = await c.put("/api/settings", json={"changes": {"trading_enabled": True}})
            assert bad.status_code == 400
            bad = await c.put("/api/settings", json={"changes": {"excluded_wallets": ["not-an-address"]}})
            assert bad.status_code == 400
            ok = await c.put("/api/settings", json={"changes": {"min_buy_sol": 2.5}})
            assert ok.status_code == 200 and ok.json()["settings"]["min_buy_sol"] == 2.5
            await c.put("/api/settings", json={"changes": {"min_buy_sol": 2.0}})
            sec = (await c.get("/api/settings")).json()["secrets"]
            assert all(isinstance(v, (bool, list)) for v in sec.values())   # never the key itself
            assert (await c.get("/api/wallets/xyz")).status_code == 400


async def test_signal_storage_and_alert_dedupe(ctx):
    """Same level twice -> one alert; upgrade -> new alert; small change -> no resend."""
    import datetime as dt
    from radar.services.signal_engine import store_and_alert
    s = dict(await ctx.settings.get())
    s["telegram_enabled"] = False            # dashboard-only channel in tests
    mint = "Mint9999999999999999999999999999999999999999"
    await ctx.pool.execute("INSERT INTO tokens(mint, name, symbol, last_price_sol, last_mc_sol, last_liquidity_sol) "
                           "VALUES ($1, 'Test Token', 'TST', 0.00000003, 30, 12) ON CONFLICT DO NOTHING", mint)
    now = dt.datetime.now(dt.timezone.utc)

    def sig(level, n, total):
        buyers = [{"wallet": f"W{i}", "position_sol": total / n, "cluster_id": f"W{i}", "score": 80, "win_rate": 0.7}
                  for i in range(n)]
        return {"mint": mint, "level": level, "raw_buyers": n, "independent_buyers": n, "total_buy_sol": total,
                "total_sell_sol": 0.0, "net_flow_sol": total, "window_start": now, "window_end": now,
                "first_entry_price_sol": 3e-8, "price_at_detection_sol": 3e-8, "runup_multiple": 1.0,
                "market_cap_sol": 30.0, "liquidity_sol": 12.0, "risk_level": "MEDIUM", "avg_buyer_score": 80.0,
                "verification": "verified", "buyers": buyers, "downgrade_reasons": []}

    await store_and_alert(ctx, sig("WATCH", 2, 6.0), s)
    await store_and_alert(ctx, sig("WATCH", 2, 6.5), s)          # same level, tiny change -> no new alert
    rows = await ctx.pool.fetch("SELECT level, reason, status FROM alerts WHERE mint = $1 ORDER BY id", mint)
    assert [(r["level"], r["reason"]) for r in rows] == [("WATCH", "new")]
    assert rows[0]["status"] == "dashboard_only"
    await store_and_alert(ctx, sig("STRONG", 3, 12.0), s)        # upgrade -> alert
    rows = await ctx.pool.fetch("SELECT level, reason FROM alerts WHERE mint = $1 ORDER BY id", mint)
    assert [(r["level"], r["reason"]) for r in rows] == [("WATCH", "new"), ("STRONG", "upgrade")]
    sigs = await ctx.pool.fetch("SELECT level, status FROM smart_money_signals WHERE mint = $1 ORDER BY id", mint)
    assert [(r["level"], r["status"]) for r in sigs] == [("WATCH", "superseded"), ("STRONG", "active")]
    msg = await ctx.pool.fetchval("SELECT message FROM alerts WHERE mint = $1 AND level = 'STRONG'", mint)
    assert "SMART MONEY ALERT" in msg and mint in msg and "Test Token" in msg and "pump.fun/coin/" in msg


async def test_exit_alert_when_signal_wallets_sell(ctx):
    import datetime as dt
    from radar.services.signal_engine import check_exits
    s = dict(await ctx.settings.get())
    s["telegram_enabled"] = False
    mint = "MintExit99999999999999999999999999999999999"
    det = dt.datetime.now(dt.timezone.utc) - dt.timedelta(minutes=30)
    buyers = [{"wallet": "ExitWalletA", "position_sol": 4.0}, {"wallet": "ExitWalletB", "position_sol": 4.0}]
    await ctx.pool.execute("INSERT INTO tokens(mint, name, symbol) VALUES ($1,'Exit Token','EXT') ON CONFLICT DO NOTHING", mint)
    sid = await ctx.pool.fetchval("""INSERT INTO smart_money_signals(mint, level, detected_at, window_start, window_end,
        raw_buyers, independent_buyers, total_buy_sol, net_flow_sol, verification, buyers)
        VALUES ($1,'WATCH',$2,$2,$2,2,2,8,8,'verified',$3) RETURNING id""", mint, det, buyers)
    rows = []
    for w in ("ExitWalletA", "ExitWalletB"):
        rows.append((f"buy-{w}", w, det - dt.timedelta(minutes=2), "buy", 4.0, 1000.0))
    rows.append(("sell-A", "ExitWalletA", det + dt.timedelta(minutes=10), "sell", 5.0, 1000.0))   # A exits fully
    for sig_, w, t, side, sol, tok in rows:
        await ctx.pool.execute("""INSERT INTO wallet_transactions(signature, event_key, wallet, mint, slot, block_time,
            tx_type, sol_amount, token_amount, source, verification_status)
            VALUES ($1,'trade:0',$2,$3,1,$4,$5,$6,$7,'rpc_tx','verified')""", sig_, w, mint, t, side, sol, tok)
    await check_exits(ctx, mint, s)
    await check_exits(ctx, mint, s)          # idempotent: no second EXIT50
    alerts = await ctx.pool.fetch("SELECT level, reason, message FROM alerts WHERE mint = $1", mint)
    assert [(a["level"], a["reason"]) for a in alerts] == [("EXIT50", "exit")]
    assert "SMART MONEY EXIT" in alerts[0]["message"] and "50%" in alerts[0]["message"]
    assert sid


async def test_holder_estimate_from_real_trades_of_a_token_seen_from_creation(ctx):
    """Token 2iDdpR…pump: create + bundle buys + completing buys are in the fixtures."""
    from radar.db import repo
    from radar.decoder.tx import decode_transaction
    from radar.services.analytics_runner import holder_snapshot
    from radar.services.stream import FlowAggregator
    mint = "2iDdpRNvUywMpcL6aKT75brjm9ehko4z9ss6DnE9pump"
    agg = FlowAggregator()
    for sig, tx in fixtures():
        if tx["meta"]["err"] is None:
            for t in decode_transaction(tx, sig).trades:
                if t.mint == mint:
                    agg.add(t)
    async with ctx.pool.acquire() as conn:
        await conn.execute("DELETE FROM token_wallet_flows WHERE mint = $1", mint)
        await repo.upsert_flows(conn, agg.drain())
        await conn.execute("UPDATE tokens SET stream_from_creation = true WHERE mint = $1", mint)
    t = await ctx.pool.fetchrow("SELECT * FROM tokens WHERE mint = $1", mint)
    hs = await holder_snapshot(ctx, t, 1_000_000_000.0)
    assert hs["source"] == "trade_estimate" and hs["complete"]
    top = hs["holders"][0]
    assert top["wallet"] == "5XCbWZyQT3Hy3i6LUkD2yEoo3mduAEvsnEw3huEPJYwo"   # dev: 3.46 SOL first buy
    assert 5 < top["pct"] < 15
    assert hs["flow_metrics"]["buyers"] >= 6 and hs["flow_metrics"]["fees_sol"] > 0
