from conftest import build_series, build_state, dex_pair
from core.config import Settings
from scanner.pipeline import evaluate
from scoring.opportunity import WEIGHTS, compute_opportunity

DIMENSIONS = {"momentum", "holder", "liquidity", "dev", "whale", "onchain", "early_signal", "smart_money", "social",
              "narrative", "security"}


def test_all_dimensions_present(good_state):
    assert set(good_state.subscores) == DIMENSIONS
    for k in ("smart_money", "social", "narrative", "security"):
        assert good_state.subscores[k].score is None and good_state.subscores[k].note == "not_implemented"


def test_unknown_factor_is_excluded_not_zero():
    st = build_state(dex_pair(), holders=False)
    assert st.subscores["holder"].score is None
    mom = st.subscores["momentum"]
    vt = next(f for f in mom.factors if f.key == "vol_trend_5m")      # no history yet
    assert vt.available is False and vt.points == 0
    avail_max = sum(f.max_points for f in mom.factors if f.available)
    assert avail_max < sum(f.max_points for f in mom.factors)
    assert mom.score == round(100 * sum(f.points for f in mom.factors if f.available) / avail_max)


def test_opportunity_only_uses_available_weights(good_state):
    sc = good_state.score
    avail = [(k, w, s) for k, w, s in sc.parts if s is not None]
    assert {k for k, _, _ in avail} >= {"momentum"}
    assert "liquidity" not in dict((k, w) for k, w, _ in sc.parts), "liquidity is Risk, not Opportunity"
    assert all(s is None for k, _, s in sc.parts if k in ("smart_money", "social", "narrative"))
    expected = round(sum(w * s for _, w, s in avail) / sum(w for _, w, _ in avail))
    assert sc.total == expected
    assert sc.coverage_pct == round(100 * sum(w for _, w, _ in avail) / sum(WEIGHTS.values()))


def test_contributions_explain_the_total(good_state):
    sc = good_state.score
    assert abs(sum(p for _, p, _, _ in sc.contributions) - sc.total) <= 1.5
    for key, pts, value, source in sc.contributions:
        assert source, key


def test_risk_does_not_change_opportunity():
    a = build_state(dex_pair(mint="A"))
    b = build_state(dex_pair(mint="B"))
    b.dev.current_pct = 40               # much riskier dev, same activity
    evaluate(b, Settings(), None)
    assert b.risk.score > a.risk.score
    assert b.subscores["momentum"].score == a.subscores["momentum"].score


def test_absolute_volume_vs_acceleration():
    """A $10K token accelerating fast can out-rank a $50K token with flat volume on momentum."""
    flat_big = [(ago, dex_pair(mint="BIG", vol=(50_000, 600_000), m5=(300, 290), pc5=0.0)) for ago in (600, 300, 0)]
    fast_small = [(600, dex_pair(mint="FAST", vol=(2_000, 40_000), m5=(30, 30), pc5=0.0)),
                  (300, dex_pair(mint="FAST", vol=(3_000, 40_000), m5=(40, 30), pc5=2.0)),
                  (0, dex_pair(mint="FAST", vol=(10_000, 45_000), m5=(160, 60), pc5=25.0))]
    big, _ = build_series(flat_big)
    fast, _ = build_series(fast_small)
    assert big.market.vol_5m > fast.market.vol_5m
    assert fast.subscores["momentum"].score > big.subscores["momentum"].score


def test_no_opportunity_without_quality():
    st = build_state(dex_pair())
    st.quality = None
    assert compute_opportunity(st, st.subscores) is None


def test_filters_unknown_fails():
    st = build_state(dex_pair(liq=None))
    assert "liq_unknown" in st.filter_fails


def test_filters_pass_good(good_state):
    assert good_state.filter_fails == []


def test_filters_flag_bad(bad_state):
    assert {"liq_below", "bs_below", "top10_above"} <= set(bad_state.filter_fails)


def test_liquidity_and_concentration_do_not_drive_opportunity():
    """Same activity, very different liquidity / concentration -> same Opportunity (they move Risk instead)."""
    deep = build_state(dex_pair(mint="D", liq=150_000))
    thin = build_state(dex_pair(mint="T", liq=12_000))
    thin.holders.top10_pct = 60
    evaluate(thin, Settings(), None)
    assert deep.score.total == thin.score.total
    assert thin.risk.score > deep.risk.score


def test_holder_growth_enters_opportunity_with_history():
    import time
    from history.store import HolderSnap, TokenHistory
    from conftest import SOL_USD
    now = time.time()
    h = TokenHistory()
    owners = lambda n: {f"w{i}": 1_000_000 for i in range(n)}
    h.add_holders(HolderSnap(now - 900, 100, owners(100), True))
    h.add_holders(HolderSnap(now - 600, 105, owners(105), True))
    h.add_holders(HolderSnap(now - 300, 110, owners(110), True))
    h.add_holders(HolderSnap(now, 130, owners(130), True))
    a = build_state(dex_pair(mint="A"))
    b = build_state(dex_pair(mint="B"), history=h)
    assert dict((k, s) for k, _, s in b.score.parts)["holder_growth"] is not None
    assert b.score.coverage_pct > a.score.coverage_pct
