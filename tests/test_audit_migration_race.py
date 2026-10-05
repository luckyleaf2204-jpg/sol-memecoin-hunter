"""Baseline audit — migration race around the ON_CURVE gate (D1 entry_allow_curve=False):
CASE A decided ON_CURVE -> migration -> execution; CASE B decided on the AMM -> the market reports the curve again
before execution; CASE C curve -> PumpSwap/Raydium transition. A BUY may only fill on a non-curve market."""
import asyncio

import pytest

from test_bot_v2 import FakeJupiter, bot, good
from test_step2_chasing import bot as gate_bot, post_tok
from trading.lifecycle_decision import TRADE, WATCH


def _rec():
    return {"entry_location": "PULLBACK", "entry_extension": 5.0,
            "entry_location_detail": {"history_s": 400.0, "last_low_age_s": 300.0}}


def test_case_b_amm_decision_then_curve_at_execution_is_not_bought():
    st = good()
    b = bot([st], FakeJupiter())
    assert not st.market.is_curve
    b.tick()
    assert st.mint in b.intents                                    # decided on the AMM
    st.market.dex_id = "pumpfun"                                   # the market reports the curve again
    asyncio.run(b.execute_intents())
    assert st.mint not in b.book.positions and not b.intents
    assert any("ON_CURVE" in a.text for a in b.activity)


def test_case_b_allowed_when_the_flag_is_on():
    st = good()
    b = bot([st], FakeJupiter(), entry_allow_curve=True)
    b.tick()
    st.market.dex_id = "pumpfun"
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions


def test_case_a_and_c_curve_blocked_then_migrated_market_may_trade():
    b = gate_bot([post_tok()])
    st = b.engine.published[0]
    st.market.dex_id = "pumpfun"                                   # CASE A: decided while on the curve
    rec = _rec()
    assert b._entry_location_gate(st, rec, TRADE, 0.0) == WATCH and "entry_location: ON_CURVE" in rec["blocked_by"]
    for dex in ("pumpswap", "raydium"):                            # CASE C: migrated -> a fresh decision may trade
        st.market.dex_id = dex
        rec = _rec()
        assert b._entry_location_gate(st, rec, TRADE, 0.0) == TRADE


def test_case_a_stale_blocked_decision_never_executes_after_migration():
    """A WATCH (blocked) decision never created an intent, so a migration alone cannot make it buy."""
    b = gate_bot([post_tok()])
    st = b.engine.published[0]
    st.market.dex_id = "pumpfun"
    rec = _rec()
    b._entry_location_gate(st, rec, TRADE, 0.0)
    assert rec["decision"] == WATCH and st.mint not in b.intents
    st.market.dex_id = "pumpswap"
    asyncio.run(b.execute_intents())
    assert st.mint not in b.book.positions


# ---------------------------------------------------------------- the window DURING the awaits (quote / probe)
import copy  # noqa: E402

from trading import jupiter as J  # noqa: E402


class PublishDuringQuote(FakeJupiter):
    """The engine publishes a NEW snapshot (copies, like ScannerEngine.publish) while the BUY quote is in flight."""
    def __init__(self, bot_ref, change):
        super().__init__()
        self.bot_ref, self.change = bot_ref, change

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps, **kw):
        q = await self.quote(input_mint, output_mint, amount_raw, slippage_bps)
        b = self.bot_ref[0]
        if output_mint != J.WSOL and b is not None:
            fresh = []
            for st in b.engine.published:
                c = copy.copy(st)
                c.market = copy.copy(st.market)
                self.change(b, c)
                fresh.append(c)
            b.engine.published = fresh                            # the bot's local `st` is now an old snapshot
        return J.QuoteResult(J.OK, quote=q, http=200, attempts=1)


def _run(change):
    ref = [None]
    st = good()
    b = bot([st], PublishDuringQuote(ref, change))
    ref[0] = b
    b.tick()
    assert st.mint in b.intents and not st.market.is_curve
    asyncio.run(b.execute_intents())
    return b, st


def test_curve_reported_while_the_quote_is_in_flight_is_not_bought():
    def to_curve(b, c):
        c.market.dex_id = "pumpfun"
    b, st = _run(to_curve)
    assert st.mint not in b.book.positions
    assert any("final validation" in a.text and "ON_CURVE" in a.text for a in b.activity)


def test_decision_withdrawn_while_the_quote_is_in_flight_is_not_bought():
    def withdraw(b, c):
        b.decisions[c.mint] = {**b.decisions[c.mint], "decision": "WATCH"}
    b, st = _run(withdraw)
    assert st.mint not in b.book.positions


def test_token_gone_from_the_feed_while_quoting_is_not_bought():
    ref = [None]
    st = good()

    class Vanish(FakeJupiter):
        async def quote_result(self, im, om, amount, slip, **kw):
            q = await self.quote(im, om, amount, slip)
            ref[0].engine.published = []
            return J.QuoteResult(J.OK, quote=q, http=200, attempts=1)
    b = bot([st], Vanish())
    ref[0] = b
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint not in b.book.positions


def test_unchanged_market_still_buys():
    b, st = _run(lambda b, c: None)
    assert st.mint in b.book.positions


# ---------------------------------------------------------------- the window DURING _latency_probe()
class PublishDuringProbe(FakeJupiter):
    """BUY quote OK; the change (a NEW published snapshot, like ScannerEngine.publish) happens on the SECOND quote,
    i.e. the re-quote made inside _latency_probe() — after the pre-quote checks, before buy_from_quote()."""
    def __init__(self, bot_ref, change):
        super().__init__()
        self.bot_ref, self.change, self.n = bot_ref, change, 0

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps, **kw):
        q = await self.quote(input_mint, output_mint, amount_raw, slippage_bps)
        if output_mint != J.WSOL:
            self.n += 1
            if self.n == 2:                                       # the latency-probe re-quote is in flight
                b = self.bot_ref[0]
                fresh = []
                for st in b.engine.published:
                    c = copy.copy(st)
                    c.market = copy.copy(st.market)
                    self.change(b, c)
                    fresh.append(c)
                b.engine.published = fresh
        return J.QuoteResult(J.OK, quote=q, http=200, attempts=1)


def _run_probe(change):
    ref = [None]
    st = good()
    j = PublishDuringProbe(ref, change)
    b = bot([st], j, latency_probe=True)
    ref[0] = b
    b._probe_rng.uniform = lambda a, c: 0.0                     # no real 0.4-1.5 s wait in the test
    b.tick()
    assert st.mint in b.intents
    asyncio.run(b.execute_intents())
    assert j.n == 2                                             # the probe really ran (BUY quote + re-quote)
    return b, st


@pytest.mark.parametrize("change", ["curve", "withdrawn"])
def test_state_change_during_the_latency_probe_cancels_the_buy(change):
    def apply(b, c):
        if change == "curve":
            c.market.dex_id = "pumpfun"                           # the market reports the bonding curve
        else:
            b.decisions[c.mint] = {**b.decisions[c.mint], "decision": "WATCH"}   # decision withdrawn
    b, st = _run_probe(apply)
    assert st.mint not in b.book.positions
    assert any("final validation" in a.text for a in b.activity)


def test_probe_with_an_unchanged_state_still_buys():
    b, st = _run_probe(lambda b, c: None)
    assert st.mint in b.book.positions


# ---------------------------------------------------------------- the fill and the entry use the FRESH state
def test_fill_and_entry_baselines_come_from_the_fresh_state():
    """Liquidity and price published during the probe: the fill's reference price and the position's entry
    liquidity / volume (baselines of liquidity_collapse / volume_collapse) are the fresh ones, not the snapshot
    taken before the BUY quote."""
    def move(b, c):
        c.market.liquidity_usd = c.market.liquidity_usd * 3
        c.market.vol_5m = (c.market.vol_5m or 1000.0) * 2
        c.market.price_usd = c.market.price_usd * 1.05
    ref = [None]
    st = good()
    old_liq, old_vol, old_px = st.market.liquidity_usd, st.market.vol_5m or 1000.0, st.market.price_usd
    j = PublishDuringProbe(ref, move)
    b = bot([st], j, latency_probe=True)
    ref[0] = b
    b._probe_rng.uniform = lambda a, c: 0.0
    b.tick()
    asyncio.run(b.execute_intents())
    p = b.book.positions[st.mint]
    buy = [e for e in b.book.executions if e.side == "BUY"][-1]
    assert p.entry_liq == old_liq * 3 and p.entry_vol == old_vol * 2
    assert buy.ref_price == old_px * 1.05


def test_risk_rising_during_the_probe_is_seen_by_the_entry_risk_buffer():
    from trading.config import TradingConfig
    def riskier(b, c):
        c.risk = copy.copy(c.risk)
        c.risk.score = b.cfg.entry_max_risk + 5                   # above the entry risk buffer
    ref = [None]
    st = good()
    j = PublishDuringProbe(ref, riskier)
    b = bot([st], j, latency_probe=True)
    b.cfg.experimental = True                                     # the entry risk buffer is part of that engine
    ref[0] = b
    b._probe_rng.uniform = lambda a, c: 0.0
    b.tick()
    if st.mint not in b.intents:                                  # the experimental engine decides on its own
        b.decisions[st.mint] = {**b.decisions.get(st.mint, {}), "decision": "TRADE", "engine": "experimental",
                                "risk_allowed": True}
        b.intents[st.mint] = {"mint": st.mint, "usd": 50.0, "ts": __import__("time").time()}
    st.risk.score = min(st.risk.score, b.cfg.entry_max_risk - 10)  # low at quote time
    asyncio.run(b.execute_intents())
    assert st.mint not in b.book.positions and any(x["stage"] == "entry" for x in b.buffer_blocks)
    assert TradingConfig().entry_max_risk == b.cfg.entry_max_risk


def test_sell_fill_uses_the_state_published_during_the_sell_quote():
    import time
    from test_v12 import MINT, opened
    b, st, p = opened()

    class PublishDuringSell(FakeJupiter):
        async def quote_result(self, im, om, amount, slip, **kw):
            q = await self.quote(im, om, amount, slip)
            if om == J.WSOL:
                fresh = []
                for s0 in b.engine.published:
                    c = copy.copy(s0)
                    c.market = copy.copy(s0.market)
                    c.market.price_usd = s0.market.price_usd * 1.02      # newer print while quoting
                    fresh.append(c)
                b.engine.published = fresh
            return J.QuoteResult(J.OK, quote=q, http=200, attempts=1)
    b.jupiter = PublishDuringSell(sell_price=p.entry_price * 1.35)
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 1.35                         # TP1 (non-HARD: ref = market price)
    b.tick()
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.ref_price == p.entry_price * 1.35 * 1.02 and MINT in b.book.positions   # half sold at TP1
