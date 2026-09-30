"""PROOF: the Early Signal is based on CHANGE OVER TIME, not on current absolute values."""
from conftest import build_series, build_state, dex_pair

M = "E"


def quiet(ago, vol5=1_000, txns=(20, 20), mc=60_000, liq=40_000):
    return ago, dex_pair(mint=M, vol=(vol5, 40_000), m5=txns, mc=mc, fdv=mc, price=f"{mc/1e9:.10f}", liq=liq, pc5=0.0)


def rising_series():
    """Low activity for 25 min, then volume / txns / buy pressure / MC / liquidity all accelerate."""
    pts = [quiet(a) for a in (1500, 1400, 1300, 1200, 1100, 1000, 900, 800, 700)]
    pts += [quiet(600, 1_100, (22, 20), 61_000, 40_500), quiet(300, 2_000, (30, 25), 66_000, 41_000)]
    pts.append((0, dex_pair(mint=M, vol=(15_000, 50_000), m5=(220, 60), mc=95_000, fdv=95_000,
                            price="0.000095", liq=50_000, pc5=40.0)))
    return pts


def test_rising_from_low_activity_is_early():
    st, _ = build_series(rising_series())
    e = st.early
    assert e.strength is not None and e.transition is True
    assert e.fired_count >= 3 and e.strength >= 50
    assert e.is_early is True
    assert st.lifecycle == "EARLY_MOMENTUM"


def test_high_but_flat_activity_is_not_early():
    """Big absolute numbers that are NOT changing -> no early signal."""
    big = lambda ago: (ago, dex_pair(mint=M, vol=(80_000, 960_000), m5=(400, 380), mc=400_000, fdv=400_000,
                                     price="0.0004", liq=90_000, pc5=0.5))
    st, _ = build_series([big(a) for a in range(1500, -1, -100)])
    assert st.early.strength is not None
    assert st.early.is_early is False
    assert st.early.fired_count == 0
    assert st.early.transition is False


def test_same_current_values_different_history_different_result():
    """Identical CURRENT snapshot, two different pasts -> only the accelerating one is early."""
    current = rising_series()[-1]
    flat_past = [(a, current[1]) for a in (1500, 1300, 1100, 900, 700, 600, 300)]
    rising, _ = build_series(rising_series())
    flat, _ = build_series(flat_past + [current])
    assert rising.market.vol_5m == flat.market.vol_5m
    assert rising.early.is_early is True
    assert flat.early.is_early is False


def test_single_snapshot_is_unknown_not_false():
    st = build_state(dex_pair(mint=M, vol=(90_000, 100_000), m5=(900, 100)))   # huge absolute values
    assert st.early.strength is None and st.early.is_early is None
    assert st.early.note == "needs_history"


def test_short_history_is_unknown():
    st, _ = build_series([quiet(300), quiet(0, 20_000, (200, 50))])
    assert st.early.strength is None


def test_declining_activity_not_early():
    pts = [(a, dex_pair(mint=M, vol=(30_000 - a * 10, 400_000), m5=(200, 200), mc=300_000, fdv=300_000,
                        price="0.0003", liq=80_000)) for a in (1500, 1200, 900, 600, 300)]
    pts.append((0, dex_pair(mint=M, vol=(3_000, 400_000), m5=(30, 60), mc=250_000, fdv=250_000, price="0.00025", liq=70_000)))
    st, _ = build_series(pts)
    assert st.early.is_early is False


def test_not_implemented_signals_excluded():
    st, _ = build_series(rising_series())
    na = [x for x in st.early.signals if x.key in ("smart_money", "social_accel", "narrative_accel")]
    assert na and all(x.fired is None and x.weight == 0 for x in na)
    assert st.early.coverage_pct < 100
