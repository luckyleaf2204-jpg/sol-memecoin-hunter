"""Smart Money Score, co-buy signals (incl. related wallets), risk, peak validation, backtest."""
import pytest

from radar.analytics.backtest import BtTrade, run_backtest, simulate, BtSignal
from radar.analytics.performance import Performance
from radar.analytics.price_hits import Bar, PeakConfig, analyse_entry, horizon_returns, validated_peak
from radar.analytics.relationships import (Edge, cluster_sizes, clusters, funding_edges, sync_buy_edges,
                                           transfer_edges)
from radar.analytics.risk import RiskInputs, assess, wash_share
from radar.analytics.scoring import category_for, rank_official, score_wallet
from radar.analytics.signals import SmartBuy, SmartSell, evaluate, flow_label
from radar.settings import DEFAULTS

S = dict(DEFAULTS)
T0 = 1_790_000_000


def perf(**kw) -> Performance:
    p = Performance(period_days=30, coverage_complete=True)
    base = dict(positions_total=15, positions_qualifying=12, closed_qualifying=12, wins=9, losses=3, win_rate=0.75,
                median_buy_sol=6.0, avg_buy_sol=6.5, realized_pnl_sol=25.0, roi=0.4, hit_rate_2x=0.5,
                hit_rate_5x=0.2, hit_rate_10x=0.08, pnl_ex_best_sol=12.0, top_win_share=0.35, verified_share=0.9)
    base.update(kw)
    for k, v in base.items():
        setattr(p, k, v)
    p.metrics.update({"share_positions_qualifying": 0.8, "median_to_mean": 0.92,
                      "positions_with_price_data": 12, "median_entry_share_of_liquidity": 0.1})
    return p


# ---------------- score ----------------

def test_score_is_bounded_and_explainable():
    r = score_wallet(perf(), S)
    assert 0 <= r.score <= 100
    assert set(r.components) >= {"win_rate", "median_buy", "realized_pnl", "roi", "early_entry", "stability"}
    assert r.qualifies and r.confidence == "HIGH"
    total = (20 * r.components["win_rate"]["factor"] + 20 * r.components["median_buy"]["factor"]
             + 15 * r.components["realized_pnl"]["factor"] + 15 * r.components["roi"]["factor"]
             + 20 * r.components["early_entry"]["factor"] + 10 * r.components["stability"]["factor"])
    assert r.score == pytest.approx(round(total * r.components["multiplier"], 2))


def test_low_win_rate_fails_mandatory_criteria():
    r = score_wallet(perf(win_rate=0.55, wins=6, losses=6), S)
    assert not r.qualifies and any("win rate" in x for x in r.reasons)


def test_few_trades_means_low_confidence_and_not_official():
    r = score_wallet(perf(closed_qualifying=3, positions_qualifying=3, wins=3, losses=0, win_rate=1.0), S)
    assert r.confidence == "LOW" and not r.eligible_official


def test_unverified_data_lowers_confidence():
    r = score_wallet(perf(verified_share=0.3), S)
    assert r.confidence == "LOW"


def test_wallet_without_backfilled_history_is_never_official():
    good = perf(wins=10, losses=2, win_rate=10 / 12, median_buy_sol=12.0, realized_pnl_sol=60.0, roi=0.9)
    r = score_wallet(good, S, history_read=False)
    assert r.confidence == "LOW" and not r.eligible_official


def test_incomplete_backfill_caps_confidence():
    p = perf()
    p.coverage_complete = False
    assert score_wallet(p, S).confidence == "MEDIUM"


def test_bigger_median_buy_scores_higher_but_absolute_size_is_not_everything():
    small = score_wallet(perf(median_buy_sol=2.1), S).components["median_buy"]["factor"]
    big = score_wallet(perf(median_buy_sol=30), S).components["median_buy"]["factor"]
    assert big > small
    p = perf(median_buy_sol=30)
    p.metrics["median_to_mean"] = 0.1      # one giant outlier
    assert score_wallet(p, S).components["median_buy"]["factor"] < big


def test_dependence_on_one_win_and_clusters_lower_stability():
    a = score_wallet(perf(), S).components["stability"]["factor"]
    b = score_wallet(perf(top_win_share=0.95, pnl_ex_best_sol=-1), S).components["stability"]["factor"]
    c = score_wallet(perf(), S, cluster_size=4).components["stability"]["factor"]
    assert b < a and c < a


def test_high_frequency_wallets_are_dampened():
    base = score_wallet(perf(), S).score
    hf = score_wallet(perf(closed_qualifying=120, positions_qualifying=120, positions_total=400), S).score
    assert hf < base


def test_categories_and_official_ranking():
    assert category_for(90) == "EXCELLENT" and category_for(80) == "STRONG"
    assert category_for(70) == "WATCHLIST" and category_for(50) == "NOT_PRIORITY"
    good = score_wallet(perf(wins=10, losses=2, win_rate=10 / 12, median_buy_sol=12.0, realized_pnl_sol=60.0, roi=0.9), S)
    assert good.score >= 65 and good.eligible_official
    low = score_wallet(perf(closed_qualifying=2, positions_qualifying=2, wins=2, losses=0, win_rate=1.0), S)
    weak = score_wallet(perf(), S)          # passes every mandatory criterion but scores < 65
    assert weak.qualifies and weak.score < 65 and not weak.eligible_official
    ranked = rank_official({"A": good, "B": low, "C": weak})
    assert [w for w, _ in ranked] == ["A"]


# ---------------- relationships / signals ----------------

def test_common_funder_and_transfers_build_clusters():
    edges = funding_edges({"w1": ("F", "s1"), "w2": ("F", "s2"), "w3": ("G", "s3")})
    edges += transfer_edges([("w3", "w4", "sol", 1.0, "s4")])
    cl = clusters(["w1", "w2", "w3", "w4", "w5"], edges)
    assert cl["w1"] == cl["w2"] and cl["w3"] == cl["w4"] and cl["w5"] == "w5"
    assert cluster_sizes(cl)["w1"] == 2


def test_exchange_like_funder_is_not_a_relationship():
    funders = {f"w{i}": ("CEX", f"s{i}") for i in range(30)}
    assert funding_edges(funders, exchange_fanout=20) == []


def test_synchronised_buyers_are_linked():
    buys = {"a": [("m1", 10), ("m2", 50), ("m3", 90)], "b": [("m1", 11), ("m2", 50), ("m3", 91)],
            "c": [("m1", 400)]}
    e = sync_buy_edges(buys)
    assert len(e) == 1 and {e[0].a, e[0].b} == {"a", "b"}


def sb(w, t, sol, pos=None, cluster=None, ver="verified", price=1e-6, score=80):
    return SmartBuy(w, t, sol, pos if pos is not None else sol, price, ver, cluster or w, score, 0.7, f"sig-{w}-{t}")


def test_watch_strong_very_strong_levels():
    now = T0 + 200
    two = [sb("a", T0, 3), sb("b", T0 + 60, 3)]
    assert evaluate(two, [], now, S, 1e-6, "LOW").level == "WATCH"
    three = two + [sb("c", T0 + 100, 4)]
    assert evaluate(three, [], now, S, 1e-6, "LOW").level == "STRONG"
    five = three + [sb("d", T0 + 120, 5), sb("e", T0 + 150, 6)]
    assert evaluate(five, [], now, S, 1e-6, "LOW").level == "VERY_STRONG"


def test_five_wallets_of_one_owner_are_one_buyer():
    buys = [sb(f"w{i}", T0 + i, 5, cluster="owner") for i in range(5)]
    r = evaluate(buys, [], T0 + 10, S, 1e-6, "LOW")
    assert r.raw_buyers == 5 and r.independent_buyers == 1 and r.level is None


def test_positions_below_2_sol_and_old_buys_are_ignored():
    buys = [sb("a", T0, 1.5), sb("b", T0 + 30, 3), sb("c", T0 - 400, 9)]
    r = evaluate(buys, [], T0 + 60, S, 1e-6, "LOW")
    assert r.raw_buyers == 1 and r.level is None


def test_position_can_reach_2_sol_through_several_buys():
    buys = [sb("a", T0, 1.0, pos=1.0), sb("a", T0 + 10, 1.5, pos=2.5), sb("b", T0 + 20, 3)]
    r = evaluate(buys, [], T0 + 60, S, 1e-6, "LOW")
    assert r.raw_buyers == 2 and r.level == "WATCH"


def test_too_late_entry_is_rejected():
    buys = [sb("a", T0, 3, price=1e-6), sb("b", T0 + 60, 3, price=1e-6)]
    r = evaluate(buys, [], T0 + 100, S, current_price=3e-6, risk_level="LOW")
    assert r.level is None and r.runup == pytest.approx(3)


def test_strong_requires_verified_buys_and_unverified_never_counts():
    buys = [sb("a", T0, 4), sb("b", T0 + 5, 4), sb("c", T0 + 9, 4, ver="partially_verified")]
    r = evaluate(buys, [], T0 + 20, S, 1e-6, "LOW")
    assert r.level == "WATCH" and r.downgrade_reasons
    buys2 = [sb("a", T0, 4), sb("b", T0 + 5, 4, ver="unverified")]
    assert evaluate(buys2, [], T0 + 20, S, 1e-6, "LOW").raw_buyers == 1


def test_high_risk_caps_signal():
    buys = [sb("a", T0, 4), sb("b", T0 + 5, 4), sb("c", T0 + 9, 4)]
    assert evaluate(buys, [], T0 + 20, S, 1e-6, "HIGH").level == "WATCH"


def test_net_flow_and_flow_label():
    buys = [sb("a", T0, 4), sb("b", T0 + 5, 4)]
    r = evaluate(buys, [SmartSell("x", T0 + 10, 6)], T0 + 20, S, 1e-6, "LOW")
    assert r.net_flow_sol == pytest.approx(2)
    assert flow_label(10, 12) == "ACCUMULATING" and flow_label(-8, 12) == "DISTRIBUTING"
    assert flow_label(0.5, 12) == "BALANCED"


# ---------------- risk ----------------

def test_risk_levels():
    safe = assess(RiskInputs(dev_holding_pct=1, top10_pct=20, holders_source="rpc", early_buyers_common_funder_share=0,
                             bundle_wallets=0, liquidity_sol=40, volume_5m_sol=20, wash_volume_share=0.05,
                             dev_sold_share=0, dev_sells=0, mint_authority=None, freeze_authority=None, on_curve=True))
    assert safe.level == "LOW" and "không phải" in safe.disclaimer
    bad = assess(RiskInputs(dev_holding_pct=25, top10_pct=70, mint_authority=None, freeze_authority=None))
    assert bad.level == "HIGH"
    unknown = assess(RiskInputs())
    assert unknown.level in ("MEDIUM", "HIGH")      # missing data is never "safe"


def test_wash_trading_detection():
    trades = [("w", T0 + i * 100, "buy", 1.0) for i in range(6)] + \
             [("w", T0 + i * 100 + 20, "sell", 1.0) for i in range(6)] + [("x", T0, "buy", 2.0)]
    assert wash_share(trades) > 0.8


# ---------------- price validation ----------------

def bar(i, close, high=None, vol=5.0, trades=5, liq=30.0, hc=None):
    return Bar(ts=T0 + i * 60, close=close, high=high, volume_sol=vol, trades=trades, liquidity_sol=liq,
               high_confirmed=hc)


def test_single_thin_wick_does_not_count_as_10x():
    bars = [bar(0, 1.0), bar(1, 1.2), bar(2, 1.1, high=12.0, vol=0.2, trades=1), bar(3, 1.3), bar(4, 1.2)]
    peak, _, _ = validated_peak(bars, T0, PeakConfig())
    assert peak < 2


def test_sustained_move_with_volume_counts():
    bars = [bar(0, 1.0), bar(1, 3.0, high=5.2), bar(2, 5.0, high=5.5), bar(3, 4.0)]
    a = analyse_entry(bars, T0, 1.0, T0 + 10 * 86400, PeakConfig())
    assert a["hits"][2] and a["hits"][5] and not a["hits"][10]


def test_horizon_returns_use_historical_prices_not_current():
    bars = [bar(0, 1.0), bar(5, 2.0), bar(60, 0.5)]
    r = horizon_returns(bars, T0, 1.0, now=T0 + 10 * 86400)
    assert r["5m"] == pytest.approx(1.0)          # price at entry+5m
    assert r["1h"] == pytest.approx(-0.5)
    assert r["7d"] is None                        # no bar close enough to entry+7d


def test_future_horizons_are_unknown():
    r = horizon_returns([bar(0, 1.0)], T0, 1.0, now=T0 + 600)
    assert r["1h"] is None and r["24h"] is None


# ---------------- backtest ----------------

def test_backtest_has_no_look_ahead_wallet_selection():
    """A wallet whose winning history only completes AFTER the signal must not be smart at signal time."""
    trades = []
    # wallets a, b, c win 6 trades each in days 1-6, all closed before day 8
    for w in "abc":
        for i in range(6):
            t = T0 + i * 86400 + ord(w)
            trades += [BtTrade(w, f"old{i}{w}", t, t, "buy", 3, 100, 0.03, 0.03, 30, "verified", f"s{w}{i}b", "trade:0"),
                       BtTrade(w, f"old{i}{w}", t + 600, t + 600, "sell", 6, 100, 0.06, 0.06, 30, "verified",
                               f"s{w}{i}s", "trade:0")]
    # day 8: a, b, c co-buy token NEW -> signal
    t8 = T0 + 8 * 86400
    for k, w in enumerate("abc"):
        trades.append(BtTrade(w, "NEW", t8 + k * 30, t8 + k * 30, "buy", 4, 100, 0.04, 1e-6, 30, "verified",
                              f"n{w}", "trade:0"))
    # day 8: d, e co-buy token LATE but their record is only built later (days 9-15)
    for k, w in enumerate("de"):
        trades.append(BtTrade(w, "LATE", t8 + k * 30, t8 + k * 30, "buy", 4, 100, 0.04, 1e-6, 30, "verified",
                              f"l{w}", "trade:0"))
        for i in range(6):
            t = T0 + (9 + i) * 86400 + k
            trades += [BtTrade(w, f"fut{i}{w}", t, t, "buy", 3, 100, 0.03, 0.03, 30, "verified", f"f{w}{i}b", "trade:0"),
                       BtTrade(w, f"fut{i}{w}", t + 600, t + 600, "sell", 6, 100, 0.06, 0.06, 30, "verified",
                               f"f{w}{i}s", "trade:0")]
    bars = {"NEW": [bar(i, 1e-6 * (1 + i / 10), vol=10) for i in range(-1, 2000)]}
    for b in bars["NEW"]:
        b.ts += 8 * 86400
    # history tokens: price doubled after each entry (bars exist only for the past)
    for w in "abc":
        for i in range(6):
            t = T0 + i * 86400 + ord(w)
            bars[f"old{i}{w}"] = [Bar(ts=t + k * 60, close=0.03 * (1 + k / 4), volume_sol=10, trades=5,
                                      liquidity_sol=40) for k in range(10)]
    s = dict(S, backtest_detection_latency_seconds=5)
    out = run_backtest(trades, bars, s)
    mints = {r["mint"] for r in out["rows"]}
    assert "NEW" in mints and "LATE" not in mints
    levels = {r["level"]: r for r in out["rows"] if r["mint"] == "NEW"}
    assert set(levels) == {"WATCH", "STRONG"}       # WATCH at the 2nd buy, upgrade at the 3rd
    row = levels["STRONG"]
    assert row["detect_ts"] == t8 + 60 + 5          # 3rd buy + detection latency
    assert row["tradable"] and row["entry_price"] > 1e-6   # slippage + fee + impact on entry


def test_simulation_applies_costs_and_detects_liquidity_loss():
    bars = [bar(i, 1.0, vol=10, liq=40) for i in range(5)] + [bar(10, 0.1, vol=2, liq=0.5)]
    r = simulate(BtSignal("M", "WATCH", T0 + 30, 2, 6), bars, dict(S))
    assert r["entry_price"] > 1.0
    assert r["liquidity_lost"] and r["drawdown_50"]
