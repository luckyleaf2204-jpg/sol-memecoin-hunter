"""PAPER trading bot: decision pipeline, risk engine, modelled execution, exits, NET P&L, backtest, API, safety."""
import json
import time

import pytest
from fastapi.testclient import TestClient

from conftest import build_state, dex_pair
from core.config import ApiKeys, Settings
from core.models import EarlySignal, LiquidityIntel, RiskFactor, RiskResult, WhaleIntel
from database.db import Database
from scanner.engine import ScannerEngine
from trading import decision as D
from trading.backtest import ASSUMPTIONS, backtest
from trading.book import PaperBook
from trading.bot import PaperBot
from trading.config import AUTO, CONFIRM, ModeNotAllowed, TradingConfig
from trading.execution import PaperExecutor, price_impact
from trading.exits import exit_signal
from trading.risk import RiskEngine
from validation.identity import apply_identity, record_claim
from web.app import STATIC, create_app

GOOD = "GoodMint1111111111111111111111111111111111"
TSLAX = "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB"
CODE = "bot-code"
SECRET = "HELIUS-SECRET-bot-0000-1111-2222-333333333333"
H = {"X-Access-Code": CODE}


def good_state(mint=GOOD, **pair):
    st = build_state(dex_pair(mint=mint, **pair))
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    st.identity.helius_checked = True
    st.holder_status = "ok"
    st.early = EarlySignal(72, True, True, 5, groups_computable=6)
    return st


class FakeEngine:
    def __init__(self, states=(), feeds=None):
        self.published = list(states)
        self.sol_price = 150.0
        self._feeds = feeds or {"dexscreener": {"ok": True, "cooldown_s": 0}}

    def feeds(self):
        if isinstance(self._feeds, Exception):
            raise self._feeds
        return self._feeds


def bot_with(states, feeds=None, **cfg):
    cfg.setdefault("seed", 3)
    b = PaperBot(FakeEngine(states, feeds), TradingConfig(**cfg))
    b.exec.rng.random = lambda: 0.99          # deterministic: no simulated tx failure unless a test wants one
    return b


# ---------------------------------------------------------------- config / safety
def test_only_paper_mode_exists():
    for m in (CONFIRM, AUTO):
        with pytest.raises(ModeNotAllowed):
            TradingConfig(mode=m)
        with pytest.raises(ModeNotAllowed):
            TradingConfig().set_mode(m)


def test_config_file_can_never_switch_on_auto(tmp_path):
    p = tmp_path / "trading.json"
    p.write_text(json.dumps({"mode": "AUTO", "max_open_positions": 3}), encoding="utf-8")
    cfg = TradingConfig.load(p)
    assert cfg.mode == "PAPER" and cfg.max_open_positions == 3


def test_no_private_key_or_signing_code_in_trading_package():
    import pathlib
    src = "".join(f.read_text(encoding="utf-8") for f in pathlib.Path(D.__file__).parent.glob("*.py")).lower()
    for word in ("private_key", "privatekey", "secret_key", "keypair", "mnemonic", "seed phrase", "sign_transaction",
                 "sendtransaction", "send_transaction", "solders", "base58.b58decode"):
        assert word not in src, word


# ---------------------------------------------------------------- vet / score / size
def test_good_token_vets_scores_and_sizes():
    st = good_state()
    v = D.vet(st, TradingConfig())
    assert D.vet_passed(v) and {c.key for c in v.checks if c.result == "N/A"} == {"x_alpha"}
    sc = D.score(st, v, TradingConfig())
    assert sc.decision == "TRADE" and sc.components["x_alpha"] is None and sc.components["smart_money"] is None
    assert sc.why and sc.invalidate
    sz = D.size(st, sc, TradingConfig(), equity=1000, cash=1000, exposure=0)
    assert 0 < sz.usd <= 50 and sz.capped_by == "max_position"


def test_tslax_apewif_identity_conflict_is_rejected():
    st = good_state(mint=TSLAX)
    record_claim(st.identity, "pumpportal", "APEWIF", "apewifdress")
    record_claim(st.identity, "dexscreener", "TSLAx", "Tesla xStock")
    apply_identity(st)
    assert st.identity.status == "CONFLICT"
    v = D.vet(st, TradingConfig())
    assert next(c for c in v.checks if c.key == "identity").result == "FAIL"
    assert D.score(st, v, TradingConfig()).decision == "REJECT"
    b = bot_with([st])
    b.tick()
    assert not b.book.positions and not any(a.kind == "BUY" for a in b.activity)


def test_unverified_identity_and_unknown_checks_block():
    st = good_state()
    st.identity.claims.clear()
    apply_identity(st)
    assert D.score(st, D.vet(st, TradingConfig()), TradingConfig()).decision == "REJECT"
    st2 = good_state()
    st2.identity.helius_checked = False                     # authorities never checked -> UNKNOWN -> no trade
    v = D.vet(st2, TradingConfig())
    assert not D.vet_passed(v) and next(c for c in v.checks if c.key == "authorities").result == "UNKNOWN"


def test_active_authorities_and_dangerous_token2022_fail():
    st = good_state()
    st.identity.mint_authority = "7pt9tkctJPK7PPNQJ77GKg8ZffSF6QxoMiCFYHxrtaCj"
    st.identity.extensions = ["permanent_delegate", "transfer_hook", "metadata"]
    v = {c.key: c.result for c in D.vet(st, TradingConfig()).checks}
    assert v["authorities"] == "FAIL" and v["token_2022"] == "FAIL"


def test_size_respects_every_cap():
    st = good_state(liq=1_000)                              # tiny pool: 2 % of liquidity = $20
    sc = D.score(st, D.vet(st, TradingConfig(min_liquidity_usd=500)), TradingConfig(min_liquidity_usd=500))
    assert D.size(st, sc, TradingConfig(), 1000, 1000, 0).usd == 20.0
    sc.confidence, sc.opportunity = 100, 100
    assert D.size(good_state(), sc, TradingConfig(), 1000, cash=12, exposure=0).usd == 12.0
    assert D.size(good_state(), sc, TradingConfig(), 1000, 1000, exposure=245).usd == 0.0   # < $10 room -> no trade


# ---------------------------------------------------------------- risk engine
def _rk(cfg=None, **kw):
    args = dict(mint=GOOD, usd=40, equity=1000, peak=1000, day_start=1000, open_positions=0, holding=False,
                in_cooldown=False, exposure=0, est_impact=0.002, feeds_ok=True)
    args.update(kw)
    return RiskEngine(cfg or TradingConfig()).check_entry(**args)


def test_risk_limits():
    assert _rk().allowed
    assert not _rk(TradingConfig(kill_switch=True)).allowed
    assert not _rk(open_positions=5).allowed
    assert not _rk(equity=940, day_start=1000).allowed                       # -6 % today
    assert not _rk(usd=60).allowed                                            # > 5 % position
    assert not _rk(exposure=230).allowed                                      # exposure > 25 %
    assert not _rk(est_impact=0.05).allowed                                   # slippage > 3 %
    assert not _rk(est_impact=None).allowed
    assert not _rk(holding=True).allowed and not _rk(in_cooldown=True).allowed


def test_drawdown_breach_engages_kill_switch():
    cfg = TradingConfig()
    d = _rk(cfg, equity=790, peak=1000, day_start=790)
    assert not d.allowed and cfg.kill_switch is True


def test_risk_engine_error_fails_closed():
    r = RiskEngine(TradingConfig())
    d = r.check_entry(mint=GOOD, usd="x", equity=1000, peak=1000, day_start=1000, open_positions=0, holding=False,
                      in_cooldown=False, exposure=0, est_impact=0.01, feeds_ok=True)
    assert not d.allowed and "risk engine error" in d.reasons[0]


def test_bot_does_not_buy_when_risk_engine_crashes(monkeypatch):
    b = bot_with([good_state()])
    monkeypatch.setattr(b.risk, "_check", lambda *a, **k: 1 / 0)
    b.tick()
    assert not b.book.positions and any(a.kind == "BLOCK" for a in b.activity)


# ---------------------------------------------------------------- execution model
def test_price_impact_and_fees_by_route():
    assert price_impact(1_000, 100_000) == pytest.approx(0.02)
    assert price_impact(1_000, None) is None
    ex = PaperExecutor(seed=1)
    ex.rng.random = lambda: 0.99
    amm = ex.buy(good_state(), 40, 150.0)
    assert amm.status == "FILLED" and amm.route == "PumpSwap AMM via Jupiter"
    assert amm.fee_usd == pytest.approx(40 * 0.003) and amm.network_fee_usd == pytest.approx(0.00011 * 150)
    assert amm.fill_price > amm.ref_price and amm.tokens == pytest.approx((40 - amm.fee_usd) / amm.fill_price)
    curve = good_state(dex="pumpfun")
    c = ex.buy(curve, 40, 150.0)
    assert c.route == "Pump.fun bonding curve" and c.fee_usd == pytest.approx(40 * 0.0125)


def test_slippage_tolerance_and_failed_tx_pay_network_fee():
    ex = PaperExecutor(seed=1, max_slippage_pct=3.0)
    big = ex.buy(good_state(liq=2_000), 500, 150.0)          # impact 50 %
    assert big.status == "FAILED" and "slippage" in big.reason and big.network_fee_usd > 0 and big.tokens == 0
    ex.rng.random = lambda: 0.0                              # simulated dropped transaction
    f = ex.buy(good_state(), 20, 150.0)
    assert f.status == "FAILED" and f.network_fee_usd > 0
    book = PaperBook(1000)
    book.record(f)
    assert book.failed == 1 and book.cash == pytest.approx(1000 - f.network_fee_usd)


def test_execution_is_deterministic():
    a = PaperExecutor(seed=9).buy(good_state(), 30, 150.0)
    b = PaperExecutor(seed=9).buy(good_state(), 30, 150.0)
    assert (a.status, a.fill_price, a.latency_ms) == (b.status, b.fill_price, b.latency_ms)


# ---------------------------------------------------------------- full paper loop, exits, P&L
def _open(b, st):
    b.tick()
    assert st.mint in b.book.positions, [a.text for a in b.activity]
    return b.book.positions[st.mint]


def _move(st, factor, **market):
    st.market.price_usd *= factor
    for k, v in market.items():
        setattr(st.market, k, v)
    st.stamps["market"].updated_at = time.time()


def test_paper_buy_then_stop_loss_net_pnl():
    st = good_state()
    b = bot_with([st])
    p = _open(b, st)
    assert any(a.kind == "BUY" for a in b.activity) and p.stop_price < p.entry_price
    _move(st, 0.80)
    b.tick()
    assert not b.book.positions and b.book.closed[0].exit_reason == "stop_loss"
    s = b.book.stats()
    assert s["net_pnl"] < 0 and s["losses"] == 1 and s["win_rate"] == 0.0
    assert s["gross_pnl"] == pytest.approx(s["net_pnl"] + s["fees"] + s["network_fees"] + s["slippage_cost"], abs=0.02)


def test_take_profit_partial_then_trailing_stop():
    st = good_state()
    b = bot_with([st])
    p = _open(b, st)
    _move(st, 1.35)
    b.tick()
    assert p.tp1_done and p.tokens < p.initial_tokens and p.stop_price >= p.entry_price
    _move(st, 1.3)                                            # new high
    b.tick()
    _move(st, 0.8)                                            # -20 % from the high > 15 % trailing
    b.tick()
    assert not b.book.positions and b.book.closed[0].exit_reason == "trailing_stop"
    s = b.book.stats()
    assert s["wins"] == 1 and s["net_pnl"] > 0 and s["profit_factor"] is None and s["avg_win"] > 0


def test_liquidity_shock_and_whale_dump_and_risk_spike_exit():
    for mutate, reason in ((lambda st: setattr(st, "liquidity_intel", LiquidityIntel(state="SHOCK")), "liquidity_collapse"),
                           (lambda st: setattr(st.market, "liquidity_usd", 10_000), "liquidity_collapse"),
                           (lambda st: setattr(st, "whale_intel", WhaleIntel(state="DISTRIBUTION")), "whale_dump"),
                           (lambda st: setattr(st, "risk", RiskResult(70, "HIGH")), "risk_spike"),
                           (lambda st: setattr(st, "risk", RiskResult(20, "LOW", [RiskFactor("dev_dump", 10, "rug")])), "risk_spike")):
        st = good_state()
        b = bot_with([st])
        _open(b, st)
        mutate(st)
        b.tick()
        assert b.book.closed and b.book.closed[0].exit_reason == reason, reason


def test_identity_conflict_after_entry_exits_and_no_price_means_no_guess():
    st = good_state()
    b = bot_with([st])
    p = _open(b, st)
    st.stamps["market"].updated_at = time.time() - 3600       # stale data: nothing is sold on a guess
    b.tick()
    assert st.mint in b.book.positions and p.stale
    st.stamps["market"].updated_at = time.time()
    record_claim(st.identity, "pumpportal", "OTHER", "")
    apply_identity(st)
    b.tick()
    assert b.book.closed and b.book.closed[0].exit_reason == "identity_conflict"


def test_exit_rules_unit():
    cfg = TradingConfig()
    st = good_state()
    b = bot_with([st])
    p = _open(b, st)
    now = time.time()
    assert exit_signal(p, st, p.entry_price * 1.81, cfg, now) == (1.0, "take_profit_2")
    st.lifecycle = "DECLINING"
    assert exit_signal(p, st, p.entry_price, cfg, now) == (1.0, "momentum_deterioration")
    st.lifecycle = "MOMENTUM"
    st.market.vol_5m = 1_000
    assert exit_signal(p, st, p.entry_price * 0.95, cfg, now, entry_vol=40_000) == (1.0, "volume_collapse")
    assert exit_signal(p, None, p.entry_price, cfg, now + 3 * 3600) == (1.0, "max_hold_time")


# ---------------------------------------------------------------- feeds / 429 / kill switch
def test_api_429_or_feed_loss_never_makes_the_bot_buy():
    for feeds in ({"dexscreener": {"ok": False, "cooldown_s": 0, "last_status": 429, "last_error": "HTTP 429"}},
                  {"dexscreener": {"ok": True, "cooldown_s": 40.0}},
                  RuntimeError("feed state unavailable")):
        b = bot_with([good_state()], feeds=feeds)
        b.tick()
        assert not b.book.positions and b.modules["risk"].status == "BLOCKED", feeds


def test_stale_market_data_blocks_entries():
    st = good_state()
    st.stamps["market"].updated_at = time.time() - 120
    b = bot_with([st])
    b.tick()
    assert not b.book.positions


def test_kill_switch_blocks_entries_but_exits_still_work():
    st = good_state()
    b = bot_with([st])
    _open(b, st)
    b.set_kill(True)
    other = good_state(mint="Other111111111111111111111111111111111111")
    b.engine.published.append(other)
    _move(st, 0.8)
    b.tick()
    assert other.mint not in b.book.positions and b.book.closed[0].exit_reason == "stop_loss"
    assert b.modules["risk"].status == "BLOCKED" and any(a.kind == "KILL" for a in b.activity)


def test_bot_state_persists_and_reloads(tmp_path):
    st = good_state()
    b = PaperBot(FakeEngine([st]), TradingConfig(seed=3), state_path=tmp_path / "paper.json")
    b.exec.rng.random = lambda: 0.99
    b.tick()
    b2 = PaperBot(FakeEngine([st]), TradingConfig(), state_path=tmp_path / "paper.json")
    assert set(b2.book.positions) == {GOOD} and b2.book.cash == pytest.approx(b.book.cash)


# ---------------------------------------------------------------- backtest
def _row(ts, price, liq=40_000, **kw):
    r = {"ts": ts, "mint": GOOD, "symbol": "BT", "price": price, "mc": price * 1e9, "liquidity": liq, "vol_5m": 40_000,
         "vol_1h": 120_000, "buys_5m": 300, "sells_5m": 150, "holders": 1200, "top10_pct": 12.0, "score": 80,
         "risk": 10, "dq": 90, "dq_status": "VALID", "early_signal": 72, "is_early": 1, "lifecycle": "MOMENTUM",
         "subscores": json.dumps({"momentum": 82, "holder": 100, "liquidity": 73, "onchain": 91}), "pc_5m": 5.0}
    r.update(kw)
    return r


def test_backtest_replays_snapshots_through_the_same_bot():
    t0 = time.time() - 3600
    prices = [0.0002, 0.000205, 0.00021, 0.00028, 0.00030, 0.00031, 0.00025, 0.00024]
    rows = [_row(t0 + 20 * i, p) for i, p in enumerate(prices)]
    res = backtest(rows, TradingConfig(seed=1))
    assert res["frames"] == len(prices) and res["assumptions"] == ASSUMPTIONS
    assert res["executions"] >= 2 and res["trades"], res
    assert res["stats"]["closed"] == len(res["trades"])


def test_backtest_liquidity_shock_exits():
    t0 = time.time() - 3600
    rows = [_row(t0, 0.0002), _row(t0 + 20, 0.00021), _row(t0 + 40, 0.00019, liq=8_000)]
    res = backtest(rows, TradingConfig(seed=1))
    assert [t["exit"] for t in res["trades"]] == ["liquidity_collapse"] and res["trades"][0]["net"] < 0


# ---------------------------------------------------------------- API / UI
@pytest.fixture
def web(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "w.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    st = good_state()
    eng.tracked = {st.mint: st}
    eng.published = [st]
    bot = PaperBot(eng, TradingConfig(seed=3))
    bot.exec.rng.random = lambda: 0.99
    bot.tick()
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE, bot=bot)) as c:
        yield c, bot


def test_bot_api_dashboard_payload(web):
    c, bot = web
    r = c.get("/api/bot", headers=H)
    d = r.json()
    assert d["mode"] == "PAPER" and d["allowed_modes"] == ["PAPER"]
    assert [m["key"] for m in d["modules"]] == ["scan", "vet", "size", "risk", "fills", "book"]
    assert {m["status"] for m in d["modules"]} <= {"RUN", "READY", "BLOCKED", "ERROR"}
    assert d["positions"] and d["positions"][0]["symbol"] and d["activity"][0]["kind"] == "BUY"
    assert d["sources"]["x_alpha"] == "NOT_AVAILABLE" and d["sources"]["smart_money"] == "NOT_AVAILABLE"
    assert SECRET not in r.text and "api-key" not in r.text
    vet = c.get("/api/bot/module/vet", headers=H).json()
    assert vet["items"][0]["decision"] == "TRADE" and vet["items"][0]["checks"]
    assert c.get("/api/bot/module/nope", headers=H).status_code == 404
    assert c.get(f"/api/bot/decision/{GOOD}", headers=H).json()["why"]
    assert c.get("/api/bot", headers={}).status_code == 401


def test_mode_auto_is_refused_and_kill_switch_toggles(web):
    c, bot = web
    for m in ("AUTO", "CONFIRM", "LIVE"):
        assert c.post("/api/bot/mode", headers=H, json={"mode": m}).status_code == 403
    assert c.post("/api/bot/kill", headers=H, json={"engaged": True}).json() == {"kill_switch": True}
    assert bot.cfg.kill_switch is True
    assert c.post("/api/bot/kill", headers=H, json={"engaged": "yes"}).status_code == 400


def test_bot_ui_keys_exist_and_no_secrets():
    import re
    from i18n import load
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    keys = {f"web.bot.mod.{k}" for k in ("scan", "vet", "size", "risk", "fills", "book")}
    keys |= {f"web.bot.chk.{c.key}" for c in D.vet(good_state(), TradingConfig()).checks}
    keys |= set(re.findall(r'''t\(\s*["'](web\.bot\.[A-Za-z0-9_.]+)["']''', js))
    keys = {k for k in keys if not k.endswith(".")}               # runtime prefixes, enumerated above
    for lang in ("vi", "en"):
        assert not [k for k in keys if k not in load(lang)], lang
    for word in ("privateKey", "secretKey", "mnemonic", "signTransaction", "sendTransaction"):
        assert word not in js
