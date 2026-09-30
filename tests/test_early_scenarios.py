"""Controlled scenarios requested for the Early Signal audit (S3 suppression = approved fix D4)."""
from scenarios import SCENARIOS, run

S = {sc.name: sc for sc in SCENARIOS}


def test_s1_strong_multi_signal_is_early():
    st = run(S["S1"])
    assert st.early.strength >= 70
    assert st.early.is_early is True
    assert st.early.fired_count >= 5


def test_s2_price_only_is_not_high():
    st = run(S["S2"])
    assert st.early.strength < 50
    assert st.early.is_early is False
    fired = {x.key for x in st.early.signals if x.fired}
    assert fired <= {"mc_accel"}, fired        # price move alone can light only the MC signal


def test_s4_healthy_expansion_triggers():
    st = run(S["S4"])
    assert st.early.is_early is True
    assert st.early.strength >= 60
    assert st.risk.level == "LOW"


def test_s3_risk_increases():
    s3, s4 = run(S["S3"]), run(S["S4"])
    keys = {f.key for f in s3.risk.factors}
    assert "liquidity_shock" in keys and "top10_high" in keys
    assert s3.risk.score > s4.risk.score


def test_s3_early_is_suppressed_by_risk():
    st = run(S["S3"])
    assert st.early.is_early is False
    reasons = " ".join(st.early.suppressed)
    assert "liquidity_shock" in reasons and "top10" in reasons and "risk" in reasons
    whale = next(x for x in st.early.signals if x.key == "whale_accum")
    assert not whale.fired, "concentration rising without holder growth must not count as whale accumulation (D3)"


def test_smart_money_cannot_be_represented():
    st = run(S["S1"])
    sm = next(x for x in st.early.signals if x.key == "smart_money")
    assert sm.fired is None and sm.weight == 0
