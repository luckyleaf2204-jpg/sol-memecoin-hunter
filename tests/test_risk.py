import time

from conftest import build_state, default_info, dex_pair
from core.config import Settings
from core.models import DevReport, TokenInfo, TokenState
from risk.engine import assess_risk, risk_level
from scanner.pipeline import evaluate


def test_levels():
    assert risk_level(0) == "LOW" and risk_level(30) == "LOW"
    assert risk_level(31) == "MEDIUM" and risk_level(60) == "MEDIUM"
    assert risk_level(61) == "HIGH" and risk_level(80) == "HIGH"
    assert risk_level(81) == "EXTREME"


def test_bad_token_is_extreme(bad_state):
    r = bad_state.risk
    assert r.level == "EXTREME"
    keys = {f.key for f in r.factors}
    for expected in ("top10_high", "single_whale", "few_holders", "dev_concentration", "dev_dump", "serial_launcher",
                     "dev_snipe", "low_liquidity", "mc_liq_high", "volume_anomaly", "sudden_spike", "no_socials"):
        assert expected in keys, expected
    assert all(f.source and f.category for f in r.factors)
    assert r.categories["dev"] > 0 and r.categories["manipulation"] > 0


def test_good_token_low(good_state):
    assert good_state.risk.level == "LOW", good_state.risk.factors


def test_unknown_is_flagged_not_safe():
    st = build_state(dex_pair(), holders=False, dev=False)
    keys = {f.key for f in st.risk.factors}
    assert {"holders_unknown", "dev_unknown"} <= keys


def test_invalid_market_flagged():
    st = build_state(dex_pair(liq=7.1e-07))
    keys = {f.key for f in st.risk.factors}
    assert {"invalid_market", "liquidity_unknown"} <= keys


def test_new_token_flag():
    st = build_state(dex_pair(mint="N"), info=default_info("N", age_s=120))
    assert any(f.key == "new_token" for f in st.risk.factors)


def test_unverified_dev_is_unknown_not_zero():
    from alerts.report import dev_label
    st = TokenState(info=TokenInfo(mint="x", created_at=time.time()), dev=DevReport(creator="D", balance_verified=False))
    assert dev_label(st).endswith("UNKNOWN")
    evaluate(st, Settings())
    keys = {f.key for f in st.risk.factors}
    assert "dev_unknown" in keys and "dev_concentration" not in keys
    assert st.subscores["dev"].score is None


def test_score_capped(bad_state):
    assert assess_risk(bad_state).score <= 100
