"""Bot v2: one decision logic, execution on real Jupiter quotes (paper), duplicate-order guard, re-check right
before the fill, restart, kill switch, locked live modes, dashboard payload."""
import asyncio
import json
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import build_state, dex_pair
from core.config import ApiKeys, Settings
from core.http import HttpClient
from core.models import EarlySignal, LiquidityIntel
from database.db import Database
from scanner.engine import ScannerEngine
from trading.bot import PaperBot
from trading.config import ModeNotAllowed, TradingConfig
from trading.jupiter import WSOL, JupiterQuotes
from trading.serialize import bot_status
from validation.identity import apply_identity, record_claim
from web.app import STATIC, create_app

CODE = "v2-code"
H = {"X-Access-Code": CODE}
SECRET = "HELIUS-SECRET-v2-0000-1111-2222-333333333333"


def good(mint="GoodMint1111111111111111111111111111111111", early=True):
    st = build_state(dex_pair(mint=mint))
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    st.identity.helius_checked, st.holder_status = True, "ok"
    st.early = EarlySignal(72, True, True, 5, groups_computable=6) if early else EarlySignal(40, False, False, 2, groups_computable=6)
    return st


class Eng:
    def __init__(self, states, feeds=None):
        self.published = states
        self.sol_price = 150.0
        self._feeds = feeds or {"dexscreener": {"ok": True, "cooldown_s": 0}, "pumpportal": {"connected": True}}
        self.deep_extra = set()

    def feeds(self):
        return self._feeds


class FakeJupiter:
    def __init__(self, impact="0.004", fail=False):
        self.calls, self.impact, self.fail = [], impact, fail

    async def quote(self, input_mint, output_mint, amount_raw, slippage_bps):
        self.calls.append((input_mint, output_mint, amount_raw))
        if self.fail:
            return None
        # ~ price 0.0002 USD/token at SOL 150: tokens = lamports/1e9 * 150 / 0.0002 (6 decimals)
        out = int(amount_raw / 1e9 * 150 / 0.0002 * 1e6 * 0.997)
        return {"inputMint": input_mint, "outputMint": output_mint, "outAmount": str(out), "priceImpactPct": self.impact,
                "routePlan": [{"swapInfo": {"label": "Pump.fun Amm"}}]}


def bot(states, jup=None, feeds=None, **cfg):
    b = PaperBot(Eng(states, feeds), TradingConfig(seed=4, **cfg))
    b.exec.rng.random = lambda: 0.99
    b.jupiter = jup
    return b


def run(b, now=None):
    b.tick(now)
    asyncio.run(b.execute_intents(now))


# ---------------------------------------------------------------- Jupiter execution
def test_buy_is_filled_on_a_real_jupiter_quote():
    st, j = good(), FakeJupiter()
    b = bot([st], j)
    run(b)
    assert j.calls and j.calls[0][0] == WSOL and j.calls[0][1] == st.mint
    p = b.book.positions[st.mint]
    ex = b.book.executions[-1]
    assert ex.route.startswith("Jupiter: Pump.fun Amm") and ex.price_impact_pct == pytest.approx(0.4)
    assert "real Jupiter quote" in ex.model and p.tokens > 0
    assert any(a.kind == "BUY" and "WHY:" in a.text for a in b.activity)


def test_jupiter_failure_means_no_buy():
    st = good()
    b = bot([st], FakeJupiter(fail=True))
    run(b)
    assert not b.book.positions and any(a.kind == "FAILED" and "Jupiter" in a.text for a in b.activity)


def test_quote_mismatch_is_rejected():
    def handler(req):
        return httpx.Response(200, json={"inputMint": WSOL, "outputMint": "SomethingElse", "outAmount": "5"})

    async def go():
        h = HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.0)
        q = await JupiterQuotes(h).quote(WSOL, "Wanted111", 10, 300)
        await h.aclose()
        return q
    assert asyncio.run(go()) is None


def test_slippage_too_high_at_execution_blocks():
    st = good()
    b = bot([st], FakeJupiter(impact="0.05"))                    # 5 % impact > 3 % limit
    run(b)
    assert not b.book.positions and any(a.kind == "BLOCK" and "impact" in a.text for a in b.activity)


def test_recheck_right_before_execution():
    st = good()
    b = bot([st], FakeJupiter())
    b.tick()
    assert st.mint in b.intents
    st.early = EarlySignal(40, False, False, 2, groups_computable=6)   # Early Signal no longer TRUE
    asyncio.run(b.execute_intents())
    assert not b.book.positions and any("no longer a valid Trade Candidate" in a.text for a in b.activity)


def test_kill_switch_between_decision_and_fill():
    st = good()
    b = bot([st], FakeJupiter())
    b.tick()
    b.set_kill(True)
    asyncio.run(b.execute_intents())
    assert not b.book.positions


def test_duplicate_order_guard():
    st, j = good(), FakeJupiter()
    b = bot([st], j)
    b.tick()
    b.tick()                                                     # second tick before execution: no 2nd intent
    assert list(b.intents) == [st.mint] and b.decisions[st.mint]["state"] == "DUPLICATE"
    asyncio.run(b.execute_intents())
    run(b)                                                       # already held -> no new order
    assert len(j.calls) == 1 and len([e for e in b.book.executions if e.side == "BUY"]) == 1


def test_liquidity_collapse_sells_even_when_jupiter_is_down():
    st = good()
    b = bot([st], FakeJupiter())
    run(b)
    b.jupiter = FakeJupiter(fail=True)
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    run(b)
    assert not b.book.positions and b.book.closed[0].exit_reason == "liquidity_collapse"
    sell = next(a for a in b.activity if a.kind == "SELL")
    assert "WHY:" in sell.text and "NET P&L" in sell.text


def test_429_and_websocket_disconnect():
    st = good()
    b = bot([st], FakeJupiter(), feeds={"dexscreener": {"ok": False, "cooldown_s": 0, "last_status": 429},
                                         "pumpportal": {"connected": False}})
    run(b)
    assert not b.book.positions and b.modules["risk"].status == "BLOCKED"
    b.engine._feeds = {"dexscreener": {"ok": True, "cooldown_s": 0}, "pumpportal": {"connected": False}}
    run(b)                                                       # WS down alone: market data still fresh -> allowed
    assert st.mint in b.book.positions


def test_realtime_new_token_is_evaluated_next_tick():
    b = bot([], FakeJupiter())
    run(b)
    assert not b.decisions
    st = good()
    b.engine.published = [st]
    run(b)
    assert st.mint in b.decisions and st.mint in b.book.positions
    assert any(a.kind == "DISCOVER" for a in b.activity)


def test_restart_keeps_positions_and_comes_back_in_paper(tmp_path):
    st = good()
    path = tmp_path / "paper.json"
    b = PaperBot(Eng([st]), TradingConfig(seed=4), state_path=path)
    b.exec.rng.random = lambda: 0.99
    b.jupiter = FakeJupiter()
    run(b)
    b2 = PaperBot(Eng([st]), TradingConfig.load(tmp_path / "missing.json"), state_path=path)
    b2.jupiter = FakeJupiter()
    assert st.mint in b2.book.positions and b2.cfg.mode == "PAPER" and b2.intents == {}
    run(b2)
    assert len([e for e in b2.book.executions if e.side == "BUY"]) == 1          # no duplicate after restart


def test_auto_is_locked_confirm_runs_on_paper():
    b = bot([good()])
    assert b.mode == "PAPER" and b.cfg.mode == "PAPER"
    for m in ("AUTO", "LIVE"):
        with pytest.raises(ModeNotAllowed):
            b.set_mode(m, "BẬT AUTO")
    b.set_mode("CONFIRM")
    assert b.mode == "CONFIRM" and b.cfg.mode == "PAPER"          # approval layer over the paper executor


def test_tslax_is_rejected_never_bought():
    st = good("XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB")
    record_claim(st.identity, "pumpportal", "APEWIF", "")
    record_claim(st.identity, "dexscreener", "TSLAx", "")
    apply_identity(st)
    b = bot([st], FakeJupiter())
    run(b)
    d = bot_status(b, b.engine)
    assert not b.book.positions and d["evaluated"][0]["action"] == "REJECT" and d["evaluated"][0]["identity"] == "CONFLICT"


# ---------------------------------------------------------------- dashboard payload
def test_dashboard_shows_every_evaluated_token_and_what_the_bot_does():
    b58 = "ABCDEFGHJKLMNPQRSTUVWXYZ"                             # base58: no 0 / O / I / l
    sts = [good("Many" + b58[i % 24] + b58[i // 24] + "1" * 36) for i in range(40)]
    for s in sts[5:]:
        s.early = EarlySignal(None, None, None)                                    # UNKNOWN -> WATCH (waiting), not BUY
    b = bot(sts, FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100)
    run(b)
    d = bot_status(b, b.engine)
    assert len(d["evaluated"]) == 40                                              # not capped (UI paginates)
    assert [x["action"] for x in d["evaluated"][:2]] == ["BUY", "BUY"]            # BUY first
    assert {x["action"] for x in d["evaluated"]} == {"BUY", "WATCH"}
    assert d["scan"]["total"] == 40 and d["scan"]["early_signal"] == 5 and d["scan"]["trade_candidates"] >= 1
    assert d["doing"]["kind"] == "holding" and d["positions"][0]["holding_min"] >= 0
    assert {r["key"] for r in d["exit_rules"]} >= {"take_profit", "stop_loss", "liquidity", "momentum", "risk"}
    assert d["live_available"] is False and d["mode"] == "PAPER"


def test_dashboard_doing_states():
    b = bot([], FakeJupiter())
    assert bot_status(b, b.engine)["doing"]["kind"] == "starting"
    b.engine.published = [good(early=False)]
    run(b)
    assert bot_status(b, b.engine)["doing"]["kind"] == "searching"
    b.set_kill(True)
    assert bot_status(b, b.engine)["doing"]["kind"] == "kill"


def test_api_mode_locked_and_payload_has_no_secret(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "a.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    st = good()
    eng.tracked = {st.mint: st}
    eng.published = [st]
    b = PaperBot(eng, TradingConfig(seed=4))
    b.exec.rng.random = lambda: 0.99
    b.tick()
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE, bot=b)) as c:
        r = c.post("/api/bot/mode", headers=H, json={"mode": "AUTO", "confirm": "BẬT AUTO"})
        assert r.status_code == 403 and "not installed" in r.json()["detail"]
        assert c.post("/api/bot/mode", headers=H, json={"mode": "CONFIRM"}).json()["mode"] == "CONFIRM"
        assert c.post("/api/bot/mode", headers=H, json={"mode": "PAPER"}).json()["mode"] == "PAPER"
        body = c.get("/api/bot", headers=H).text
    d = json.loads(body)
    assert d["evaluated"] and d["doing"] and SECRET not in body and "api-key" not in body


def test_bot_ui_keys_exist():
    import re
    from i18n import load
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = {f"web.bot.exit.{k}" for k in ("take_profit", "trailing", "stop_loss", "liquidity", "momentum", "risk", "time")}
    keys |= {k for k in re.findall(r'''t\(\s*["'](web\.bot\.[A-Za-z0-9_.]+)["']''', js) if not k.endswith(".")}
    for lang in ("vi", "en"):
        assert not [k for k in keys if k not in load(lang)], lang
    for word in ("privateKey", "secretKey", "signTransaction", "sendTransaction", "TRADING_WALLET"):
        assert word not in js
