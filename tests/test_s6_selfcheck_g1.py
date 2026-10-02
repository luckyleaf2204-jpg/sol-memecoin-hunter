"""s6 self-check, G1 (b)-(f): a waiting exit turns HARD when the price falls through the stop; the SELL retry never
blocks the tick; a position is never sold twice; a restart in the middle of a retry re-creates the exit on the first
tick; one Jupiter quote budget (50/min) where SELL always comes first."""
import asyncio
import time

import trading.bot as B
from test_bot_v2 import FakeJupiter, bot, good
from test_g1_sell_retry import SellScript, _signal
from test_v12 import MINT, opened
from trading import jupiter as J
from trading.bot import PaperBot
from trading.config import TradingConfig
from trading.quote_budget import QuoteBudget

MB = "GoodMint2222222222222222222222222222222222"


def _sells(b):
    return [e for e in b.book.executions if e.side == "SELL"]


def _ok(input_mint, amount_raw, price):
    out = int(amount_raw / 1e6 * price / 150 * 1e9)
    return J.QuoteResult(J.OK, quote={"inputMint": input_mint, "outputMint": J.WSOL, "inAmount": str(amount_raw),
                                      "outAmount": str(out), "priceImpactPct": "0.01", "routePlan": []}, http=200)


class PerMint(FakeJupiter):
    """BUY quotes like FakeJupiter; SELL quotes per mint: 'ok', 'timeout' or ('slow', seconds)."""
    def __init__(self, script, price=0.00016):
        super().__init__()
        self.script, self.price, self.sell_calls, self.during = script, price, [], []
        self.bot = None

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps, **kw):
        if output_mint != J.WSOL:
            return J.QuoteResult(J.OK, quote=await self.quote(input_mint, output_mint, amount_raw, slippage_bps),
                                 http=200, attempts=1)
        self.sell_calls.append(input_mint)
        beh = self.script.get(input_mint, "ok")
        if isinstance(beh, tuple):
            t0 = self.bot.ticks if self.bot else 0
            await asyncio.sleep(beh[1])
            self.during.append((self.bot.ticks if self.bot else 0) - t0)
        elif beh == "timeout":
            return J.QuoteResult(J.TIMEOUT, attempts=2)
        return _ok(input_mint, amount_raw, self.price)


def two_open(jup):
    sa, sb = good(MINT), good(MB)
    for s in (sa, sb):
        s.market.price_usd = 0.0002
    b = bot([sa, sb], jup)
    for s in (sa, sb):
        s.stamps["market"].updated_at = s.market.updated_at = time.time() - 15
    for _ in range(3):                                           # one entry per tick
        b.tick()
        asyncio.run(b.execute_intents())
    assert MINT in b.book.positions and MB in b.book.positions
    return b, sa, sb


# ---------------------------------------------------------------- (b) a waiting exit becomes HARD
def test_waiting_exit_escalates_to_hard_when_the_mark_falls_through_the_stop():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price)
    _signal(b, st, p, 1.35)                                      # TP1 (non-HARD) waits for a quote
    t = time.time()
    asyncio.run(b.execute_sells(t))
    assert not b.sell_intents[MINT]["hard"]
    p.last_price = p.stop_price * 0.9                            # price fell through the stop meanwhile
    asyncio.run(b.execute_sells(t + B.QUOTE_RETRY_WINDOW_S + 1))
    sell = _sells(b)[-1]
    assert MINT not in b.book.positions and sell.model.startswith("PAPER haircut") and "emergency" in sell.reason
    assert any("escalated to HARD" in a.text for a in b.activity)


def test_hard_signal_replacing_a_waiting_exit_keeps_the_retry_start():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price)
    _signal(b, st, p, 1.35)                                      # TP1 waits
    t = time.time()
    asyncio.run(b.execute_sells(t))
    _signal(b, st, p, 0.8)                                       # then the stop loss fires
    it = b.sell_intents[MINT]
    assert it["hard"] and it["reason"] == "stop_loss" and it["first"] == t      # not a fresh 60 s
    asyncio.run(b.execute_sells(t + B.QUOTE_RETRY_WINDOW_S + 1))
    assert MINT not in b.book.positions and "emergency" in _sells(b)[-1].reason


# ---------------------------------------------------------------- (c) the retry never blocks the tick
def test_slow_sell_quote_does_not_block_ticks_or_other_sells(monkeypatch):
    monkeypatch.setattr(B, "TICK_S", 0.02)
    j = PerMint({MINT: ("slow", 0.6), MB: "ok"})
    b, sa, sb = two_open(j)
    j.bot = b
    pa = b.book.positions[MINT]
    _signal(b, sa, pa, 0.8)                                      # A: stop loss, its quote takes 0.6 s
    stop = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(b.run(stop))
        await asyncio.sleep(0.15)                                # A's quote is in flight
        pb = b.book.positions[MB]
        sb.stamps["market"].updated_at = time.time() + 1
        sb.market.price_usd = pb.entry_price * 0.8               # B hits its stop while A is still quoting
        for _ in range(100):
            await asyncio.sleep(0.02)
            if MB not in b.book.positions and MINT not in b.book.positions:
                break
        stop.set()
        await task
    asyncio.run(scenario())
    assert j.during and j.during[0] >= 5                         # >= 5 ticks ran during A's 0.6 s quote
    assert MINT not in b.book.positions and MB not in b.book.positions
    assert len(_sells(b)) == 2


def test_concurrent_quotes_fill_as_they_land():
    j = PerMint({MINT: ("slow", 0.4), MB: "ok"})
    b, sa, sb = two_open(j)
    for s, m in ((sa, MINT), (sb, MB)):
        _signal(b, s, b.book.positions[m], 0.8)
    t0 = time.monotonic()
    asyncio.run(b.execute_sells())
    assert time.monotonic() - t0 < 0.8                           # not sequential
    assert [e.mint for e in _sells(b)] == [MB, MINT]             # B filled before A's slow quote returned


# ---------------------------------------------------------------- (d) never sold twice
def test_two_overlapping_sell_rounds_sell_once():
    j = PerMint({MINT: ("slow", 0.2)})
    b, st, p = opened()
    b.jupiter = j
    _signal(b, st, p, 0.8)

    async def both():
        await asyncio.gather(b.execute_sells(), b.execute_sells())
    asyncio.run(both())
    assert len(_sells(b)) == 1 and MINT not in b.book.positions


def test_position_changed_while_quoting_is_not_filled_on_the_old_quote():
    j = PerMint({MINT: ("slow", 0.1)})
    b, st, p = opened()
    b.jupiter = j
    _signal(b, st, p, 0.8)

    async def scenario():
        task = asyncio.create_task(b.execute_sells())
        await asyncio.sleep(0.02)
        p.tokens *= 0.5                                          # another fill changed the size mid-quote
        await task
    asyncio.run(scenario())
    assert not _sells(b) and MINT in b.sell_intents              # re-decided next round, nothing sold blind


# ---------------------------------------------------------------- (e) restart in the middle of a retry
def test_restart_during_retry_re_creates_the_exit_on_the_first_tick(tmp_path):
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price * 0.8)
    _signal(b, st, p, 0.8)
    asyncio.run(b.execute_sells())
    assert MINT in b.sell_intents and MINT in b.book.positions
    b.book.save(tmp_path / "pb.json")                            # process dies here; intents were in memory
    b2 = PaperBot(b.engine, TradingConfig(seed=4), state_path=tmp_path / "pb.json")
    b2.jupiter = SellScript([J.OK], p.entry_price * 0.8)
    assert MINT in b2.book.positions and not b2.sell_intents
    st.stamps["market"].updated_at = time.time() + 1             # first tick after the restart, current price
    b2.tick()
    assert b2.sell_intents[MINT]["reason"] == "stop_loss" and b2.sell_intents[MINT]["hard"]
    asyncio.run(b2.execute_sells())
    assert MINT not in b2.book.positions


# ---------------------------------------------------------------- (f) one quote budget, SELL first
def test_budget_unit():
    qb = QuoteBudget(per_min=5, sell_reserve=2)
    assert [qb.take("buy", 0.0) for _ in range(4)] == [True, True, True, False]     # 5 - 2 reserved for SELL
    assert qb.take("truth", 1.0) is False and qb.take("probe", 1.0) is False
    assert qb.take("sell", 1.0) and qb.take("sell", 1.0) and not qb.take("sell", 1.0)
    assert qb.take("buy", 61.0)                                  # the window slides
    assert qb.take("buy", 61.0, sells_pending=False) and not qb.take("buy", 62.0, sells_pending=True)
    assert qb.as_dict(62.0)["refused"] == {"buy": 2, "truth": 1, "probe": 1, "sell": 1}


def test_exhausted_budget_sell_still_gets_quotes_buy_truth_probe_wait():
    b, st, p = opened()
    j = SellScript([J.OK], p.entry_price * 0.8)
    b.jupiter = j
    b.quote_budget = QuoteBudget(per_min=50, sell_reserve=10)
    for _ in range(40):
        assert b.quote_budget.take("buy")                        # buys / truth used everything they may use
    qr = asyncio.run(b._buy_quote(MB, 10 ** 8))
    assert qr.status == J.BUDGET and qr.transient          # BUY: transient -> its own retry window
    assert asyncio.run(b._latency_probe(st, MINT, 1000, {"outAmount": "1"})) is None
    _signal(b, st, p, 0.8)
    asyncio.run(b.execute_sells())
    assert MINT not in b.book.positions and j.sells == 1         # SELL used the reserved part
    assert b.quote_budget.as_dict()["by_kind"]["sell"] == 1


def test_pending_sell_blocks_buy_quotes_even_with_room():
    b, st, p = opened()
    b.jupiter = SellScript([J.TIMEOUT], p.entry_price)
    b.quote_budget = QuoteBudget()
    _signal(b, st, p, 0.8)
    asyncio.run(b.execute_sells())                               # SELL retrying
    assert asyncio.run(b._buy_quote(MB, 10 ** 8)).status == J.BUDGET
    assert asyncio.run(b.common_source_round(time.time() + 2)) == 0


def test_sell_budget_exhausted_is_transient_not_a_haircut():
    b, st, p = opened()
    b.jupiter = SellScript([J.OK], p.entry_price * 0.8)
    b.quote_budget = QuoteBudget(per_min=1, sell_reserve=1)
    b.quote_budget.take("sell")
    _signal(b, st, p, 0.8)
    asyncio.run(b.execute_sells())
    assert MINT in b.book.positions and b.sell_intents[MINT]["retries"] == 1 and not _sells(b)


def test_server_wires_the_budget():
    import inspect

    import web.app as webapp
    assert "QuoteBudget()" in inspect.getsource(webapp.create_app)


def test_a_single_sell_quote_call_is_short():
    assert B.SELL_QUOTE_BUDGET_S <= 5.0 and 1 <= B.SELL_QUOTE_ATTEMPTS <= 2      # the bot-level retry does the rest
