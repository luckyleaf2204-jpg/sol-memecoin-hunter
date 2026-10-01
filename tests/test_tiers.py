"""4 early tiers: ⚡ Pre-Early · 👀 Early Watch · 🎯 Early Signal (D1–D8, unchanged) · 🟢 Trade Candidate."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import build_state, dex_pair, default_info
from core.config import ApiKeys, Settings
from core.models import DataQuality, EarlySignal, Issue, RiskFactor, RiskResult, TokenState
from database.db import Database
from intel.early_watch import TOP_N, compute_early_watch, select_watch
from scanner.engine import ScannerEngine
from scoring.ranking import rank_early
from trading.bot import PaperBot
from trading.config import TradingConfig
from validation.identity import apply_identity, record_claim
from web.app import create_app

CODE = "tier-code"
SECRET = "HELIUS-SECRET-tiers-0000-1111-2222-333333333333"
H = {"X-Access-Code": CODE}
GOOD = "GoodMint1111111111111111111111111111111111"
TSLAX = "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB"


def verified(st, symbol="TKN"):
    record_claim(st.identity, "dexscreener", symbol, "")
    apply_identity(st)
    st.identity.helius_checked = True
    st.holder_status = "ok"
    return st


def aged(mint, minutes, **pair):
    return verified(build_state(dex_pair(mint=mint, **pair), info=default_info(mint, age_s=minutes * 60)))


# ---------------------------------------------------------------- 👀 Early Watch
def test_early_watch_window_and_rank():
    st = aged("W1", 6)
    w = compute_early_watch(st)
    assert w.eligible and w.rank is not None and w.excluded_by == []
    assert set(w.components) == {"momentum", "opportunity", "risk"}
    base = (40 * w.components["momentum"] + 35 * w.components["opportunity"] + 25 * w.components["risk"]) / 100
    assert w.rank == pytest.approx(round(base * (0.5 + 0.5 * w.confidence / 100), 1))
    for minutes in (2, 12):
        assert not compute_early_watch(aged("W2", minutes)).eligible
    st.info.created_at, st.market.pair_created_at = None, None
    assert not compute_early_watch(st).eligible                       # unknown age is never guessed


def test_early_watch_partial_is_allowed_but_gaps_are_listed():
    st = aged("W3", 5)
    st.holders, st.dev = None, None
    st.holder_status = "pending"
    w = compute_early_watch(st)
    assert w.rank is not None and set(w.missing) >= {"holders", "dev_verified"} and w.confidence < 100
    full = compute_early_watch(aged("W4", 5))
    assert full.confidence == 100 and full.missing == []
    st2 = aged("W5", 5)
    st2.quality = DataQuality(30, "INVALID", [Issue("critical", "market", "no_market")])
    st2.score = None
    w2 = compute_early_watch(st2)
    assert "dq_invalid" not in w2.excluded_by and w2.components["opportunity"] is None and "opportunity" in w2.missing


def test_early_watch_exclusions():
    c = aged(TSLAX, 5, mc=353_569, fdv=353_569, price="0.000353569")
    record_claim(c.identity, "pumpportal", "APEWIF", "")
    apply_identity(c)
    assert compute_early_watch(c).excluded_by == ["identity_conflict"] and compute_early_watch(c).rank is None
    r = aged("W6", 5)
    r.risk = RiskResult(30, "LOW", [RiskFactor("dev_dump", 10, "rug")])
    assert "rug_flag" in compute_early_watch(r).excluded_by
    bad = aged("W7", 5, liq=7.1e-07)
    assert "dq_invalid" in compute_early_watch(bad).excluded_by
    h = aged("W8", 5)
    h.holder_status = "invalid"
    assert "holder_anomaly" in compute_early_watch(h).excluded_by


def test_select_watch_caps_at_top_50_ranked():
    sts = []
    for i in range(70):
        st = aged(f"S{i:03d}", 3 + (i % 7))
        st.early_watch = compute_early_watch(st)
        st.early_watch.rank = float(i)
        sts.append(st)
    out = select_watch(sts)
    assert len(out) == TOP_N == 50 and out[0].early_watch.rank == 69.0 and out[-1].early_watch.rank == 20.0


# ---------------------------------------------------------------- 🎯 Early Signal untouched
def test_early_signal_tier_is_rank_early_unchanged():
    from scenarios import SCENARIOS, run
    got = {sc.name: run(sc).early for sc in SCENARIOS}
    assert [(got[k].strength, got[k].is_early) for k in ("S1", "S2", "S3", "S4")] == \
           [(74, True), (14, False), (47, False), (65, True)]
    bad = build_state(dex_pair(mint="BADX", liq=7.1e-07))
    good = build_state(dex_pair(mint="GOODX"))
    assert rank_early([bad, good]) == [s for s in rank_early([bad, good])]   # same function, same rule
    assert bad not in rank_early([bad, good])                                 # INVALID never listed


# ---------------------------------------------------------------- 🟢 Trade Candidate
class FakeEngine:
    def __init__(self, states, feeds=None):
        self.published = states
        self.sol_price = 150.0
        self._feeds = feeds or {"dexscreener": {"ok": True, "cooldown_s": 0}}

    def feeds(self):
        return self._feeds


def bot_for(states, feeds=None):
    b = PaperBot(FakeEngine(states, feeds), TradingConfig(seed=3))
    b.exec.rng.random = lambda: 0.99
    return b


def good_trade(mint=GOOD):
    st = verified(build_state(dex_pair(mint=mint)))
    st.early = EarlySignal(72, True, True, 5, groups_computable=6)
    return st


def test_verified_vetted_risk_ok_token_is_a_trade_candidate():
    st = good_trade()
    b = bot_for([st])
    b.cfg.enabled = True
    b.tick()
    cands = b.trade_candidates()
    assert [c[0].mint for c in cands] == [GOOD] and cands[0][2] is True     # bought -> held candidate
    rec = cands[0][1]
    assert rec["decision"] == "TRADE" and rec["vet_passed"] and rec["why"] and rec["invalidate"]


def test_tslax_apewif_and_unverified_never_become_candidates():
    c = good_trade(TSLAX)
    record_claim(c.identity, "pumpportal", "APEWIF", "apewifdress")
    record_claim(c.identity, "dexscreener", "TSLAx", "Tesla xStock")
    apply_identity(c)
    u = good_trade("Unverified111111111111111111111111111111111")
    u.identity.claims.clear()
    apply_identity(u)
    b = bot_for([c, u])
    b.tick()
    assert b.trade_candidates() == [] and not b.book.positions


def test_api_429_or_feed_loss_empties_trade_candidates():
    for feeds in ({"dexscreener": {"ok": False, "cooldown_s": 0, "last_status": 429, "last_error": "HTTP 429"}},
                  {"dexscreener": {"ok": True, "cooldown_s": 30}}):
        b = bot_for([good_trade()], feeds)
        b.tick()
        assert b.trade_candidates() == [] and not b.book.positions


def test_missing_holder_data_is_never_a_trade_candidate():
    st = good_trade()
    st.holders, st.holder_status = None, "pending"
    b = bot_for([st])
    b.tick()
    assert b.trade_candidates() == []
    assert b.decisions[GOOD]["decision"] in ("WATCH", "REJECT")


def test_stale_decisions_expire():
    st = good_trade()
    b = bot_for([st])
    b.tick(now=time.time() - 120)
    st.stamps["market"].updated_at = time.time()
    assert b.trade_candidates() == []


def test_kill_switch_removes_candidates():
    st = good_trade()
    b = bot_for([st])
    b.set_kill(True)
    b.tick()
    assert b.trade_candidates() == []


# ---------------------------------------------------------------- API
@pytest.fixture
def web(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "t.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    pre = verified(build_state(dex_pair(mint="PreMint111111111111111111111111111111111"),
                               info=default_info("PreMint111111111111111111111111111111111", age_s=90)))
    watch = aged("WatchMint11111111111111111111111111111111", 6)
    trade = good_trade()
    for st in (pre, watch, trade):
        eng._evaluate(st)
        if st is trade:
            st.early = EarlySignal(72, True, True, 5, groups_computable=6)
    eng.tracked = {s.mint: s for s in (pre, watch, trade)}
    eng.published = [pre, watch, trade]
    bot = PaperBot(eng, TradingConfig(seed=3))
    bot.exec.rng.random = lambda: 0.99
    bot.tick()
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE, bot=bot)) as c:
        yield c


def test_tier_api(web):
    c = web
    pre = c.get("/api/early/pre_early", headers=H).json()
    assert pre["items"] and pre["items"][0]["pre_early"]["status"] in ("PRE_EARLY", "NOT_YET", "UNKNOWN", "BLOCKED")
    assert pre["items"][0]["pre_early"]["data"]                          # data level always labelled
    w = c.get("/api/early/watch", headers=H).json()
    assert [x["mint"] for x in w["items"]] == ["WatchMint11111111111111111111111111111111"]
    ew = w["items"][0]["early_watch"]
    assert ew["rank"] is not None and ew["data"].startswith("dữ liệu") and "missing" in ew
    sig = c.get("/api/early/signal", headers=H).json()
    assert all(x["dq"] != "INVALID" for x in sig["items"])
    tr = c.get("/api/early/trade", headers=H)
    items = tr.json()["items"]
    assert GOOD in [x["mint"] for x in items]                         # the 6-min watch token may qualify too
    assert all(x["identity"] == "VERIFIED" and x["trade"]["opportunity"] is not None and x["trade"]["why"] for x in items)
    assert "PreMint111111111111111111111111111111111" not in [x["mint"] for x in items]
    assert c.get("/api/early/bogus", headers=H).status_code == 404
    assert c.get("/api/early/trade").status_code == 401
    for u in ("/api/early/pre_early", "/api/early/watch", "/api/early/signal", "/api/early/trade"):
        body = c.get(u, headers=H).text
        assert SECRET not in body and "api-key" not in body


def test_tier_ui_keys_exist():
    import re
    from i18n import load
    from web.app import STATIC
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = {f"web.tier.{p}{t}" for p in ("", "rule.", "empty.") for t in ("pre_early", "watch", "signal", "trade")}
    keys |= {f"web.sort.{k}" for k in ("tier", "mc_high", "mc_low", "age", "momentum", "opp", "confidence", "risk_low",
                                        "vol_rise", "buy_share")}
    keys |= {f"web.ew.miss.{k}" for k in ("market_fresh", "identity_verified", "holders", "dev_verified", "momentum", "opportunity")}
    keys |= {f"web.ew.ex.{k}" for k in ("identity_conflict", "dq_invalid", "rug_flag", "holder_anomaly")}
    keys |= {k for k in re.findall(r'''t\(\s*["']((?:web\.ew|web\.trade|web\.tier)\.[A-Za-z0-9_.]+)["']''', js) if not k.endswith(".")}
    for lang in ("vi", "en"):
        assert not [k for k in keys if k not in load(lang)], lang
