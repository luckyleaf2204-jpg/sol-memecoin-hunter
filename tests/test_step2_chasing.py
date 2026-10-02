"""Step 2 — no chasing: entry_location is a real gate for lifecycle BUYs (extension_5m > 40 % or EXTENDED / MID_MOVE
-> WATCH), PULLBACK / SECOND_WAVE take entry slots first, S_price is an inverted U, momentum weighs less in
Opportunity."""
import asyncio

import pytest

import trading.bot as B
import trading.entry_location as EL
from core.models import Factor, SubScore
from test_bot_v2 import bot
from test_jupiter_exec import ScriptedJupiter
from test_lifecycle import SECOND_WAVE, Store, history, post_tok, run_bot
from trading import jupiter as J
from trading.experimental import s_price
from trading.models import TRADE, WATCH
from scoring.opportunity import WEIGHTS, compute_opportunity


def _st(pc5):
    st = post_tok()
    st.market.price_change_5m = pc5
    return st


@pytest.mark.parametrize("pc, want", [(-10, 0.0), (0, 0.0), (10, 0.5), (20, 1.0), (40, 1.0), (70, 0.5), (100, 0.0),
                                      (180, 0.0)])
def test_s_price_is_an_inverted_u(pc, want):
    assert s_price(_st(pc)) == pytest.approx(want)


def test_s_price_unknown_stays_unknown():
    st = _st(None)
    st.mc_track = None
    assert s_price(st) is None


def test_momentum_weighs_less_in_opportunity(good_state):
    assert WEIGHTS["momentum"] == 30 and WEIGHTS["holder_growth"] == 25 and WEIGHTS["whale"] == 10
    subs = {"momentum": SubScore("momentum", 100, [Factor("m", 1, 1, True, 1, "x")]),
            "holder": SubScore("holder", 0, [Factor("growth_15m", 0, 10, True, 0, "x")]),
            "whale": SubScore("whale", 0, [Factor("w", 0, 10, True, 0, "x")])}
    r = compute_opportunity(good_state, subs)
    assert r.total == round(100 * 30 / (30 + 25 + 10))          # was 45 / 80 = 56 before step 2


# ---------------------------------------------------------------- gate unit
def gate(rec, **cfg):
    b = bot([post_tok()], **cfg)
    st = b.engine.published[0]
    return b._entry_location_gate(st, rec, TRADE, 0.0), rec


@pytest.mark.parametrize("loc, ext, blocked", [("EXTENDED", 10.0, True), ("MID_MOVE", 5.0, True),
                                               ("EARLY_ENTRY", 41.0, True), ("PULLBACK", 12.0, False),
                                               ("SECOND_WAVE", None, False), ("UNKNOWN", None, False),
                                               ("EARLY_ENTRY", 40.0, False)])
def test_gate_rules(loc, ext, blocked):
    d, rec = gate({"entry_location": loc, "entry_extension": ext})
    assert (d == WATCH) is blocked
    if blocked:
        assert rec["entry_gate_blocked"] and any(r.startswith("entry_location") for r in rec["blocked_by"])


def test_gate_off_and_non_trade_untouched():
    assert gate({"entry_location": "EXTENDED", "entry_extension": 90.0}, entry_location_gate=False)[0] == TRADE
    b = bot([post_tok()])
    assert b._entry_location_gate(b.engine.published[0], {"entry_location": "EXTENDED"}, WATCH, 0.0) == WATCH


# ---------------------------------------------------------------- integration (lifecycle engine)
def _fake_location(name, ext):
    def f(points, now, post_state=None, buyer_acceleration=None):
        return {"entry_location": name, "extension_5m_pct": ext, "dist_from_low_pct": None, "dist_from_high_pct": None,
                "pullback_depth_pct": None, "volume_retention": None, "liquidity_retention": None,
                "acceleration": None, "buyer_retention": None}
    return f


def test_lifecycle_trade_extended_is_not_bought(monkeypatch):
    monkeypatch.setattr(EL, "entry_location", _fake_location("EXTENDED", 65.0))
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.OK]), history(SECOND_WAVE))
    rec = b.decisions[st.mint]
    assert rec["would_have_bought_lifecycle"] and rec["decision"] == WATCH and not b.book.positions
    assert "entry_location: EXTENDED" in rec["blocked_by"] and not b.intents


def test_lifecycle_trade_pullback_is_bought(monkeypatch):
    monkeypatch.setattr(EL, "entry_location", _fake_location("PULLBACK", 8.0))
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.OK]), history(SECOND_WAVE))
    assert b.decisions[st.mint]["decision"] == TRADE and st.mint in b.book.positions


def test_preferred_locations_take_the_entry_slot_first(monkeypatch):
    monkeypatch.setattr(EL, "entry_location", _fake_location("EARLY_ENTRY", 5.0))
    monkeypatch.setattr(B, "MAX_ENTRIES_PER_TICK", 1)
    a, c = post_tok(), post_tok()
    a.info.mint, c.info.mint = "A" * 43, "C" * 43
    b = bot([a, c], ScriptedJupiter([J.OK]))
    b.cfg.experimental, b.cfg.lifecycle = True, True
    b.engine.history = Store({a.mint: history(SECOND_WAVE), c.mint: history(SECOND_WAVE)})
    b.decisions[c.mint] = {"entry_location": "PULLBACK"}        # last known location of C
    b.tick()
    assert list(b.intents) == [c.mint]
