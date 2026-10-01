"""Liquidity baseline matrix (audit 2026-10-01): bonding curve vs AMM, graduation / migration resets the baseline,
real collapses still SHOCK, missing / stale liquidity is UNKNOWN, never 0. Thresholds unchanged (SHOCK = -30 %)."""
import time

from core.models import MarketData, TokenInfo, TokenState
from history.store import TokenHistory
from intel.liquidity import SHOCK_DROP_PCT, analyze_liquidity
from intel.metrics import MetricBuilder
from trading import decision as D
from trading.config import TradingConfig

SOL = 150.0
CURVE, AMM = "CurvePair111", "AmmPair111"


def curve_md(real_sol, pair=CURVE, price=1e-5):
    return MarketData(price_usd=price, market_cap=price * 1e9, liquidity_usd=real_sol * SOL,
                      liquidity_source="pumpfun_curve", dex_id="pumpfun", pair_address=pair)


def amm_md(liq, pair=AMM, price=1e-4):
    return MarketData(price_usd=price, market_cap=price * 1e9, liquidity_usd=liq,
                      liquidity_source="dexscreener_amm", dex_id="pumpswap", pair_address=pair)


def run(series, info=None, step=30):
    """series: list of MarketData, one per `step` seconds; returns LiquidityIntel at the last point."""
    t0 = time.time() - step * len(series)
    h = TokenHistory()
    st = TokenState(info=info or TokenInfo(mint="Liq" + "1" * 41, virtual_sol_reserves=None))
    for i, m in enumerate(series):
        h.add_market(m, t0 + i * step)
    st.market = series[-1]
    return analyze_liquidity(st, h, MetricBuilder(t0 + step * (len(series) - 1), 10), SOL)


def test_A_normal_bonding_curve_no_false_shock():
    # real SOL wobbling 4 -> 2.5 -> 4 SOL: -37 % in raw curve SOL, but the curve's depth (30 SOL virtual) barely moves
    li = run([curve_md(x) for x in (4, 3.5, 2.5, 3, 4, 3.8, 3.2)])
    assert li.state_raw == "SHOCK"                     # what the OLD measure reported
    assert li.state != "SHOCK" and li.basis == "curve_depth"


def test_B_growing_curve_not_rejected():
    li = run([curve_md(x) for x in (1, 3, 6, 10, 15, 22, 30)])
    assert li.state == "GROWING"


def test_C_graduation_resets_baseline():
    # curve with 80 SOL graduates -> AMM pair with $20K: different pair & source -> no comparison across them
    li = run([curve_md(80)] * 4 + [amm_md(20_000)] * 4)
    assert li.state != "SHOCK" and li.basis == "amm"


def test_D_new_amm_pair_is_the_new_baseline():
    li = run([amm_md(50_000, pair="OldPool")] * 4 + [amm_md(20_000, pair="NewPool")] * 7)
    assert li.state == "STABLE"                         # only the new pool's own points


def test_E_migration_then_real_collapse_still_shocks():
    li = run([curve_md(80)] * 3 + [amm_md(30_000)] * 4 + [amm_md(30_000), amm_md(12_000), amm_md(10_000)])
    assert li.state == "SHOCK"


def test_F_same_pair_collapse_hard_protection():
    li = run([amm_md(x) for x in (40_000, 40_000, 39_000, 38_000, 15_000, 14_000, 14_000)])
    assert li.state == "SHOCK" and li.max_drop_5m_pct >= SHOCK_DROP_PCT
    # a real curve dump (depth 30+60 -> 30+10 SOL = -56 %) is still a SHOCK
    li = run([curve_md(x) for x in (60, 60, 58, 20, 10, 10, 10)])
    assert li.state == "SHOCK"


def test_G_missing_liquidity_is_unknown_not_zero():
    info = TokenInfo(mint="Liq" + "1" * 41, quote_mint="USD1ttGY1N17NEEHLmELoaybftRBUSErhqYiQzvEmuB")
    li = run([curve_md(x) for x in (4, 2, 4, 2, 4, 2, 4)], info=info)       # non-SOL quote curve: no depth basis
    assert li.state == "UNKNOWN"
    st = TokenState(info=TokenInfo(mint="Liq" + "1" * 41))
    st.market = MarketData(price_usd=1e-5, market_cap=1e4, liquidity_usd=None)
    v = D.vet(st, TradingConfig())
    assert {c.key: c.result for c in v.checks}["liquidity"] == D.UNKNOWN    # never assumed 0


def test_H_stale_liquidity_is_not_realtime():
    st = TokenState(info=TokenInfo(mint="Liq" + "1" * 41))
    st.market = amm_md(50_000)
    st.market.updated_at = time.time() - 600
    v = D.vet(st, TradingConfig())
    assert {c.key: c.result for c in v.checks}["data_quality"] == D.FAIL       # stale -> not usable for a BUY


def test_curve_uses_reported_virtual_base_when_available():
    info = TokenInfo(mint="Liq" + "1" * 41, virtual_sol_reserves=35.0, real_sol_reserves=5.0)
    li = run([curve_md(x) for x in (5, 4, 3, 5, 4, 3, 5)], info=info)
    assert li.state != "SHOCK"


def test_native_sol_quote_curve_is_measured():
    """Pump.fun reports a classic SOL curve's quote mint as the System Program id (live audit 2026-10-01: 120/129
    curves were wrongly UNKNOWN before this)."""
    for q in ("11111111111111111111111111111111", "So11111111111111111111111111111111111111112", ""):
        info = TokenInfo(mint="Liq" + "1" * 41, quote_mint=q)
        assert run([curve_md(x) for x in (60, 60, 58, 20, 10, 10, 10)], info=info).state == "SHOCK"
        assert run([curve_md(x) for x in (4, 3.5, 2.5, 3, 4, 3.8, 3.2)], info=info).state != "SHOCK"
