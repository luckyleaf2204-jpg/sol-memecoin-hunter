"""EXPERIMENTAL engine (spec Part 3): soft EarlyScore with age thresholds, holder rule, prior risk, hard gates kept,
candidate before quote, simulated fill when the quote fails, A/B against the OLD engine (which is unchanged)."""
import asyncio
import time

import pytest

from core.models import HolderIntel, LiquidityIntel, RiskFactor, RiskResult
from research.dataset import DatasetRecorder
from test_bot_v2 import bot, good
from test_jupiter_exec import ScriptedJupiter
from trading import decision as D
from trading import experimental as X
from trading import jupiter as J
from trading.config import TradingConfig
from validation.identity import apply_identity, record_claim

CFG = TradingConfig(experimental=True)


def hot(age_s=60, holders=None, now=None, **mk):
    """A strong young token: +45 % / 5m, 45 % turnover, 75 % buys, healthy liquidity, verified on-chain gates."""
    now = now or time.time()
    st = good()
    st.info.created_at = now - age_s
    m = st.market
    m.price_change_5m, m.vol_5m, m.market_cap, m.liquidity_usd = 45.0, 90_000.0, 200_000.0, 40_000.0
    m.buys_5m, m.sells_5m, m.updated_at = 450, 150, now
    for k, v in mk.items():
        setattr(m, k, v)
    st.lifecycle = "EARLY_MOMENTUM"
    if holders is None:
        st.holder_status, st.holders = "", None
    else:
        st.holders.holder_count = holders
        st.holders.top10_pct = 25.0
        st.holder_intel = HolderIntel(new_per_min=6.0)
    return st


def ev(st, now=None, cfg=CFG):
    now = now or time.time()
    v = D.vet(st, cfg, now)
    sc = D.score(st, v, cfg)
    return X.evaluate(st, v, sc, cfg, now), sc


# ---------------------------------------------------------------- age thresholds
@pytest.mark.parametrize("age,bucket,theta,gamma", [(45, "<90s", .55, .40), (150, "90s-5m", .65, .55),
                                                   (900, ">5m", .75, .70)])
def test_age_buckets_and_thresholds(age, bucket, theta, gamma):
    es = X.early_score(hot(age_s=age, holders=120), cfg=CFG)
    assert es.bucket == bucket and (es.theta, es.gamma) == (theta, gamma)


def test_young_strong_token_passes_without_holder_data():
    xd, sc = ev(hot(age_s=60))
    es = xd.es
    assert es.components["S_holder"] == 0.0 and "holders_null" in es.penalties      # NULL -> 0 + conf -0.25
    assert es.passed and es.score >= .55 and es.confidence >= .40
    assert xd.decision == D.TRADE, xd.blocked_by                                     # no hard fail, no 7/7 needed


def test_missing_holders_lowers_confidence_never_rejects():
    with_h = X.early_score(hot(age_s=150, holders=120), cfg=CFG)
    without = X.early_score(hot(age_s=150), cfg=CFG)
    assert without.confidence < with_h.confidence
    xd, _ = ev(hot(age_s=900))
    assert xd.decision != D.REJECT


@pytest.mark.parametrize("n,expect", [(8, 0.1), (25, None), (49, None), (300, None)])
def test_holder_rule_bands_and_no_hard_reject_below_50(n, expect):
    st = hot(age_s=150, holders=n)
    comp, null = X.holder_component(st)
    assert not null
    if expect is not None:
        assert comp == expect
    else:
        assert 0 < comp <= 1
    xd, _ = ev(st)
    assert xd.decision != D.REJECT and not any("holder" in b for b in xd.rejected)


def test_whale_component_unused_below_30_holders():
    es = X.early_score(hot(age_s=150, holders=20), cfg=CFG)
    assert es.weights["S_whale"] == 0 and "S_whale" not in es.missing


def test_weak_momentum_is_watch_at_every_age():
    for age in (60, 150, 900):
        st = hot(age_s=age, holders=120, price_change_5m=2.0, vol_5m=3_000.0, buys_5m=100, sells_5m=100)
        xd, _ = ev(st)
        assert xd.decision == D.WATCH and any(b.startswith("early_score_low") for b in xd.blocked_by)


def test_older_tokens_need_the_stricter_threshold():
    st = hot(age_s=900, holders=120, price_change_5m=20.0)
    xd, _ = ev(st)
    assert xd.es.bucket == ">5m" and (xd.decision == D.TRADE) == (xd.es.score >= .75 and xd.es.confidence >= .70)


# ---------------------------------------------------------------- hard gates kept
def test_identity_conflict_rejects_pending_never_buys():
    st = hot()
    record_claim(st.identity, "pumpportal", "OTHER", "")
    apply_identity(st)
    assert ev(st)[0].decision == D.REJECT and "identity_conflict" in ev(st)[0].rejected
    st = hot()
    st.identity.claims.clear()
    apply_identity(st)
    assert ev(st)[0].decision == D.PENDING_IDENTITY


def test_risk_over_60_and_rug_and_shock_reject():
    st = hot()
    st.risk = RiskResult(70, "HIGH")
    assert "risk_gt_60" in ev(st)[0].rejected
    st = hot()
    st.risk = RiskResult(20, "LOW", [RiskFactor("dev_dump", 20, "rug")])
    assert ev(st)[0].decision == D.REJECT
    st = hot()
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    assert "liquidity_shock" in ev(st)[0].rejected


def test_dangerous_authority_and_token2022_reject():
    st = hot()
    st.identity.mint_authority = "SomeAuthority111"
    assert "authorities" in ev(st)[0].rejected
    st = hot()
    st.identity.extensions = ["permanent_delegate"]
    st.identity.token_program = D.T22
    xd, _ = ev(st)
    assert xd.decision == D.REJECT and "token_2022" in xd.rejected


def test_unchecked_authority_is_not_a_pass():
    st = hot()
    st.identity.helius_checked = False
    xd, _ = ev(st)
    assert xd.decision == D.WATCH and "gate_unknown:authorities" in xd.blocked_by


def test_top10_extreme_rejects_only_after_3_minutes():
    young, old = hot(age_s=100, holders=60), hot(age_s=400, holders=60)
    young.holders.top10_pct = old.holders.top10_pct = 95.0
    assert "top10_extreme" not in ev(young)[0].rejected
    assert "top10_extreme" in ev(old)[0].rejected


def test_dev_dump_rejects():
    st = hot()
    st.dev.balance_verified, st.dev.status = True, "SOLD ALL"
    assert "dev" in ev(st)[0].rejected


def test_dangerous_liquidity_rejects():
    st = hot(liquidity_usd=2_000.0)
    assert "liquidity" in ev(st)[0].rejected


def test_opportunity_and_confidence_floors_kept():
    xd, sc = ev(hot())
    assert sc.opportunity >= 65 and sc.confidence >= 60
    st = hot()
    st.score = None
    xd2, sc2 = ev(st)
    if sc2.opportunity is None or sc2.opportunity < 65:
        assert xd2.decision != D.TRADE


# ---------------------------------------------------------------- prior risk
def test_prior_risk_on_brand_new_token():
    st = hot(age_s=30, liquidity_usd=5_000.0)
    st.risk = RiskResult(0, "LOW")
    es = X.early_score(st, cfg=CFG)
    assert es.observed_risk == 0 and es.prior_risk >= 40 and es.final_risk == es.prior_risk
    assert X.prior_risk(hot(age_s=900, holders=200), 900)[0] == 0


# ---------------------------------------------------------------- D1-D8 untouched, A/B
def test_old_engine_and_d1_d8_unchanged_in_ab():
    st = hot(age_s=60)
    early_before = st.early
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.tick()
    rec = b.decisions[st.mint]
    v = D.vet(st, b.cfg)
    assert rec["old_decision"] == D.score(st, v, b.cfg).decision and rec["engine"] == "experimental"
    assert st.early is early_before                                                   # Early Signal (D1-D8) as computed
    assert rec["early_score"]["bucket"] == "<90s" and "blocked_by_old" in rec


def test_old_engine_still_the_default():
    assert TradingConfig().experimental is False
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.tick()
    assert "engine" not in b.decisions[st.mint] and b.decisions[st.mint]["early_score"]   # logged for A/B only


# ---------------------------------------------------------------- execution
def buy_route(b):
    return [e for e in b.book.executions if e.side == "BUY"][-1].route


def run_exp(st, jup, tmp_path=None):
    b = bot([st], jup)
    b.cfg.experimental = True
    if tmp_path is not None:
        b.recorder = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    return b, t0


def test_candidate_quote_ok_paper_buy(tmp_path):
    st = hot(age_s=60)
    b, _ = run_exp(st, ScriptedJupiter([J.OK]), tmp_path)
    assert st.mint in b.book.positions and "SIMULATED" not in buy_route(b)
    row = b.recorder.db.execute("SELECT engine, new_candidate, old_decision, new_decision, early_score, age_bucket, "
                                "bought, simulated_fill FROM candidates").fetchone()
    assert row[0] == "experimental" and row[1] == 1 and row[3] == "TRADE" and row[4] >= .55 and row[5] == "<90s"
    assert row[6] == 1 and row[7] == 0


def test_no_route_keeps_candidate_and_fills_simulated(tmp_path):
    st = hot(age_s=60)
    b, _ = run_exp(st, ScriptedJupiter([J.NO_ROUTE]), tmp_path)
    p = b.book.positions[st.mint]
    assert "SIMULATED" in buy_route(b) and p.setup.endswith("+noquote")
    assert any("SIMULATED FILL" in a.text for a in b.activity)
    row = b.recorder.db.execute("SELECT quote_status, bought, simulated_fill FROM candidates").fetchone()
    assert row == ("NO_ROUTE", 1, 1)
    assert b.audit.funnel["buy_simulated_noquote"] == 1


def test_429_retries_then_buys_on_real_quote():
    st = hot(age_s=60)
    jup = ScriptedJupiter([J.RATE_LIMITED, J.OK])
    b, t0 = run_exp(st, jup)
    assert not b.book.positions and st.mint in b.intents
    asyncio.run(b.execute_intents(t0 + 3))
    assert st.mint in b.book.positions and "SIMULATED" not in buy_route(b)


def test_timeout_then_5xx_past_window_is_simulated_not_dropped():
    st = hot(age_s=60)
    b, t0 = run_exp(st, ScriptedJupiter([J.TIMEOUT, J.API_ERROR]))
    asyncio.run(b.execute_intents(t0 + 61))
    assert st.mint in b.book.positions and "SIMULATED" in buy_route(b)


def test_simulated_fill_still_respects_risk_engine():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.cfg.experimental = True
    b.set_kill(True)
    b.tick()
    asyncio.run(b.execute_intents())
    assert not b.book.positions


def test_old_engine_never_fills_without_quote():
    st = good()
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.tick()
    asyncio.run(b.execute_intents())
    assert not b.book.positions


class FakeCredits:
    def __init__(self, ok=True, daily_budget=333_333):
        self.ok, self.daily_budget = ok, daily_budget

    def allow(self, cost):
        return self.ok


class FakeRpc:
    has_das = True

    def __init__(self, ok=True, answer=True, budget=333_333):
        self.calls, self.answer, self.credits = [], answer, FakeCredits(ok, budget)

    async def das_get_asset(self, mint):
        self.calls.append(mint)
        if not self.answer:
            return None
        return {"token_program": "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "extensions": [],
                "mint_authority": "", "freeze_authority": "", "symbol": "TKN", "name": "Token"}


class Tok:
    def __init__(self, i):
        self.mint = f"Mint{i:04d}" + "1" * 36


def test_fast_lane_never_exceeds_configured_rate():
    from scanner.fast_lane import FastLane
    rpc = FakeRpc()
    lane = FastLane(rpc, per_min=5, cooldown_s=20)
    toks = [Tok(i) for i in range(100)]
    t0 = 1_000_000.0
    times = []
    for k in range(0, 300, 3):                                   # a round every 3 s for 5 minutes
        before = len(rpc.calls)
        asyncio.run(lane.round([x for x in toks if x.mint not in lane.checked], lambda st, a: None, now=t0 + k))
        times += [t0 + k] * (len(rpc.calls) - before)
    assert times and all(sum(1 for u in times if t <= u < t + 60) <= 5 for t in times)
    assert lane.stats(t0 + 300)["rate_limited_skips"] > 0


def test_fast_lane_limit_capped_by_helius_budget():
    from scanner.fast_lane import FastLane
    lane = FastLane(FakeRpc(budget=100_000), per_min=50)
    assert lane.per_min() == max(1, int(100_000 / 24 / 60 * 0.10 / 10))       # 10 % of the paced hourly budget


def test_fast_lane_per_ca_cooldown_and_cache():
    from scanner.fast_lane import FastLane
    rpc = FakeRpc(answer=False)
    lane = FastLane(rpc, per_min=100, cooldown_s=20, cache_ttl_s=60)
    t = Tok(1)
    asyncio.run(lane.round([t], lambda st, a: None, now=100.0))
    asyncio.run(lane.round([t], lambda st, a: None, now=110.0))            # failed 10 s ago: cooldown
    assert len(rpc.calls) == 1 and lane.n["cooldown_skips"] == 1
    asyncio.run(lane.round([t], lambda st, a: None, now=125.0))
    assert len(rpc.calls) == 2
    rpc.answer = True
    asyncio.run(lane.fetch("CacheMe1", now=200.0))
    asyncio.run(lane.fetch("CacheMe1", now=230.0))                          # within TTL: no second call
    assert rpc.calls.count("CacheMe1") == 1 and lane.n["cache_hits"] == 1
    asyncio.run(lane.fetch("CacheMe1", now=300.0))                          # TTL expired
    assert rpc.calls.count("CacheMe1") == 2


def test_quota_unavailable_means_watch_never_buy_never_reject():
    from scanner.fast_lane import FastLane
    rpc = FakeRpc(ok=False)
    lane = FastLane(rpc, per_min=100)
    st = hot()
    st.identity.helius_checked = False
    asyncio.run(lane.round([st], lambda s, a: None, now=100.0))
    assert rpc.calls == [] and lane.n["quota_errors"] == 1 and lane.stats(100.0)["paused"]
    xd, _ = ev(st)
    assert xd.decision == D.WATCH and "gate_unknown:authorities" in xd.blocked_by and not xd.rejected


def test_fast_lane_only_for_tokens_genuinely_near_a_buy():
    near, far, rejected = hot(), hot(price_change_5m=0.0, vol_5m=500.0, buys_5m=50, sells_5m=60), hot()
    far.info.mint, rejected.info.mint = "Far" + "1" * 41, "Rej" + "1" * 41
    for x in (near, far, rejected):
        x.identity.helius_checked = False
    rejected.risk = RiskResult(70, "HIGH")
    b = bot([near, far, rejected], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.tick()
    hint = b._deep_hint(b.last_tick)
    assert hint == {near.mint}


def test_authority_fast_lane_checks_hinted_tokens(tmp_path):
    from core.config import ApiKeys, Settings
    from database.db import Database
    from scanner.engine import ScannerEngine
    from scanner.fast_lane import FastLane

    eng = ScannerEngine(Settings(), Database(tmp_path / "e.db"), keys=ApiKeys(), on_log=lambda m: None)
    rpc = FakeRpc()
    eng.rpc, eng.fast = rpc, FastLane(rpc, per_min=10)
    eng._evaluate = lambda st: None
    st, other = hot(), hot()
    other.info.mint = "Other" + "1" * 39
    for x in (st, other):
        x.identity.helius_checked = False
    eng.tracked = {st.mint: st, other.mint: other}
    eng.deep_hint = {st.mint}

    async def go():
        eng._stop = asyncio.Event()
        task = asyncio.create_task(eng._authority_worker())
        await asyncio.sleep(0.2)
        eng._stop.set()
        await task
    asyncio.run(go())
    assert rpc.calls == [st.mint] and st.identity.helius_checked and not other.identity.helius_checked
    xd, _ = ev(st)
    assert "gate_unknown:authorities" not in xd.blocked_by
    assert eng.fast.stats()["fast_getasset_calls"] == 1


def test_token2022_unknown_is_watch_not_pass():
    st = hot()
    st.identity.helius_checked = False
    xd, _ = ev(st)
    assert "gate_unknown:token_2022" in xd.blocked_by and xd.decision == D.WATCH


def test_ab_old_fields_identical_with_and_without_experimental():
    a_st, b_st = hot(age_s=150, holders=80), hot(age_s=150, holders=80)
    a, b = bot([a_st], ScriptedJupiter([J.OK])), bot([b_st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    now = time.time()
    a.tick(now)
    b.tick(now)
    ra, rb = a.decisions[a_st.mint], b.decisions[b_st.mint]
    for k in ("old_decision", "old_early", "old_opportunity", "old_confidence", "blocked_by_old", "old_candidate"):
        assert ra[k] == rb[k], k                                     # OLD is never overwritten by NEW
    assert ra["old_decision"] == ra["decision"]                      # OLD engine decides when experimental is off
    assert rb["new_opportunity"] == rb["old_opportunity"] and rb["new_early"] in ("PASS", "LOW", "UNKNOWN")


def test_opportunity_decomposition_is_consistent():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from audit_bottlenecks import decompose
    from core.config import Settings
    st = hot(age_s=150, holders=80)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.tick()
    rec = b.decisions[st.mint]
    d = decompose(st, rec, Settings(), 150.0)
    comp = d["components"]
    recomputed = round(sum(D.WEIGHTS[k] * v for k, v in comp.items()) / sum(D.WEIGHTS[k] for k in comp))
    assert recomputed == rec["opportunity"]
    assert all(v < 65 for v in d["weak_components"].values())
    assert (rec["opportunity"] >= 65) == (d["classification"] == [])
