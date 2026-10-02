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
