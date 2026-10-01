"""Bonding-curve validation after the 2026-10-01 audit + Trade Candidate requires Early Signal TRUE."""
import asyncio
import time

import pytest

from conftest import SOL_USD, build_state, dex_pair, default_info
from core.config import ApiKeys, Settings
from core.models import EarlySignal, INVALID
from database.db import Database
from intel.pre_early import compute_pre_early
from pumpfun.client import parse_coin
from pumpfun.stream import parse_event
from scanner.engine import ScannerEngine
from trading.bot import PaperBot
from trading.config import TradingConfig
from validation.identity import apply_identity, record_claim
from validation.market import curve_reserve_usd

MINT = "CurveMint11111111111111111111111111111111"


def curve_info(real, virt, *, mayhem="", complete=False, fetched_ago=5, age_s=90):
    return default_info(MINT, age_s=age_s, complete=complete, real_sol_reserves=real, virtual_sol_reserves=virt,
                        pump_updated_at=time.time() - fetched_ago, mayhem_state=mayhem, creator="Dev111")


def curve_state(info, **pair):
    p = dict(mint=MINT, dex="pumpfun", liq=None, mc=6_000, fdv=6_000, price="0.000006")
    p.update(pair)
    return build_state(dex_pair(**p), info=info)


def issue(st, key):
    return next((i for i in st.quality.issues if i.key == key), None)


# ---------------------------------------------------------------- reserve rules
def test_valid_standard_curve():
    st = curve_state(curve_info(25.0, 55.0))
    assert st.market.liquidity_source == "pumpfun_curve" and st.market.liquidity_usd == pytest.approx(25 * SOL_USD)
    assert not any(i.key.startswith("curve_") for i in st.quality.issues)


def test_brand_new_token_small_reserve_is_kept_not_rejected():
    st = curve_state(curve_info(0.031, 30.031))                     # real audit sample: 0.031 SOL ≈ $3.6
    assert st.market.liquidity_usd == pytest.approx(0.031 * SOL_USD)
    i = issue(st, "curve_small")
    assert i and i.severity == "warning" and st.dq_status != INVALID


def test_empty_curve_is_unknown_liquidity_not_zero():
    st = curve_state(curve_info(0.0, 30.0))
    assert st.market.liquidity_usd is None and issue(st, "curve_empty").severity == "warning"
    assert st.dq_status != INVALID


def test_mayhem_curve_uses_reported_reserve():
    st = curve_state(curve_info(0.23, 13.359, mayhem="active"))     # audit sample 35Bu3y…: virtual − real = 13.13
    assert st.market.liquidity_usd == pytest.approx(0.23 * SOL_USD) and not issue(st, "curve_inconsistent")
    std = curve_state(curve_info(0.23, 13.359))                      # same numbers WITHOUT mayhem flag
    assert std.market.liquidity_usd is None and issue(std, "curve_inconsistent").severity == "warning"


def test_missing_or_stale_curve_is_unknown_never_assumed():
    for info, key in ((curve_info(None, None), "curve_unavailable"), (curve_info(25.0, 55.0, fetched_ago=600), "curve_stale")):
        st = curve_state(info)
        assert st.market.liquidity_usd is None and issue(st, key).severity == "warning" and st.dq_status != INVALID
    usd, prob = curve_reserve_usd(curve_info(25.0, 55.0), None, time.time())
    assert usd is None and prob[0] == "sol_price_missing"


def test_malformed_reserve_stays_critical():
    for real in (-1.0, 5_000.0):
        st = curve_state(curve_info(real, real + 30))
        assert st.dq_status == INVALID and issue(st, "curve_malformed").severity == "critical"


def test_graduated_token_never_uses_old_curve():
    st = curve_state(curve_info(0.0, 30.0, complete=True))
    assert st.market.liquidity_usd is None and issue(st, "curve_graduated")
    amm = build_state(dex_pair(mint=MINT, dex="pumpswap", liq=60_000), info=curve_info(0.0, 30.0, complete=True))
    assert amm.market.liquidity_usd == 60_000 and not issue(amm, "curve_graduated")   # AMM pool after migration
    from dex.dexscreener import pick_best_pair
    best = pick_best_pair([dex_pair(mint=MINT, dex="pumpfun", liq=None), dex_pair(mint=MINT, dex="pumpswap", liq=60_000)])
    assert best["dexId"] == "pumpswap"


def test_parsers_read_mayhem_and_quote():
    info = parse_coin({"mint": MINT, "symbol": "M", "mayhem_state": "paused", "virtual_sol_reserves": 6_548_000_000,
                       "real_sol_reserves": 0, "quote_mint": "11111111111111111111111111111111"})
    assert info.mayhem_state == "paused" and info.virtual_sol_reserves == pytest.approx(6.548)
    ev = parse_event({"txType": "create", "mint": MINT, "is_mayhem_mode": True, "marketCapSol": 49.7})
    assert ev.mayhem_state == "active"


def test_missing_curve_does_not_block_pre_early():
    from history.store import TokenHistory
    from scanner.pipeline import evaluate, ingest_market
    from dex.dexscreener import parse_pair
    now = time.time()
    st = curve_state(curve_info(None, None, age_s=100))
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    h = TokenHistory()
    for k, ago in enumerate((80, 60, 40, 20, 0)):
        p = dex_pair(mint=MINT, dex="pumpfun", liq=None, mc=6_000 * (1 + 0.3 * k), fdv=6_000 * (1 + 0.3 * k),
                     price=f"{6e-6 * (1 + 0.3 * k):.12f}", vol=(2_000 * (k + 1) ** 2, 2_000 * (k + 1) ** 2),
                     m5=(12 * (k + 1) ** 2, 4 * (k + 1)), h1=(12 * (k + 1) ** 2, 4 * (k + 1)))
        ingest_market(st, parse_pair(p), {}, SOL_USD, h, now - ago)
        evaluate(st, Settings(), h, now - ago, SOL_USD)
    pe = compute_pre_early(st, h, now)
    assert pe.status != "BLOCKED" and "dq_invalid" not in pe.blocked_by
    assert next(s for s in pe.signals if s.key == "liquidity_growth").fired is None     # curve UNKNOWN, not a signal


def test_young_curve_tokens_get_their_curve_refreshed(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "c.db"), keys=ApiKeys(), on_log=lambda m: None)
    st = curve_state(curve_info(None, None, age_s=60))
    st.info.pump_updated_at = None
    eng.tracked = {MINT: st}
    asked = []

    async def coin(m):
        asked.append(m)
        return parse_coin({"mint": m, "symbol": "TKN", "real_sol_reserves": 2_000_000_000,
                           "virtual_sol_reserves": 32_000_000_000})
    eng.pump.coin = coin
    asyncio.run(eng.refresh_curves(force=True))
    assert asked == [MINT] and st.info.real_sol_reserves == pytest.approx(2.0)   # not a "candidate" (MC $6K) before


def test_identity_conflict_still_excluded_with_a_valid_curve():
    st = curve_state(curve_info(25.0, 55.0))
    record_claim(st.identity, "pumpportal", "APEWIF", "")
    record_claim(st.identity, "dexscreener", "TSLAx", "")
    apply_identity(st)
    from scanner.pipeline import evaluate
    evaluate(st, Settings(), None, time.time(), SOL_USD)
    assert st.dq_status == INVALID and any(i.key == "identity_conflict" for i in st.quality.issues)


# ---------------------------------------------------------------- Trade Candidate: Early Signal TRUE is mandatory
class FakeEngine:
    def __init__(self, states):
        self.published = states
        self.sol_price = 150.0

    def feeds(self):
        return {"dexscreener": {"ok": True, "cooldown_s": 0}}


def trade_state(early):
    st = build_state(dex_pair(mint="TradeMint1111111111111111111111111111111"))
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    st.identity.helius_checked, st.holder_status = True, "ok"
    st.early = early
    return st


@pytest.mark.parametrize("early", [None, EarlySignal(None, None, None), EarlySignal(45, False, False, 2, groups_computable=6)])
def test_early_unknown_or_false_is_never_a_trade_candidate(early):
    st = trade_state(early)
    assert st.score.total >= 65                                       # high Opportunity / Momentum anyway
    b = PaperBot(FakeEngine([st]), TradingConfig(seed=3))
    b.exec.rng.random = lambda: 0.99
    b.tick()
    rec = b.decisions[st.mint]
    assert rec["decision"] != "TRADE" and not b.book.positions and b.trade_candidates() == []
    chk = next(c for c in rec["checks"] if c["key"] == "early_signal")
    assert chk["result"] in ("UNKNOWN", "FAIL")


def test_early_true_verified_vet_risk_trade_is_allowed():
    st = trade_state(EarlySignal(72, True, True, 5, groups_computable=6))
    b = PaperBot(FakeEngine([st]), TradingConfig(seed=3))
    b.exec.rng.random = lambda: 0.99
    b.tick()
    rec = b.decisions[st.mint]
    assert rec["decision"] == "TRADE" and rec["vet_passed"] and rec["risk_allowed"] is not False
    assert [c[0].mint for c in b.trade_candidates()] == [st.mint] and st.mint in b.book.positions


# ---------------------------------------------------------------- Helius credit budget (quota incident 2026-10-01)
def test_credit_meter_budget_pacing_and_quota_stop():
    from solana_data.rpc import CreditMeter
    m = CreditMeter(monthly_credits=30_000)                      # 1,000 / day
    assert m.daily_budget == 1_000 and m.allow(10)
    m.spend(990)
    assert not m.allow(20) and m.allow(10)
    midnight = time.time() - (time.time() % 86400)
    m2 = CreditMeter(30_000)
    m2.spend(400)
    assert not m2.paced_ok(midnight + 0.25 * 86400) and m2.paced_ok(midnight + 0.40 * 86400)   # 25 %+5 % < 40 %
    m2.exhausted_at = time.time()                                # Helius said "max usage reached"
    assert not m2.allow(1) and m2.state()["quota_exhausted"]


def test_das_respects_budget_and_marks_quota(tmp_path):
    import httpx
    from core.http import HttpClient
    from solana_data.rpc import SolanaRpc

    def handler(req):
        return httpx.Response(429, text="max usage reached")

    async def go():
        h = HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.0)
        r = SolanaRpc(h, "k")
        a = await r.das_get_asset(MINT)
        b = await r.das_get_asset(MINT)                          # quota known: not even sent
        await h.aclose()
        return r, a, b
    r, a, b = asyncio.run(go())
    assert a is None and b is None and r.credits.state()["quota_exhausted"] and r.credits.denied >= 1


def test_deep_scans_stop_when_budget_is_used(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "b.db"), keys=ApiKeys(helius="k"), on_log=lambda m: None)
    st = curve_state(curve_info(25.0, 55.0))
    st.watch = True
    eng.tracked = {MINT: st}
    eng.rpc.credits.used = eng.rpc.credits.daily_budget
    assert eng._deep_batch(time.time()) == []
    assert eng.feeds()["helius"]["credits"]["remaining"] == 0


# ---------------------------------------------------------------- progressive deep scan
def test_budget_skip_is_logged_and_listed(tmp_path):
    logs = []
    eng = ScannerEngine(Settings(), Database(tmp_path / "s.db"), keys=ApiKeys(helius="k"), on_log=logs.append)
    st = curve_state(curve_info(25.0, 55.0))
    st.watch = True
    eng.tracked = {MINT: st}
    eng.rpc.credits.used = eng.rpc.credits.daily_budget
    assert eng._deep_batch(time.time()) == []
    assert eng.deep_skipped and any("deep scan skipped" in m and "UNKNOWN" in m for m in logs)
    assert eng.feeds()["helius"]["deep_skipped"]


def test_deep_scale_rises_near_budget(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "d.db"), keys=ApiKeys(helius="k"), on_log=lambda m: None)
    midnight = time.time() - (time.time() % 86400)
    noon = midnight + 43_200
    c = eng.rpc.credits
    c.used = 0
    assert eng.deep_scale(noon) == 1.0
    c.used = int(c.daily_budget * 0.55 * 0.85)
    assert eng.deep_scale(noon) == 2.0
    c.used = int(c.daily_budget * 0.55 * 0.99)
    assert eng.deep_scale(noon) == 4.0


def test_stale_cached_holders_are_unknown_for_vet():
    from core.models import SourceStamp
    from trading import decision as D
    st = trade_state(EarlySignal(72, True, True, 5, groups_computable=6))
    st.stamps["holders"] = SourceStamp("helius_das", time.time() - 3600)
    v = {c.key: c.result for c in D.vet(st, TradingConfig()).checks}
    assert v["holders"] == "UNKNOWN" and v["top_holders"] == "UNKNOWN"
    st.stamps["holders"] = SourceStamp("helius_das", time.time() - 60)
    v = {c.key: c.result for c in D.vet(st, TradingConfig()).checks}
    assert v["holders"] == "PASS"


def test_bot_positions_stay_in_the_deep_pool(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "p.db"), keys=ApiKeys(), on_log=lambda m: None)
    st = trade_state(EarlySignal(72, True, True, 5, groups_computable=6))
    eng.tracked = {st.mint: st}
    eng.published = [st]
    bot = PaperBot(eng, TradingConfig(seed=3))
    bot.exec.rng.random = lambda: 0.99
    bot.tick()
    assert st.mint in bot.book.positions and "position" in eng.deep_reasons(st)


def test_credit_monitoring_hour_day_projection(monkeypatch):
    from solana_data.rpc import DEFAULT_MONTHLY_CREDITS, CreditMeter, SolanaRpc
    from core.http import HttpClient
    monkeypatch.delenv("HELIUS_MONTHLY_CREDITS", raising=False)
    assert SolanaRpc(HttpClient(), "k").credits.monthly == DEFAULT_MONTHLY_CREDITS == 10_000_000
    monkeypatch.setenv("HELIUS_MONTHLY_CREDITS", "3000000")
    assert SolanaRpc(HttpClient(), "k").credits.daily_budget == 100_000
    m = CreditMeter(10_000_000)
    now = time.time()
    m.started = now - 7200
    m.spend(10, now - 4000)                           # older than an hour: not in /hour (spends are chronological)
    for i in range(29, -1, -1):
        m.spend(10, now - 60 * i)                     # 300 credits in the last 30 min
    s = m.state()
    assert s["per_hour"] == 300 and s["per_day"] == 310 and s["calls_today"] == 31 and s["since_start"] == 310
    elapsed = min(now % 86400, now - m.started)
    expected = int(310 / elapsed * 86400 * 30) if elapsed >= 3600 else 300 * 24 * 30
    assert abs(s["projected_month"] - expected) <= expected * 0.01 + 1 and s["monthly_plan"] == 10_000_000
