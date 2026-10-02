"""Step D — exits filled with the no-quote haircut are counted and reported apart (with and without them)."""
import asyncio
import time

import pytest

from test_bot_v2 import FakeJupiter
from test_stepB_report import epoch, row
from test_v12 import opened
from trading.sample_report import report


def test_haircut_exit_is_flagged_in_the_journal():
    b, st, p = opened()
    b.jupiter = FakeJupiter(fail=True)                       # no SELL quote
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.8
    b.tick()
    asyncio.run(b.execute_sells())
    r = b.book.journal[-1]
    assert r["haircut"] is True and r["haircut_exits"] == 1


def test_quote_exit_is_not_a_haircut():
    b, st, p = opened()
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = b.jupiter.sell_price = p.entry_price * 0.8
    b.tick()
    asyncio.run(b.execute_sells())
    assert b.book.journal[-1]["haircut"] is False


def test_report_splits_haircut_trades():
    e = epoch()
    rows = [row(e, 10), row(e, -40, "stop_loss"), row(e, 20)]
    rows[1]["haircut"] = True
    h = report(rows, e)["haircut"]
    assert h["n_haircut_trades"] == 1 and h["share_pct"] == pytest.approx(33.3)
    assert h["haircut_trades"]["7%"]["expectancy_pct"] == pytest.approx(-47.0)
    assert h["without_haircut_trades"]["7%"]["expectancy_pct"] == pytest.approx((3 + 13) / 2)
    assert h["without_haircut_trades"]["7%"]["n"] == 2
