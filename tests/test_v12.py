"""V1.2 price accounting: provenance of every price, quote parsing / direction / decimals, discrepancy classes
(stale / pair / decimals), the PRE-ENTRY MARK bug fix (a DexScreener print fetched before the Jupiter fill must not
trigger stop / TP or set MFE / MAE), common-source shadow exit model, slippage decomposition, no look-ahead,
TREMOR / FWO / THESIS / blobtle regressions on the recorded V1.1 numbers."""
import asyncio
import time

import pytest

from test_bot_v2 import FakeJupiter, bot, good
from trading import jupiter as J
from trading.price_provenance import (STALE_MS, CommonSourceExit, buy_quote_obs, classify_discrepancy,
                                      decimals_suspect, market_obs, pct, sell_quote_obs)

MINT = "GoodMint1111111111111111111111111111111111"
CURVE, AMM = "CurvePair1111", "AmmPool22222"


def buy_quote(sol_in=0.05, tokens_out=1_000_000.0, decimals=6, pool=CURVE, slot=None):
    q = {"inputMint": J.WSOL, "outputMint": MINT, "inAmount": str(int(sol_in * 1e9)),
         "outAmount": str(int(tokens_out * 10 ** decimals)), "priceImpactPct": "0.012",
         "routePlan": [{"swapInfo": {"label": "Pump.fun", "ammKey": pool}}]}
    if slot is not None:
        q["contextSlot"] = slot
    return q


def mk(price, ts, age_s=0.0, pair=CURVE):
    return {"price": price, "ts": ts, "age_ms": 1000 * age_s, "pair": pair}


def qt(price, ts, pair=CURVE):
    return {"price": price, "ts": ts, "pair": pair}


def opened(price="0.0002"):
    """A bot holding one position filled on a (fake) Jupiter quote; the market stamp predates the fill."""
    st = good(MINT)
    st.market.price_usd = float(price)
    b = bot([st], FakeJupiter())
    st.stamps["market"].updated_at = st.market.updated_at = time.time() - 15   # the print the decision saw: 15 s old
    b.tick()
    asyncio.run(b.execute_intents())
    assert MINT in b.book.positions
    return b, st, b.book.positions[MINT]


# ---------------------------------------------------------------- provenance / parsing
def test_buy_quote_parsing_direction_and_amounts():
    o = buy_quote_obs(buy_quote(0.05, 1_000_000), usd=7.5, decimals=6, mint=MINT, t_req=100.0, t_resp=100.25)
    assert o.source == "jupiter_buy_quote" and o.quote_mint == J.WSOL and o.base_mint == MINT
    assert o.price == pytest.approx(7.5 / 1_000_000)            # USD in / tokens out
    assert o.base_amount == pytest.approx(1_000_000) and o.quote_amount == pytest.approx(0.05)
    assert o.latency_ms == pytest.approx(250) and o.pair == CURVE and o.confidence == "HIGH"
    assert o.slot is None                                        # not returned -> never guessed
    assert buy_quote_obs(buy_quote(slot=123), 7.5, 6, MINT, 0, 0).slot == 123


def test_sell_quote_direction_is_token_to_sol():
    q = {"inputMint": MINT, "outputMint": J.WSOL, "outAmount": str(int(0.04 * 1e9)), "routePlan": []}
    o = sell_quote_obs(q, tokens=1_000_000, sol_price=150.0, mint=MINT, t_req=0, t_resp=0.1)
    assert o.price == pytest.approx(0.04 * 150 / 1_000_000)      # SOL out x SOL price / tokens in
    assert o.source == "jupiter_sell_quote" and o.quote_amount == pytest.approx(0.04)


def test_unparseable_quote_stays_unknown():
    o = buy_quote_obs({"routePlan": []}, 7.5, 6, MINT, 0, 0)
    assert o.price is None and o.note == "unparseable quote"
    assert sell_quote_obs({}, 10, 150, MINT, 0, 0).price is None
    assert sell_quote_obs({"outAmount": "1000"}, 10, None, MINT, 0, 0).price is None   # no SOL price -> no guess


def test_decimal_correctness():
    q = buy_quote(0.05, 1_000_000, decimals=6)
    right = buy_quote_obs(q, 7.5, 6, MINT, 0, 0).price
    wrong = buy_quote_obs(q, 7.5, 9, MINT, 0, 0).price            # decimals 9 on a 6-decimal token
    assert wrong == pytest.approx(right * 1000)
    assert decimals_suspect(wrong, right) and not decimals_suspect(right * 1.5, right)
    assert classify_discrepancy(mk(right, 0), qt(wrong, 1), [], [])["class"] == "DECIMALS_SUSPECT"


def test_market_observation_provenance_and_staleness():
    st = good(MINT)
    now = time.time()
    st.market.updated_at = now - 2
    o = market_obs(st, now)
    assert o.source.startswith("dexscreener:") and o.price == st.market.price_usd and o.base_mint == MINT
    assert o.age_ms == pytest.approx(2000, abs=5) and o.confidence == "HIGH" and o.slot is None
    st.market.updated_at = now - 12
    assert market_obs(st, now).confidence == "MEDIUM"
    st.market.price_usd = None
    assert market_obs(st, now).price is None and market_obs(st, now).confidence == "UNKNOWN"


# ---------------------------------------------------------------- discrepancy classes
def test_source_consistency_within_fee_and_impact():
    c = classify_discrepancy(mk(1.0e-5, 0, 1), qt(1.014e-5, 1), [], [])
    assert c["class"] == "CONSISTENT" and c["discrepancy_pct"] == pytest.approx(1.4) and c["discrepancy_bps"] == 140


def test_pair_consistency_curve_vs_amm_is_not_a_price_discrepancy():
    """Migration pair switch: the market print is the curve, the route is the AMM pool -> PAIR_MISMATCH, not alpha."""
    c = classify_discrepancy(mk(1.0e-5, 0, 1, CURVE), qt(1.5e-5, 1, AMM), [], [])
    assert c["class"] == "PAIR_MISMATCH" and "CurvePai" in c["evidence"]


def test_stale_price_detection_needs_a_later_print_to_confirm():
    m, q = mk(1.0e-5, 0, age_s=8), qt(1.5e-5, 1)
    assert classify_discrepancy(m, q, [], [])["class"] == "UNEXPLAINED"              # unproven
    assert classify_discrepancy(m, q, [mk(1.45e-5, 6)], [])["class"] == "STALE_MARKET"
    fresh = mk(1.0e-5, 0, age_s=1)
    assert STALE_MS == 5000 and classify_discrepancy(fresh, q, [mk(1.45e-5, 6)], [])["class"] == "UNEXPLAINED"


# ---------------------------------------------------------------- the 4 forensic cases (recorded V1.1 numbers)
def test_tremor_regression_stale_market_confirmed():
    m = mk(8.434e-06, 0, age_s=18.6)
    c = classify_discrepancy(m, qt(1.271e-05, 1), [mk(1.205e-05, 5)], [])
    assert c["class"] == "STALE_MARKET" and c["discrepancy_pct"] == pytest.approx(50.7, abs=0.1)
    assert pct(1.2998e-05, 1.271e-05) == pytest.approx(2.27, abs=0.01)    # fill vs quote = simulated latency slip only


def test_fwo_regression_stale_market_confirmed():
    c = classify_discrepancy(mk(5.848e-06, 0, age_s=13.2), qt(3.466e-06, 1), [mk(4.283e-06, 4), mk(3.736e-06, 9)],
                             [mk(1.122e-05, -30)])
    assert c["class"] == "STALE_MARKET" and c["discrepancy_pct"] == pytest.approx(-40.7, abs=0.1)


def test_blobtle_regression_stale_probable():
    c = classify_discrepancy(mk(9.009e-06, 0, age_s=20.1), qt(9.672e-06, 1), [], [mk(7.846e-06, -30)])
    assert c["class"] == "STALE_MARKET_PROBABLE" and c["discrepancy_pct"] == pytest.approx(7.4, abs=0.1)


def test_thesis_regression_stays_unexplained():
    """The next print moved AWAY from the quote: staleness is not proven -> UNEXPLAINED, never forced into a class."""
    c = classify_discrepancy(mk(1.013e-05, 0, age_s=17.3), qt(8.33e-06, 1), [mk(1.077e-05, 7)], [mk(9.5e-06, -30)])
    assert c["class"] == "UNEXPLAINED" and c["discrepancy_pct"] == pytest.approx(-17.8, abs=0.1)


# ---------------------------------------------------------------- PRE-ENTRY MARK bug (fixed)
def test_fill_price_and_entry_valuation():
    b, st, p = opened()
    ex = next(e for e in b.book.executions if e.side == "BUY")
    assert p.entry_price == ex.fill_price and "real Jupiter quote" in ex.model
    assert p.last_price == p.entry_price                        # valued at its own fill, not the 15 s old print
    pv = b.provenance[MINT]
    assert pv["entry_fill"]["price"] == ex.fill_price and pv["entry_quote"]["source"] == "jupiter_buy_quote"
    assert pv["quote_market"]["age_ms"] >= 14_000 and "after the fill" in pv["stop_reference_source"]
    assert pv["fill_vs_quote_pct"] == pytest.approx(ex.slippage_pct, abs=0.01)   # fill = quote x (1 + latency slip)


def test_stop_reference_ignores_print_fetched_before_the_fill():
    """BUG (BLOCUS / >_ / Papu): the print the decision saw (fetched before the buy) was compared with the Jupiter
    fill on the next tick -> immediate fake stop loss. Now it is ignored until a print fetched after the fill."""
    b, st, p = opened()
    st.market.price_usd = p.entry_price * 0.70                  # the OLD print is 30 % below the fill
    b.tick()
    assert MINT in b.book.positions and p.stale and p.low_price is None   # no SL, no MAE from a pre-entry print
    st.stamps["market"].updated_at = time.time() + 1            # a print fetched after the fill
    b.tick()
    assert b.book.closed and b.book.closed[0].exit_reason == "stop_loss"


def test_take_profit_ignores_print_fetched_before_the_fill():
    """BUG (bwam): a pre-entry print 128 % above the fill fired TP1 + TP2 at 6 s and logged MFE +128 %."""
    b, st, p = opened()
    st.market.price_usd = p.entry_price * 2.3
    b.tick()
    assert MINT in b.book.positions and not p.tp1_done and p.high_price == p.entry_price
    assert not any(a.kind == "SELL" for a in b.activity)


def test_exit_reference_and_mfe_mae_use_post_entry_marks():
    b, st, p = opened()
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.95
    b.tick()
    assert p.low_price == pytest.approx(p.entry_price * 0.95) and p.last_price == pytest.approx(p.entry_price * 0.95)
    assert b.provenance[MINT]["marks"][-1]["post_entry"] is True
    st.market.price_usd = p.entry_price * 0.80
    st.stamps["market"].updated_at = time.time() + 2
    b.tick()
    pv = b.provenance[MINT]
    assert pv["exit"]["reason"] == "stop_loss" and pv["exit"]["exit_fill_source"] == "liquidity_model_at_dexscreener_mark"
    assert pv["exit"]["exit_market"]["price"] == pytest.approx(p.entry_price * 0.80)
    assert pv["exit"]["path"]["mae_pct"] == pytest.approx(-20.0, abs=0.01)


def test_non_price_hard_exit_is_not_delayed_and_valued_at_fill_without_post_entry_mark():
    from core.models import LiquidityIntel
    b, st, p = opened()
    st.market.price_usd = p.entry_price * 0.5                   # pre-entry print: must not value the exit
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()
    c = b.book.closed[0]
    assert c.exit_reason == "liquidity_collapse"
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.ref_price == pytest.approx(p.entry_price)       # not the 50 % lower stale print


def test_pnl_reconciles_with_cash():
    b, st, p = opened()
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.8
    b.tick()
    c = b.book.closed[0]
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert c.realized_usd == pytest.approx(sell.usd_in - sell.network_fee_usd)
    assert b.provenance[MINT]["exit"]["realized_pnl_pct"] == pytest.approx(100 * (c.realized_usd - c.cost_usd) / c.cost_usd,
                                                                          abs=0.001)


# ---------------------------------------------------------------- common-source shadow exit model
def test_common_source_exit_model_rules_and_pnl():
    cs = CommonSourceExit(1.0, 0, sl_pct=15, tp1_pct=30, tp1_frac=0.5, tp2_pct=80, trailing_pct=20, max_hold_s=3600)
    cs.on_price(1.3, 10)                                         # TP1: half sold at 1.3
    assert cs.tp1_done and cs.closed_at is None
    cs.on_price(1.5, 20)
    cs.on_price(1.19, 30)                                        # trailing 20 % from 1.5 = 1.2
    s = cs.summary()
    assert s["exit_reason"] == "trailing_stop" and s["pnl_pct"] == pytest.approx(100 * (0.5 * 1.3 + 0.5 * 1.19 - 1))
    assert s["mfe_pct"] == pytest.approx(50) and s["mae_pct"] == pytest.approx(0)
    sl = CommonSourceExit(1.0, 0, 15, 30, 0.5, 80, 20, 3600)
    sl.on_price(0.84, 5)
    assert sl.summary()["exit_reason"] == "stop_loss" and sl.summary()["pnl_pct"] == pytest.approx(-16)
    sl.on_price(2.0, 6)                                          # after the close nothing changes (no look-ahead)
    assert sl.summary()["mfe_pct"] == pytest.approx(0) and sl.exit_price == 0.84
    mh = CommonSourceExit(1.0, 0, 15, 30, 0.5, 80, 20, 60)
    mh.on_price(1.05, 61)
    assert mh.summary()["exit_reason"] == "max_hold"


class SellJupiter:
    """Buy quotes like FakeJupiter; sell quotes at a scripted executable price."""
    def __init__(self, sell_price):
        self.sell_price, self.buy = sell_price, FakeJupiter()

    async def quote(self, *a):
        return await self.buy.quote(*a)

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps):
        if input_mint == J.WSOL:
            return J.QuoteResult(J.OK, quote=await self.buy.quote(input_mint, output_mint, amount_raw, slippage_bps),
                                 http=200, attempts=1)
        sol_out = amount_raw / 1e6 * self.sell_price / 150
        return J.QuoteResult(J.OK, quote={"inputMint": input_mint, "outputMint": output_mint, "inAmount": str(amount_raw),
                                          "outAmount": str(int(sol_out * 1e9)), "priceImpactPct": "0.01",
                                          "routePlan": [{"swapInfo": {"label": "Pump.fun", "ammKey": CURVE}}]},
                             http=200, attempts=1)


def test_common_source_round_marks_on_jupiter_sell_quotes():
    b, st, p = opened()
    j = SellJupiter(p.entry_price * 0.8)
    b.jupiter = j
    n = asyncio.run(b.common_source_round(time.time() + 1))
    assert n == 1
    pv = b.provenance[MINT]
    assert pv["jupiter_marks"][-1]["source"] == "jupiter_sell_quote"
    assert pv["common_source"]["exit_reason"] == "stop_loss" and pv["common_source"]["pnl_pct"] == pytest.approx(-20, abs=0.1)
    assert MINT in b.book.positions                              # shadow only: production untouched


# ---------------------------------------------------------------- slippage / no look-ahead
def test_slippage_decomposition_formula():
    """reported total = Jupiter route impact + simulated latency slip; quote-vs-market is NOT inside it."""
    b, st, p = opened()
    ex = next(e for e in b.book.executions if e.side == "BUY")
    pv = b.provenance[MINT]
    assert ex.price_impact_pct == pytest.approx(0.4)             # FakeJupiter priceImpactPct 0.004
    assert pv["fill_vs_quote_pct"] == pytest.approx(ex.slippage_pct, abs=0.01)
    q, m = pv["entry_quote"]["price"], pv["quote_market"]["price"]
    assert pv["discrepancy_quote_vs_market_pct"] == pytest.approx(pct(q, m))
    assert pv["fill_vs_market_pct"] == pytest.approx(pct(ex.fill_price, m))


def test_no_look_ahead_decision_snapshot_is_frozen():
    st = good(MINT)
    b = bot([st], FakeJupiter())
    b.tick()
    it = b.intents[MINT]
    snap = dict(it["decision_market"])
    st.market.price_usd *= 3                                     # a later print
    assert it["decision_market"] == snap and it["decision_market"]["price"] != st.market.price_usd
    asyncio.run(b.execute_intents())
    pv = b.provenance[MINT]
    assert pv["decision_market"]["price"] == snap["price"]
    # later prints validate a discrepancy class but never change the recorded entry
    entry = pv["entry_fill"]["price"]
    classify_discrepancy(pv["quote_market"], pv["entry_quote"], [mk(1.0, time.time() + 9)], [])
    assert pv["entry_fill"]["price"] == entry


def test_provenance_exit_is_recorded_on_the_final_close_after_partial_tp1():
    """Regression (live V1.2 run, SOLCAT): TP1 partial then risk exit -> the provenance exit was never written."""
    b, st, p = opened()
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 1.35                  # TP1 (30 %): half sold
    b.tick()
    asyncio.run(b.execute_intents())
    asyncio.run(b.execute_sells())
    assert p.tp1_done and b.provenance[MINT]["exit"] is None and MINT in b.book.positions
    from core.models import LiquidityIntel
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()
    pv = b.provenance[MINT]
    assert MINT not in b.book.positions and pv["exit"]["reason"] == "liquidity_collapse"
    assert pv["exit"]["realized_pnl_pct"] is not None
