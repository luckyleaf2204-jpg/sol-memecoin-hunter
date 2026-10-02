"""Review round 3 (B4): break-even and trailing stops are protective; a waiting exit whose mark is through the stop
escalates even when the route impact is above max_slippage; escalated reasons count as stops in the report."""
import asyncio
import time

import pytest

from test_g1_sell_retry import SellScript, _signal
from test_stepB_report import epoch, row
from test_v12 import MINT, opened
from trading import jupiter as J
from trading.exit_policy import PROTECTIVE, base_reason, is_protective
from trading.sample_report import report


def test_protective_set():
    assert {"stop_loss", "break_even_stop", "trailing_stop", "risk_spike"} <= set(PROTECTIVE)
    assert not is_protective("take_profit_1") and is_protective("take_profit_1->stop_loss")
    assert base_reason("momentum_deterioration->stop_loss") == "stop_loss" and base_reason(None) == ""


def test_trailing_stop_intent_is_hard():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price)
    p.tp1_done, p.high_price = True, p.entry_price * 2
    _signal(b, st, p, 1.5)                                       # 25 % below the high: trailing stop
    it = b.sell_intents[MINT]
    assert it["reason"] == "trailing_stop" and it["hard"]


class BadRoute(SellScript):
    """SELL quotes OK but with a price impact far above max_slippage."""
    async def quote_result(self, *a, **kw):
        r = await super().quote_result(*a[:4])
        if r.ok:
            r.quote["priceImpactPct"] = "0.5"
        return r


def test_bad_route_waits_unless_the_mark_is_through_the_stop():
    b, st, p = opened()
    b.jupiter = BadRoute([J.OK], p.entry_price * 0.8)
    b.sell_intents[MINT] = {"frac": 1.0, "reason": "max_hold_time", "mark": p.entry_price * 1.05, "ts": time.time(),
                            "hard": False}
    p.last_price = p.entry_price * 1.05
    asyncio.run(b.execute_sells())
    assert MINT in b.book.positions and MINT not in b.sell_intents          # waits (re-signalled next tick)
    b.sell_intents[MINT] = {"frac": 1.0, "reason": "max_hold_time", "mark": p.stop_price * 0.9, "ts": time.time(),
                            "hard": False}
    p.last_price = p.stop_price * 0.9                                         # through the stop meanwhile
    asyncio.run(b.execute_sells())
    assert MINT not in b.book.positions
    assert b.book.journal[-1]["exit_reason"] == "max_hold_time->stop_loss"


def test_escalated_reasons_count_in_the_sl_gap_scenario():
    e = epoch()
    rows = [row(e, -8.0, "take_profit_1->stop_loss"), row(e, -3.0, "break_even_stop"), row(e, 30.0, "take_profit_2")]
    r = report(rows, e)
    assert r["sl_gap_scenario"]["hard_exits"] == 2 and r["sl_gap_scenario"]["stop_exits"] == 1
    assert r["reference_only"]["sl_exits"] == 1


# ---------------------------------------------------------------- B6: stale positions are closed and counted
import trading.bot as B  # noqa: E402


def _no_price(st, p):
    """The token's print stays older than the fill: no validated price for this position."""
    st.stamps["market"].updated_at = p.opened_at - 100
    st.market.updated_at = p.opened_at - 100


def _run_ticks(b, minutes, t0=None, step=20.0):
    t = t0 or time.time()
    for _ in range(int(minutes * 60 / step)):
        t += step
        b.tick(t)
    return t


def test_stale_position_is_force_closed_after_10_healthy_minutes_with_quote_or_haircut():
    b, st, p = opened()
    b.jupiter = SellScript([J.NO_ROUTE], p.entry_price)
    _no_price(st, p)
    t = _run_ticks(b, 9)
    assert MINT not in b.sell_intents                                          # 9 min: still waiting
    _run_ticks(b, 2, t)
    it = b.sell_intents[MINT]
    assert it["reason"] == "stale_timeout" and it["hard"] and it["mark"] == p.last_price
    asyncio.run(b.execute_sells())
    j = b.book.journal[-1]
    assert MINT not in b.book.positions and j["exit_reason"] == "stale_timeout" and j["haircut"]
    assert j["net_pnl_pct"] < -25                                              # the haircut is in the P&L


def test_stale_token_gone_from_the_feed_is_closed_at_the_haircut():
    b, st, p = opened()
    _no_price(st, p)
    b.jupiter = SellScript([J.OK], p.entry_price)
    t = _run_ticks(b, 5)
    from test_bot_v2 import good
    b.engine.published = [good("OtherMint111111111111111111111111111111111")]   # feed alive, our token gone
    _run_ticks(b, 6, t)
    j = b.book.journal[-1]
    assert MINT not in b.book.positions and j["exit_reason"] == "stale_timeout" and j["haircut"]


def test_restore_with_an_old_last_price_ts_does_not_close():
    """After a restart the stale clock starts at 0: an old last_price_ts from the book never closes a position."""
    from trading.bot import PaperBot
    from trading.config import TradingConfig
    b, st, p = opened()
    p.last_price_ts = time.time() - 3 * 3600                                    # saved 3 h ago
    b2 = PaperBot(b.engine, TradingConfig(seed=4))
    b2.book = b.book
    b2.jupiter = SellScript([J.OK], p.entry_price)
    _no_price(st, p)
    t = time.time()
    b2.tick(t)
    b2.tick(t + 20)
    assert MINT in b2.book.positions and MINT not in b2.sell_intents and b2.stale_s.get(MINT, 0) <= 20


def test_feed_down_for_more_than_10_minutes_does_not_close():
    b, st, p = opened()
    b.jupiter = SellScript([J.OK], p.entry_price)
    _no_price(st, p)
    b.engine._feeds = {"dexscreener": {"ok": False, "last_error": "503"}, "pumpportal": {"connected": True}}
    t = _run_ticks(b, 15)                                                       # whole feed down 15 min
    assert MINT in b.book.positions and MINT not in b.sell_intents and not b.stale_s.get(MINT)
    b.engine.published = []                                                    # nothing published at all
    b.engine._feeds = {"dexscreener": {"ok": True, "cooldown_s": 0}, "pumpportal": {"connected": True}}
    _run_ticks(b, 15, t)
    assert MINT in b.book.positions and not b.stale_s.get(MINT)


def test_jupiter_breaker_open_does_not_count_as_stale():
    b, st, p = opened()

    class Health:
        def cooling(self, source):
            return 30.0

    class Http:
        health = Health()

    class DownJupiter(SellScript):
        http = Http()
    b.jupiter = DownJupiter([J.COOLDOWN], p.entry_price)
    _no_price(st, p)
    _run_ticks(b, 15)
    assert MINT in b.book.positions and MINT not in b.sell_intents


def test_a_pause_between_ticks_is_not_stale_time():
    b, st, p = opened()
    b.jupiter = SellScript([J.OK], p.entry_price)
    _no_price(st, p)
    t = time.time()
    b.tick(t)
    b.tick(t + 3600)                                                            # the loop slept an hour
    assert b.stale_s[MINT] <= B.STALE_MAX_STEP_S + 1 and MINT not in b.sell_intents   # not 3600 s


def test_protective_intent_of_a_vanished_token_is_not_dropped():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price * 0.8)
    _signal(b, st, p, 0.8)
    b.engine.published = []
    asyncio.run(b.execute_sells())
    assert MINT not in b.book.positions and b.book.journal[-1]["exit_reason"] == "stop_loss"


def test_stale_timeout_counts_in_the_report():
    e = epoch()
    r = report([row(e, -30.0, "stale_timeout"), row(e, 10.0, "take_profit_1")], e)
    assert r["n"] == 2 and r["sl_gap_scenario"]["hard_exits"] == 1


# ---------------------------------------------------------------- C4: the SL-gap floor only on losing stops
def test_sl_gap_floor_only_on_losing_real_stops():
    from trading.sample_report import gross_of
    assert gross_of({"gross_move_pct": 25.0, "exit_reason": "trailing_stop"}, sl_gap=True) == 25.0
    assert gross_of({"gross_move_pct": -5.0, "exit_reason": "trailing_stop"}, sl_gap=True) == -5.0
    assert gross_of({"gross_move_pct": 1.0, "exit_reason": "break_even_stop"}, sl_gap=True) == 1.0
    assert gross_of({"gross_move_pct": -2.0, "exit_reason": "break_even_stop"}, sl_gap=True) == -20.0
    assert gross_of({"gross_move_pct": -8.0, "exit_reason": "take_profit_1->stop_loss"}, sl_gap=True) == -20.0
    assert gross_of({"gross_move_pct": 3.0, "exit_reason": "risk_spike"}, sl_gap=True) == 3.0
    assert gross_of({"gross_move_pct": -30.0, "exit_reason": "stop_loss"}, sl_gap=True) == -30.0


def test_sl_gap_scenario_keeps_a_winning_trailing_stop():
    e = epoch()
    rows = [row(e, 25.0, "trailing_stop"), row(e, -8.0, "stop_loss"), row(e, 4.0, "whale_dump")]
    m = report(rows, e)["sl_gap_scenario"]["by_cost"]["5%"]
    assert m["expectancy_pct"] == pytest.approx((20 - 25 - 1) / 3, abs=1e-3)
