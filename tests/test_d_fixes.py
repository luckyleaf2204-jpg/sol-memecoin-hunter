"""Approved fixes D1–D8 + on-chain queue 15. Each test reproduces the live-audit case that exposed the bug."""
import time

from conftest import SOL_USD, build_series, build_state, default_info, dex_pair, good_dev
from core.config import Settings
from core.models import HolderStats, TokenState
from dex.dexscreener import parse_pair
from history.store import HolderSnap, TokenHistory
from holders.analyzer import compute_stats
from intel.early_signal import compute_early_signal
from intel.events import EventDetector
from scanner.pipeline import evaluate, ingest_market


def sig(st, key):
    return next(x for x in st.early.signals if x.key == key)


def quiet(mint, ago, **kw):
    base = dict(vol=(1_000, 40_000), m5=(20, 20), mc=60_000, fdv=60_000, price="0.00006", liq=40_000, pc5=0.0)
    base.update(kw)
    return ago, dex_pair(mint=mint, **base)


# ---------------------------------------------------------------- D1: volume/txn need buying or non-falling MC
def test_d1_selloff_volume_spike_does_not_fire():
    """$PROPANA: vol x7.95, txns x4.5, but buy share 44% and MC -47%."""
    pts = [quiet("P", a, mc=27_000, fdv=27_000, price="0.000027") for a in (1500, 1300, 1100, 900, 700)]
    pts.append((300, dex_pair(mint="P", vol=(1_012, 40_000), m5=(24, 10), mc=27_489, fdv=27_489, price="0.000027489")))
    pts.append((0, dex_pair(mint="P", vol=(8_046, 40_000), m5=(67, 87), mc=14_465, fdv=14_465, price="0.000014465")))
    st, _ = build_series(pts)
    for key in ("volume_accel", "txn_accel"):
        x = sig(st, key)
        assert x.fired is False and "D1" in x.raw.get("blocked", ""), (key, x.raw)


def test_d1_rising_volume_with_buying_fires():
    pts = [quiet("R", a) for a in (1500, 1300, 1100, 900, 700, 600)]
    pts += [(300, dex_pair(mint="R", vol=(2_000, 40_000), m5=(30, 25), mc=62_000, fdv=62_000, price="0.000062")),
            (0, dex_pair(mint="R", vol=(9_000, 45_000), m5=(160, 60), mc=80_000, fdv=80_000, price="0.00008"))]
    st, _ = build_series(pts)
    assert sig(st, "volume_accel").fired is True


# ---------------------------------------------------------------- D2: buy share, +10 pp
def test_d2_unbounded_ratio_no_longer_fires():
    """$VSOF: 239/4 -> 264/4 (ratio 60 -> 66) is +0.1 pp of buy share: must not fire."""
    pts = [(a, dex_pair(mint="V", vol=(2_000, 30_000), m5=(239, 4))) for a in (900, 600, 300)]
    pts.append((0, dex_pair(mint="V", vol=(2_100, 30_000), m5=(264, 4))))
    st, _ = build_series(pts)
    x = sig(st, "buy_pressure")
    assert x.fired is False and abs(x.raw["delta_pp"]) < 1


def test_d2_real_shift_fires():
    pts = [(a, dex_pair(mint="B", m5=(50, 50))) for a in (900, 600, 300)]
    pts.append((0, dex_pair(mint="B", m5=(140, 60))))                # 50% -> 70% = +20 pp
    st, _ = build_series(pts)
    x = sig(st, "buy_pressure")
    assert x.fired is True and x.raw["delta_pp"] == 20.0


def test_d2_zero_sells_is_defined():
    pts = [(a, dex_pair(mint="Z", m5=(10, 10))) for a in (900, 600, 300)]
    pts.append((0, dex_pair(mint="Z", m5=(30, 0))))
    st, _ = build_series(pts)
    assert sig(st, "buy_pressure").fired is not None


# ---------------------------------------------------------------- D5: pair change = data break
def test_d5_graduation_does_not_fake_acceleration():
    """$O: ~$30K/5m on the curve, graduates, new AMM pair restarts at $1.1K then $42K."""
    pts = [(a, dex_pair(mint="O", pair="CURVE", dex="pumpswap", vol=(30_000, 200_000), m5=(300, 200), liq=9_000))
           for a in (1500, 1300, 1100, 900, 700, 500)]
    pts += [(300, dex_pair(mint="O", pair="AMM", vol=(1_124, 1_124), m5=(30, 18), liq=20_000)),
            (0, dex_pair(mint="O", pair="AMM", vol=(42_419, 43_000), m5=(1339, 686), liq=23_861))]
    st, h = build_series(pts)
    assert len(h.breaks) == 1
    liq = sig(st, "liquidity_growth")
    assert liq.fired is None and "DATA BREAK" in liq.raw["missing"]
    assert st.early.transition is None              # no same-pair baseline
    assert st.early.is_early is not True
    events = EventDetector().detect(st, h, time.time())
    assert "DATA_BREAK" in {e.type for e in events}


def test_d5_volume_compared_only_within_pair():
    # old pair A has a point exactly 5 min ago; new pair B only exists for 1 min -> no same-pair comparison
    pts = [(900, dex_pair(mint="Q", pair="A", vol=(30_000, 200_000))), (300, dex_pair(mint="Q", pair="A", vol=(1_000, 200_000))),
           (60, dex_pair(mint="Q", pair="B", vol=(1_000, 1_000))), (0, dex_pair(mint="Q", pair="B", vol=(20_000, 21_000)))]
    st, _ = build_series(pts)
    x = sig(st, "volume_accel")
    assert x.fired is None and "DATA BREAK" in x.raw["missing"]
    # once the new pair has its own 5-minute history, the comparison is legitimate again
    pts2 = pts[:2] + [(290, dex_pair(mint="Q", pair="B", vol=(1_000, 1_000))), (0, dex_pair(mint="Q", pair="B", vol=(20_000, 21_000)))]
    st2, _ = build_series(pts2)
    assert sig(st2, "volume_accel").raw.get("before_5m") == 1_000


# ---------------------------------------------------------------- D6: invalid holder data
def test_d6_top10_over_100_is_invalid():
    stats = compute_stats({"a": 60e6, "b": 50e6}, 100e6, {})
    assert stats.valid is False and "top10" in stats.invalid_reason


def test_d6_invalid_holders_never_reach_scores():
    st = build_state(dex_pair(), holders=False)
    st.holder_status, st.holder_error = "invalid", "holder count 66 -> 7 (-89%) in 5.0 min"
    evaluate(st, Settings())
    assert st.subscores["holder"].score is None
    assert st.metric("holders").note == "holders_invalid"
    assert any(i.key == "holders_invalid" for i in st.quality.issues)
    assert "INVALID" in sig(st, "holder_accel").raw["missing"] or sig(st, "holder_accel").fired is None


def test_d6_engine_rejects_collapse(tmp_path):
    import asyncio
    from database.db import Database
    from scanner.engine import ScannerEngine
    eng = ScannerEngine(Settings(), Database(tmp_path / "t.db"), on_log=lambda m: None)
    st = TokenState(info=default_info("H", creator=""))
    st.info.creator, st.info.total_supply, st.info.real_sol_reserves = "D", 1e9, 1.0
    h = eng.history.get("H")
    h.add_holders(HolderSnap(time.time() - 60, 66, {f"w{i}": 1e6 for i in range(66)}, True))

    async def fake_analyze(*a, **k):
        return compute_stats({f"w{i}": 1e6 for i in range(7)}, 1e9, {}, holder_count=7, source="helius_das",
                             complete=True)

    async def no_dev(*a, **k):
        return None

    eng.holders.analyze = fake_analyze
    eng.dev.analyze = no_dev
    asyncio.run(eng._deep(st))
    asyncio.run(eng.http.aclose())
    assert st.holders is None and st.holder_status == "invalid" and "66 -> 7" in st.holder_error
    assert len(h.holders) == 1, "invalid snapshot must not enter the history"


# ---------------------------------------------------------------- D7 / D3 / D8
def _holder_series(counts, top10=12.0, organic_dust=False):
    """quiet market; holder snapshots at -10m, -5m, now with the given counts."""
    now = time.time()
    st = TokenState(info=default_info("W", age_s=3 * 3600))
    st.dev = good_dev()
    h = TokenHistory()
    s = Settings()
    for ago in range(1500, 600, -120):
        ingest_market(st, parse_pair(dex_pair(mint="W")), {}, SOL_USD, h, now - ago)
    for i, ago in enumerate((600, 300, 0)):
        n = counts[i]
        owners = {f"w{j}": (top10 / 100 * 1e9 / 10) for j in range(min(10, n))}
        owners |= {f"x{j}": (1.0 if organic_dust and i == 2 else 1e6) for j in range(10, n)}
        h.add_holders(HolderSnap(now - ago, n, owners, True))
        st.holders = HolderStats(holder_count=n, top10_pct=top10, source="helius_das", complete_list=True,
                                 owner_amounts=owners, fetched_at=now - ago)
        ingest_market(st, parse_pair(dex_pair(mint="W")), {}, SOL_USD, h, now - ago)
        evaluate(st, s, h, now - ago, SOL_USD)
    return st


def test_d7_small_holder_counts_not_eligible():
    st = _holder_series((2, 2, 3))                      # $ZYROX: 2 -> 3 holders (+50%)
    x = sig(st, "holder_accel")
    assert x.fired is None and "< 50" in x.raw["missing"]
    assert sig(st, "whale_accum").fired is None
    assert st.subscores["holder"].score is None and st.subscores["holder"].note == "holder_ineligible"


def test_d7_needs_ten_new_holders():
    st = _holder_series((60, 62, 69))                   # +11% but only +7 holders
    x = sig(st, "holder_accel")
    assert x.fired is False and "D7" in x.raw.get("blocked", "")


def test_d3_whale_needs_holder_growth():
    st = _holder_series((100, 100, 100), top10=12.0)
    st2 = _holder_series((100, 100, 100), top10=40.0)   # concentration up, no new holders
    x = sig(st2, "whale_accum")
    assert x.fired is not True


def test_d8_suspicious_holders_block_whale():
    st = _holder_series((100, 120, 200), top10=12.0, organic_dust=True)
    if st.holder_intel.organic == "SUSPICIOUS":
        assert sig(st, "whale_accum").fired is not True
        assert st.subscores["whale"].score is None


# ---------------------------------------------------------------- D4 suppression
def test_d4_suppression_reasons():
    from intel.early_signal import suppression_reasons
    from core.models import RiskFactor, RiskResult
    st = build_state(dex_pair())
    st.holders.top10_pct = 40
    r = RiskResult(score=65, level="HIGH", factors=[RiskFactor("liquidity_shock", 25, "rug")])
    reasons = " ".join(suppression_reasons(st, r))
    assert "liquidity_shock" in reasons and "top10" in reasons and "risk 65" in reasons
    st.holders.top10_pct = 12
    assert suppression_reasons(st, RiskResult(score=10, level="LOW")) == []


# ---------------------------------------------------------------- queue
def test_onchain_queue_is_15():
    assert Settings().deep_per_cycle == 15
