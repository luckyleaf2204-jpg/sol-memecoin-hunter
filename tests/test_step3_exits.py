"""Step 3 — exit variants for A/B (all OFF by default; trading/exits.py unchanged): wide stop with risk-constant
sizing, TP2 runner, short time stop without a new high."""
import asyncio
import time

import pytest

from test_bot_v2 import bot, good
from test_v12 import MINT, opened
from trading import decision as D
from trading.config import TradingConfig
from trading.exit_options import RUNNER_TRAILING, TIME_STOP, apply_exit_options, on_filled_sell, stop_pct
from trading.models import Position

NOW = 10_000.0


def pos(**kw):
    p = Position(id=1, mint="M", symbol="T", opened_at=NOW - 60, entry_price=1.0, tokens=100.0, cost_usd=100.0,
                 initial_tokens=100.0, stop_price=0.85, tp1_price=1.3, tp2_price=1.8, trailing_pct=15.0, high_price=1.0)
    for k, v in kw.items():
        setattr(p, k, v)
    return p


def test_defaults_are_off_and_noop():
    c = TradingConfig()
    assert c.wide_stop_pct is None and c.tp2_runner_frac == 0.0 and c.time_stop_min is None and stop_pct(c) == 15.0
    p = pos()
    for sig in (None, (1.0, "stop_loss"), (1.0, "take_profit_2"), (0.5, "take_profit_1"), (1.0, "trailing_stop")):
        assert apply_exit_options(p, sig, 1.0, c, NOW + 10_000) == sig
    assert not p.runner_active


# ---------------------------------------------------------------- wide stop, constant risk
@pytest.mark.parametrize("stop", [20.0, 22.0, 25.0])
def test_wide_stop_keeps_risk_at_one_percent(stop):
    c = TradingConfig(wide_stop_pct=stop)
    st = good()
    st.market.liquidity_usd = 10_000_000                       # no liquidity cap
    sc = D.score(st, D.vet(st, c), c)
    sz = D.size(st, sc, c, 1000.0, 1000.0, 0.0)
    assert sz.usd * stop / 100 <= 1000 * c.risk_per_trade_pct / 100 + 1e-6      # loss at the stop <= 1 % equity
    base = D.size(st, sc, TradingConfig(), 1000.0, 1000.0, 0.0)
    assert sz.usd <= base.usd and f"stop {stop:.0f}%" in sz.reasons[0]


def test_wide_stop_sets_the_stop_price():
    b, st, p = opened()
    assert p.stop_price == pytest.approx(p.entry_price * 0.85)
    b2 = bot([good("Wide1111111111111111111111111111111111111")], b.jupiter, wide_stop_pct=22.0)
    st2 = b2.engine.published[0]
    b2.tick()
    asyncio.run(b2.execute_intents())
    p2 = b2.book.positions[st2.mint]
    assert p2.stop_price == pytest.approx(p2.entry_price * 0.78)


# ---------------------------------------------------------------- runner
def test_runner_keeps_a_fraction_at_tp2_then_trails():
    c = TradingConfig(tp2_runner_frac=0.25, runner_trailing_pct=25.0)
    p = pos(tp1_done=True, high_price=1.9)
    assert apply_exit_options(p, (1.0, "take_profit_2"), 1.9, c, NOW) == (0.75, "take_profit_2")
    p.tokens = 25.0
    on_filled_sell(p, "take_profit_2", c)
    assert p.runner_active and p.stop_price == pytest.approx(1.0) and p.trailing_pct == 25.0
    assert apply_exit_options(p, (1.0, "take_profit_2"), 2.0, c, NOW) is None          # runs above TP2
    p.high_price = 3.0
    assert apply_exit_options(p, (1.0, "take_profit_2"), 2.2, c, NOW) == (1.0, RUNNER_TRAILING)   # -26.7 % < -25 %
    assert apply_exit_options(p, (1.0, "trailing_stop"), 1.5, c, NOW) == (1.0, RUNNER_TRAILING)
    p.opened_at = NOW - c.max_hold_min * 60
    p.high_price = 2.0
    assert apply_exit_options(p, (1.0, "take_profit_2"), 1.95, c, NOW) == (1.0, "max_hold_time")


def test_runner_in_the_bot():
    b, st, p = opened()
    b.cfg.tp2_runner_frac = 0.25
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = b.jupiter.sell_price = p.entry_price * 1.9            # straight to TP2
    b.tick()
    asyncio.run(b.execute_sells())
    assert MINT in b.book.positions and p.runner_active
    assert p.tokens == pytest.approx(p.initial_tokens * 0.25, rel=1e-6)
    st.stamps["market"].updated_at = time.time() + 2
    st.market.price_usd = b.jupiter.sell_price = p.high_price * 0.8             # -20 % from the high (trailing 15 %)
    b.tick()
    asyncio.run(b.execute_sells())
    assert MINT not in b.book.positions and b.book.closed[0].exit_reason == RUNNER_TRAILING


# ---------------------------------------------------------------- time stop
def test_time_stop_without_a_new_high():
    c = TradingConfig(time_stop_min=20.0)
    p = pos(opened_at=NOW - 25 * 60)
    assert apply_exit_options(p, None, 1.0, c, NOW) == (1.0, TIME_STOP)              # never a new high
    p.high_ts = NOW - 5 * 60
    assert apply_exit_options(p, None, 1.0, c, NOW) is None                          # recent new high
    p.high_ts = NOW - 21 * 60
    assert apply_exit_options(p, None, 1.0, c, NOW) == (1.0, TIME_STOP)
    assert apply_exit_options(p, None, None, c, NOW) is None                         # no price: never a guess
    assert apply_exit_options(p, (1.0, "stop_loss"), 0.8, c, NOW) == (1.0, "stop_loss")   # real exits first


def test_time_stop_in_the_bot_tracks_new_highs():
    b, st, p = opened()
    b.cfg.time_stop_min = 20.0
    t = time.time()
    st.stamps["market"].updated_at = t + 1
    st.market.price_usd = p.entry_price * 1.1                                    # new high -> high_ts
    b.tick(t + 1)
    assert p.high_ts == pytest.approx(t + 1)
    st.stamps["market"].updated_at = t + 1300
    st.market.price_usd = p.entry_price * 1.05                                   # no new high for 21+ min
    b.tick(t + 1300)
    it = b.sell_intents[MINT]
    assert it["reason"] == TIME_STOP and it["hard"] is False
