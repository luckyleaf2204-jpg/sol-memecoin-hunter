"""G1 — SELL quotes keep their classification: OK fills, NO_ROUTE / INVALID haircut, transient errors (429, timeout,
5xx, breaker open) keep the intent and retry inside QUOTE_RETRY_WINDOW_S; only a HARD exit takes the haircut after
the window. SELL quotes have priority over truth / probe quotes."""
import asyncio
import time

import pytest

import trading.bot as B
from test_bot_v2 import NoRouteJupiter
from test_v12 import MINT, opened
from trading import jupiter as J


class SellScript:
    """BUY quotes OK; SELL quotes follow a script of statuses (the last repeats), OK at `price`."""
    def __init__(self, statuses, price):
        self.statuses, self.price, self.sells = list(statuses), price, 0

    async def quote(self, *a):
        return None

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps):
        s = self.statuses[min(self.sells, len(self.statuses) - 1)]
        self.sells += 1
        if s != J.OK:
            return J.QuoteResult(s, http=429 if s == J.RATE_LIMITED else None, attempts=3)
        out = int(amount_raw / 1e6 * self.price / 150 * 1e9)
        return J.QuoteResult(J.OK, quote={"inputMint": input_mint, "outputMint": output_mint, "inAmount": str(amount_raw),
                                          "outAmount": str(out), "priceImpactPct": "0.01", "routePlan": []}, http=200)


def _signal(b, st, p, factor):
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * factor
    b.tick()


@pytest.mark.parametrize("status", [J.RATE_LIMITED, J.TIMEOUT, J.API_ERROR, J.COOLDOWN])
def test_transient_error_keeps_the_intent_and_retries_then_fills(status):
    b, st, p = opened()
    j = SellScript([status, J.OK], p.entry_price * 0.8)
    b.jupiter = j
    _signal(b, st, p, 0.8)                                       # stop loss (HARD)
    t = time.time()
    asyncio.run(b.execute_sells(t))
    it = b.sell_intents[MINT]
    assert MINT in b.book.positions and it["retries"] == 1 and it["next"] == pytest.approx(t + 2.5)
    asyncio.run(b.execute_sells(t + 1))                          # backing off: no quote call
    assert j.sells == 1
    asyncio.run(b.execute_sells(t + 3))                          # retry -> OK -> filled on the quote
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert MINT not in b.book.positions and "real Jupiter quote" in sell.model and b.sell_quote_stats[status] == 1


def test_hard_exit_haircut_only_after_the_retry_window():
    b, st, p = opened()
    b.jupiter = SellScript([J.RATE_LIMITED], p.entry_price * 0.8)
    _signal(b, st, p, 0.8)
    t = time.time()
    asyncio.run(b.execute_sells(t))
    _signal(b, st, p, 0.8)                                       # the next tick must not reset the retry state
    assert b.sell_intents[MINT]["first"] == t
    asyncio.run(b.execute_sells(t + B.QUOTE_RETRY_WINDOW_S - 1))
    assert MINT in b.book.positions
    asyncio.run(b.execute_sells(t + B.QUOTE_RETRY_WINDOW_S + 1))
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert MINT not in b.book.positions and sell.model.startswith("PAPER haircut") and "emergency" in sell.reason


def test_non_hard_exit_is_never_sold_blind():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price * 1.35)
    _signal(b, st, p, 1.35)                                      # TP1 (non-HARD)
    t = time.time()
    asyncio.run(b.execute_sells(t))
    asyncio.run(b.execute_sells(t + B.QUOTE_RETRY_WINDOW_S + 1))
    assert MINT in b.book.positions and MINT not in b.sell_intents and p.tokens == p.initial_tokens
    assert any("postponed" in a.text for a in b.activity)
    assert not [e for e in b.book.executions if e.side == "SELL"]


def test_no_route_is_a_haircut_for_any_exit():
    b, st, p = opened()
    b.jupiter = NoRouteJupiter()
    _signal(b, st, p, 1.35)                                      # TP1, but no executable route
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.model.startswith("PAPER haircut") and b.sell_quote_stats[J.NO_ROUTE] == 1


def test_sells_have_priority_over_truth_and_probe_quotes():
    b, st, p = opened()
    j = SellScript([J.RATE_LIMITED], p.entry_price)
    b.jupiter = j
    _signal(b, st, p, 0.8)
    assert MINT in b.sell_intents
    assert asyncio.run(b.common_source_round(time.time() + 2)) == 0 and j.sells == 0   # truth quotes wait
    b.cfg.latency_probe = True
    assert asyncio.run(b._latency_probe(st, MINT, 1000, {"outAmount": "1"})) is None   # probe waits too
