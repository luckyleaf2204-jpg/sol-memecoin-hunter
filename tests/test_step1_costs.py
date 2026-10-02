"""Step 1 — clean numbers: no simulated (NO_ROUTE) fills by default and never in the main P&L; HARD / SL exits are
filled on a Jupiter SELL quote or with a large haircut (never near the DexScreener mark); priority fee / Jito tip and
long-tail dump slippage in the cost model; P&L at 5 / 7 / 10 % round-trip cost."""
import asyncio
import time

import pytest

from core.models import LiquidityIntel
from test_bot_v2 import FakeJupiter, NoRouteJupiter, bot, good
from test_v12 import MINT, opened
from trading.book import PaperBook, cost_stress, mid_price
from trading.config import TradingConfig
from trading.execution import NETWORK_FEE_SOL, PaperExecutor
from trading.models import Execution


def crash(b, st, p, factor=0.7):
    st.stamps["market"].updated_at = time.time() + 1          # a print observed after the fill
    st.market.price_usd = p.entry_price * factor
    b.tick()


def test_defaults():
    c = TradingConfig()
    assert c.paper_fill_without_quote is False and c.hard_exit_no_quote_haircut_pct == 30.0
    assert c.priority_fee_sol == 0.005 and tuple(c.cost_stress_pct) == (5.0, 7.0, 10.0)
    assert c.stop_loss_pct == 15.0 and c.max_slippage_pct == 3.0          # nothing else changed


# ---------------------------------------------------------------- HARD / SL exits
def test_stop_loss_is_filled_on_a_jupiter_sell_quote_even_above_the_impact_limit():
    b, st, p = opened()
    b.jupiter.impact, b.jupiter.sell_price = "0.08", p.entry_price * 0.7    # 8 % impact > 3 % limit
    crash(b, st, p)
    assert b.sell_intents[MINT]["hard"] and MINT in b.book.positions
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert MINT not in b.book.positions and b.book.closed[0].exit_reason == "stop_loss"
    assert "real Jupiter quote" in sell.model and sell.ref_price == pytest.approx(p.entry_price * 0.7)


def test_stop_loss_without_quote_takes_the_configured_haircut():
    b, st, p = opened()
    b.jupiter = NoRouteJupiter()
    b.cfg.hard_exit_no_quote_haircut_pct = 50.0
    crash(b, st, p)
    asyncio.run(b.execute_sells())
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.fill_price == pytest.approx(p.entry_price * 0.7 * 0.5) and sell.model.startswith("PAPER haircut")
    assert sell.usd_in == pytest.approx(sell.tokens * sell.fill_price) and sell.network_fee_usd > 0


def test_risk_exit_without_any_jupiter_client_is_a_haircut_not_a_mark_fill():
    b, st, p = opened()
    b.jupiter = None
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()                                                   # no async step needed: filled at once
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert b.book.closed[0].exit_reason == "liquidity_collapse"
    assert sell.fill_price == pytest.approx(p.last_price * 0.7)            # mark = the fill: no post-entry print


def test_hard_exit_replaces_a_pending_take_profit_intent():
    b, st, p = opened()
    b.sell_intents[MINT] = {"frac": 0.5, "reason": "take_profit_1", "mark": p.entry_price, "ts": 0, "hard": False}
    crash(b, st, p)
    assert b.sell_intents[MINT]["reason"] == "stop_loss" and b.sell_intents[MINT]["frac"] == 1.0


# ---------------------------------------------------------------- fees / slippage tail
def test_priority_fee_on_every_tx_and_configurable():
    ex = PaperExecutor(seed=1)
    assert ex.network_fee(150.0) == pytest.approx((NETWORK_FEE_SOL + 0.005) * 150)
    b = bot([good()], FakeJupiter(), priority_fee_sol=0.01)
    assert b.exec.network_fee(100.0) == pytest.approx((NETWORK_FEE_SOL + 0.01) * 100)
    b.exec.rng.random = lambda: 0.0                            # every tx fails: the fee is still paid
    failed = b.exec.buy(good(), 40, 150.0)
    assert failed.status == "FAILED" and failed.network_fee_usd == pytest.approx((NETWORK_FEE_SOL + 0.01) * 150)


def test_dump_tail_only_while_dumping():
    ex = PaperExecutor(seed=3)
    st = good()
    st.market.price_change_5m = -5.0
    state = ex.rng.getstate()
    assert ex.dump_tail(st) == 0.0 and ex.rng.getstate() == state          # no draw: other runs unchanged
    st.market.price_change_5m = -60.0
    ex.rng.expovariate = lambda lam: 2.0
    assert ex.dump_tail(st) == pytest.approx(0.10 * 0.60 * 2.0)
    ex.rng.expovariate = lambda lam: 50.0
    assert ex.dump_tail(st) == pytest.approx(0.25)                         # capped


def test_dumping_sell_is_worse_than_calm_sell():
    def sell(pc5):
        ex = PaperExecutor(seed=5)
        ex.rng.random = lambda: 0.99
        ex.rng.expovariate = lambda lam: 1.0
        st = good()
        st.market.price_change_5m = pc5
        return ex.sell(st, 1000, 0.0002, 150.0, "stop_loss", force=True)
    calm, dump = sell(-5.0), sell(-60.0)
    # latency slip: 0.495 % + min(2 %, 5 % x |5m|) -> calm 0.25 %, dump 2 %; dump tail 0.10 x 0.60 x 1.0 = 6 %
    assert dump.fill_price < calm.fill_price
    assert dump.slippage_pct - calm.slippage_pct == pytest.approx((2.0 - 0.25) + 6.0, abs=1e-6)


# ---------------------------------------------------------------- main P&L / cost stress
def _closed(setup="", gross_exit=1.2, cost=100.0, realized=None):
    from trading.models import Position
    p = Position(id=1, mint="M" + setup + str(gross_exit), symbol="T", opened_at=0, entry_price=1.0, tokens=0.0,
                 cost_usd=cost, initial_tokens=100.0, stop_price=0.85, tp1_price=1.3, tp2_price=1.8, trailing_pct=15,
                 high_price=1.0, status="CLOSED", setup=setup, entry_mid=1.0, exit_mid_value=100.0 * gross_exit)
    p.realized_usd = realized if realized is not None else 100.0 * gross_exit * 0.95
    return p


def test_noquote_trades_are_excluded_from_the_main_pnl():
    b = PaperBook(1000.0)
    real, sim = _closed(gross_exit=1.2), _closed(setup="NEW+noquote", gross_exit=2.0)
    b.closed = [real, sim]
    b.cash = 1000.0 + (real.realized_usd - real.cost_usd) + (sim.realized_usd - sim.cost_usd)
    s = b.stats()
    assert s["net_pnl"] == pytest.approx(real.realized_usd - real.cost_usd)
    assert s["net_pnl_all"] == pytest.approx(s["net_pnl"] + sim.realized_usd - sim.cost_usd)
    assert s["closed"] == 1 and s["noquote"]["closed"] == 1 and s["cost_stress"]["n"] == 1


def test_cost_stress_levels():
    rows = [_closed(gross_exit=1.2), _closed(gross_exit=1.04), _closed(gross_exit=0.9)]
    r = cost_stress(rows, (5.0, 7.0, 10.0))
    assert r["n"] == 3 and r["gross_move_mean_pct"] == pytest.approx((20 + 4 - 10) / 3, abs=0.01)
    assert r["levels"]["5%"]["mean_pct"] == pytest.approx((20 + 4 - 10) / 3 - 5, abs=0.01)
    assert r["levels"]["7%"]["win_rate"] == pytest.approx(33.3, abs=0.1)        # only +20 survives
    assert r["levels"]["10%"]["total_usd"] == pytest.approx(100 * (0.10 - 0.06 - 0.20), abs=0.01)
    assert r["modelled_cost_mean_pct"] is not None


def test_mid_price_backs_out_impact_and_slippage():
    buy = Execution(ts=0, mint="m", symbol="s", side="BUY", route="r", ref_price=1.0, latency_ms=0, status="FILLED",
                    fill_price=1.0 * 1.02 * 1.01, tokens=1.0, price_impact_pct=2.0, slippage_pct=1.0)
    sell = Execution(ts=0, mint="m", symbol="s", side="SELL", route="r", ref_price=1.0, latency_ms=0, status="FILLED",
                     fill_price=0.7, tokens=1.0, price_impact_pct=0.0, slippage_pct=30.0)     # 30 % slippage (not a haircut)
    assert mid_price(buy) == pytest.approx(1.0) and mid_price(sell) == pytest.approx(1.0)


def test_live_book_tracks_gross_move_of_a_real_round_trip():
    b, st, p = opened()
    b.jupiter.sell_price = p.entry_price * 1.0
    crash(b, st, p, factor=0.7)                                # SL on the print, executable ~ flat
    asyncio.run(b.execute_sells())
    c = b.book.closed[0]
    assert c.gross_move_pct is not None and -5 < c.gross_move_pct < 5    # executable price, not the -30 % print
    s = b.book.stats()
    assert s["cost_stress"]["levels"]["10%"]["mean_pct"] == pytest.approx(c.gross_move_pct - 10, abs=0.01)
