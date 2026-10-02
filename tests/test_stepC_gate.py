"""Step C — every token stopped by the no-chasing gate is recorded with its reason and followed like an entry;
report: block rate, blocked vs entered forward returns vs a random baseline of the same age / liquidity."""
import random
import sqlite3

import pytest

import trading.entry_location as EL
from research.dataset import SCHEMA, DatasetRecorder
from research.gate_eval import baseline_matches, evaluate, outcome
from test_jupiter_exec import ScriptedJupiter
from test_lifecycle import SECOND_WAVE, history, post_tok, run_bot
from test_step2_chasing import _fake_location
from trading import jupiter as J


def test_bot_records_blocked_once_and_entered(tmp_path, monkeypatch):
    monkeypatch.setattr(EL, "entry_location", _fake_location("EXTENDED", 65.0))
    rec = DatasetRecorder(tmp_path / "r.db")
    st = post_tok()
    b = run_bot(st, ScriptedJupiter([J.OK]), history(SECOND_WAVE), recorder=rec)
    b.tick()                                                      # still blocked: no second event
    rows = rec.db.execute("SELECT kind, reasons, extension_5m_pct, entry_location, liquidity_usd FROM gate_events").fetchall()
    assert len(rows) == 1 and rows[0][0] == "blocked" and "EXTENDED" in rows[0][1] and rows[0][2] == 65.0
    assert rec.t[st.mint]["anchors"]["gate_blocked"][1] == pytest.approx(st.market.price_usd)
    monkeypatch.setattr(EL, "entry_location", _fake_location("PULLBACK", 5.0))
    rec2 = DatasetRecorder(tmp_path / "r2.db")
    st2 = post_tok()
    run_bot(st2, ScriptedJupiter([J.OK]), history(SECOND_WAVE), recorder=rec2)
    kinds = [r[0] for r in rec2.db.execute("SELECT kind FROM gate_events")]
    assert kinds == ["entered"] and "entered" in rec2.t[st2.mint]["anchors"]


def _db(path):
    db = sqlite3.connect(path)
    db.executescript(SCHEMA)
    return db


def test_outcome_uses_only_prices_after_the_event(tmp_path):
    db = _db(tmp_path / "o.db")
    path = [(990, 5.0), (1000, 1.0), (1300, 1.4), (1600, 1.1), (1700, 0.9)]   # 990 is BEFORE the event
    db.executemany("INSERT INTO price_path VALUES (?,?,?,?,?,?,?)", [("A", t, p, None, None, None, "scanner") for t, p in path])
    o = outcome(db, "A", 1000.0, 1.0, 600)
    assert o["ret"] == pytest.approx(10.0) and o["mfe"] == pytest.approx(40.0) and o["first_hit"] == "tp30"
    assert o["mae"] == pytest.approx(10.0)                         # the 5.0 print before t0 is ignored
    assert outcome(db, "A", 1000.0, 1.0, 3600) is None             # no price near t0 + 1h


def test_baseline_has_no_look_ahead_and_matches_buckets(tmp_path):
    db = _db(tmp_path / "b.db")
    snap = "INSERT INTO token_snapshots (snapshot_id, ca, ts, age_sec, liq_usd, price_usd) VALUES (?,?,?,?,?,?)"
    db.executemany(snap, [("1", "OK1", 950, 200, 7000, 1.0), ("2", "OK2", 990, 250, 6000, 1.0),
                          ("3", "FUTURE", 1010, 200, 7000, 1.0),          # observed after the event
                          ("4", "OLD", 990, 5000, 7000, 1.0),             # other age bucket
                          ("5", "RICH", 990, 200, 50_000, 1.0),           # other liquidity bucket
                          ("6", "SELF", 990, 200, 7000, 1.0)])            # an event token itself
    ev = {"ca": "X", "ts": 1000.0, "age_s": 180.0, "liq": 8000.0}
    m = baseline_matches(db, ev, {"SELF"}, random.Random(1))
    assert sorted(c for c, _, _ in m) == ["OK1", "OK2"] and all(ts <= 1000 for _, ts, _ in m)


def test_evaluate_report(tmp_path):
    db = _db(tmp_path / "e.db")
    ins = ("INSERT INTO gate_events (ca, ts, kind, reasons, extension_5m_pct, entry_location, age_s, liquidity_usd, "
           "price) VALUES (?,?,?,?,?,?,?,?,?)")
    db.executemany(ins, [("B1", 1000, "blocked", '["entry_location: extension_5m 65% > 40%"]', 65, "EARLY_ENTRY", 200, 7000, 1.0),
                         ("B1", 1100, "blocked", '["entry_location: EXTENDED"]', 70, "EXTENDED", 300, 7000, 1.0),
                         ("B2", 1000, "blocked", '["entry_location: MID_MOVE"]', 20, "MID_MOVE", 200, 7000, 1.0),
                         ("E1", 1000, "entered", "[]", 5, "PULLBACK", 200, 7000, 1.0)])
    pp = "INSERT INTO price_path VALUES (?,?,?,?,?,?,?)"
    for ca, end in (("B1", 0.8), ("B2", 0.7), ("E1", 1.2), ("R1", 1.05)):
        db.executemany(pp, [(ca, 1000 + 300, 1.0, None, None, None, "s"), (ca, 1000 + 600, end, None, None, None, "s")])
    db.execute("INSERT INTO token_snapshots (snapshot_id, ca, ts, age_sec, liq_usd, price_usd) VALUES ('r','R1',995,220,6500,1.0)")
    db.commit()
    db.close()
    r = evaluate(str(tmp_path / "e.db"))
    assert r["n_blocked"] == 2 and r["n_entered"] == 1 and r["block_rate_pct"] == pytest.approx(66.7)
    assert r["block_reasons"] == {"extension_5m > 40%": 1, "entry_location: MID_MOVE": 1}      # first event per CA
    h = r["by_horizon"]["10m"]
    assert h["blocked"]["mean_ret_pct"] == pytest.approx(-25.0) and h["entered"]["mean_ret_pct"] == pytest.approx(20.0)
    assert h["baseline_for_entered"]["n"] == 1 and h["baseline_for_entered"]["mean_ret_pct"] == pytest.approx(5.0)
    assert r["sample"] == {"blocked": "INSUFFICIENT (<30)", "entered": "INSUFFICIENT (<30)"}
