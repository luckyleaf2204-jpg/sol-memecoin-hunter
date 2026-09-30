"""PROOF: malformed / missing market data never gets an Opportunity score and never reaches Top Opportunities
(nor alerts, nor EARLY SIGNAL = TRUE)."""
import time

import pytest

from conftest import SOL_USD, build_series, build_state, default_info, dex_pair
from core.config import Settings
from core.models import INVALID, PARTIAL, VALID, MarketData, SourceStamp, TokenInfo, TokenState
from history.store import TokenHistory
from scanner.pipeline import evaluate
from scoring.ranking import is_rankable, rank_early, rank_opportunities
from validation.market import validate_market

# values actually observed in the dashboard / database, plus systematic malformations
MALFORMED_PAIRS = {
    "liquidity 0.00000071 (observed)": dex_pair(liq=7.1e-07),
    "liquidity 0.000000024 (observed)": dex_pair(liq=2.4e-08),
    "liquidity 0": dex_pair(liq=0),
    "liquidity negative": dex_pair(liq=-5),
    "liquidity field missing on AMM": dex_pair(liq=None),
    "price missing": dex_pair(drop=("priceUsd",)),
    "price zero": dex_pair(price="0"),
    "price NaN": dex_pair(price="NaN"),
    "price garbage string": dex_pair(price="abc"),
    "market cap 265.91 (observed)": dex_pair(mc=265.91, fdv=265.91, price="0.00000026591"),
    "market cap missing": dex_pair(drop=("marketCap",)),
    "MC inconsistent with price x supply": dex_pair(mc=200_000, price="0.02"),
    "volume 5m zero": dex_pair(vol=(0, 120_000)),
    "volume missing": dex_pair(drop=("volume",)),
    "txns missing": dex_pair(drop=("txns",)),
    "volume with zero txns": dex_pair(m5=(0, 0)),
    "pair address missing": dex_pair(pair=""),
}


@pytest.mark.parametrize("case", list(MALFORMED_PAIRS))
def test_malformed_data_is_invalid_and_never_ranked(case):
    st = build_state(MALFORMED_PAIRS[case])
    assert st.quality.status == INVALID, case
    assert st.score is None, f"{case}: INVALID data must not get an Opportunity Score"
    assert not is_rankable(st)
    good = build_state(dex_pair(mint="GOOD"))
    assert rank_opportunities([st, good]) == [good]


@pytest.mark.parametrize("case", list(MALFORMED_PAIRS))
def test_malformed_values_become_null_never_zero(case):
    st = build_state(MALFORMED_PAIRS[case])
    for fld in ("liquidity_usd", "price_usd", "market_cap", "vol_5m", "vol_1h"):
        v = getattr(st.market, fld)
        assert v is None or v > 0, f"{case}: {fld}={v!r} must be None or valid"
    for m in st.metrics:
        if m.value is None:
            assert m.confidence is None, f"{m.key}: UNKNOWN must not carry a confidence"


def test_low_risk_cannot_rescue_invalid_data():
    st = build_state(dex_pair(liq=7.1e-07))            # perfect holders + dev, broken liquidity
    assert st.quality.status == INVALID and st.score is None
    assert rank_opportunities([st]) == []


def test_invalid_data_never_early_signal_true():
    """Even with a textbook early-momentum history, INVALID current data -> is_early False, not ranked."""
    base = dex_pair(mint="E", vol=(1_000, 40_000), m5=(20, 20), mc=60_000, fdv=60_000, price="0.00006")
    pairs = [(ago, base) for ago in (1500, 1300, 1100, 900, 700)]
    pairs.append((300, dex_pair(mint="E", vol=(2_000, 40_000), m5=(30, 25), mc=62_000, fdv=62_000, price="0.000062")))
    pairs.append((0, dex_pair(mint="E", vol=(15_000, 50_000), m5=(200, 60), mc=90_000, fdv=90_000, price="0.00009",
                              liq=7.1e-07)))
    st, _ = build_series(pairs)
    assert st.quality.status == INVALID
    assert st.early.is_early is not True
    assert rank_early([st]) == [] and rank_opportunities([st]) == []


def test_top_opportunities_only_valid():
    valid = build_state(dex_pair(mint="V"))
    partial = build_state(dex_pair(mint="P"), holders=False, dev=False)
    invalid = build_state(dex_pair(mint="I", liq=2.4e-08))
    assert (valid.dq_status, partial.dq_status, invalid.dq_status) == (VALID, PARTIAL, INVALID)
    assert partial.score is not None
    assert rank_opportunities([invalid, partial, valid]) == [valid]


def test_history_stores_validated_values_only():
    h = TokenHistory()
    build_state(dex_pair(liq=7.1e-07), history=h)
    assert h.latest().liq is None, "malformed liquidity must not enter the time series"


# ---- bonding-curve reserve (root cause of the $0.00000071 liquidity) ----
def _curve_info(real, virt, fetched_ago=5):
    return default_info("C", complete=False, real_sol_reserves=real, virtual_sol_reserves=virt,
                        pump_updated_at=time.time() - fetched_ago)


def _curve_pair():
    return dex_pair(mint="C", dex="pumpfun", liq=None, mc=29_000, fdv=29_000, price="0.000029")


def test_mayhem_curve_reserves_rejected():
    st = build_state(_curve_pair(), info=_curve_info(real=3e-09, virt=256.783848295))
    assert st.market.liquidity_usd is None
    assert st.quality.status == INVALID and st.score is None
    assert any(i.key == "curve_inconsistent" for i in st.quality.issues)


def test_standard_curve_reserve_accepted():
    st = build_state(_curve_pair(), info=_curve_info(real=25.0, virt=55.0))
    assert st.market.liquidity_source == "pumpfun_curve"
    assert st.market.liquidity_usd == pytest.approx(25.0 * SOL_USD)


def test_stale_curve_reserve_rejected():
    st = build_state(_curve_pair(), info=_curve_info(real=25.0, virt=55.0, fetched_ago=600))
    assert st.market.liquidity_usd is None and st.quality.status == INVALID


def test_missing_sol_price_rejects_curve_liquidity():
    m = MarketData(price_usd=0.000029, market_cap=29_000, dex_id="pumpfun", pair_address="P", vol_5m=1000,
                   vol_1h=5000, buys_5m=10, sells_5m=5, buys_1h=50, sells_1h=40)
    issues = validate_market(m, _curve_info(real=25.0, virt=55.0), sol_price=None)
    assert m.liquidity_usd is None and any(i.field == "liquidity" for i in issues)


def test_stale_market_data_becomes_invalid(good_state):
    good_state.stamps["market"] = SourceStamp("DexScreener", time.time() - 600)
    evaluate(good_state, Settings())
    assert good_state.quality.status == INVALID and good_state.score is None


def test_no_market_data_is_invalid():
    st = TokenState(info=TokenInfo(mint="x", twitter="t"))
    evaluate(st, Settings())
    assert st.quality.status == INVALID and st.score is None


def test_every_metric_has_provenance(good_state):
    for m in good_state.metrics:
        if m.value is not None:
            assert m.source, f"{m.key} has no source"
            assert m.ts is not None, f"{m.key} has no timestamp"
            assert 0 <= m.confidence <= 1, f"{m.key} confidence {m.confidence}"
