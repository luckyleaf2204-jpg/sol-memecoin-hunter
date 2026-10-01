"""Stress: 400 tracked tokens with history through every tier, the paper bot and the tier API — time budgets
sized for Render Free (0.1 CPU ≈ 10× slower than this machine), plus DexScreener at 60 % HTTP 429."""
import asyncio
import random
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SOL_USD, dex_pair, default_info, good_dev, good_holders
from core.config import ApiKeys, Settings
from core.http import HttpClient
from core.models import TokenState
from database.db import Database
from dex.dexscreener import parse_pair
from history.store import TokenHistory
from scanner.engine import ScannerEngine
from trading.bot import PaperBot
from trading.config import TradingConfig
from validation.identity import apply_identity, record_claim
from web.app import create_app

N = 400


@pytest.fixture(scope="module")
def big_engine(tmp_path_factory):
    eng = ScannerEngine(Settings(), Database(tmp_path_factory.mktemp("s") / "s.db"), keys=ApiKeys(), on_log=lambda m: None)
    rnd = random.Random(1)
    now = time.time()
    for i in range(N):
        mint = f"Stress{i:036d}"
        age_min = rnd.uniform(0.5, 30)
        st = TokenState(info=default_info(mint, age_s=age_min * 60))
        st.holders, st.dev, st.holder_status = good_holders(), good_dev(), "ok"
        record_claim(st.identity, "dexscreener", "TKN", "")
        h = eng.history.get(mint)
        mc = rnd.uniform(5_000, 300_000)
        for k in range(8):                                    # 8 anchor points, 20 s apart
            mc *= rnd.uniform(0.9, 1.25)
            p = dex_pair(mint=mint, mc=mc, fdv=mc, price=f"{mc / 1e9:.12f}", vol=(1_000 * (k + 1), 2_000 * (k + 1)),
                         m5=(10 * (k + 1), 6 * (k + 1)), liq=rnd.uniform(3_000, 80_000))
            ts = now - 20 * (8 - k)
            from scanner.pipeline import ingest_market
            ingest_market(st, parse_pair(p), {}, SOL_USD, h, ts)
        eng.tracked[mint] = st
    t0 = time.perf_counter()
    for st in eng.tracked.values():
        eng._evaluate(st, now)
    eng._eval_s = time.perf_counter() - t0
    eng.publish()
    return eng


def test_full_evaluation_of_400_tokens_fits_the_budget(big_engine):
    # whole pipeline + groups + pre-early + early watch for 400 tokens; Render Free must stay well under 5 s
    assert big_engine._eval_s < 1.5, big_engine._eval_s
    tiers = {"pre": 0, "watch": 0}
    for st in big_engine.published:
        tiers["pre"] += st.pre_early is not None and st.pre_early.status != "NOT_ELIGIBLE"
        tiers["watch"] += st.early_watch is not None and st.early_watch.eligible
    assert tiers["pre"] > 0 and tiers["watch"] > 0


def test_bot_tick_on_400_tokens(big_engine):
    bot = PaperBot(big_engine, TradingConfig(seed=2))
    t0 = time.perf_counter()
    for _ in range(5):
        bot.tick()
    per_tick = (time.perf_counter() - t0) / 5
    assert per_tick < 0.5, per_tick
    s = bot.book.stats()
    assert s["equity"] > 0 and len(bot.book.positions) <= bot.cfg.max_open_positions
    assert s["exposure"] <= bot.cfg.max_total_exposure_pct / 100 * s["equity"] + 1e-6


def test_tier_api_under_load(big_engine):
    bot = PaperBot(big_engine, TradingConfig(seed=2))
    bot.tick()
    with TestClient(create_app(engine=big_engine, start_scanner=False, access_code="c", bot=bot)) as c:
        t0 = time.perf_counter()
        for tier in ("pre_early", "watch", "signal", "trade"):
            for _ in range(5):
                assert c.get(f"/api/early/{tier}", headers={"X-Access-Code": "c"}).status_code == 200
        home = c.get("/api/home", headers={"X-Access-Code": "c"})
        dt = time.perf_counter() - t0
        watch = c.get("/api/early/watch", headers={"X-Access-Code": "c"}).json()["items"]
    assert dt < 8, dt
    assert home.status_code == 200 and 0 < len(watch) <= 50


def test_dexscreener_stress_60pct_429():
    rnd = random.Random(5)
    mints = [f"Q{i:040d}" for i in range(400)]                 # 14 chunks per round

    def handler(req):
        if rnd.random() < 0.6:
            return httpx.Response(429, headers={"retry-after": "0"})
        chunk = req.url.path.rsplit("/", 1)[1].split(",")
        return httpx.Response(200, json=[{"chainId": "solana", "dexId": "pumpswap", "pairAddress": "P" + m,
                                          "baseToken": {"address": m}, "quoteToken": {"symbol": "SOL"},
                                          "priceUsd": "0.0001", "marketCap": 100000} for m in chunk])
    from dex.dexscreener import DexScreenerClient
    import core.http as ch

    async def go():
        old = ch.COOLDOWN_BASE_S
        ch.COOLDOWN_BASE_S = 0.02
        try:
            h = HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.0)
            d = DexScreenerClient(h)
            h._min_interval.clear(), h._base_interval.clear()
            cov = []
            for _ in range(15):
                r = await d.tokens(mints)
                cov.append(len(r or {}) / len(mints))
                await asyncio.sleep(0.02)
            await h.aclose()
            return cov
        finally:
            ch.COOLDOWN_BASE_S = old
    cov = asyncio.run(go())
    assert sum(cov) / len(cov) >= 0.6, cov                       # never collapses to the old ~4 %
