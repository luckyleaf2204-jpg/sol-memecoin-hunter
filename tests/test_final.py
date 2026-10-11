"""Final bot round: CONFIRM approval flow, SELL on Jupiter quotes, no averaging down, profit analytics by setup,
execution interface, discovery dedupe + PumpPortal reconnect."""
import asyncio
import time

import pytest

from conftest import build_state, dex_pair
from core.config import ApiKeys, Settings
from core.models import EarlySignal, TokenInfo
from database.db import Database
from scanner.engine import ScannerEngine
from test_bot_v2 import FakeJupiter, bot, good, run
from trading.bot import PaperBot
from trading.config import TradingConfig
from trading.execution import ExecutionInterface, live_available


# ---------------------------------------------------------------- CONFIRM
def test_confirm_proposes_then_fills_only_after_approval():
    st = good()
    b = bot([st], FakeJupiter())
    b.set_mode("CONFIRM")
    run(b)
    assert not b.book.positions and len(b.pending) == 1
    oid, o = next(iter(b.pending.items()))
    assert o["mint"] == st.mint and o["why"] and b.decisions[st.mint]["state"] == "AWAITING_CONFIRM"
    run(b)                                                     # still waiting: no duplicate proposal
    assert len(b.pending) == 1
    assert b.approve(oid)
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions and b.book.executions[-1].route.startswith("Jupiter")


def test_confirm_approval_is_rechecked_and_can_expire_or_be_dismissed():
    st = good()
    b = bot([st], FakeJupiter())
    b.set_mode("CONFIRM")
    run(b)
    oid = next(iter(b.pending))
    st.early = EarlySignal(40, False, False, 2, groups_computable=6)      # no longer TRUE when approved
    b.tick()
    assert b.approve(oid)
    asyncio.run(b.execute_intents())
    assert not b.book.positions                                            # re-check refused it
    st.early = EarlySignal(72, True, True, 5, groups_computable=6)
    run(b)
    oid2 = next(iter(b.pending))
    assert b.dismiss(oid2) and not b.pending
    run(b)
    oid3 = next(iter(b.pending))
    assert not b.approve(oid3, now=time.time() + 600)                      # expired


def test_restart_drops_pending_and_comes_back_in_paper(tmp_path):
    st = good()
    b = PaperBot(bot([st]).engine, TradingConfig(seed=4), state_path=tmp_path / "s.json")
    b.set_mode("CONFIRM")
    b.jupiter = FakeJupiter()
    run(b)
    assert b.pending
    b2 = PaperBot(b.engine, TradingConfig(seed=4), state_path=tmp_path / "s.json")
    assert b2.mode == "PAPER" and b2.pending == {} and not b2.book.positions


# ---------------------------------------------------------------- SELL on quotes
def _held(jup=None):
    st = good()
    b = bot([st], jup or FakeJupiter())
    run(b)
    return st, b, b.book.positions[st.mint]


def test_take_profit_sells_on_a_jupiter_quote():
    class QuoteBoth(FakeJupiter):
        async def quote(self, input_mint, output_mint, amount_raw, slippage_bps):
            if output_mint.startswith("So111"):                            # token -> SOL
                self.calls.append(("sell", amount_raw))
                return {"inputMint": input_mint, "outputMint": output_mint, "priceImpactPct": "0.003",
                        "outAmount": str(int(amount_raw / 1e6 * 0.0002 * 1.4 / 150 * 1e9)),
                        "routePlan": [{"swapInfo": {"label": "Raydium"}}]}
            return await super().quote(input_mint, output_mint, amount_raw, slippage_bps)
    st, b, p = _held(QuoteBoth())
    st.market.price_usd *= 1.4
    st.stamps["market"].updated_at = time.time()
    b.tick()
    assert st.mint in b.sell_intents                                       # waits for its quote
    asyncio.run(b.execute_sells())
    sell = b.book.executions[-1]
    assert sell.side == "SELL" and sell.route == "Jupiter: Raydium" and p.tp1_done
    assert any(a.kind == "SELL" and "WHY:" in a.text for a in b.activity)


def test_sell_without_quote_takes_the_haircut_and_waits_on_high_impact():
    st, b, p = _held()
    st.market.price_usd *= 1.9
    st.stamps["market"].updated_at = time.time()
    b.jupiter = FakeJupiter(fail=True)
    b.tick()
    asyncio.run(b.execute_sells())
    sell = b.book.executions[-1]                                   # exit never skipped, never at the mark (fix 5)
    assert not b.book.positions and sell.model.startswith("PAPER haircut")
    assert sell.fill_price == pytest.approx(st.market.price_usd * (1 - b.cfg.hard_exit_no_quote_haircut_pct / 100))
    st2, b2, p2 = _held()
    b2.jupiter = FakeJupiter(impact="0.08")                     # exit quote impact 8 % > 3 %
    st2.market.price_usd *= 1.9
    st2.stamps["market"].updated_at = time.time()
    b2.tick()
    asyncio.run(b2.execute_sells())
    assert st2.mint in b2.book.positions and any("waits" in a.text for a in b2.activity)


def test_stop_loss_never_waits_for_jupiter():
    st, b, p = _held()
    b.jupiter = FakeJupiter(fail=True)
    st.market.price_usd *= 0.7
    st.stamps["market"].updated_at = time.time()
    b.tick()
    assert b.sell_intents[st.mint]["hard"] is True                 # same loop iteration: execute_sells()
    asyncio.run(b.execute_sells())                                  # Jupiter down -> HAIRCUT fill, no waiting
    assert not b.sell_intents and not b.book.positions and b.book.closed[0].exit_reason == "stop_loss"
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.fill_price == pytest.approx(st.market.price_usd * (1 - b.cfg.hard_exit_no_quote_haircut_pct / 100))
    assert sell.model.startswith("PAPER haircut")


def test_no_averaging_down_no_martingale():
    st, b, p = _held()
    st.market.price_usd *= 0.9                                             # losing, still a TRADE setup
    st.stamps["market"].updated_at = time.time()
    for _ in range(3):
        run(b)
    buys = [e for e in b.book.executions if e.side == "BUY"]
    assert len(buys) == 1 and b.book.positions[st.mint].cost_usd == pytest.approx(p.cost_usd)


# ---------------------------------------------------------------- analytics
def test_profit_analytics_by_setup_and_small_sample_flag():
    st, b, p = _held()
    st.market.price_usd *= 0.7
    st.stamps["market"].updated_at = time.time()
    b.jupiter.sell_price = st.market.price_usd
    b.tick()
    asyncio.run(b.execute_sells())
    s = b.book.stats()
    assert s["closed"] == 1 and s["expectancy"] == pytest.approx(s["avg_loss"]) and s["sample_note"] == "insufficient"
    setup = s["by_setup"][0]
    assert setup["setup"] == "early_signal+momentum" and setup["trades"] == 1 and setup["net"] < 0
    assert s["fees"] >= 0 and s["slippage_cost"] >= 0 and s["max_drawdown_pct"] > 0


# ---------------------------------------------------------------- execution interface
def test_execution_interface_and_no_live_executor():
    assert live_available() is False
    with pytest.raises(NotImplementedError):
        ExecutionInterface().buy(None, 1, 1)
    b = bot([good()])
    assert isinstance(b.exec, ExecutionInterface) and b.exec.live is False


# ---------------------------------------------------------------- discovery
def test_discovery_dedupes_the_same_mint(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "d.db"), keys=ApiKeys(), on_log=lambda m: None)
    m = "DupMint11111111111111111111111111111111111"

    async def lists(n):
        return [TokenInfo(mint=m, symbol="DUP", created_at=time.time() - 30, sources={"pumpfun"})]
    eng.pump.latest = eng.pump.recently_traded = lists
    eng._queue = asyncio.Queue()
    eng._queue.put_nowait(TokenInfo(mint=m, symbol="DUP", created_at=time.time() - 30, sources={"pumpportal"}))
    added = asyncio.run(eng.discover(full=True))
    assert added == 1 and list(eng.tracked) == [m]
    assert set(eng.tracked[m].identity.claims) == {"pumpfun", "pumpportal"}


def test_pumpportal_reconnects_after_disconnect(monkeypatch):
    import json
    import websockets
    from core.http import HttpClient
    from pumpfun.stream import PumpPortalStream
    attempts, logs = [], []

    class FakeWS:
        def __init__(self):
            self.sent = 0

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def send(self, m):
            pass

        async def recv(self):
            self.sent += 1
            if self.sent == 1:
                return json.dumps({"txType": "create", "mint": "WsMint1111111111111111111111111111111111",
                                   "symbol": "WS", "name": "ws"})
            raise ConnectionError("socket closed")

    def connect(*a, **k):
        attempts.append(1)
        if len(attempts) == 1:
            raise OSError("network down")
        return FakeWS()
    monkeypatch.setattr(websockets, "connect", connect)
    q = asyncio.Queue()
    s = PumpPortalStream(q, HttpClient().health, on_log=logs.append)

    async def go():
        stop = asyncio.Event()
        task = asyncio.create_task(s.run(stop))
        for _ in range(100):
            await asyncio.sleep(0.05)
            if s.events_total and not s.connected:
                break
        stop.set()
        await asyncio.wait_for(task, 10)
    import pumpfun.stream as ps
    monkeypatch.setattr(ps.asyncio, "wait_for", asyncio.wait_for)
    asyncio.run(go())
    assert len(attempts) >= 2 and s.connects >= 1 and s.events_total == 1 and q.qsize() == 1
    assert any("error" in m for m in logs) and any("connected" in m for m in logs)


# ---------------------------------------------------------------- dashboard mapping (web-3)
def test_exit_engine_mapping_uses_exit_thresholds():
    from core.models import LiquidityIntel, RiskResult
    from trading.serialize import bot_status, exit_status
    st, b, p = _held()
    e = exit_status(p, st, b.cfg)
    assert set(e) == {"take_profit", "stop_loss", "liquidity", "momentum", "risk"}
    assert e["stop_loss"]["state"] == "ok" and e["liquidity"]["state"] == "ok"
    st.market.price_usd *= 0.9
    st.stamps["market"].updated_at = time.time()
    b.tick()
    assert exit_status(p, st, b.cfg)["stop_loss"]["state"] == "near"
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    st.risk = RiskResult(70, "HIGH")
    e = exit_status(p, st, b.cfg)
    assert e["liquidity"]["state"] == "hit" and e["risk"]["state"] == "hit"
    d = bot_status(b, b.engine)
    assert d["version"] == "web-16" and d["positions"][0]["exit_state"].startswith("EXIT")
    rows = {r["key"]: r for r in d["exit_engine"]}
    assert rows["liquidity"]["hit"] == 1 and rows["take_profit"]["watching"] == 1


def test_bot_page_has_the_seven_areas_and_route_alias():
    from web.app import STATIC
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    for area in ("bot-doing", "bot-status", "bot-scan", "bot-candidates", "bot-positions", "bot-exit", "bot-activity"):
        assert f'id="{area}"' in js, area
    assert '"#/" + h.slice(1)' in js                                  # "#bot" works like "#/bot"
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert "app.js?v=web-35" in html and "styles.css?v=web-35" in html  # cache-busting on deploy
