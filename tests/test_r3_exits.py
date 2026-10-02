"""Review round 3 (B4): break-even and trailing stops are protective; a waiting exit whose mark is through the stop
escalates even when the route impact is above max_slippage; escalated reasons count as stops in the report."""
import asyncio
import time

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


def _stale(b, st, p, minutes):
    """No validated price since `minutes` (the print stays older than the fill)."""
    p.last_price_ts = time.time() - minutes * 60
    st.stamps["market"].updated_at = p.opened_at - 100
    st.market.updated_at = p.opened_at - 100


def test_stale_position_is_force_closed_after_10_min_with_quote_or_haircut():
    b, st, p = opened()
    b.jupiter = SellScript([J.NO_ROUTE], p.entry_price)
    _stale(b, st, p, 9)
    b.tick()
    assert MINT not in b.sell_intents                                          # 9 min: still waiting
    _stale(b, st, p, B.STALE_TIMEOUT_S / 60 + 1)
    b.tick()
    it = b.sell_intents[MINT]
    assert it["reason"] == "stale_timeout" and it["hard"] and it["mark"] == p.last_price
    asyncio.run(b.execute_sells())
    j = b.book.journal[-1]
    assert MINT not in b.book.positions and j["exit_reason"] == "stale_timeout" and j["haircut"]
    assert j["net_pnl_pct"] < -25                                              # the haircut is in the P&L


def test_stale_token_gone_from_the_feed_is_closed_at_the_haircut():
    b, st, p = opened()
    b.jupiter = SellScript([J.OK], p.entry_price)
    p.last_price_ts = time.time() - B.STALE_TIMEOUT_S - 60
    b.engine.published = []                                                    # the token left the feed
    b.tick()
    j = b.book.journal[-1]
    assert MINT not in b.book.positions and j["exit_reason"] == "stale_timeout" and j["haircut"]


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
