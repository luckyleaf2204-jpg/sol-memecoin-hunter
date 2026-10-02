"""Lifecycle-Aware Hunter V1: classifier, transitions, the three setup engines, no look-ahead, missing data,
cluster / funding independence, the lifecycle decision with unchanged safety gates, PaperBot end-to-end, A/B."""
import asyncio
import time

import pytest

from core.models import HolderIntel, RiskFactor, RiskResult
from history.store import Point, TokenHistory
from research.antirug import features
from research.dataset import DatasetRecorder
from research.onchain import features_at
from test_bot_v2 import bot
from test_experimental import curve_tok, hot
from test_jupiter_exec import ScriptedJupiter
from trading import decision as D
from trading import jupiter as J
from trading import lifecycle as LC
from trading import lifecycle_decision as LD
from trading import setup_new, setup_premigration, setup_second_wave as SW
from trading.config import TradingConfig
from validation.identity import apply_identity, record_claim

CFG = TradingConfig(experimental=True, lifecycle=True)


def new_tok(progress=20.0, now=None, **kw):
    now = now or time.time()
    st = curve_tok(virtual_sol=45, real_sol=15, age_s=kw.pop("age_s", 150), holders=kw.pop("holders", 1200), **kw)
    st.info.curve_progress, st.info.pump_updated_at = progress, now
    st.holder_intel = HolderIntel(growth_5m_pct=15.0, new_per_min=6.0)
    return st


def post_tok(now=None):
    now = now or time.time()
    st = hot(age_s=3600, holders=1200)
    st.info.complete, st.info.pump_updated_at, st.info.curve_progress = True, now, 100.0
    st.market.pair_created_at = now - 1800
    return st


def history(points, pair="PAIR111", src="dexscreener_amm", now=None):
    """points: [(dt_from_now, price, liq, vol_5m, buys, sells)]"""
    h = TokenHistory()
    now = time.time() if now is None else now
    for dt, price, liq, vol, b, s in points:
        h.points.append(Point(now + dt, price, price * 1e9, liq, src, vol, vol * 6, b, s, b * 6, s * 6, pair))
    return h


SECOND_WAVE = [(-1700, 1.00, 40_000, 20_000, 60, 40), (-1600, 1.30, 40_000, 50_000, 90, 30),
               (-1500, 1.60, 40_000, 60_000, 120, 40), (-1400, 1.45, 39_000, 40_000, 50, 60),
               (-1200, 1.20, 38_000, 30_000, 40, 50), (-900, 1.24, 38_500, 30_000, 60, 40),
               (-600, 1.28, 39_000, 30_000, 70, 30), (-5, 1.35, 39_500, 30_000, 70, 30)]


class Store:
    def __init__(self, m):
        self._h = m


def ev(st, h=None, tr=None, oc=None, cfg=CFG, now=None):
    now = now or time.time()
    v = D.vet(st, cfg, now)
    sc = D.score(st, v, cfg)
    return LD.evaluate(st, v, sc, cfg, now, tr, h, oc)


# ---------------------------------------------------------------- 1-6 classifier + transitions
def test_new_pre_post_classification():
    assert LC.classify(new_tok(20), CFG).lifecycle == LC.NEW and LC.classify(new_tok(20), CFG).confidence == LC.HIGH
    pre = LC.classify(new_tok(85), CFG)
    assert pre.lifecycle == LC.PRE_MIGRATION and pre.confidence == LC.HIGH and pre.migration_progress == 85
    post = LC.classify(post_tok(), CFG)
    assert post.lifecycle == LC.POST_MIGRATION and post.confidence == LC.HIGH and post.pair_type == "amm"


def test_lifecycle_never_from_age_alone():
    old_curve = new_tok(20, age_s=7200)                        # 2 h old but still early on the curve
    assert LC.classify(old_curve, CFG).lifecycle == LC.NEW
    stale = new_tok(85)
    stale.info.pump_updated_at = time.time() - 3600            # progress not reliable -> PRE not active
    li = LC.classify(stale, CFG)
    assert li.lifecycle == LC.PRE_MIGRATION and li.confidence == LC.LOW and not li.active


def test_unknown_and_migration_conflicts():
    st = new_tok(20)
    st.market.dex_id, st.market.liquidity_source = "pumpswap", "dexscreener_amm"   # curve says not complete
    li = LC.classify(st, CFG)
    assert li.lifecycle == LC.UNKNOWN and li.migration_status == "conflict"
    st = post_tok()
    st.market.dex_id = "pumpfun"                                # complete but market still the curve
    assert LC.classify(st, CFG).migration_status == "conflict"
    st = hot()
    st.market = None
    st.info.complete, st.info.pump_updated_at = None, None
    assert LC.classify(st, CFG).lifecycle == LC.UNKNOWN
    xd = ev(post_tok() if False else _conflict())
    assert xd.decision != D.TRADE and "migration_conflict" in xd.blocked_by


def _conflict():
    st = new_tok(20)
    st.market.dex_id, st.market.liquidity_source = "pumpswap", "dexscreener_amm"
    return st


def test_transitions_and_migration_closes_new_setup():
    tr = LC.LifecycleTracker()
    st = new_tok(20)
    t0 = time.time()
    a, m1 = tr.update(st, LC.classify(st, CFG, t0), t0)
    st.info.curve_progress = 85
    b, m2 = tr.update(st, LC.classify(st, CFG, t0 + 60), t0 + 60)
    p = post_tok(t0 + 120)
    p.info.mint = st.mint
    c, m3 = tr.update(p, LC.classify(p, CFG, t0 + 120), t0 + 120)
    assert a.new_start_ts == t0 and b.premigration_start_ts == t0 + 60 and c.postmigration_start_ts == t0 + 120
    assert not m1 and not m2 and m3 and c.post_pair == "PAIR111" and c.migration_ts == p.market.pair_created_at
    assert [x[1] for x in c.history] == [LC.NEW, LC.PRE_MIGRATION, LC.POST_MIGRATION]


# ---------------------------------------------------------------- 7-9 setup engines
def test_new_setup_score_components_and_unknown_smart_money():
    st = new_tok(20)
    s = setup_new.score(features(st), None)
    assert s.components["smart_money"] is None and "smart_money" in s.unknown and "anti_rug" in s.unknown
    assert s.score is not None and 0 <= s.score <= 100 and s.extra["unknown_independence"] is True
    assert s.extra["independent_smart_money_count"] is None and s.extra["gmgn_rug_proxy"] is None


def test_new_setup_missing_holder_and_dev_lower_confidence_not_fail():
    full = setup_new.score(features(new_tok(20)), None)
    st = new_tok(20)
    st.holder_status, st.holders = "", None
    st.dev.balance_verified = False
    miss = setup_new.score(features(st), None)
    assert miss.components["holder"] is None and miss.data_confidence < full.data_confidence
    xd = ev(st)
    assert xd.decision != D.REJECT


def test_premigration_progression_uses_only_past_curve_points():
    now = time.time()
    pts = [(now - 300, 6_000.0), (now - 200, 7_000.0), (now - 1, 8_000.0)]
    future = pts + [(now + 60, 1.0)]                            # injected future point must not matter
    assert setup_premigration.progression_component(pts, now) == setup_premigration.progression_component(future, now)
    s = setup_premigration.score(features(new_tok(85)), None, pts, now)
    assert s.components["proximity"] is not None and s.components["progression"] > 0


@pytest.mark.parametrize("cut,state", [(-1650, SW.POST_MIGRATED), (-1490, SW.FIRST_PUMP), (-1150, SW.PULLBACK),
                                       (-850, SW.SUPPORT), (0, SW.READY)])
def test_second_wave_state_machine(cut, state):
    now = time.time()
    h = history(SECOND_WAVE)
    pts = [(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in h.points]
    ps = SW.post_state(pts, now + cut, CFG, 30)
    assert ps.state == state, (cut, ps.state, ps.reason)


def test_second_wave_invalid_when_liquidity_or_volume_dies():
    rows = [list(r) for r in SECOND_WAVE]
    rows[-1][2] = 10_000                                        # liquidity collapsed
    h = history([tuple(r) for r in rows])
    pts = [(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in h.points]
    assert SW.post_state(pts, time.time(), CFG, 30).state == SW.INVALID
    assert SW.post_state([(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in history(SECOND_WAVE).points],
                         time.time(), CFG, 65).state == SW.INVALID


def test_price_up_volume_up_alone_is_not_a_second_wave():
    rows = [(-600 + 60 * i, 1.0 + 0.1 * i, 40_000, 20_000 + 5_000 * i, 80, 20) for i in range(10)]
    pts = [(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in history(rows).points]
    ps = SW.post_state(pts, time.time(), CFG, 20)
    assert ps.state in (SW.FIRST_PUMP, SW.POST_MIGRATED) and SW.score(ps, {}).blocks


# ---------------------------------------------------------------- 10-11 no look-ahead
def test_future_feature_must_not_change_past_score():
    now = time.time()
    base = history(SECOND_WAVE, now=now)
    fut = history(SECOND_WAVE + [(400, 0.2, 5_000, 1_000, 5, 95)], now=now)   # a future crash
    t0 = now - 2
    a = [(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in base.points]
    b = [(p.ts, p.price, p.liq, p.vol_5m, p.buy_share) for p in fut.points]
    sa, sb = SW.post_state(a, t0, CFG, 30), SW.post_state(b, t0, CFG, 30)
    assert sa.__dict__ == sb.__dict__ and SW.score(sa, {}).score == SW.score(sb, {}).score
    rec = {"status": "ok", "mint_sigs_complete": True, "mint_sigs": [(100, 1000), (101, 1001)], "creator": "C",
           "early_txs": [{"slot": 100, "t": 1000, "who": "A", "side": "buy", "deltas": {"A": 10}},
                         {"slot": 150, "t": 2000, "who": "C", "side": "sell", "deltas": {"C": -5}}],
           "creator_flows": [{"t": 2000, "side": "sell", "sol_out": []}], "total_supply": 1000}
    assert features_at(rec, 1500)["dev_sells"] == 0 and features_at(rec, 2500)["dev_sells"] == 1


# ---------------------------------------------------------------- 15-16 cluster / funding independence
def _onchain(creator_funded=False, sync=False):
    early = [{"slot": 100, "t": 1000, "who": "Pool", "side": None, "deltas": {"Pool": -30, "B1": 10, "B2": 10, "B3": 10}},
             {"slot": 101, "t": 1001, "who": "B4", "side": "buy", "deltas": {"Pool": -5, "B4": 5}},
             {"slot": 102, "t": 1002, "who": "B5", "side": "buy", "deltas": {"Pool": -5, "B5": 5}}]
    if not sync:
        early[0]["deltas"] = {"Pool": -10, "B1": 10}
    flows = [{"t": 990, "side": "buy", "sol_out": [("B1", 1.0)] if creator_funded else []}]
    return {"status": "ok", "mint_sigs_complete": True, "mint_sigs": [(100, 1000), (101, 1001), (102, 1002)],
            "creator": "Dev", "early_txs": early, "creator_flows": flows, "total_supply": 1000}


def test_cluster_and_funding_independence():
    clean, clus = features_at(_onchain(), 2000), features_at(_onchain(sync=True), 2000)
    funded = features_at(_onchain(creator_funded=True), 2000)
    assert clean["sync_buy_slots"] == 0 and clus["sync_buy_slots"] == 1 and funded["creator_funded_buyers"] == 1
    assert setup_new.anti_rug_component(clean) > setup_new.anti_rug_component(clus) > setup_new.anti_rug_component(funded) \
        or setup_new.anti_rug_component(funded) < setup_new.anti_rug_component(clean)
    assert setup_premigration.independence_component(funded) < setup_premigration.independence_component(clean)
    assert setup_premigration.independence_component(None) is None             # never assumed independent


# ---------------------------------------------------------------- 17-19 safety gates kept
def test_hard_gates_still_block_lifecycle_setups():
    st = new_tok(20)
    st.risk = RiskResult(70, "HIGH")
    assert ev(st).decision == D.REJECT
    st = new_tok(20)
    st.identity.mint_authority = "Auth111"
    assert "authorities" in ev(st).rejected
    st = new_tok(20)
    record_claim(st.identity, "pumpportal", "OTHER", "")
    apply_identity(st)
    assert ev(st).decision == D.REJECT


def test_opportunity_is_logged_not_a_gate():
    st = new_tok(20)
    xd = ev(st)
    assert not any(b == "opportunity" or b == "confidence" for b in xd.blocked_by)
    assert any("Opportunity" in w and "not a gate" in w for w in xd.why)


# ---------------------------------------------------------------- 20-25, 29-30 PaperBot end to end
def run_bot(st, jup, h=None, recorder=None):
    b = bot([st], jup)
    b.cfg.experimental, b.cfg.lifecycle = True, True
    if h is not None:
        b.engine.history = Store({st.mint: h})
    if recorder is not None:
        b.recorder = recorder
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    return b


def test_second_wave_ready_token_is_bought_and_tagged(tmp_path):
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.OK]), history(SECOND_WAVE), DatasetRecorder(tmp_path / "r.db"))
    rec = b.decisions[st.mint]
    assert rec["engine"] == "lifecycle" and rec["lifecycle_name"] == LC.POST_MIGRATION
    assert rec["post_state"] == SW.READY and rec["setup_type"] == "SECOND_WAVE", rec["blocked_by"]
    p = b.book.positions[st.mint]
    assert p.entry_lifecycle == LC.POST_MIGRATION and p.entry_setup == "SECOND_WAVE" and p.entry_setup_score >= 70
    assert b.fill_log[-1]["lifecycle"] == LC.POST_MIGRATION
    row = b.recorder.db.execute("SELECT lifecycle, setup_type, setup_score, post_state FROM token_snapshots "
                                "WHERE setup_type IS NOT NULL LIMIT 1").fetchone()
    assert row[0] == LC.POST_MIGRATION and row[1] == "SECOND_WAVE" and row[3] == SW.READY


def test_first_pump_is_not_chased():
    st = post_tok()
    pump = [(-300, 1.0, 40_000, 20_000, 60, 40), (-200, 1.5, 40_000, 60_000, 150, 20), (-5, 1.7, 41_000, 80_000, 200, 20)]
    b = run_bot(st, ScriptedJupiter([J.OK]), history(pump))
    assert not b.book.positions and b.decisions[st.mint]["post_state"] in (SW.FIRST_PUMP, SW.POST_MIGRATED)


def test_quote_fail_and_slippage_still_apply(tmp_path):
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.NO_ROUTE]), history(SECOND_WAVE))
    assert not b.book.positions                                  # step 1: no simulated fill by default
    st2 = post_tok()
    b2 = bot([st2], ScriptedJupiter([J.OK]))
    b2.cfg.experimental, b2.cfg.lifecycle = True, True
    b2.engine.history = Store({st2.mint: history(SECOND_WAVE)})
    b2.exec._slip = lambda s: 0.029
    b2.tick()
    asyncio.run(b2.execute_intents())
    assert not b2.book.positions and b2.fill_log[-1]["candidate_status"] == "slippage_blocked"


def test_sizing_and_exits_unchanged_and_ab_fields():
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.OK]), history(SECOND_WAVE))
    rec = b.decisions[st.mint]
    v = D.vet(st, b.cfg)
    sc = D.score(st, v, b.cfg)
    start = b.cfg.starting_balance                              # sizing at decision time: fresh book, no exposure
    assert rec["size_usd"] == D.size(st, sc, b.cfg, start, start, 0.0).usd   # existing sizing, unchanged
    for k in ("old_decision", "experimental_decision", "would_have_bought_old", "would_have_bought_experimental",
              "would_have_bought_lifecycle", "setup_type", "setup_score", "lifecycle_name"):
        assert k in rec, k
    assert rec["old_decision"] == sc.decision                   # OLD engine unchanged
    s = b.lifecycle_summary()
    assert s["by_lifecycle"]["POST_MIGRATION"]["buys"] == 1 and s["doing"]


def test_old_engine_unchanged_when_lifecycle_off():
    st = post_tok()
    b = bot([st], ScriptedJupiter([J.OK]))
    b.tick()
    rec = b.decisions[st.mint]
    assert "engine" not in rec and "lifecycle_name" not in rec
