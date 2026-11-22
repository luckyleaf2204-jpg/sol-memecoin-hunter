"""Phase 2: holder estimate, linked holders, flow quality, new risk checks."""
import pytest

from radar.analytics.holders import Flow, estimate_holders, flow_metrics, linked_groups, unnatural_volume
from radar.analytics.risk import RiskInputs, assess

SUPPLY = 1_000_000_000.0


def f(w, bought, sold=0.0, buy_sol=1.0, sell_sol=0.0, nb=1, ns=0):
    return Flow(w, bought, sold, buy_sol, sell_sol, 0.0125 * (buy_sol + sell_sol), nb, ns)


def test_holder_estimate_nets_buys_and_sells_and_excludes_pool():
    hs = estimate_holders([f("A", 80e6, 20e6), f("B", 30e6), f("C", 5e6, 5e6), f("POOL", 500e6)], SUPPLY,
                          exclude={"POOL"})
    assert [h["wallet"] for h in hs] == ["A", "B"]          # C sold everything, pool excluded
    assert hs[0]["pct"] == pytest.approx(6.0)


def test_linked_groups_by_common_funder_and_exchange_ignored():
    holders = [{"wallet": "A", "pct": 6}, {"wallet": "B", "pct": 4}, {"wallet": "C", "pct": 3}, {"wallet": "D", "pct": 2}]
    funders = {"A": "F1", "B": "F1", "C": "CEX", "D": "CEX"}
    g = linked_groups(holders, funders, funder_children={"F1": 2, "CEX": 500})
    assert len(g) == 1 and set(g[0]["wallets"]) == {"A", "B"} and g[0]["pct"] == 10


def test_holder_funding_another_top_holder_is_linked():
    holders = [{"wallet": "A", "pct": 6}, {"wallet": "B", "pct": 4}]
    g = linked_groups(holders, {"B": "A"})
    assert g and set(g[0]["wallets"]) == {"A", "B"}


def test_bot_volume_pattern_is_flagged():
    bots = [f(f"b{i}", 1e6, 1e6, buy_sol=2.0, sell_sol=1.98, nb=5, ns=5) for i in range(20)]
    m = flow_metrics(bots + [f("real", 10e6, 0, buy_sol=3.0)])
    flag, reasons = unnatural_volume(m, mc_sol=30.0)
    assert flag and m["round_trip_volume_share"] > 0.9 and m["size_uniformity"] > 0.9
    organic = [f(f"w{i}", 1e6 * (i + 1), 0, buy_sol=0.5 + i * 0.37) for i in range(20)]
    assert unnatural_volume(flow_metrics(organic), mc_sol=60.0)[0] is False


def test_new_risk_checks():
    base = dict(dev_holding_pct=1, top10_pct=20, holders_source="trade_estimate", early_buyers_common_funder_share=0,
                bundle_wallets=0, liquidity_sol=40, volume_5m_sol=10, wash_volume_share=0.05, dev_sold_share=0,
                dev_sells=0, mint_authority=None, freeze_authority=None, on_curve=True)
    ok = assess(RiskInputs(**base, max_holder_pct=3, linked_holders_pct=0, linked_groups=0, fresh_top_holders=0,
                           unnatural_volume=False))
    assert ok.level == "LOW"
    big = assess(RiskInputs(**base, max_holder_pct=7, max_holder_wallet="Whale111"))
    assert any(c["check"] == "single_holder" and c["status"] == "fail" for c in big.checks)
    assert big.level == "MEDIUM"
    huge = assess(RiskInputs(**base, max_holder_pct=12))
    assert huge.level == "HIGH"                                 # > 2 x 5 % is critical
    ring = assess(RiskInputs(**base, linked_holders_pct=15, linked_groups=2, fresh_top_holders=3))
    assert ring.level == "HIGH"
    # unknown phase-2 data is listed but adds no point
    unk = assess(RiskInputs(**base))
    assert unk.level == "LOW" and any(c["check"] == "fresh_wallets" and c["status"] == "unknown" for c in unk.checks)
