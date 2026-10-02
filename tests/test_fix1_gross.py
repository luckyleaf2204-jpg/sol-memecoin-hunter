"""Fix 1 — a no-quote haircut exit is part of the gross move (mid = the haircut price) and the stop-gap floor
(-20 %) applies to every HARD exit, not only stop_loss."""
import asyncio
import time

import pytest

from test_bot_v2 import FakeJupiter, NoRouteJupiter
from test_stepB_report import epoch, row
from test_v12 import opened
from trading.book import mid_price
from trading.execution import HAIRCUT_MODEL
from trading.models import Execution
from trading.sample_report import report


def test_haircut_fill_mid_is_the_fill():
    ex = Execution(ts=0, mint="m", symbol="s", side="SELL", route="HAIRCUT", ref_price=1.0, latency_ms=0,
                   status="FILLED", fill_price=0.7, tokens=1.0, price_impact_pct=0.0, slippage_pct=30.0,
                   model=HAIRCUT_MODEL)
    assert mid_price(ex) == pytest.approx(0.7)


def test_haircut_exit_loss_is_in_the_gross_move():
    b, st, p = opened()
    b.jupiter = NoRouteJupiter()                    # no SELL quote -> haircut 30 %
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.8
    b.tick()
    asyncio.run(b.execute_sells())
    r = b.book.journal[-1]
    assert r["haircut"] and r["gross_move_pct"] == pytest.approx(100 * (0.8 * 0.7 * p.entry_price / p.entry_mid - 1), abs=0.01)
    assert r["gross_move_pct"] < -40                      # was ~ -20 % when the haircut was backed out as a cost


@pytest.mark.parametrize("reason", ["risk_spike", "liquidity_collapse", "whale_dump", "holder_anomaly",
                                    "identity_conflict", "stop_loss"])
def test_stop_gap_floor_on_every_hard_exit(reason):
    e = epoch()
    rows = [row(e, -8.0, reason), row(e, 30.0, "take_profit_2"), row(e, -5.0, "trailing_stop")]
    g = report(rows, e)["sl_gap_scenario"]
    assert g["hard_exits"] == 2                       # B4: a trailing stop is protective too (gap floor applies)
    assert g["by_cost"]["5%"]["expectancy_pct"] == pytest.approx((-25 + 25 - 25) / 3, abs=1e-3)   # -8, -5 -> -20
