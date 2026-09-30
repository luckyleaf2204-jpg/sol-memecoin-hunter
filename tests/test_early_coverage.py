"""Early Signal requires ≥ 4 of the 7 implemented signal groups to be computable; otherwise UNKNOWN (never 0/high)."""
from conftest import build_series, dex_pair
from intel.early_signal import MIN_COMPUTABLE_GROUPS, MIN_STRENGTH, WEIGHTS


def test_constants():
    assert MIN_COMPUTABLE_GROUPS == 4 and len(WEIGHTS) == 7 and MIN_STRENGTH == 50


def test_beagle_case_single_group_is_unknown():
    """$BEAGLE: only MC acceleration computable (+43% MC) -> previously 66, now UNKNOWN."""
    pts = [(a, dex_pair(mint="B", vol=(0, 0), m5=(0, 0), liq=None, dex="pumpfun", mc=3_350, fdv=3_350,
                        price="0.00000335")) for a in (900, 600, 300)]
    pts.append((0, dex_pair(mint="B", vol=(0, 0), m5=(0, 0), liq=None, dex="pumpfun", mc=4_792, fdv=4_792,
                            price="0.000004792")))
    st, _ = build_series(pts, holders=False)
    e = st.early
    assert e.groups_computable < 4
    assert e.strength is None and e.is_early is None
    assert e.note == "insufficient_coverage"
    assert st.subscores["early_signal"].score is None      # not 0


def test_four_groups_is_scored():
    pts = [(a, dex_pair(mint="F", m5=(50, 50))) for a in (900, 600, 300)]
    pts.append((0, dex_pair(mint="F", m5=(140, 60), vol=(90_000, 120_000))))
    st, _ = build_series(pts, holders=False)
    assert st.early.groups_computable >= 4
    assert st.early.strength is not None


def test_three_groups_unknown_even_if_all_fire():
    """Volume, txns, buy pressure all fire strongly, but liquidity/MC/holders/whale unavailable -> UNKNOWN."""
    base = dict(liq=None, dex="pumpfun")
    pts = [(900, dex_pair(mint="T", vol=(1_000, 40_000), m5=(20, 20), **base)),
           (300, dex_pair(mint="T", vol=(1_000, 40_000), m5=(20, 20), **base)),
           (0, dex_pair(mint="T", vol=(20_000, 60_000), m5=(200, 40), **base))]
    st, _ = build_series(pts, holders=False)
    e = st.early
    assert e.groups_computable == 3
    assert [x.fired for x in e.signals if x.key in ("volume_accel", "txn_accel", "buy_pressure")] == [True, True, True]
    assert e.strength is None and e.is_early is None and e.note == "insufficient_coverage"
