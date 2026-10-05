"""Baseline audit — D1 sizing over random inputs: $50 is a MINIMUM (never bought below it), never above the 8 %
position cap, the 1 % risk budget at the stop, 2 % of pool liquidity, cash or exposure room; otherwise SKIP."""
import random

import trading.decision as D
from test_trading import good_state
from trading.config import TradingConfig
from trading.exit_options import stop_pct


def test_sizing_never_breaks_a_limit():
    rng = random.Random(11)
    st = good_state()
    base = TradingConfig()
    sc = D.score(st, D.vet(st, base), base)
    n_trade = n_skip = 0
    for _ in range(4000):
        c = TradingConfig(wide_stop_pct=rng.choice([None, None, 20.0, 25.0]))
        sc.opportunity, sc.confidence = rng.randint(65, 100), rng.randint(60, 100)
        st.market.liquidity_usd = rng.choice([1_000, 2_400, 2_600, 10_000, 80_000])
        st.market.price_change_5m = rng.choice([5.0, 60.0])
        equity = rng.choice([400.0, 620.0, 700.0, 1000.0, 1500.0])
        cash = rng.choice([30.0, 60.0, equity])
        exposure = rng.choice([0.0, equity * 0.2, equity * 0.24])
        sz = D.size(st, sc, c, equity, cash, exposure)
        if sz.usd == 0:
            n_skip += 1
            continue
        n_trade += 1
        assert sz.usd >= c.min_position_usd - 1e-9                                  # minimum eligibility
        assert sz.usd <= equity * c.max_position_pct / 100 + 1e-6                   # 8 % cap
        assert sz.usd * stop_pct(c) / 100 <= equity * c.risk_per_trade_pct / 100 + 1e-6   # 1 % risk at the stop
        assert sz.usd <= st.market.liquidity_usd * c.max_liquidity_pct / 100 + 1e-6
        assert sz.usd <= cash + 1e-6
        assert sz.usd <= equity * c.max_total_exposure_pct / 100 - exposure + 1e-6
    assert n_trade > 100 and n_skip > 100


def test_risk_engine_rejects_a_position_above_8_percent_at_execution():
    from trading.risk import RiskEngine
    r = RiskEngine(TradingConfig()).check_entry(mint="M", usd=81.0, equity=1000, peak=1000, day_start=1000,
                                                open_positions=0, holding=False, in_cooldown=False, exposure=0,
                                                est_impact=0.002, feeds_ok=True)
    assert not r.allowed
