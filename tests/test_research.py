"""Research dataset (spec Part 1/2): every discovery is logged on a timeline, candidates + Jupiter status are recorded,
forward returns / MFE / MAE / simulated P&L are computed without look-ahead — and the log never changes trading."""
import asyncio
import json
import random
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from core.models import EarlySignal, MarketData
from research.dataset import SNAP_OFFSETS, DatasetRecorder, export_csv
from test_bot_v2 import FakeJupiter, bot, good
from test_jupiter_exec import ScriptedJupiter
from test_states import complete, napoleon
from trading import jupiter as J

ROOT = Path(__file__).resolve().parent.parent
B58 = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def q(db, sql, *a):
    return db.execute(sql, a).fetchall()


def drive(b, t0, seconds, step=5, price=None):
    for k in range(0, seconds + 1, step):
        now = t0 + k
        if price:
            for st in b.engine.published:
                st.market.price_usd = price(k)
        b.tick(now)
        asyncio.run(b.execute_intents(now))
    return t0 + seconds


def test_every_discovery_gets_a_timeline_and_price_path(tmp_path):
    st = napoleon("Res" + "A" * 41)                     # not a candidate: still fully logged
    b = bot([st], FakeJupiter())
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0
    drive(b, t0, 130)
    db = rec.db
    assert q(db, "SELECT COUNT(*) FROM token_discovery")[0][0] == 1
    reasons = q(db, "SELECT reason, CAST(ts - ? AS INT) FROM token_snapshots WHERE ca=? ORDER BY ts", t0, st.mint)
    timeline = [r for r in reasons if r[0] == "timeline"]
    assert len(timeline) == sum(1 for o in SNAP_OFFSETS if o <= 130)         # discovery, +30s, +1m, +2m
    pts = q(db, "SELECT COUNT(*) FROM price_path WHERE ca=?", st.mint)[0][0]
    assert 8 <= pts <= 10                                                    # 15 s sampling while < 15 min
    row = q(db, "SELECT blocked_by, early_signal, d7, s_volume_accel, opportunity FROM token_snapshots LIMIT 1")[0]
    assert json.loads(row[0]) and row[1] in ("true", "false", "unknown") and row[2] in (-1, 0, 1)


def test_candidate_quote_and_bought_are_recorded(tmp_path):
    st = good()
    b = bot([st], ScriptedJupiter([J.RATE_LIMITED, J.OK]))
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0 - 900
    drive(b, t0, 10)
    c = q(rec.db, "SELECT quote_status, bought, risk_allowed, time_from_discovery_to_candidate_sec, "
                  "time_from_candidate_to_quote_sec, would_have_bought_if_quote_ok FROM candidates")
    assert len(c) == 1 and c[0][0] == "OK" and c[0][1] == 1 and c[0][2] == 1 and c[0][3] >= 900
    assert c[0][4] is not None and c[0][5] == 0
    stages = [r[0] for r in q(rec.db, "SELECT stage FROM token_snapshots ORDER BY ts")]
    assert "candidate" in stages and "bought" in stages
    quotes = q(rec.db, "SELECT jupiter_status, jupiter_quote_ok FROM token_snapshots WHERE reason='quote'")
    assert ("RATE_LIMITED", 0) in quotes and ("OK", 1) in quotes


def test_quote_failure_is_missed_edge_not_silence(tmp_path):
    st = good()
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    drive(b, t0, 5)
    row = q(rec.db, "SELECT quote_status, bought, would_have_bought_if_quote_ok FROM candidates")[0]
    assert row == ("NO_ROUTE", 0, 1)


def test_forward_returns_mfe_mae_and_simulated_pnl(tmp_path):
    st = good()
    st.market.price_usd = 1.0
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0
    # +50 % at 60-90 s, then back down to -20 %
    drive(b, t0, 3800, step=15, price=lambda k: 1.5 if 60 <= k < 90 else (0.8 if k >= 300 else 1.0))
    fr = {h: (r, mfe, mae, tp, sl, first) for h, r, mfe, mae, tp, sl, first in q(
        rec.db, "SELECT horizon, return_pct, mfe_pct, mae_pct, hit_tp30, hit_sl15, first_hit FROM forward_returns "
                "WHERE anchor='discovery'")}
    assert abs(fr["30s"][0]) < 1e-6
    assert round(fr["2m"][1]) == 50 and fr["2m"][3] == 1 and fr["2m"][5] == "tp30"
    assert round(fr["10m"][0]) == -20 and round(fr["10m"][2]) == -20
    assert round(fr["1h"][0]) == -20
    sim = q(rec.db, "SELECT simulated_pnl_if_forced, sim_exit, sim_done FROM candidates")[0]
    assert sim[2] == 1 and sim[1] == "tp30" and sim[0] > 0                  # missed edge measured despite NO_ROUTE


def test_followups_after_scanner_drops_the_token(tmp_path):
    class Dex:
        calls = 0

        async def tokens(self, mints):
            Dex.calls += 1
            return {m: (MarketData(price_usd=2.0, market_cap=2e6), {}) for m in mints}
    st = good()
    st.market.price_usd = 1.0
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db", dex=Dex())
    t0 = time.time()
    st.info.discovered_at = t0
    drive(b, t0, 60, step=15)
    b.engine.published = []                                                  # pruned by the scanner
    b.tick(t0 + 1800)
    n = asyncio.run(rec.run_followups(t0 + 1800))
    assert n == 1 and Dex.calls == 1
    b.tick(t0 + 1800 + 20)
    r30 = q(rec.db, "SELECT return_pct, observed FROM forward_returns WHERE anchor='discovery' AND horizon='30m'")
    assert r30 and round(r30[0][0]) == 100 and r30[0][1] == "followup"


def test_recorder_never_changes_decisions_or_buys(tmp_path):
    def world(seed):
        rnd = random.Random(seed)
        out = []
        for i in range(40):
            st = napoleon("Rnd" + B58[i % 24] + B58[i // 24] + "1" * 39)
            if rnd.random() < 0.5:
                complete(st)
            if rnd.random() < 0.2:
                st.early = EarlySignal(None, None, None)
            out.append(st)
        return out
    a, c = bot(world(5), FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100), \
        bot(world(5), FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100)
    c.recorder = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    for k in range(4):
        for x in (a, c):
            x.tick(t0 + 5 * k)
            asyncio.run(x.execute_intents(t0 + 5 * k))
    strip = lambda d: {m: (r["decision"], r["opportunity"], r["confidence"], r["state"]) for m, r in d.items()}  # noqa: E731
    assert strip(a.decisions) == strip(c.decisions)
    assert set(a.book.positions) == set(c.book.positions) and a.book.positions
    assert q(c.recorder.db, "SELECT COUNT(*) FROM token_discovery")[0][0] == 40


def test_recorder_errors_never_break_the_bot(tmp_path):
    st = good()
    b = bot([st], FakeJupiter())

    class Broken:
        def __getattr__(self, k):
            def boom(*a, **kw):
                raise RuntimeError("disk full")
            return boom
    b.recorder = Broken()
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions and any("research log error" in x.text for x in b.activity)


def test_restart_keeps_anchors_and_export(tmp_path):
    st = good()
    st.market.price_usd = 1.0
    b = bot([st], FakeJupiter())
    b.recorder = rec = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0
    drive(b, t0, 30, step=15)
    rec.close()
    again = DatasetRecorder(tmp_path / "r.db")
    assert st.mint in again.t and again.t[st.mint]["anchors"]["discovery"][1] == 1.0
    csv = "".join(export_csv(tmp_path / "r.db", "token_snapshots"))
    assert csv.splitlines()[0].startswith("snapshot_id,ca,ts") and len(csv.splitlines()) >= 3
    assert again.summary()["tokens"] == 1


def test_analyzer_runs_on_a_dataset(tmp_path):
    st = good()
    st.market.price_usd = 1.0
    b = bot([st], ScriptedJupiter([J.OK]))
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    t0 = time.time()
    st.info.discovered_at = t0
    drive(b, t0, 1300, step=15, price=lambda k: 1.0 + k / 600)
    b.recorder.close()
    out = subprocess.run([sys.executable, str(ROOT / "tools" / "analyze_dataset.py"), "--db", str(tmp_path / "r.db")],
                         capture_output=True, text=True, encoding="utf-8")
    assert out.returncode == 0, out.stderr[-800:]
    assert "Rules vs label A" in out.stdout and "Experiment 4" in out.stdout and "NOT evidence" in out.stdout


def test_outcome_has_no_lookahead():
    sys.path.insert(0, str(ROOT / "tools"))
    from analyze_dataset import outcome
    path = [(0, 1.0), (10, 5.0), (100, 1.5), (400, 1.0), (600, 1.3)]
    o = outcome(path, 10, 5.0, 0.04)              # anchored AT the spike: the spike itself is not a future gain
    assert o["A"] == 0 and round(o["ret10"], 2) == -0.74
    o = outcome(path, 0, 1.0, 0.04)
    assert o["A"] == 1 and o["B"] == 1
    sqlite3.connect(":memory:").close()
