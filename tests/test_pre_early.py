"""PRE-EARLY layer (tokens 1–3 min old). Separate from Early Signal / D1–D8, which must be unaffected."""
import asyncio
import copy
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import SOL_USD, dex_pair, default_info, good_dev, good_holders
from core.config import ApiKeys, Settings
from core.models import RiskFactor, RiskResult, TokenState
from database.db import Database
from dex.dexscreener import parse_pair
from history.store import HolderSnap, TokenHistory
from intel.pre_early import SIGNALS, compute_pre_early
from scanner.engine import ScannerEngine
from scanner.pipeline import evaluate, ingest_market
from validation.identity import apply_identity, record_claim
from web.app import create_app

CODE = "pe-code"
SECRET = "HELIUS-SECRET-pre-early-0000-1111-2222-333333333333"
MINT = "PreEarLy111111111111111111111111111111111"


def _pair(mc, vol, buys, sells, liq, pair="P1"):
    return dex_pair(mint=MINT, mc=mc, fdv=mc, price=f"{mc / 1e9:.12f}", liq=liq, pair=pair,
                    vol=(vol, vol), m5=(buys, sells), h1=(buys, sells), pc5=0.0)


BREAKOUT = [  # (seconds ago, mc, cumulative 5m volume, buys, sells, liquidity) — token created 150 s ago
    (120, 10_000, 2_000, 10, 6, 8_000), (100, 10_500, 2_600, 13, 8, 8_200), (80, 11_000, 3_200, 16, 9, 8_400),
    (60, 11_800, 4_000, 20, 10, 8_700), (40, 14_000, 7_500, 34, 13, 9_800), (20, 16_500, 12_000, 52, 16, 11_000),
    (0, 19_000, 18_000, 75, 20, 12_500)]
FLAT = [(a, 10_000, 2_000 + 100 * i, 10 + i, 8 + i, 8_000) for i, a in enumerate((120, 100, 80, 60, 40, 20, 0))]


def build(series, *, age_s=150, verified=True, now=None):
    now = now or time.time()
    st = TokenState(info=default_info(MINT, age_s=age_s))
    st.holders, st.dev, st.holder_status = good_holders(), good_dev(), "ok"
    if verified:
        record_claim(st.identity, "dexscreener", "TKN", "")
        apply_identity(st)
    h = TokenHistory()
    for ago, mc, vol, b, s_, liq in series:
        ts = now - ago
        ingest_market(st, parse_pair(_pair(mc, vol, b, s_, liq)), {}, SOL_USD, h, ts)
        evaluate(st, Settings(), h, ts, SOL_USD)
    return st, h, now


def sig(pe, key):
    return next(s for s in pe.signals if s.key == key)


# ---------------------------------------------------------------- rules
def test_breakout_is_pre_early_with_reasons():
    st, h, now = build(BREAKOUT)
    assert st.dq_status != "INVALID" and st.risk.score <= 60
    pe = compute_pre_early(st, h, now)
    assert pe.status == "PRE_EARLY" and pe.fired >= 3 and pe.computable >= 3
    assert sig(pe, "buy_pressure").fired is True
    assert sig(pe, "mc_velocity").fired is True and "+90%" in sig(pe, "mc_velocity").value
    assert sig(pe, "volume_accel").fired is True and sig(pe, "txn_accel").fired is True
    assert sig(pe, "holder_growth").fired is None          # no 2 holder snapshots -> UNKNOWN, not a signal
    assert [s.key for s in pe.signals] == list(SIGNALS)


def test_flat_token_is_not_yet():
    st, h, now = build(FLAT)
    pe = compute_pre_early(st, h, now)
    assert pe.status == "NOT_YET" and pe.computable >= 3


def test_unknown_is_never_counted_as_a_signal():
    st, h, now = build(BREAKOUT[-2:])                       # only 20 s of history
    pe = compute_pre_early(st, h, now)
    assert pe.status == "UNKNOWN"
    unknown = [s for s in pe.signals if s.fired is None]
    assert pe.fired == sum(1 for s in pe.signals if s.fired is True)
    assert len(unknown) >= 4 and all(s.note for s in unknown)   # every UNKNOWN says what data is missing


@pytest.mark.parametrize("age_s", [30, 5 * 60])
def test_only_tokens_one_to_three_minutes_old(age_s):
    st, h, now = build(BREAKOUT, age_s=age_s)
    assert compute_pre_early(st, h, now).status == "NOT_ELIGIBLE"
    st.info.created_at = None
    st.market.pair_created_at = None
    assert compute_pre_early(st, h, now).status == "NOT_ELIGIBLE"      # unknown age -> not guessed


def test_unverified_or_conflicting_identity_blocks():
    st, h, now = build(BREAKOUT, verified=False)
    pe = compute_pre_early(st, h, now)
    assert pe.status == "BLOCKED" and "identity_unverified" in pe.blocked_by
    record_claim(st.identity, "pumpportal", "OTHER", "")
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    assert "identity_conflict" in compute_pre_early(st, h, now).blocked_by


def test_high_risk_rug_and_holder_problems_block():
    st, h, now = build(BREAKOUT)
    st.risk = RiskResult(score=65, level="HIGH")
    assert compute_pre_early(st, h, now).blocked_by == ["risk_high"]
    st.risk = RiskResult(score=20, level="LOW", factors=[RiskFactor("liquidity_shock", 25, "rug")])
    assert compute_pre_early(st, h, now).blocked_by == ["rug_flag"]
    st.risk = RiskResult(score=20, level="LOW")
    st.holders.top10_pct = 48.0
    assert compute_pre_early(st, h, now).blocked_by == ["top10"]
    st.holders.top10_pct, st.holder_status = 12.0, "invalid"
    assert "holder_anomaly" in compute_pre_early(st, h, now).blocked_by


def test_falling_mc_is_never_pre_early():
    falling = [(a, mc, v, b, s, l) for (a, _, v, b, s, l), mc in
               zip(BREAKOUT, (19_000, 17_500, 16_000, 15_000, 13_500, 12_500, 11_000))]
    st, h, now = build(falling)
    pe = compute_pre_early(st, h, now)
    assert pe.status != "PRE_EARLY" and sig(pe, "mc_velocity").fired is False


def test_pair_change_is_not_compared_across_pairs():
    st, h, now = build(BREAKOUT[:4])
    for ago, mc, vol, b, s_, liq in BREAKOUT[4:]:           # graduation: new pair, fresh counters
        ingest_market(st, parse_pair(_pair(mc, vol // 10, b // 5, s_ // 5, liq, pair="P2")), {}, SOL_USD, h, now - ago)
    pe = compute_pre_early(st, h, now)
    assert sig(pe, "volume_accel").fired is None and sig(pe, "txn_accel").fired is None   # < 3 points on P2


def test_holder_growth_uses_two_valid_snapshots():
    st, h, now = build(BREAKOUT)
    h.add_holders(HolderSnap(now - 100, 40, {}, True))
    h.add_holders(HolderSnap(now - 5, 70, {}, True))
    assert sig(compute_pre_early(st, h, now), "holder_growth").fired is True


def test_pre_early_never_changes_early_signal_or_scores():
    st, h, now = build(BREAKOUT)
    before = copy.deepcopy((st.early, st.score, st.risk, st.quality, st.lifecycle))
    compute_pre_early(st, h, now)
    assert (st.early, st.score, st.risk, st.quality, st.lifecycle) == before
    assert st.early.strength is None and st.early.is_early is not True   # Early Signal: still UNKNOWN at 2.5 min


# ---------------------------------------------------------------- engine + API
@pytest.fixture
def eng(tmp_path):
    e = ScannerEngine(Settings(), Database(tmp_path / "pe.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    st, h, now = build(BREAKOUT)
    e.tracked[MINT] = st
    e.history._h[MINT] = h
    e._evaluate(st, now)
    e.published = [st]
    return e


def test_engine_sets_pre_early_priority_and_helius_pool(eng):
    from scanner.scheduler import hot_reasons
    st = eng.tracked[MINT]
    assert st.pre_early.is_pre_early
    assert "pre_early" in hot_reasons(st)
    assert MINT in {s.mint for s in eng._deep_pool()}       # holder data gets checked for young breakouts


def test_api_shows_pre_early_separately_with_reasons(eng):
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE)) as c:
        home = c.get("/api/home", headers={"X-Access-Code": CODE})
        view = c.get(f"/api/token/{MINT}", headers={"X-Access-Code": CODE}).json()
    d = home.json()
    assert d["counts"]["pre_early"] == 1 and d["pre_early"][0]["mint"] == MINT
    pe = d["pre_early"][0]["pre_early"]
    assert pe["status"] == "PRE_EARLY" and pe["label"] == "PRE-EARLY" and pe["reasons"]
    assert pe["data"].startswith("dữ liệu đủ") and pe["computable"] >= 3
    sigs = view["pre_early"]["signals"]
    assert len(sigs) == 6 and {s["state"] for s in sigs} <= {"fired", "off", "unknown"}
    assert any(s["state"] == "unknown" for s in sigs)        # missing data shown as UNKNOWN, with its rule
    unk = next(s for s in sigs if s["state"] == "unknown")
    assert unk["value"] == "KHÔNG RÕ" and unk["rule"].startswith("cần")     # Vietnamese reason for missing data
    assert SECRET not in home.text and "api-key" not in home.text


# ---------------------------------------------------------------- live
@pytest.mark.live
def test_live_new_tokens_pre_early_is_safe():
    """Real Pump.fun + DexScreener: every freshly created token gets a valid status, and none is PRE-EARLY
    without a verified identity or with fewer than 3 computable signals."""
    async def go():
        e = ScannerEngine(Settings(), Database(":memory:"), keys=ApiKeys(), on_log=lambda m: None)
        try:
            await e.discover(full=True)
            for _ in range(3):
                await e.refresh_market()
                for st in list(e.tracked.values()):
                    e._evaluate(st)
                await asyncio.sleep(20)
            return list(e.tracked.values())
        finally:
            await e.http.aclose()
    sts = asyncio.run(go())
    assert sts
    for st in sts:
        pe = st.pre_early
        assert pe is not None and pe.status in ("PRE_EARLY", "NOT_YET", "UNKNOWN", "BLOCKED", "NOT_ELIGIBLE")
        if pe.is_pre_early:
            assert st.identity.status == "VERIFIED" and pe.computable >= 3 and pe.fired >= 3
        assert pe.fired == sum(1 for s in pe.signals if s.fired is True)


def test_missing_market_data_is_unknown_not_blocked_and_never_pre_early():
    st, h, now = build(BREAKOUT)
    from core.models import Issue, DataQuality
    st.quality = DataQuality(score=30, status="INVALID", issues=[Issue("critical", "market", "no_market")])
    st.risk = RiskResult(score=70, level="HIGH")               # inflated by missing data only
    pe = compute_pre_early(st, h, now)
    assert pe.status == "UNKNOWN" and pe.blocked_by == []
    st.quality = DataQuality(score=30, status="INVALID", issues=[Issue("critical", "market_cap", "mc_implausible")])
    pe = compute_pre_early(st, h, now)
    assert pe.status == "BLOCKED" and "dq_invalid" in pe.blocked_by and "risk_high" in pe.blocked_by
