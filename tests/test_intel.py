"""Liquidity / holder / whale intelligence, lifecycle and event detection."""
import time

from conftest import build_series, build_state, dex_pair, good_holders
from core.config import Settings
from core.models import HolderStats, TokenState
from history.store import HolderSnap, TokenHistory
from intel.events import EventDetector
from intel.holders import analyze_holders
from intel.liquidity import classify_state, impact_pct
from intel.metrics import MetricBuilder
from intel.whales import analyze_whales
from scanner.pipeline import evaluate


# ---------------------------------------------------------------- liquidity
def test_liquidity_states():
    t0 = 1000.0
    assert classify_state([(t0, 100), (t0 + 60, 101)])[0] == "UNKNOWN"          # < 3 min span
    assert classify_state([(t0, 100), (t0 + 300, 101), (t0 + 600, 102)])[0] == "STABLE"
    assert classify_state([(t0, 100), (t0 + 300, 104), (t0 + 600, 110)])[0] == "GROWING"
    assert classify_state([(t0, 100), (t0 + 300, 97), (t0 + 600, 92)])[0] == "FALLING"
    assert classify_state([(t0, 100), (t0 + 200, 60), (t0 + 600, 62)])[0] == "SHOCK"


def test_slippage_constant_product():
    assert impact_pct(1_000, 20_000) == 100 * 1_000 / 21_000
    assert impact_pct(1_000, None) is None


def test_liquidity_shock_is_risk_and_event():
    pts = [(600, dex_pair(liq=80_000)), (300, dex_pair(liq=80_000)), (240, dex_pair(liq=79_000)),
           (180, dex_pair(liq=35_000))]
    st, h = build_series(pts)
    assert st.liquidity_intel.state == "SHOCK"
    assert any(f.key == "liquidity_shock" and f.category == "rug" for f in st.risk.factors)
    events = EventDetector().detect(st, h, time.time() - 180)
    assert {"LIQUIDITY_REMOVE", "RUG_WARNING"} <= {e.type for e in events}


# ---------------------------------------------------------------- holders / whales
def _snap(ts, owners: dict, complete=True):
    return HolderSnap(ts, len(owners), owners, complete)


def _state_with_holders(amounts):
    st = build_state(dex_pair())
    st.holders = HolderStats(holder_count=len(amounts), top10_pct=10, source="helius_das", complete_list=True,
                             owner_amounts=amounts, fetched_at=time.time())
    return st


def test_holder_growth_churn_retention_organic():
    now = time.time()
    first = {f"w{i}": 1_000_000 for i in range(100)}
    later = {f"w{i}": 1_000_000 for i in range(90)} | {f"n{i}": 500_000 for i in range(40)}
    h = TokenHistory()
    h.add_holders(_snap(now - 600, first))
    h.add_holders(_snap(now - 300, first))
    h.add_holders(_snap(now, later))
    st = _state_with_holders(later)
    hi = analyze_holders(st, h, MetricBuilder(now, 20))
    assert hi.growth_5m_pct == 30.0 and hi.prev_growth_5m_pct == 0.0 and hi.accel == 30.0
    assert hi.new_holders == 40 and hi.lost_holders == 10 and hi.churn_pct == 10.0
    assert hi.early_retention_pct == 90.0
    assert hi.organic == "ORGANIC"


def test_dust_airdrop_is_suspicious():
    now = time.time()
    before = {f"w{i}": 1_000_000 for i in range(50)}
    after = before | {f"dust{i}": 1.0 for i in range(60)}           # 60 wallets with 1 token each
    h = TokenHistory()
    h.add_holders(_snap(now - 300, before))
    h.add_holders(_snap(now, after))
    st = _state_with_holders(after)
    st.market.buys_5m = 100
    hi = analyze_holders(st, h, MetricBuilder(now, 20))
    assert hi.organic == "SUSPICIOUS" and "dust_new" in hi.flags


def test_holder_growth_needs_two_snapshots():
    st = build_state(dex_pair())
    hi = analyze_holders(st, TokenHistory(), MetricBuilder(time.time(), 20))
    assert hi.growth_5m_pct is None and hi.organic == "UNKNOWN"


def test_whale_accumulation_and_distribution():
    now = time.time()
    h = TokenHistory()
    h.add_holders(_snap(now - 300, {"A": 20_000_000, "B": 5_000_000}))
    h.add_holders(_snap(now, {"A": 40_000_000, "B": 5_000_000, "C": 15_000_000}))
    st = build_state(dex_pair())
    st.holders = good_holders()
    wi = analyze_whales(st, h, MetricBuilder(now, 20))
    assert wi.state == "ACCUMULATION" and wi.delta_pct == 3.5 and wi.entries == ["C"]
    h2 = TokenHistory()
    h2.add_holders(_snap(now - 300, {"A": 40_000_000}))
    h2.add_holders(_snap(now, {"A": 5_000_000}))
    wi2 = analyze_whales(st, h2, MetricBuilder(now, 20))
    assert wi2.state == "DISTRIBUTION" and wi2.exits == ["A"]


# ---------------------------------------------------------------- lifecycle
def test_lifecycle_new_and_unknown():
    from conftest import default_info
    assert build_state(dex_pair(), info=default_info(age_s=120)).lifecycle == "NEW"
    st = TokenState(info=default_info(created_at=None))
    evaluate(st, Settings())
    assert st.lifecycle == "UNKNOWN"


def test_lifecycle_distribution():
    st = build_state(dex_pair(m5=(20, 60), vol=(10_000, 150_000)))
    st.market.price_change_1h = 2.0
    evaluate(st, Settings(), None)
    assert st.lifecycle == "DISTRIBUTION"


# ---------------------------------------------------------------- events
def test_volume_spike_event_and_dedup():
    pts = [(300, dex_pair(vol=(2_000, 40_000))), (0, dex_pair(vol=(9_000, 45_000)))]
    st, h = build_series(pts)
    det = EventDetector()
    now = time.time()
    assert "VOLUME_SPIKE" in {e.type for e in det.detect(st, h, now)}
    assert det.detect(st, h, now + 30) == []                    # cooldown: no duplicate


def test_no_event_from_single_snapshot():
    st = build_state(dex_pair(vol=(90_000, 100_000)))
    h = TokenHistory()
    assert EventDetector().detect(st, h, time.time()) == []


def test_dev_sell_event():
    st = build_state(dex_pair())
    h = TokenHistory()
    now = time.time()
    h.add_dev_balance(now - 300, 10_000_000)
    h.add_dev_balance(now, 4_000_000)
    assert "DEV_SELL" in {e.type for e in EventDetector().detect(st, h, now)}


def test_curve_reserve_drop_is_not_lp_removal():
    """A bonding curve has no LP to remove: reserve drops are a sell-off flag, never LIQUIDITY_REMOVE / rug-by-LP."""
    from conftest import default_info
    info = default_info("C", complete=False, real_sol_reserves=40.0, virtual_sol_reserves=70.0,
                        pump_updated_at=time.time())
    st, h = build_series([(600, dex_pair(mint="C", dex="pumpfun", liq=None)),
                          (300, dex_pair(mint="C", dex="pumpfun", liq=None))], info=info)
    st.info.real_sol_reserves, st.info.virtual_sol_reserves, st.info.pump_updated_at = 15.0, 45.0, time.time()
    from conftest import SOL_USD
    from dex.dexscreener import parse_pair
    from scanner.pipeline import ingest_market
    now = time.time() - 240
    ingest_market(st, parse_pair(dex_pair(mint="C", dex="pumpfun", liq=None)), {}, SOL_USD, h, now)
    evaluate(st, Settings(), h, now, SOL_USD)
    assert st.liquidity_intel.state == "SHOCK"
    keys = {f.key for f in st.risk.factors}
    assert "curve_drain" in keys and "liquidity_shock" not in keys
    types = {e.type for e in EventDetector().detect(st, h, now)}
    assert "LIQUIDITY_REMOVE" not in types
