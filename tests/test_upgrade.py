"""web-2 upgrade: tiered refresh scheduler, smart priority, MC journey, 4 home groups, resilience.

Also proves that refreshing faster does NOT change Early Signal (D1–D8) results: the history keeps the
same ~20s anchor points, so a 5s-cadence feed and a 20s-cadence feed give identical Early Signal output.
"""
import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SOL_USD, build_series, build_state, dex_pair, default_info, good_holders
from core.config import ApiKeys, Settings
from core.http import COOLDOWN_BASE_S, HttpClient
from core.models import EarlySignal, Issue, McTrack, RiskFactor, RiskResult, TokenInfo, TokenState
from database.db import Database
from dex.dexscreener import parse_pair
from history.store import POINT_SPACING_S, HolderSnap, TokenHistory
from intel.mc_track import anchor_at_discovery, confirm_quote, compact_path, compute_trend, mc_scenario, update_mc_track
from scanner import scheduler as sch
from scanner.engine import ScannerEngine
from scanner.pipeline import ingest_market
from scoring.groups import classify_group
from web.app import STATIC, create_app

SECRET = "HELIUS-SECRET-aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
CODE = "code-xyz"
H = {"X-Access-Code": CODE}


# ---------------------------------------------------------------- scheduler
class FakeClock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def test_tier_clock_cadence_backoff_and_measured_interval():
    c = FakeClock()
    tc = sch.TierClock({"market": 10.0}, clock=c)
    assert tc.due("market")
    for _ in range(3):
        tc.start("market")
        c.t += 0.5                           # the round itself takes 0.5s
        tc.done("market", ok=True)
        assert not tc.due("market")
        c.t += 9.5                           # next start exactly 10s after the previous start
        assert tc.due("market")
    assert tc.stats()["market"]["measured_s"] == 10.0
    # failures back off exponentially: 20s, 40s, ... capped
    tc.start("market")
    assert tc.done("market", ok=False) == 20.0
    tc.start("market")
    assert tc.done("market", ok=False) == 40.0
    for _ in range(10):
        tc.start("market")
        d = tc.done("market", ok=False)
    assert d == sch.MAX_BACKOFF_S
    tc.start("market")
    assert tc.done("market", ok=True) == 10.0 and tc.tiers["market"].failures == 0


def _st(mint, *, age_s=7200, vol5=5_000, txns=(40, 30), pc5=1.0, watch=False, seen_ago=3600):
    st = build_state(dex_pair(mint=mint, vol=(vol5, 60_000), m5=txns, pc5=pc5),
                     info=default_info(mint, age_s=age_s))
    st.info.discovered_at = time.time() - seen_ago
    st.watch = watch
    return st


def test_priority_classes_and_intervals():
    now = time.time()
    new = _st("NEW", age_s=120, seen_ago=100)
    rising = _st("RISE", pc5=35.0)
    normal = _st("NORM")
    quiet = _st("QUIET", vol5=100, txns=(2, 1), pc5=0.0)
    starred = _st("STAR", vol5=100, txns=(2, 1), watch=True)
    assert sch.priority_class(new, now) == "hot" and "new" in new.priority_reasons
    assert sch.priority_class(rising, now) == "hot" and "mc_rising" in rising.priority_reasons
    assert sch.priority_class(normal, now) == "normal"
    assert sch.priority_class(quiet, now) == "quiet"
    assert sch.priority_class(starred, now) == "hot"
    for st in (new, rising, normal, quiet, starred):
        st.refreshed["market"] = now - 6            # fetched 6s ago
    due = sch.market_due([new, rising, normal, quiet, starred], now)
    assert {s.mint for s in due} == {"NEW", "RISE", "STAR"}          # hot = 5s; normal waits for 10s, quiet 30s
    assert due[0].mint == "STAR" and due[1].mint == "NEW"             # priority order: starred, new, MC rising
    for st in (normal, quiet):
        st.refreshed["market"] = now - 11
    due = {s.mint for s in sch.market_due([normal, quiet], now)}
    assert due == {"NORM"}
    quiet.refreshed["market"] = now - 31
    assert {s.mint for s in sch.market_due([quiet], now)} == {"QUIET"}


def test_deep_due_prioritises_hot_and_respects_limit():
    now = time.time()
    hot = [_st(f"H{i}", pc5=40.0) for i in range(3)]
    cold = [_st(f"C{i}") for i in range(5)]
    for st in hot + cold:
        st.refreshed["holders"] = now - 35          # hot every 30s -> due; normal every 60s -> not yet
    assert [s.mint for s in sch.deep_due(cold + hot, now, 10)] == ["H0", "H1", "H2"]
    for st in cold:
        st.refreshed["holders"] = now - 61
    todo = sch.deep_due(cold + hot, now, 4)
    assert len(todo) == 4 and [s.mint for s in todo[:3]] == ["H0", "H1", "H2"]


# ---------------------------------------------------------------- MC journey
def _ingest(st, mc, ts, pair="P1", critical=False, dex="pumpswap"):
    p = parse_pair(dex_pair(mint=st.mint, mc=mc, fdv=mc, price=str(mc / 1e9), pair=pair, dex=dex))
    ingest_market(st, p, {}, SOL_USD, None, ts)
    if critical:
        st.market_issues = [Issue("critical", "market_cap", "x")]
    return update_mc_track(st, ts)


def _mcs(tr):
    return [p[1] for p in tr.path]


def test_initial_mc_saved_once_never_overwritten_and_history():
    st = TokenState(info=default_info("MCT"))
    t0 = time.time() - 600
    _ingest(st, 8_000, t0 - 30, critical=True)                 # invalid observation -> NOT the initial MC
    assert st.mc_track.initial_mc is None
    tr = _ingest(st, 8_000, t0)
    assert tr.initial_mc == 8_000 and tr.initial_ts == t0 and tr.initial_source == "DexScreener"
    for i, mc in enumerate((9_000, 25_000, 20_000, 67_000, 125_000, 110_000)):
        _ingest(st, mc, t0 + 60 * (i + 1))
    tr = st.mc_track
    assert tr.initial_mc == 8_000 and tr.initial_ts == t0        # never replaced by the current MC
    assert tr.ath_mc == 125_000
    assert _mcs(tr) == [8_000, 25_000, 67_000, 125_000]          # >= 30 % moves only
    assert tr.path[0][2] == "initial"
    assert tr.gain_x(110_000) == pytest.approx(13.75)
    assert [m for m, _ in compact_path(tr, 110_000)] == [8_000, 25_000, 67_000, 125_000, 110_000]


def test_initial_mc_anchored_at_discovery_from_source():
    now = time.time()
    # PumpPortal create event: marketCapSol is in QUOTE units -> pending until the quote is confirmed SOL
    st = TokenState(info=TokenInfo(mint="PP1", discovery_mc_sol=30.0, discovered_at=now))
    assert not anchor_at_discovery(st, 150.0) and st.mc_track.pending is not None
    assert confirm_quote(st, True)                              # Pump.fun / DexScreener: quote is SOL
    assert st.mc_track.initial_mc == pytest.approx(4_500) and st.mc_track.initial_ts == now   # discovery time
    assert "PumpPortal" in st.mc_track.initial_source
    # later DexScreener MC (and any other source) never replaces it
    _ingest(st, 60_000, now + 120)
    assert not anchor_at_discovery(st, 999.0)
    assert st.mc_track.initial_mc == pytest.approx(4_500) and st.mc_track.ath_mc == 60_000
    # Pump.fun usd_market_cap when no SOL price / no PumpPortal value
    st2 = TokenState(info=TokenInfo(mint="PF1", pump_usd_mc=12_345.0, discovered_at=now))
    assert anchor_at_discovery(st2, None) and st2.mc_track.initial_mc == 12_345.0
    assert "Pump.fun" in st2.mc_track.initial_source
    # curve quoted in another token (e.g. TSLAx): marketCapSol × SOL price would be wrong -> discarded,
    # the anchor then comes from the first validated USD market cap (DexScreener)
    st5 = TokenState(info=TokenInfo(mint="QT1", discovery_mc_sol=11.3, discovered_at=now))
    anchor_at_discovery(st5, 150.0)
    assert not confirm_quote(st5, False) and st5.mc_track.pending is None and st5.mc_track.initial_mc is None
    _ingest(st5, 9_000, now + 30)
    assert st5.mc_track.initial_mc == 9_000 and st5.mc_track.initial_source == "DexScreener"
    # no sourced value -> no anchor yet (never invented); implausible values rejected
    st3 = TokenState(info=TokenInfo(mint="NO1", discovery_mc_sol=30.0, discovered_at=now))
    assert not anchor_at_discovery(st3, None) and st3.mc_track.initial_mc is None
    st4 = TokenState(info=TokenInfo(mint="BAD1", pump_usd_mc=12.0, discovered_at=now))
    assert not anchor_at_discovery(st4, None) and st4.mc_track.initial_mc is None


def test_engine_anchors_on_discovery_and_persists_immediately(tmp_path):
    db = Database(tmp_path / "a.db")
    eng = ScannerEngine(Settings(), db, keys=ApiKeys(), on_log=lambda m: None)
    eng.sol_price = 100.0

    async def latest(n):
        return [TokenInfo(mint="DISC1", created_at=time.time() - 30, discovery_mc_sol=40.0, sources={"pumpportal"}),
                TokenInfo(mint="DISC2", created_at=time.time() - 30, pump_usd_mc=7_000.0, sources={"pumpfun"},
                          symbol="TKN")]

    async def none(*a, **k):
        return []
    eng.pump.latest, eng.pump.recently_traded = latest, none
    asyncio.run(eng.discover(full=True))
    assert eng.tracked["DISC1"].mc_track.initial_mc is None and eng.tracked["DISC1"].mc_track.pending
    saved = db.load_mc_tracks()                                  # written in the same round, not 20s later
    assert saved["DISC2"].initial_mc == 7_000.0 and "DISC1" not in saved
    # Pump.fun record of DISC1 arrives: quote is SOL -> the DISCOVERY value is committed and saved at once
    eng._merge_info(eng.tracked["DISC1"], TokenInfo(mint="DISC1", quote_mint="So11111111111111111111111111111111111111112",
                                                    sources={"pumpfun"}))
    eng._save_mc()
    assert db.load_mc_tracks()["DISC1"].initial_mc == pytest.approx(4_000)


def test_migrate_graduate_keeps_anchor_and_continuous_history(tmp_path):
    st = TokenState(info=default_info("GRAD"))
    t0 = time.time() - 900
    _ingest(st, 20_000, t0, pair="CURVE1", dex="pumpfun")
    _ingest(st, 60_000, t0 + 300, pair="CURVE1", dex="pumpfun")
    tr = _ingest(st, 66_000, t0 + 360, pair="AMM1")              # graduation: +10 % only, still recorded
    assert tr.initial_mc == 20_000 and tr.initial_ts == t0       # anchor untouched by the new pair
    assert [(p[1], p[2]) for p in tr.path] == [(20_000, "initial"), (60_000, ""), (66_000, "migrate")]
    assert tr.migrations == [{"ts": t0 + 360, "from": "CURVE1", "to": "AMM1", "mc_before": 60_000, "mc_after": 66_000}]
    _ingest(st, 70_000, t0 + 420, pair="AMM1")                   # same pair again: no new migration
    assert len(tr.migrations) == 1 and tr.gain_x(70_000) == pytest.approx(3.5)
    assert ("⇄" in "".join(("⇄" if t == "migrate" else "") for _, t in compact_path(tr, 70_000)))
    # survives a restart with the pair memory, so a restart is not mistaken for a migration
    db = Database(tmp_path / "g.db")
    db.save_mc_tracks([("GRAD", tr)])
    got = db.load_mc_tracks()["GRAD"]
    assert got.initial_mc == 20_000 and got.last_pair == "AMM1" and got.migrations[0]["to"] == "AMM1"
    assert [p[2] for p in got.path] == ["initial", "", "migrate"]
    st2 = TokenState(info=default_info("GRAD"), mc_track=got)
    _ingest(st2, 71_000, t0 + 480, pair="AMM1")
    assert len(st2.mc_track.migrations) == 1


def test_mc_history_is_bounded_and_keeps_anchor():
    st = TokenState(info=default_info("LONG"))
    t0 = time.time() - 100_000
    mc = 5_000.0
    for i in range(120):
        mc = mc * (1.5 if i % 2 == 0 else 0.6)
        _ingest(st, max(mc, 2_000), t0 + 60 * i)
    tr = st.mc_track
    assert len(tr.path) <= 40 and tr.path[0][2] == "initial" and tr.initial_mc == tr.path[0][1]


def test_mc_journey_survives_restart_and_rediscovery(tmp_path):
    db = Database(tmp_path / "m.db")
    now = time.time()
    t = McTrack(first_seen=now - 100, initial_mc=8_000, initial_ts=now - 90, initial_source="DexScreener",
                ath_mc=50_000, ath_ts=now - 10, path=[(now - 90, 8_000, "initial"), (now - 10, 50_000, "")])
    db.save_mc_tracks([("RST", t)])
    # a later save with a different "initial" must not overwrite the stored one
    t2 = McTrack(first_seen=now, initial_mc=99_000, initial_ts=now, ath_mc=60_000, ath_ts=now, path=[(now, 60_000, "")])
    db.save_mc_tracks([("RST", t2)])
    got = db.load_mc_tracks()["RST"]
    assert got.initial_mc == 8_000 and got.initial_ts == pytest.approx(now - 90) and got.ath_mc == 60_000
    eng = ScannerEngine(Settings(), db, keys=ApiKeys(), on_log=lambda m: None)
    eng.sol_price = 100.0
    assert eng._add(TokenInfo(mint="RST", created_at=now - 200, discovery_mc_sol=500.0))   # restart
    assert eng.tracked["RST"].mc_track.initial_mc == 8_000       # discovery value does NOT replace the anchor
    # pruned and discovered again -> same anchor from the in-memory stash
    eng.tracked["RST"].info.created_at = now - 10 * 3600
    eng.prune()
    assert "RST" not in eng.tracked
    assert eng._add(TokenInfo(mint="RST", created_at=now - 60, pump_usd_mc=77_000.0))
    assert eng.tracked["RST"].mc_track.initial_mc == 8_000


def test_old_two_value_path_rows_still_load(tmp_path):
    db = Database(tmp_path / "o.db")
    db.conn.execute("INSERT INTO mc_track (mint, first_seen, initial_mc, initial_ts, initial_source, ath_mc, ath_ts, path)"
                    " VALUES ('OLD', 1, 5000, 1, 'DexScreener', 9000, 2, '[[1, 5000], [2, 9000]]')")
    db.conn.commit()
    got = db.load_mc_tracks()["OLD"]
    assert got.path == [(1.0, 5000.0, ""), (2.0, 9000.0, "")] and got.migrations == []


# ---------------------------------------------------------------- MC Scenario / Reference
def test_mc_scenario_is_reference_only_and_unknown_without_data():
    st = TokenState(info=default_info("SCN"))
    assert mc_scenario(st) is None                               # no market -> "Chưa đủ dữ liệu"
    st = build_state(dex_pair(mint="SCN", mc=120_000, fdv=120_000, price="0.00012"))
    update_mc_track(st)
    sc = mc_scenario(st)
    assert sc["kind"] == "reference"
    assert sc["basis"]["mc"] == 120_000 and "DexScreener" in sc["basis"]["source"]
    assert [lv["mc"] for lv in sc["levels"]] == [200e3, 300e3, 500e3]
    assert all(lv["type"] == "round_level" for lv in sc["levels"])
    assert sc["levels"][0]["multiple"] == pytest.approx(200e3 / 120e3, rel=1e-2)
    assert sc["refs"] == []                                      # nothing sourced above current MC -> no refs
    for lv in sc["levels"]:
        assert set(lv) == {"mc", "multiple", "reached_before", "type"}   # no probability / target / estimate
    st.dev.history_verified, st.dev.prev_best_ath = True, 1_200_000
    ref = next(r for r in mc_scenario(st)["refs"] if r["key"] == "dev_best")
    assert ref["source"] and ref["mc"] == 1_200_000
    st.dev.history_verified = False                              # unverified history is never used
    assert not any(r["key"] == "dev_best" for r in mc_scenario(st)["refs"])
    st.info.ath_usd_mc = 400_000                                 # Pump.fun ATH: sourced reference + "reached"
    sc = mc_scenario(st)
    assert any(r["key"] == "pump_ath" and "Pump.fun" in r["source"] for r in sc["refs"])
    assert [lv["reached_before"] for lv in sc["levels"]] == [True, True, False]


def test_mc_scenario_none_for_invalid_or_stale_data():
    bad = build_state(dex_pair(mint="SBAD", liq=7.1e-07))
    assert bad.dq_status == "INVALID" and mc_scenario(bad) is None
    st = build_state(dex_pair(mint="SMAL", mc=500, fdv=500, price="0.0000005"))      # implausible MC
    assert mc_scenario(st) is None


def test_scenario_labels_in_ui_say_reference_not_prediction():
    from i18n import load
    vi = load("vi")
    assert vi["web.pf.scenario"] == "MC Scenario / Reference"
    assert "KHÔNG phải dự đoán" in vi["web.scn.disclaimer"]
    assert vi["web.nodata"] == "Chưa có dữ liệu"
    js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert 'scenarioHtml' in js and 't("web.nodata")' in js


# ---------------------------------------------------------------- HTTP resilience
def _client(handler, **kw):
    return HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.001, **kw)


def test_timeout_returns_none_and_trips_cooldown():
    calls = []

    def handler(req):
        calls.append(req.url)
        raise httpx.ReadTimeout("timed out", request=req)

    async def go():
        c = _client(handler)
        r1 = await c.get_json("https://api.example.com/x", source="ex", retries=1, timeout=0.5)
        n = len(calls)
        r2 = await c.get_json("https://api.example.com/x", source="ex")      # inside cooldown: no network call
        st = c.health.get("ex")
        await c.aclose()
        return r1, r2, n, st
    r1, r2, n, st = asyncio.run(go())
    assert r1 is None and r2 is None and n == 2 and len(calls) == 2
    assert not st.ok and "ReadTimeout" in st.last_error and st.skipped == 1
    assert st.consecutive_failures == 1


def test_rate_limit_backoff_then_success_and_cooldown_grows():
    seq = [429, 429, 200]

    def handler(req):
        code = seq.pop(0) if seq else 503
        return httpx.Response(code, json={"ok": code == 200}, headers={"retry-after": "0"})

    async def go():
        c = _client(handler)
        ok = await c.get_json("https://api.example.com/a", source="ex")          # 429, 429, 200 -> data
        bad = await c.get_json("https://api.example.com/a", source="ex", retries=0)   # 503 -> None + cooldown
        d1 = c.health.cooling("ex")
        c.health.get("ex").cooldown_until = 0                                    # expire, fail again
        await c.get_json("https://api.example.com/a", source="ex", retries=0)
        d2 = c.health.cooling("ex")
        await c.aclose()
        return ok, bad, d1, d2
    ok, bad, d1, d2 = asyncio.run(go())
    assert ok == {"ok": True} and bad is None
    assert COOLDOWN_BASE_S - 1 < d1 <= COOLDOWN_BASE_S and 2 * COOLDOWN_BASE_S - 1 < d2 <= 2 * COOLDOWN_BASE_S


def test_http_4xx_does_not_trip_cooldown():
    async def go():
        c = _client(lambda req: httpx.Response(404, text="not found"))
        await c.get_json("https://api.example.com/coin", source="ex")
        cool = c.health.cooling("ex")
        await c.aclose()
        return cool
    assert asyncio.run(go()) == 0


def test_scanner_survives_api_failures(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "f.db"), keys=ApiKeys(), on_log=lambda m: None)
    st = _st("KEEP")
    eng.tracked = {"KEEP": st}
    before = st.market

    async def dead(*a, **k):
        return None

    async def boom(*a, **k):
        raise RuntimeError("pump down")
    eng.dex.tokens = dead
    eng.pump.latest = boom
    eng.pump.recently_traded = dead
    eng.pump.coin = dead
    asyncio.run(eng.tick())
    assert eng.clock.tiers["discovery"].failures == 1           # discovery backed off, did not crash
    assert eng.clock.tiers["market"].failures == 1              # DexScreener down -> market tier backs off
    assert eng.tracked["KEEP"].market is before                 # data kept, never wiped by a failure
    assert eng.published and eng.published[0].mint == "KEEP"


# ---------------------------------------------------------------- Early Signal unchanged by faster refresh
def _step_series(cadence):
    from test_early_signal import rising_series
    base = sorted(rising_series(), key=lambda x: -x[0])
    out = []
    for ago in range(1500, -1, -cadence):
        pair = next(p for a, p in base if a >= ago and all(not (ago <= a2 < a) for a2, _ in base))
        out.append((ago, pair))
    return out


def test_faster_refresh_gives_identical_early_signal():
    now = time.time()
    slow, hs = build_series(_step_series(20), now=now)
    fast, hf = build_series(_step_series(5), now=now)
    assert [p.ts for p in hs.points] == [p.ts for p in hf.points]          # same 20s anchors
    a, b = slow.early, fast.early
    assert (a.strength, a.is_early, a.transition, a.fired_count, a.groups_computable, a.suppressed) == \
           (b.strength, b.is_early, b.transition, b.fired_count, b.groups_computable, b.suppressed)
    assert [(x.key, x.fired, x.value) for x in a.signals] == [(x.key, x.fired, x.value) for x in b.signals]
    assert a.is_early is True


def test_history_anchor_spacing_and_holder_anchors():
    h = TokenHistory()
    m = parse_pair(dex_pair())
    t0 = 1_000_000.0
    for i in range(0, 121, 5):
        h.add_market(m, t0 + i)
    gaps = [b.ts - a.ts for a, b in zip(list(h.points), list(h.points)[1:])]
    assert all(g >= POINT_SPACING_S for g in gaps[:-1]) and h.points[-1].ts == t0 + 120
    for i in range(0, 301, 30):
        h.add_holders(HolderSnap(t0 + i, 100 + i, {}, True))
    assert [s.ts - t0 for s in h.holders] == [0, 90, 180, 270, 300]


def test_trend_is_same_pair_only():
    st = TokenState(info=default_info("TR"))
    h = TokenHistory()
    now = time.time()
    ingest_market(st, parse_pair(dex_pair(mint="TR", pair="A", m5=(50, 50), mc=50_000, fdv=50_000, price="0.00005")), {}, SOL_USD, h, now - 300)
    ingest_market(st, parse_pair(dex_pair(mint="TR", pair="B", m5=(90, 10), mc=80_000, fdv=80_000, price="0.00008")), {}, SOL_USD, h, now)
    tr = compute_trend(st, h, now)
    assert tr["mc_chg_5m_pct"] == 60.0                 # MC is token-level
    assert tr["buy_share"] == 90.0 and tr["buy_pp_5m"] is None   # buy share never compared across pairs (D5)


# ---------------------------------------------------------------- groups
def _verified(st):
    from validation.identity import apply_identity, record_claim
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    return st


def _early(strength, fired, is_early, suppressed=()):
    return EarlySignal(strength=strength, is_early=is_early, transition=is_early, fired_count=fired,
                       suppressed=list(suppressed), groups_computable=5 if strength is not None else 2)


def test_groups_follow_existing_results_only():
    st = _verified(build_state(dex_pair(mint="G1")))
    st.holder_status = "ok"
    st.early = _early(72, 4, True)
    assert classify_group(st)[0] == "opportunity"
    st.early = _early(None, 0, None)                                  # UNKNOWN never becomes a signal
    assert classify_group(st) == ("nodata", ["early_unknown"])
    st.early = _early(60, 4, False, suppressed=["top10 40% > 35%"])
    assert classify_group(st)[0] == "watch"                           # suppressed -> never Cơ hội
    st.early = _early(72, 4, True)
    st.holder_status, st.holders = "invalid", None
    assert classify_group(st) == ("excluded", ["holder_anomaly"])
    bad = build_state(dex_pair(mint="G2", liq=7.1e-07))
    assert classify_group(bad)[0] == "excluded" and "dq_invalid" in classify_group(bad)[1]
    risky = build_state(dex_pair(mint="G3"))
    risky.risk = RiskResult(score=75, level="HIGH", factors=[RiskFactor("liquidity_shock", 25, "rug")])
    g, why = classify_group(risky)
    assert g == "excluded" and "risk_high" in why and "rug:liquidity_shock" in why


def test_missing_data_is_nodata_but_malformed_data_is_excluded():
    from scanner.pipeline import evaluate
    st = TokenState(info=default_info("NOMKT"))
    evaluate(st, Settings(), TokenHistory(), time.time(), SOL_USD)
    assert st.dq_status == "INVALID" and st.score is None           # still INVALID for every score
    g, why = classify_group(st)
    assert g == "nodata" and "no_market" in why                    # but shown as "not enough data"
    bad = build_state(dex_pair(mint="MAL", liq=7.1e-07))            # malformed value -> excluded
    assert classify_group(bad)[0] == "excluded"


def test_opportunity_needs_verified_holders_and_valid_data():
    st = build_state(dex_pair(mint="G4"))
    st.early = _early(72, 4, True)
    st.holder_status = "ok"
    assert classify_group(st)[0] == "watch" and "identity_unverified" in classify_group(st)[1]   # not verified
    _verified(st)
    st.holder_status = "pending"                                     # holders not verified yet
    assert classify_group(st)[0] == "watch"
    st.holder_status = "ok"
    st.early = _early(55, 3, False)                                  # no Early TRUE: needs Opportunity >= 60 too
    st.score.total = 59
    assert classify_group(st)[0] == "watch"
    st.score.total = 60
    assert classify_group(st)[0] == "opportunity"


# ---------------------------------------------------------------- API: home groups, profile, no secrets
@pytest.fixture
def web(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "w.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    good = _verified(build_state(dex_pair(mint="GoodMint1111111111111111111111111111111111")))
    good.holder_status = "ok"
    good.early = _early(72, 4, True)
    update_mc_track(good)
    good.group, good.group_reasons = classify_group(good)
    bad = build_state(dex_pair(mint="BadMint22222222222222222222222222222222222", liq=7.1e-07))
    bad.group, bad.group_reasons = classify_group(bad)
    eng.tracked = {s.mint: s for s in (good, bad)}
    eng.published = [good, bad]
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE)) as c:
        yield c


def test_home_groups_and_profile(web):
    d = web.get("/api/home", headers=H).json()
    assert set(d["groups"]) == {"opportunity", "watch", "nodata", "excluded"}
    opp = d["groups"]["opportunity"]
    assert len(opp) == 1 and opp[0]["fired"] == 4
    p = opp[0]["profile"]
    assert p["initial_mc"] == 200_000 and p["initial_label"] == "$200K" and p["gain_x"] == 1.0
    assert p["dev"]["related"] is None and p["social"]["activity"] is None     # not implemented -> never guessed
    assert p["scenario"]["levels"][0]["label"] == "$300K"
    assert [c["mint"][:3] for c in d["groups"]["excluded"]] == ["Bad"]
    assert d["server_time"] > 0


def test_status_reports_actual_refresh(web):
    r = web.get("/api/status", headers=H).json()["refresh"]
    assert r["market_s"] == {"hot": 5.0, "normal": 10.0, "quiet": 30.0}
    assert r["holders_s"] == {"hot": 30.0, "normal": 60.0} and r["dev_s"] == {"hot": 60.0, "normal": 120.0}
    assert {"discovery", "market", "persist", "holders"} <= set(r["tiers"])


def test_no_secret_in_new_endpoints(web):
    for u in ("/api/home", "/api/status", "/api/list/new", "/api/token/GoodMint1111111111111111111111111111111111",
              "/static/app.js", "/i18n/vi.json"):
        body = web.get(u, headers=H).text
        assert SECRET not in body and "api-key=" not in body, u
    assert web.get("/api/home").status_code == 401                 # access code still required


# ---------------------------------------------------------------- refresh / Helius budget
def test_discovery_and_market_rounds_never_call_helius(tmp_path):
    eng = ScannerEngine(Settings(), Database(tmp_path / "h.db"), keys=ApiKeys(helius=SECRET), on_log=lambda m: None)
    calls = []

    async def das(*a, **k):
        calls.append("das")
        return None
    eng.rpc.das_token_accounts = das
    eng.holders.analyze = das
    eng.dev.analyze = das

    async def latest(n):
        return [TokenInfo(mint=f"NEW{i}", created_at=time.time() - 20) for i in range(50)]

    async def empty(*a, **k):
        return {}
    eng.pump.latest, eng.pump.recently_traded, eng.dex.tokens = latest, latest, empty
    for _ in range(3):
        asyncio.run(eng.tick())
        eng.clock = sch.TierClock()                            # force every tier due again
    assert len(eng.tracked) == 50 and calls == []              # 3 discovery + market rounds: zero Helius calls


def test_deep_pool_and_budget(tmp_path):
    eng = ScannerEngine(Settings(deep_per_cycle=15, deep_max_per_min=10), Database(tmp_path / "p.db"),
                        keys=ApiKeys(), on_log=lambda m: None)
    fresh = TokenState(info=TokenInfo(mint="FRESH", created_at=time.time() - 30))   # new, no market yet
    cand = [_st(f"C{i}", vol5=30_000) for i in range(12)]                          # market candidates
    sig = _st("SIG", vol5=100, txns=(2, 1))
    sig.market.market_cap = 8_000                                                  # below candidate thresholds
    sig.group = "watch"                                                            # ...but has signals
    star = TokenState(info=TokenInfo(mint="STAR", created_at=time.time() - 30), watch=True)
    for st in cand + [fresh, sig, star]:
        eng.tracked[st.mint] = st
    pool = {s.mint for s in eng._deep_pool()}
    assert "FRESH" not in pool                                  # discovery alone never spends Helius
    assert {"SIG", "STAR"} <= pool and all(c.mint in pool for c in cand)
    now = time.time()
    first = eng._deep_batch(now)
    assert len(first) == 10 and first[0].mint == "STAR"         # capped per minute, starred first
    assert eng._deep_batch(now + 1) == []                       # budget used up for this minute
    assert len(eng._deep_batch(now + 61)) > 0                   # next minute: budget again


def test_new_and_rising_tokens_come_first_in_deep_queue(tmp_path):
    now = time.time()
    young = _st("YOUNG", age_s=300, vol5=30_000)
    rising = _st("RISING", pc5=45.0, vol5=30_000)
    plain = _st("PLAIN", vol5=30_000)
    for st in (young, rising, plain):
        st.refreshed["holders"] = now - 100
    order = [s.mint for s in sch.deep_due([plain, rising, young], now, 5)]
    assert order == ["YOUNG", "RISING", "PLAIN"]


# ---------------------------------------------------------------- timeout / rate limit / backoff per source
def test_cooldown_is_per_source_and_throttle_spaces_calls():
    hits = []

    def handler(req):
        hits.append((req.url.host, time.monotonic()))
        return httpx.Response(503 if req.url.host == "down.example.com" else 200, json={"ok": 1})

    async def go():
        c = _client(handler)
        c.set_rate("up.example.com", 600)                       # 1 call / 0.1s
        await c.get_json("https://down.example.com/x", source="down", retries=0)
        assert c.health.cooling("down") > 0
        a = await c.get_json("https://up.example.com/x", source="up")
        b = await c.get_json("https://up.example.com/x", source="up")
        await c.aclose()
        return a, b
    a, b = asyncio.run(go())
    assert a == b == {"ok": 1}                                  # a broken source does not block another one
    ups = [ts for host, ts in hits if host == "up.example.com"]
    assert ups[1] - ups[0] >= 0.09


def test_retry_after_header_is_honoured_and_invalid_json_does_not_crash():
    seq = [httpx.Response(429, headers={"retry-after": "1"}), httpx.Response(200, text="not json")]
    t = []

    def handler(req):
        t.append(time.monotonic())
        return seq.pop(0)

    async def go():
        c = _client(handler)
        r = await c.get_json("https://api.example.com/q", source="q", retries=1)
        st = c.health.get("q")
        await c.aclose()
        return r, st
    r, st = asyncio.run(go())
    assert r is None and st.last_error == "invalid JSON" and t[1] - t[0] >= 0.9


# ---------------------------------------------------------------- coin profile
def test_coin_profile_unknowns_are_never_zero(web):
    from api.serialize import profile
    st = TokenState(info=TokenInfo(mint="PROF1", created_at=time.time() - 60))
    p = profile(st)
    assert p["initial_mc"] is None and p["initial_label"] == "Chưa có dữ liệu" and p["gain_x"] is None
    assert p["scenario"] is None and p["mc_path"] == []
    assert p["dev"]["known"] is False and p["dev"]["holding_pct"] is None and p["dev"]["prev_tokens"] is None
    assert all(v is None for k, v in p["onchain"].items() if k not in ("pair", "pair_label", "holder_status"))
    assert p["social"] == {"links": {}, "category": [], "activity": None}


def test_coin_profile_full(web):
    d = web.get("/api/token/GoodMint1111111111111111111111111111111111", headers=H).json()
    c, p = d["card"], d["card"]["profile"]
    assert c["group"] == "opportunity" and c["fired"] == 4
    assert p["initial_mc"] == 200_000 and p["first_seen"] > 0 and p["age_at_discovery_min"] is not None
    assert p["dev"]["known"] is True and p["dev"]["prev_tokens"] == 3 and p["dev"]["graduated"] == 1
    assert p["dev"]["related"] is None                          # never guessed
    assert p["onchain"]["pair"] == "graduated" and p["onchain"]["txns_5m"] == 450
    assert set(p["social"]["links"]) == {"twitter", "telegram", "website"}
    assert p["scenario"]["kind"] == "reference" and p["scenario"]["basis"]["label"] == "$200K"
    assert d["server_time"] > 0 and c["updated_at"] is not None


def test_ui_has_no_key_anywhere(web):
    for f in STATIC.rglob("*"):
        if f.suffix in (".js", ".html", ".css", ".webmanifest", ".json"):
            txt = f.read_text(encoding="utf-8")
            for word in ("HELIUS_API_KEY", "api-key", "helius-rpc.com", "TELEGRAM_BOT_TOKEN", "APP_ACCESS_CODE="):
                assert word not in txt, (f.name, word)
    body = web.get("/api/home", headers=H).text + web.get("/api/status", headers=H).text
    assert SECRET not in body and "helius-rpc.com/?" not in body


# ---------------------------------------------------------------- Early Signal / D1–D8 behaviour unchanged
def test_s1_s4_early_signal_golden_values():
    """Exact values from the approved D1–D8 audit (S1=74 TRUE, S2=14 FALSE, S3=47 FALSE suppressed, S4=65 TRUE)."""
    from scenarios import SCENARIOS, run
    got = {sc.name: run(sc) for sc in SCENARIOS}
    want = {"S1": (74, True, 6, 7, 0), "S2": (14, False, 1, 7, 0), "S3": (47, False, 4, 7, 3), "S4": (65, True, 6, 7, 0)}
    for name, (strength, is_early, fired, groups, n_supp) in want.items():
        e = got[name].early
        assert (e.strength, e.is_early, e.fired_count, e.groups_computable, len(e.suppressed)) == \
               (strength, is_early, fired, groups, n_supp), name
