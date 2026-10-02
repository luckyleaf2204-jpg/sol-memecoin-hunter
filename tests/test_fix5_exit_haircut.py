"""Fix 5 — a NON-HARD exit (TP, trailing, momentum, time) without an executable SELL quote takes the same haircut
as a HARD exit; it is never filled on the liquidity model at the DexScreener mark."""
import asyncio
import time

import pytest

from test_bot_v2 import FakeJupiter
from test_v12 import MINT, opened


def _tp1(b, st, p):
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 1.35               # TP1 (non-HARD)
    b.tick()


def test_take_profit_without_quote_is_a_haircut():
    b, st, p = opened()
    b.jupiter = FakeJupiter(fail=True)
    _tp1(b, st, p)
    assert b.sell_intents[MINT]["hard"] is False
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.reason == "take_profit_1" and sell.model.startswith("PAPER haircut")
    assert sell.fill_price == pytest.approx(p.entry_price * 1.35 * 0.7)
    assert b.book.journal == [] and p.haircut_exits == 1


def test_no_jupiter_client_every_exit_is_a_haircut():
    b, st, p = opened()
    b.jupiter = None
    _tp1(b, st, p)                                            # filled at once in the tick
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.model.startswith("PAPER haircut") and sell.fill_price == pytest.approx(p.entry_price * 1.35 * 0.7)


def test_take_profit_with_quote_is_unchanged():
    b, st, p = opened()
    b.jupiter.sell_price = p.entry_price * 1.35
    _tp1(b, st, p)
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert "real Jupiter quote" in sell.model and p.haircut_exits == 0
