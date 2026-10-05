"""Baseline audit — migration race around the ON_CURVE gate (D1 entry_allow_curve=False):
CASE A decided ON_CURVE -> migration -> execution; CASE B decided on the AMM -> the market reports the curve again
before execution; CASE C curve -> PumpSwap/Raydium transition. A BUY may only fill on a non-curve market."""
import asyncio

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
