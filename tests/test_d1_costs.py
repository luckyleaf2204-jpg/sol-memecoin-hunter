"""D1 — the losses are mostly fixed costs on tiny trades and curve fees: a $50 floor (inside the caps AND the 1 %
risk budget) and no entry on the Pump.fun bonding curve."""
import pytest

import trading.decision as D
from test_bot_v2 import good
from test_step2_chasing import gate
from test_trading import good_state
from trading.config import TradingConfig
from trading.execution import PaperExecutor
from trading.lifecycle_decision import TRADE, WATCH


def _sc(st, c):
    sc = D.score(st, D.vet(st, c), c)
    sc.confidence, sc.opportunity = 60, 66                    # a weak candidate: old sizing gave ~$20
    return sc


def test_small_size_is_raised_to_the_floor():
    c = TradingConfig()
    st = good_state()
    st.market.liquidity_usd = 1_000_000
    sz = D.size(st, _sc(st, c), c, 1000, 1000, 0)
    assert sz.usd == 50.0 and "raised to the minimum" in sz.reasons[-1]
    old = D.size(st, _sc(st, c), TradingConfig(min_position_usd=10, max_position_pct=5), 1000, 1000, 0)
    assert old.usd < 25                                        # what the bot used to buy


def test_floor_never_breaks_the_risk_budget_or_a_cap():
    st = good_state()
    st.market.liquidity_usd = 1_000_000
    wide = TradingConfig(wide_stop_pct=25.0)                   # $10 risk / 25 % = $40 < $50
    assert D.size(st, _sc(st, wide), wide, 1000, 1000, 0).usd == 0.0
    c = TradingConfig()
    assert D.size(st, _sc(st, c), c, 500, 500, 0).usd == 0.0   # 8 % of $500 = $40 < $50: no trade


def test_curve_tokens_are_not_entered():
    d, rec = gate({"entry_location": "PULLBACK", "entry_extension": 5.0})
    assert d == TRADE                                          # the gate's default token is not on the curve
    from test_step2_chasing import bot, post_tok
    b = bot([post_tok()])
    st = b.engine.published[0]
    st.market.dex_id = "pumpfun"                               # on the bonding curve
    rec = {"entry_location": "PULLBACK", "entry_extension": 5.0,
           "entry_location_detail": {"history_s": 400.0, "last_low_age_s": 300.0}}
    assert b._entry_location_gate(st, rec, TRADE, 0.0) == WATCH and "entry_location: ON_CURVE" in rec["blocked_by"]
    b.cfg.entry_allow_curve = True
    rec.pop("blocked_by"); rec.pop("entry_gate_blocked")
    assert b._entry_location_gate(st, rec, TRADE, 0.0) == TRADE


@pytest.mark.parametrize("usd, max_cost", [(50, 8.0)])
def test_round_trip_cost_at_the_new_floor_off_curve(usd, max_cost):
    import statistics
    costs = []
    for seed in range(300):
        ex = PaperExecutor(seed=seed)
        ex.priority_fee_sol = TradingConfig().priority_fee_sol
        st = good()
        st.market.liquidity_usd, st.market.price_change_5m, st.market.dex_id = 20_000, 5, "pumpswap"
        b = ex.buy(st, usd, 150.0)
        if b.status != "FILLED":
            continue
        s = ex.sell(st, b.tokens, st.market.price_usd, 150.0, "x", force=True)
        if s.status == "FILLED":
            costs.append(100 * (usd + b.network_fee_usd + s.network_fee_usd - s.usd_in) / usd)
    assert statistics.mean(costs) < max_cost                   # was 12-17 % at $15-25 / 14-22 % on the curve
