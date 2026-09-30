from analytics.backtest import run_backtest
from core.models import DataQuality, Event, MarketData, OpportunityResult, TokenInfo, TokenState
from database.db import Database


def _st(mint, price, score, dq="VALID"):
    st = TokenState(info=TokenInfo(mint=mint, symbol=mint),
                    market=MarketData(price_usd=price, market_cap=price * 1e9))
    st.score = OpportunityResult(total=score, coverage_pct=100, parts=[], contributions=[])
    st.quality = DataQuality(score=90 if dq == "VALID" else 10, status=dq)
    return st


def test_snapshots_alerts_watchlist(tmp_path):
    db = Database(tmp_path / "t.db")
    st = _st("A", 1.0, 50)
    db.upsert_token(st)
    db.insert_snapshots([st], ts=1000)
    assert len(db.snapshots("A")) == 1
    db.add_watch("A")
    assert db.watchlist() == ["A"]
    db.remove_watch("A")
    assert db.watchlist() == []
    db.insert_alert(st, "msg", False)
    assert db.last_alert_ts("A") is not None


def test_backtest_no_lookahead(tmp_path):
    db = Database(tmp_path / "t.db")
    t0 = 10_000
    # A: signal (85) at t0, then doubles -> 2x hit
    db.insert_snapshots([_st("A", 1.0, 60)], ts=t0 - 60)
    db.insert_snapshots([_st("A", 1.0, 85)], ts=t0)
    for k, p in ((300, 1.3), (600, 2.1), (900, 1.8), (1800, 1.5)):
        db.insert_snapshots([_st("A", p, 50)], ts=t0 + k)
    # B: signal then rug
    db.insert_snapshots([_st("B", 1.0, 85)], ts=t0)
    for k, p in ((300, 0.4), (900, 0.1), (1800, 0.05)):
        db.insert_snapshots([_st("B", p, 20)], ts=t0 + k)
    # C: price spike BEFORE the signal must not count as a hit
    db.insert_snapshots([_st("C", 5.0, 40)], ts=t0 - 300)
    db.insert_snapshots([_st("C", 1.0, 82)], ts=t0)
    for k in (300, 900, 1800):
        db.insert_snapshots([_st("C", 1.0, 40)], ts=t0 + k)

    rows = {(r.threshold, r.window): r for r in run_backtest(db.conn, thresholds=(80, 90))}
    r = rows[(80, "15m")]
    assert r.signals == 3 and r.evaluated == 3
    assert round(r.hits["2x"]) == 33       # only A
    assert round(r.drop50_pct) == 33       # only B
    assert rows[(90, "15m")].signals == 0
    assert rows[(80, "6h")].evaluated == 0  # not enough recorded data -> not counted


def test_backtest_ignores_non_valid_signals_and_invalid_prices(tmp_path):
    db = Database(tmp_path / "t.db")
    t0 = 10_000
    # high score but PARTIAL data -> not a signal
    db.insert_snapshots([_st("P", 1.0, 95, dq="PARTIAL")], ts=t0)
    db.insert_snapshots([_st("P", 3.0, 20)], ts=t0 + 900)
    # VALID signal; a later INVALID row with a bogus 100x price must not count as an outcome
    db.insert_snapshots([_st("V", 1.0, 85)], ts=t0)
    db.insert_snapshots([_st("V", 100.0, 20, dq="INVALID")], ts=t0 + 300)
    db.insert_snapshots([_st("V", 1.1, 20)], ts=t0 + 900)
    r = {(x.threshold, x.window): x for x in run_backtest(db.conn, thresholds=(80,))}[(80, "15m")]
    assert r.signals == 1 and r.evaluated == 1
    assert r.hits["2x"] == 0


def test_migration_adds_columns_to_old_db(tmp_path):
    import sqlite3
    path = tmp_path / "old.db"
    c = sqlite3.connect(path)
    c.execute("CREATE TABLE snapshots (id INTEGER PRIMARY KEY, ts REAL, mint TEXT, price REAL, score INTEGER)")
    c.commit(); c.close()
    db = Database(path)
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(snapshots)")}
    assert {"dq", "dq_status", "liquidity_source"} <= cols


def test_events_roundtrip(tmp_path):
    db = Database(tmp_path / "t.db")
    db.insert_events([Event(1.0, "M", "S", "VOLUME_SPIKE", "positive", {"before": 1, "now": 5}, "dex")])
    ev = db.recent_events(10)
    assert ev[0].type == "VOLUME_SPIKE" and ev[0].params == {"before": 1, "now": 5}
    assert db.stats()["events"] == 1


def test_early_signal_backtest_column(tmp_path):
    db = Database(tmp_path / "t.db")
    st = _st("E", 1.0, 10)
    from core.models import EarlySignal
    st.early = EarlySignal(strength=75, is_early=True, transition=True)
    db.insert_snapshots([st], ts=10_000)
    db.insert_snapshots([_st("E", 2.5, 10)], ts=10_900)
    r = {(x.threshold, x.window): x for x in run_backtest(db.conn, thresholds=(70,), column="early_signal")}[(70, "15m")]
    assert r.signals == 1 and r.hits["2x"] == 100
