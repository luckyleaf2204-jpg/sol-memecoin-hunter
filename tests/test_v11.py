"""Lifecycle V1.1 research: money flow, wallet independence / funding clusters, exit liquidity, entry location,
fast-SL forensics, slippage buckets, no look-ahead, UNKNOWN propagation, PRE shadow — production unchanged."""
import asyncio
import time

import pytest

from core.models import LiquidityIntel, WhaleIntel
from research.v11_analysis import pattern_effect, sim_net, slippage_bucket, walk_forward_plan
from test_bot_v2 import bot
from test_experimental import hot
from test_jupiter_exec import ScriptedJupiter
from test_lifecycle import SECOND_WAVE, Store, history, new_tok, post_tok
from trading import decision as D
from trading import jupiter as J
from trading.config import ModeNotAllowed
from trading.entry_location import entry_location
from trading.exit_liquidity import exit_liquidity
from trading.money_flow import money_flow_features, score_money_flow

T = 10_000.0


def tx(t, who, side, sol, slot=None, amount=10.0, pre=None):
    d = {"Pool": -amount, who: amount} if side == "buy" else {"Pool": amount, who: -amount}
    return {"slot": slot or int(t), "t": t, "who": who, "side": side, "sol": -sol if side == "buy" else sol,
            "deltas": d, "pre_bal": {who: pre} if pre else {}}


def rec(txs, creator="Dev"):
    return {"status": "ok", "creator": creator, "txs": txs, "sigs": [(x["slot"], x["t"]) for x in txs]}


def flow(n_prev=4, n_now=8, sol=0.5, start=0):
    prev = [tx(T - 100 + i, f"P{i}", "buy", sol) for i in range(n_prev)]
    now = [tx(T - 50 + i, f"B{start + i}", "buy", sol) for i in range(n_now)]
    return prev + now


# ---------------------------------------------------------------- money flow / independence
def test_buyer_growth_inflow_and_acceleration():
    f = money_flow_features(rec(flow(n_prev=5)), T)              # >= 5 sampled trades needed in both minutes
    assert f["status"] == "ok" and f["unique_buyer_count"] == 8 and f["buyer_acceleration"] == 1.6
    assert f["net_sol_inflow"] == 4.0 and f["sol_inflow_acceleration"] == 1.5 and f["new_buyer_count"] == 8
    assert money_flow_features(rec(flow(n_prev=4)), T)["sol_inflow_acceleration"] is None   # too few: UNKNOWN
    s = score_money_flow(f)
    assert s["money_flow_score"] is not None and s["independence"] == "UNKNOWN"     # no funding data yet


def test_independence_needs_funding_data_and_clusters_are_not_independent():
    f = money_flow_features(rec(flow()), T)
    assert f["independent_buyers"] is None                                          # never assumed
    funders = {f"B{i}": {"status": "ok", "funder": "Hub" if i < 4 else f"F{i}"} for i in range(8)}
    g = money_flow_features(rec(flow()), T, funders=funders)
    assert g["funding_cluster_max"] == 4 and g["independent_buyers"] == 4             # 8 wallets != 8 buyers
    s = score_money_flow(g)
    assert s["independence"] == "MEASURED" and s["cluster_risk"] >= 50


def test_creator_related_buyers():
    funders = {f"B{i}": {"status": "ok", "funder": "Dev" if i == 0 else ("DevFunder" if i == 1 else f"F{i}")}
               for i in range(8)}
    f = money_flow_features(rec(flow()), T, creator="Dev", funders=funders, creator_funder="DevFunder")
    assert f["creator_related_buyers"] == 2


def test_repeated_and_synchronized_buyers():
    txs = [tx(T - 100 + i, f"W{i}", "buy", 0.3) for i in range(5)] + \
          [tx(T - 40, "W0", "buy", 0.3, slot=500), tx(T - 40, "W1", "buy", 0.3, slot=500),
           tx(T - 40, "W2", "buy", 0.3, slot=500), tx(T - 30, "W0", "buy", 0.3), tx(T - 20, "N1", "buy", 0.3)]
    f = money_flow_features(rec(txs), T)
    assert f["repeat_buyer_count"] >= 3 and f["repeat_buyer_ratio"] > 0.5 and f["synchronized_buy_share"] == 0.6


def test_unknown_propagates_and_never_passes():
    f = money_flow_features(None, T)
    s = score_money_flow(f)
    assert s["money_flow_score"] is None and s["independence"] == "UNKNOWN" and s["cluster_risk"] is None
    f2 = money_flow_features(rec(flow(n_now=3)), T)
    assert f2["status"].startswith("UNKNOWN") and score_money_flow(f2)["money_flow_score"] is None


# ---------------------------------------------------------------- exit liquidity
def test_exit_liquidity_creator_sell_top_holder_and_whale():
    st = hot()
    st.info.total_supply = 1_000.0
    st.whale_intel = WhaleIntel(state="DISTRIBUTION")
    st.liquidity_intel = LiquidityIntel(state="FALLING", change_5m_pct=-20.0)
    txs = [tx(T - 50 + i, f"B{i}", "buy", 0.2) for i in range(3)] + \
          [tx(T - 40, "Dev", "sell", 3.0), tx(T - 30, "Whale", "sell", 2.0, pre=50.0), tx(T - 20, "X", "sell", 1.0)]
    el = exit_liquidity(rec(txs), st, T)
    c = el["components"]
    assert c["dev_sell"] == 1.0 and c["top_holder_sell"] == 1.0 and c["whale_distribution"] == 1.0
    assert el["exit_liquidity_risk"] >= 60
    calm = exit_liquidity(rec(flow()), hot(), T)
    assert calm["exit_liquidity_risk"] is None or calm["exit_liquidity_risk"] < el["exit_liquidity_risk"]


def test_exit_liquidity_unknown_without_data():
    st = hot()
    st.whale_intel, st.liquidity_intel = None, None
    assert exit_liquidity(None, st, T)["exit_liquidity_risk"] is None


# ---------------------------------------------------------------- entry location
def test_entry_location_classes():
    now = T
    early = [(now - 300 + 30 * i, 1.0 + 0.02 * i, 1000, 50_000) for i in range(11)]
    assert entry_location(early, now)["entry_location"] == "EARLY_ENTRY"
    ext = [(now - 300 + 30 * i, 1.0 * (1.15 ** i), 1000, 50_000) for i in range(11)]
    e = entry_location(ext, now)
    assert e["entry_location"] == "EXTENDED" and e["extension_5m_pct"] > 100
    pb = [(now - 300, 1.0, 1000, 50_000), (now - 200, 2.0, 3000, 50_000), (now - 100, 1.6, 1500, 48_000),
          (now - 10, 1.55, 1200, 47_000)]
    p = entry_location(pb, now)
    assert p["entry_location"] == "PULLBACK" and p["pullback_depth_pct"] >= 20
    assert entry_location(pb, now, post_state="SECOND_WAVE_READY")["entry_location"] == "SECOND_WAVE"


# ---------------------------------------------------------------- no look-ahead
def test_future_data_never_changes_past_shadow_scores():
    base = flow()
    fut = base + [tx(T + 20, "Late", "sell", 50.0), tx(T + 25, "Dev", "sell", 9.0)]
    a, b = money_flow_features(rec(base), T), money_flow_features(rec(fut), T)
    assert a == b and score_money_flow(a) == score_money_flow(b)
    st = hot()
    assert exit_liquidity(rec(base), st, T) == exit_liquidity(rec(fut), st, T)
    pts = [(T - 300 + 30 * i, 1.0 + 0.02 * i, 1000, 50_000) for i in range(11)]
    assert entry_location(pts, T) == entry_location(pts + [(T + 30, 9.0, 9e9, 1.0)], T)


# ---------------------------------------------------------------- analysis helpers
@pytest.mark.parametrize("v,b", [(0.5, "<1%"), (1.5, "1-2%"), (2.5, "2-3%"), (3.5, "3-4%"), (4.5, ">4%"), (None, "UNKNOWN")])
def test_slippage_buckets(v, b):
    assert slippage_bucket(v) == b


def test_sim_net_and_pattern_effect_and_walk_forward():
    path = [(1, 1.0), (2, 1.4), (3, 0.5)]
    assert sim_net(path, 0, 1.0, 3.0)[1] == "tp" and sim_net(path, 1.5, 1.4, 3.0)[1] == "sl"
    trades = [{"pnl_pct": -20, "fast_sl": True, "ext": True, "win": False}, {"pnl_pct": 30, "ext": False, "win": True},
              {"pnl_pct": -5, "ext": None, "win": False}]
    e = pattern_effect(trades, lambda t: t.get("ext"))
    assert e["flagged"] == 1 and e["fast_sl_flagged"] == 1 and e["unknown"] == 1 and e["sample"] == "INSUFFICIENT SAMPLE"
    assert walk_forward_plan(["2026-10-01"])["status"].startswith("INSUFFICIENT")
    assert walk_forward_plan(["d1", "d2", "d3", "d4"])["out_of_sample"] == ["d4"]


# ---------------------------------------------------------------- production behaviour
def run(st, h=None):
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental, b.cfg.lifecycle = True, True
    if h is not None:
        b.engine.history = Store({st.mint: h})
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    return b


def test_pre_migration_is_shadow_only():
    st = new_tok(85)
    st.info.curve_progress = 92.0
    b = run(st)
    rec_ = b.decisions[st.mint]
    if rec_.get("pre_shadow_decision") == "WOULD_BUY":
        assert rec_["decision"] == D.WATCH and "pre_migration_shadow" in rec_["blocked_by"]
    assert not b.book.positions                                  # never a PRE BUY in V1.1
    b2 = bot([new_tok(92)], ScriptedJupiter([J.OK]))
    b2.cfg.experimental, b2.cfg.lifecycle, b2.cfg.pre_migration_shadow = True, True, True
    from trading import lifecycle_decision as LD
    st2 = new_tok(92)
    v = D.vet(st2, b2.cfg)
    sc = D.score(st2, v, b2.cfg)
    d = LD.evaluate(st2, v, sc, b2.cfg)
    assert d.decision != D.TRADE


def test_second_wave_still_buys_and_shadow_never_changes_the_decision():
    st = post_tok()
    b = run(st, history(SECOND_WAVE))
    rec_ = b.decisions[st.mint]
    assert st.mint in b.book.positions and rec_["decision"] == D.TRADE      # production V1 unchanged
    assert "money_flow_score" in rec_ and rec_["shadow_B_would_buy"] is False  # money flow UNKNOWN -> B does not buy
    assert rec_["entry_location"] in ("SECOND_WAVE", "UNKNOWN")


def test_fast_sl_flag_and_pre_entry_snapshots():
    st = post_tok()
    b = run(st, history(SECOND_WAVE))
    fx = b.forensics[st.mint]
    assert set(fx["pre_entry"]) == {"T-30s", "T-10s", "T-5s"} and "shadow_at_entry" in fx
    p = b.book.positions[st.mint]
    st.market.price_usd = p.entry_price * 0.5                    # crash -> production stop loss
    st.stamps["market"].updated_at = time.time() + 1             # observed AFTER the fill (V1.2: a pre-entry print
    b.tick(time.time() + 8)                                      # never triggers the stop; was clock-resolution luck)
    assert fx.get("fast_sl_flag") is True and "stop_loss" in fx["fast_sl_reason"]
    assert b.fill_log[-1]["fill_vs_ref_pct"] is not None


def test_auto_stays_locked():
    b = bot([hot()], ScriptedJupiter([J.OK]))
    with pytest.raises(ModeNotAllowed):
        b.set_mode("AUTO")
