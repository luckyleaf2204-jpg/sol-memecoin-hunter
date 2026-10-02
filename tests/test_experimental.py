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


def run_exp(st, jup, tmp_path=None, simulated=False):
    b = bot([st], jup)
    b.cfg.experimental = True
    b.cfg.paper_fill_without_quote = simulated             # opt-in (default OFF since step 1)
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


def test_no_route_is_not_filled_by_default():
    """Step 1: paper_fill_without_quote is OFF by default — a NO_ROUTE candidate is not bought on the model."""
    st = hot(age_s=60)
    b, _ = run_exp(st, ScriptedJupiter([J.NO_ROUTE]))
    assert b.cfg.paper_fill_without_quote is False and not b.book.positions
    assert not any(e.side == "BUY" and e.status == "FILLED" for e in b.book.executions)


def test_no_route_keeps_candidate_and_fills_simulated(tmp_path):
    st = hot(age_s=60)
    b, _ = run_exp(st, ScriptedJupiter([J.NO_ROUTE]), tmp_path, simulated=True)
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
    b, t0 = run_exp(st, ScriptedJupiter([J.TIMEOUT, J.API_ERROR]), simulated=True)
    asyncio.run(b.execute_intents(t0 + 61))
    assert st.mint in b.book.positions and "SIMULATED" in buy_route(b)


def test_simulated_fill_still_respects_risk_engine():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.cfg.experimental = True
    b.cfg.paper_fill_without_quote = True
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


def test_fast_lane_default_5_and_quota_governor_drops_to_2():
    from scanner.fast_lane import FastLane

    class Credits(FakeCredits):
        def __init__(self, used, exhausted=False):
            super().__init__(True, 300_000)
            self.used, self.exhausted = used, exhausted

        def state(self):
            return {"daily_budget": self.daily_budget, "used": self.used, "quota_exhausted": self.exhausted}
    rpc = FakeRpc()
    noon = 86400 * 20000 + 43200                                         # half the day elapsed -> 165K allowed
    rpc.credits = Credits(used=50_000)
    lane = FastLane(rpc)
    assert lane.per_min_cfg == 5 and lane.per_min(noon) == 5 and not lane.throttled
    rpc.credits = Credits(used=150_000)                                  # > 80 % of the paced allowance
    assert lane.per_min(noon) == 2 and lane.throttled
    rpc.credits = Credits(used=0, exhausted=True)
    assert lane.per_min(noon) == 2


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


# ---------------------------------------------------------------- liquidity floor A/B
def curve_tok(virtual_sol, real_sol, sol=150.0, age_s=60, **kw):
    st = hot(age_s=age_s, **kw)
    m = st.market
    m.dex_id, m.liquidity_source, m.liquidity_usd = "pumpfun", "pumpfun_curve", real_sol * sol
    st.info.virtual_sol_reserves, st.info.real_sol_reserves, st.info.sol_price = virtual_sol, real_sol, sol
    st.info.complete, st.info.quote_mint = False, "11111111111111111111111111111111"
    return st


def test_curve_amm_equivalent_passes_where_old_raw_floor_fails():
    st = curve_tok(virtual_sol=40, real_sol=10)          # real $1,500 (OLD FAIL) · AMM-equivalent 2*40*150 = $12,000
    xd, _ = ev(st)
    lq = xd.es.liquidity
    assert lq["old"] == D.FAIL and lq["decision"] == "PASS" and lq["model"] == "curve_amm_equivalent"
    assert lq["equivalent_usd"] == 12_000 and lq["confidence"] == "HIGH"
    assert "hard:liquidity" not in xd.blocked_by and "gate_unknown:liquidity" not in xd.blocked_by


def test_curve_below_floor_still_rejected_same_10k():
    st = curve_tok(virtual_sol=31, real_sol=1)           # 2*31*150 = $9,300 < $10,000
    xd, _ = ev(st)
    assert xd.es.liquidity["decision"] == "FAIL" and "liquidity" in xd.rejected


def test_curve_unverifiable_is_watch_never_buy():
    st = curve_tok(virtual_sol=None, real_sol=10)
    xd, _ = ev(st)
    assert xd.es.liquidity["decision"] == "UNKNOWN" and "gate_unknown:liquidity" in xd.blocked_by
    assert xd.decision == D.WATCH


def test_curve_with_shock_or_rug_or_risk_fails_liquidity():
    for mod in ("shock", "rug", "risk"):
        st = curve_tok(virtual_sol=60, real_sol=30)
        if mod == "shock":
            st.liquidity_intel = LiquidityIntel(state="SHOCK")
        elif mod == "rug":
            st.risk = RiskResult(10, "LOW", [RiskFactor("dev_dump", 10, "rug")])
        else:
            st.risk = RiskResult(70, "HIGH")
        xd, _ = ev(st)
        assert xd.es.liquidity["decision"] == "FAIL" and xd.decision == D.REJECT


def test_amm_floor_unchanged():
    assert ev(hot(liquidity_usd=8_000.0))[0].es.liquidity["decision"] == "FAIL"
    lq = ev(hot(liquidity_usd=50_000.0))[0].es.liquidity
    assert lq["decision"] == "PASS" and lq["model"] == "amm_reported" and lq["old"] == D.PASS


def test_old_engine_liquidity_rule_untouched():
    st = curve_tok(virtual_sol=40, real_sol=10)
    v = D.vet(st, CFG)
    assert {c.key: c.result for c in v.checks}["liquidity"] == D.FAIL      # OLD/VET still on raw real SOL


def test_fill_failure_logs_breakdown_and_keeps_candidate(tmp_path):
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    b.exec._slip = lambda s: 0.029                       # simulated latency slippage 2.9 % + impact 0.4 % > 3 %
    b.tick()
    asyncio.run(b.execute_intents())
    assert not b.book.positions
    f = b.fill_log[-1]
    assert f["status"] == "FAILED" and f["max_slippage_pct"] == 3.0
    assert abs(f["total_slippage_pct"] - (f["jupiter_impact_pct"] + f["latency_slippage_pct"])) < 1e-6
    assert any("PAPER FILL FAILED" in a.text and "candidate kept" in a.text for a in b.activity)
    row = b.recorder.db.execute("SELECT fill_status, total_slippage_pct, max_slippage_pct, fill_fail_reason, "
                                "new_liquidity_decision, liquidity_model_used FROM candidates").fetchone()
    assert row[0] == "FAILED" and row[1] > row[2] and "slippage" in row[3] and row[4] == "PASS"
    assert b.audit.funnel["fill_fail"] == 1


# ---------------------------------------------------------------- entry risk buffer (NEW BUYs only)
def test_entry_risk_buffer_bands():
    for score, expect in ((55, D.TRADE), (57, D.WATCH), (61, D.REJECT)):
        st = hot()
        st.risk = RiskResult(score, "MEDIUM")
        xd, _ = ev(st)
        assert xd.decision == expect, (score, xd.blocked_by)
        assert (any(b.startswith("entry_risk_buffer") for b in xd.blocked_by)) == (score == 57)


def test_entry_buffer_rechecked_at_entry_keeps_candidate(tmp_path):
    st = hot(age_s=60)
    st.risk = RiskResult(40, "LOW")
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    b.tick()
    st.risk = RiskResult(58, "MEDIUM")                      # risk rose between candidate and entry
    asyncio.run(b.execute_intents())
    assert not b.book.positions and b.buffer_blocks[-1]["risk_at_entry"] == 58
    assert b.buffer_blocks[-1]["risk_at_candidate"] == 40
    assert any("ENTRY RISK BUFFER" in a.text for a in b.activity)
    row = b.recorder.db.execute("SELECT entry_blocked_by_risk_buffer, risk_at_candidate, risk_at_entry, "
                                "would_have_bought_if_quote_ok FROM candidates").fetchone()
    assert row == (1, 40.0, 58.0, 1)


def test_entry_buffer_never_touches_held_positions():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions
    st.risk = RiskResult(58, "MEDIUM")                      # above the buffer, below the hard limit
    b.tick()
    asyncio.run(b.execute_sells())
    assert st.mint in b.book.positions                      # exits unchanged: only Risk > 60 / rug sells


def test_old_and_new_entry_risk_logged_for_ab():
    st = hot()
    st.risk = RiskResult(57, "MEDIUM")
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.tick()
    rec = b.decisions[st.mint]
    assert rec["old_entry_risk_pass"] is True and rec["new_entry_risk_pass"] is False


# ---------------------------------------------------------------- latency slippage models
def test_latency_models_current_conservative_empirical():
    from trading.execution import PaperExecutor
    st = hot()
    st.market.price_change_5m = 30.0
    cur, cons, emp = PaperExecutor(7), PaperExecutor(7, latency_model="CONSERVATIVE"), PaperExecutor(7, latency_model="EMPIRICAL")
    a = cur._slip(st)
    assert 0.015 <= a <= 0.0205 and cur.last_model_used == "CURRENT"          # 5 % of 30 % + 0-0.5 % noise
    assert abs(cons._slip(st) - min(0.03, a * 1.5)) < 1e-12
    emp.empirical = lambda s: None                                           # not enough samples
    assert abs(emp._slip(st) - a) < 1e-12 and "not enough samples" in emp.last_model_used
    emp.empirical = lambda s: 0.004
    assert emp._slip(st) == 0.004 and emp.last_model_used == "EMPIRICAL"


def test_empirical_needs_min_samples_and_uses_p75():
    st = hot()
    b = bot([st], ScriptedJupiter([J.OK]))
    for i in range(49):
        b.latency_samples.append(("10-40%", i / 10_000))
    assert b._empirical_slip(st) is None
    st.market.price_change_5m = 20.0
    b.latency_samples.append(("10-40%", 0.0049))
    assert abs(b._empirical_slip(st) - 0.0036) < 1e-9                       # P75 = sorted[int(0.75*49)] = 0.0036
    assert b.latency_stats()["empirical_active"] is True


def test_latency_probe_measures_real_drift_and_fill_record_is_complete(monkeypatch):
    class Drifting(ScriptedJupiter):
        async def quote_result(self, *a, **kw):
            r = await super().quote_result(*a[:4])
            if self.calls == 2:                                               # the re-quote: 1 % fewer tokens
                r.quote["outAmount"] = str(int(int(r.quote["outAmount"]) * 0.99))
            return r
    st = hot(age_s=60)
    b = bot([st], Drifting([J.OK]))
    b.cfg.experimental, b.cfg.latency_probe = True, True
    monkeypatch.setattr(asyncio, "sleep", _no_sleep)
    b.tick()
    asyncio.run(b.execute_intents())
    f = b.fill_log[-1]
    assert f["requote_drift_bps"] == 100 and len(b.latency_samples) == 1
    for k in ("quote_ts", "execution_ts", "quote_price", "simulated_execution_price", "jupiter_impact_bps",
              "latency_slippage_bps", "total_slippage_bps", "max_slippage_bps", "fill_result", "latency_model",
              "risk_at_candidate", "risk_at_quote", "risk_at_entry"):
        assert k in f, k
    assert f["max_slippage_bps"] == 300 and f["total_slippage_bps"] == f["jupiter_impact_bps"] + f["latency_slippage_bps"] \
        or abs(f["total_slippage_bps"] - f["jupiter_impact_bps"] - f["latency_slippage_bps"]) <= 1


_real_sleep = asyncio.sleep


async def _no_sleep(*_a, **_k):
    await _real_sleep(0)


# ---------------------------------------------------------------- risk-spike forensics
def test_forensics_snapshots_entry_offsets_and_exit():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    fx = b.forensics[st.mint]
    assert fx["snaps"][0]["at"] == "ENTRY" and fx["ctx"]["risk_at_entry"] is not None
    for k in (5, 10, 15, 30, 60):
        b.tick(t0 + k)
    assert [s["at"] for s in fx["snaps"]] == ["ENTRY", "+5s", "+10s", "+15s", "+30s", "+60s"]
    assert all("factors" in s and "market_age_s" in s for s in fx["snaps"])


def test_risk_spike_classification():
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
    from audit_bottlenecks import classify_spike

    def snap(at, t, risk, factors, price=1.0, age=2.0, hf=100.0, dev="HOLDING", liq="STABLE"):
        return {"at": at, "t": t, "risk": risk, "factors": factors, "price": price, "market_age_s": age,
                "holders_fetched": hf, "dev_status": dev, "liq_state": liq}
    base = ["age:young:5"]
    refresh = {"symbol": "A", "ctx": {}, "exit": None, "snaps": [
        snap("ENTRY", 0, 50, base), snap("+15s", 15, 66, base + ["holders:top10_high:16"], hf=110.0)]}
    real = {"symbol": "B", "ctx": {}, "exit": None, "snaps": [
        snap("ENTRY", 0, 50, base), snap("+10s", 10, 75, base + ["dev:dev_sold:25"], price=0.8, dev="MAJOR SELL")]}
    stale = {"symbol": "C", "ctx": {}, "exit": None, "snaps": [
        snap("ENTRY", 0, 50, base), snap("+5s", 5, 65, base + ["data:market_stale:15"], age=95.0)]}
    flap = {"symbol": "D", "ctx": {}, "exit": None, "snaps": [
        snap("ENTRY", 0, 50, base), snap("+5s", 5, 64, base + ["manipulation:wash:14"]),
        snap("+10s", 10, 50, base)]}
    calm = {"symbol": "E", "ctx": {}, "exit": None, "snaps": [snap("ENTRY", 0, 50, base), snap("+5s", 5, 52, base)]}
    assert classify_spike(real)["class"] == "REAL_NEW_RISK"
    assert classify_spike(stale)["class"] == "STALE_DATA"
    assert classify_spike(flap)["class"] == "FALSE_POSITIVE"
    assert classify_spike(calm) is None
    r = classify_spike(refresh)
    assert r["class"] == "DATA_REFRESH" and r["added_factors"] == ["holders:top10_high:16"]


# ---------------------------------------------------------------- round 2: AUTO latency tiers, risk attribution
def _bot_with_samples(values):
    b = bot([hot()], ScriptedJupiter([J.OK]))
    b.cfg.latency_slippage_model = "AUTO"
    for v in values:
        b.latency_samples.append(("10-40%", v))
    return b


def test_auto_latency_tiers():
    import random as _r
    rnd = _r.Random(3)
    calm = [rnd.uniform(0, 0.006) for _ in range(120)]           # stable 0-60 bps
    b = _bot_with_samples(calm[:49])
    assert b.latency_tier()[0] == "CURRENT" and b._empirical_slip(hot()) is None
    b = _bot_with_samples(calm[:60])
    tier, p, _ = b.latency_tier()
    assert tier == "EMPIRICAL_P90" and p == 0.90
    v = b._empirical_slip(hot())
    assert abs(v - sorted(calm[:60])[int(0.9 * 59)]) < 1e-12
    b = _bot_with_samples(calm[:120])
    assert b.latency_tier()[0] == "EMPIRICAL_P75"


def test_auto_latency_stays_safe_when_distribution_is_unstable():
    early = [0.0005] * 50
    late = [0.008] * 50                                          # regime change: drift x16
    b = _bot_with_samples(early + late)
    assert b.latency_tier()[0] in ("CURRENT", "EMPIRICAL_P90") and b.latency_tier()[0] != "EMPIRICAL_P75"
    spiky = [0.0002] * 45 + [0.05] * 15                          # erratic tail: P90 >> P75
    assert _bot_with_samples(spiky).latency_tier()[0] == "CURRENT"


def test_total_slippage_over_3pct_still_blocks_with_empirical():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    b.exec.latency_model = "AUTO"
    b.cfg.latency_slippage_model = "AUTO"
    for _ in range(60):
        b.latency_samples.append(("<10%", 0.028))                 # measured 2.8 % + impact 0.4 % > 3 %
    b.tick()
    asyncio.run(b.execute_intents())
    assert not b.book.positions and b.fill_log[-1]["candidate_status"] == "slippage_blocked"
    assert b.fill_log[-1]["latency_model"] == "EMPIRICAL_P90"


def test_data_refresh_is_not_counted_as_new_risk():
    from trading.bot import attribute_risk
    entry = {"risk": 41, "factors": ["age:young:5"], "holders_stamp": None, "dev_stamp": None}
    later = {"risk": 81, "holders_stamp": 1015.0, "dev_stamp": 1016.0,
             "factors": ["age:young:5", "holders:top10_high:25", "holders:single_whale:10", "holders:few_holders:8",
                         "dev:dev_concentration:15"]}
    a = attribute_risk(entry, later, buy_ts=1000.0)
    assert a["risk_new"] == 0 and a["risk_data_refresh"] == 58 and a["spike_type"] == "DATA_REFRESH"
    # dev data already present at entry, worse after a later fetch = a real change
    entry2 = dict(entry, dev_stamp=990.0, factors=["age:young:5"])
    later2 = dict(later, factors=["age:young:5", "dev:dev_sold:30"], risk=71)
    a2 = attribute_risk(entry2, later2, buy_ts=1000.0)
    assert a2["risk_new"] == 30 and a2["spike_type"] == "RISK_NEW"
    # market-side risk (rug / liquidity) is always new
    a3 = attribute_risk(entry, dict(later, factors=["age:young:5", "rug:liquidity_shock:30"], risk=71), 1000.0)
    assert a3["risk_new"] == 30 and a3["spike_type"] == "RISK_NEW"


def test_forensic_snapshots_carry_attribution_and_timestamps():
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental = True
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    b.tick(t0 + 5)
    snap = b.forensics[st.mint]["snaps"][1]
    for k in ("risk", "risk_new", "risk_data_refresh", "risk_data_stale", "changed", "holders_stamp", "dev_stamp", "buy_ts"):
        assert k in snap, k


def test_missing_holder_dev_never_rejects_but_raises_prior():
    st = hot(age_s=900)                                          # no holders, dev unverified
    st.dev.balance_verified = False
    xd, _ = ev(st)
    assert xd.decision != D.REJECT and xd.es.prior_risk >= 30
    assert "no holder data (unobserved concentration risk)" in xd.es.prior_reasons


def test_idea_like_rug_curve_passing_floor_is_still_rejected():
    st = curve_tok(virtual_sol=45, real_sol=15)                  # AMM-equivalent $13.5K: floor PASS
    st.risk = RiskResult(30, "LOW", [RiskFactor("dev_dump", 30, "rug")])
    xd, _ = ev(st)
    assert xd.decision == D.REJECT and "rug" in xd.rejected
    st = curve_tok(virtual_sol=45, real_sol=15)
    st.dev.balance_verified, st.dev.status = True, "SOLD ALL"
    assert "dev" in ev(st)[0].rejected


def test_candidate_status_recorded_for_no_route_and_slippage(tmp_path):
    st = hot(age_s=60)
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.cfg.experimental = True
    b.recorder = DatasetRecorder(tmp_path / "a.db")
    b.tick()
    asyncio.run(b.execute_intents())
    assert b.recorder.db.execute("SELECT candidate_status FROM candidates").fetchone()[0] == "candidate_no_route"
    st2 = hot(age_s=60)
    b2 = bot([st2], ScriptedJupiter([J.OK]))
    b2.cfg.experimental = True
    b2.recorder = DatasetRecorder(tmp_path / "b.db")
    b2.exec._slip = lambda s: 0.029
    b2.tick()
    asyncio.run(b2.execute_intents())
    assert b2.recorder.db.execute("SELECT candidate_status FROM candidates").fetchone()[0] == "slippage_blocked"
