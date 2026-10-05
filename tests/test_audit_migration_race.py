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
