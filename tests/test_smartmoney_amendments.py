"""Smart-money plan amendments 4-6 and recorder instrumentation (2026-10-08). Synthetic data only — no network,
no recorded trade is read."""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

from smartmoney import analysis as A
from smartmoney import recorder as R
from smartmoney.recorder import Store

SOL = A.LAMPORTS
ROOT = Path(__file__).resolve().parents[1]
W_DAYS = 200_000 / 86_400                       # synthetic window 0 -> 200 000, cut 100 000


def put(s, ts, mint, wallet, buy, sol, tok, recv_ms=None):
    s.buf.append((ts, None, s._id("mint", mint), s._id("wallet", wallet), int(buy), int(sol), int(tok), recv_ms))


def base(tmp_path):
    s = Store(tmp_path / "t.db")
    put(s, 0, "Z", "Z", True, 0.01 * SOL, 1)
    put(s, 200_000, "Z", "Z", True, 0.01 * SOL, 1)
    return s


def rows_of(s, wallet="W", gaps=None):
    s.flush()
    w = A.window(s.db, W_DAYS)
    return {r["mint"]: r for r in A.copies_with_status(s.db, w, [s._id("wallet", wallet)], gaps)}


# ---------------------------------------------------------------- amendment 4: gap rule over the whole copy
def test_gap_between_signal_and_entry_excludes_the_copy(tmp_path):
    s = base(tmp_path)
    put(s, 100_100, "T", "W", True, 1 * SOL, 1 * SOL)                     # signal
    s.gap(100_101, 100_101 + 3600)                                        # recorder down 1 h right after it
    put(s, 100_101 + 3700, "T", "o", True, 0.1 * SOL, 0.1 * SOL)          # first trade seen: after the gap
    put(s, 100_101 + 3800, "T", "W", False, 2 * SOL, 1 * SOL)
    put(s, 100_101 + 3805, "T", "o", False, 0.2 * SOL, 0.1 * SOL)
    r = rows_of(s)[s._id("mint", "T")]
    assert r["status"] == "gap" and r["net_pct"] is None                  # old rule counted this (+100 %)


def test_gap_between_entry_and_exit_excludes_the_copy(tmp_path):
    s = base(tmp_path)
    put(s, 100_100, "T", "W", True, 1 * SOL, 1 * SOL)
    put(s, 100_104, "T", "o", True, 0.1 * SOL, 0.1 * SOL)
    s.gap(100_200, 100_200 + 400)
    put(s, 101_000, "T", "W", False, 2 * SOL, 1 * SOL)
    put(s, 101_004, "T", "o", False, 0.2 * SOL, 0.1 * SOL)
    assert rows_of(s)[s._id("mint", "T")]["status"] == "gap"


def test_short_gap_is_not_an_exclusion(tmp_path):
    s = base(tmp_path)
    put(s, 100_100, "T", "W", True, 1 * SOL, 1 * SOL)
    s.gap(100_101, 100_101 + 200)                                         # 200 s <= 5 min
    put(s, 100_400, "T", "o", True, 0.1 * SOL, 0.1 * SOL)
    assert rows_of(s)[s._id("mint", "T")]["status"] == "ok"


def test_no_trade_after_signal_is_minus_100_only_without_a_gap(tmp_path):
    s = base(tmp_path)
    put(s, 150_000, "DEAD", "W", True, 1 * SOL, 1 * SOL)
    r = rows_of(s)[s._id("mint", "DEAD")]
    assert r["status"] == "ok" and r["net_pct"] == -100.0
    (tmp_path / "b").mkdir()
    s2 = base(tmp_path / "b")
    put(s2, 150_000, "DEAD", "W", True, 1 * SOL, 1 * SOL)
    s2.gap(160_000, 190_000)                                              # recorder down until near the end
    r2 = rows_of(s2)[s2._id("mint", "DEAD")]
    assert r2["status"] == "gap" and r2["net_pct"] is None               # old rule: -100 %


# ---------------------------------------------------------------- amendment 5: PumpSwap outcome after migration
def _migrating(tmp_path):
    s = base(tmp_path)
    put(s, 100_100, "M", "W", True, 1 * SOL, 1 * SOL)
    put(s, 100_104, "M", "o", True, 0.1 * SOL, 0.1 * SOL)                # entry 1.0
    put(s, 100_900, "M", "o", True, 0.3 * SOL, 0.1 * SOL)                # last curve price 3.0
    s.done.append((s._id("mint", "M"), 100_950, 100_950_123))             # curve completes (with receipt ms)
    s.flush()
    return s


def _amm(s, rows, fetched=True, wallet_fetched=True, start=100_950, end=200_000):
    s.db.executescript(A.AMM_SCHEMA)
    m = s._id("mint", "M")
    for ts, wallet, buy, sol, tok in rows:
        s.db.execute("INSERT INTO amm_trades VALUES (?,?,?,?,?,?,?)",
                     (ts, m, s._id("wallet", wallet) if wallet else 0, int(buy), int(sol), int(tok), "test"))
    if fetched:
        s.db.execute("INSERT INTO amm_fetch VALUES (?,?,?,?,?)", (m, start, end, "test", 0.0))
    if wallet_fetched:
        s.db.execute("INSERT INTO amm_wallet_fetch VALUES (?,?,?,?,?)", (m, s._id("wallet", "W"), start, end, "test"))
    s.db.commit()


def test_migration_without_pumpswap_data_is_unresolved_and_blocks(tmp_path):
    s = _migrating(tmp_path)
    r = rows_of(s)[s._id("mint", "M")]
    assert r["status"] == "unresolved" and r["net_pct"] is None           # old code: exit at 3.0 (+200 %)


def test_migration_valued_on_pumpswap_wallet_sell(tmp_path):
    s = _migrating(tmp_path)
    _amm(s, [(101_000, None, True, 0.12 * SOL, 0.1 * SOL),              # PumpSwap price 1.2 right after
             (102_000, "W", False, 0.8 * SOL, 1 * SOL),                 # the wallet sells ON PUMPSWAP
             (102_004, None, False, 0.07 * SOL, 0.1 * SOL)])            # exit price 0.7
    r = rows_of(s)[s._id("mint", "M")]
    assert r["status"] == "ok" and r["kind"] == "wallet_sold+migrated"
    assert r["gross_pct"] == pytest.approx(-30.0) and r["net_pct"] == pytest.approx(A.net_pct(1.0, 0.7))


def test_migration_needs_the_wallets_pumpswap_activity_too(tmp_path):
    s = _migrating(tmp_path)
    _amm(s, [(101_000, None, True, 0.12 * SOL, 0.1 * SOL)], wallet_fetched=False)
    assert rows_of(s)[s._id("mint", "M")]["status"] == "unresolved"


def test_migration_timestamp_is_the_complete_event_and_receipt_is_kept(tmp_path):
    s = _migrating(tmp_path)
    assert s.db.execute("SELECT ts, recv_ms FROM completes").fetchone() == (100_950, 100_950_123)
    assert A._completion(s.db) == {s._id("mint", "M"): 100_950}


def test_run_is_blocked_while_any_copy_is_unresolved(tmp_path, monkeypatch):
    s = _migrating(tmp_path)
    w = s._id("wallet", "W")
    monkeypatch.setattr(A, "select_wallets", lambda db, win: {"eligible": [w], "selected": [w], "profit_sol": {w: 1}})
    r = A.run(str(tmp_path / "t.db"), days=W_DAYS, draws=10)
    assert r["verdict"] == "BLOCKED_MIGRATION_DATA" and r["unresolved_total"] == 1


# ---------------------------------------------------------------- amendment 6: fixed window + stopping rule
def test_window_is_fixed_and_does_not_shrink_when_the_recorder_stops(tmp_path):
    s = Store(tmp_path / "t.db")
    put(s, 1_000, "A", "x", True, 0.01 * SOL, 1)
    put(s, 5_000, "A", "x", True, 0.01 * SOL, 1)                          # recorder stopped early here
    s.flush()
    w = A.window(s.db, 21)
    assert (w.start, w.end, w.cut) == (1_000, 1_000 + 21 * 86_400, 1_000 + 21 * 86_400 // 2)


def test_analysis_refused_before_the_window_end(tmp_path):
    s = Store(tmp_path / "t.db")
    now = int(time.time())
    put(s, now - 3600, "A", "x", True, 0.01 * SOL, 1)
    s.flush()
    ok, end = A.analysis_allowed(s.db, 21)
    assert not ok and end == now - 3600 + 21 * 86_400
    assert A.analysis_allowed(s.db, 21, now=end)[0]
    for tool in ("sm_analyze.py", "sm_kol.py"):
        p = subprocess.run([sys.executable, str(ROOT / "tools" / tool), "--db", str(tmp_path / "t.db")],
                           capture_output=True, text=True, encoding="utf-8")
        assert p.returncode != 0 and "refused" in p.stderr, (tool, p.stderr[-300:])


# ---------------------------------------------------------------- receipt timestamps, heartbeat, restart gaps
def test_receipt_time_is_stored_and_old_rows_stay_null(tmp_path):
    s = Store(tmp_path / "t.db")
    put(s, 100, "A", "x", True, 0.01 * SOL, 1)                            # an old-style row
    s.add({"kind": "trade", "mint": "A", "sol": 2 * SOL, "token": 5, "is_buy": True, "wallet": "y", "ts": 1_791_000_000},
          55, recv_ms=1_791_000_001_234)
    s.flush(now=1_791_000_002.0)
    rows = s.db.execute("SELECT ts, recv_ms FROM trades ORDER BY rowid").fetchall()
    assert rows == [(100, None), (1_791_000_000, 1_791_000_001_234)]
    assert float(s.meta("heartbeat")) == 1_791_000_002.0


def test_schema_migration_on_an_old_database(tmp_path):
    import sqlite3
    db = sqlite3.connect(tmp_path / "old.db")
    db.executescript("CREATE TABLE trades (ts INTEGER NOT NULL, slot INTEGER, mint_id INTEGER NOT NULL, wallet_id "
                     "INTEGER NOT NULL, is_buy INTEGER NOT NULL, sol_lamports INTEGER NOT NULL, token_raw INTEGER NOT NULL);"
                     "CREATE TABLE gaps (start REAL NOT NULL, end REAL NOT NULL);"
                     "INSERT INTO trades VALUES (1,2,3,4,1,5,6); INSERT INTO gaps VALUES (10, 20);")
    db.commit()
    db.close()
    s = Store(tmp_path / "old.db")
    assert s.db.execute("SELECT * FROM trades").fetchone() == (1, 2, 3, 4, 1, 5, 6, None)
    assert s.db.execute("SELECT * FROM gaps").fetchone() == (10.0, 20.0, None)     # old gap kept, not edited


def test_restart_logs_the_downtime_from_the_heartbeat(tmp_path):
    s = Store(tmp_path / "t.db")
    s.flush(now=1_000.0)                                                  # last heartbeat
    g = R.startup_gap(s, now=4_600.0, reason="reboot")
    assert g == (1_000.0, 4_600.0)
    assert s.db.execute("SELECT start, end, reason FROM gaps").fetchall() == [(1_000.0, 4_600.0,
                                                                               "reboot (since last heartbeat)")]
    assert R.startup_gap(s, now=4_602.0) is None                          # nothing new to log


def test_restart_without_heartbeat_uses_the_last_trade_and_never_doubles_a_logged_gap(tmp_path):
    s = Store(tmp_path / "t.db")
    put(s, 2_000, "A", "x", True, 0.01 * SOL, 1)
    s.buf and s.db.executemany("INSERT INTO trades (ts, slot, mint_id, wallet_id, is_buy, sol_lamports, token_raw, "
                               "recv_ms) VALUES (?,?,?,?,?,?,?,?)", s.buf)
    s.buf = []
    s.db.commit()                                                         # no heartbeat (pre-amendment database)
    s.gap(2_000, 9_000, "logged by hand")
    assert R.startup_gap(s, now=9_000.0) is None
    s2 = Store(tmp_path / "u.db")
    put(s2, 2_000, "A", "x", True, 0.01 * SOL, 1)
    s2.db.executemany("INSERT INTO trades (ts, slot, mint_id, wallet_id, is_buy, sol_lamports, token_raw, recv_ms) "
                      "VALUES (?,?,?,?,?,?,?,?)", s2.buf)
    s2.db.commit()
    assert R.startup_gap(s2, now=9_000.0, reason="x")[0] == 2_000.0


def test_single_instance_lock(tmp_path):
    sys.path.insert(0, str(ROOT / "tools"))
    import sm_record
    lock = tmp_path / "recorder.lock"
    lock.write_text(str(os.getpid()))                                     # "another" live process: this one
    assert sm_record.acquire_lock(lock) is True                           # its own pid is allowed
    lock.write_text("999999")                                             # a dead pid
    assert sm_record.acquire_lock(lock) is True and lock.read_text() == str(os.getpid())
    parent = os.getppid()
    lock.write_text(str(parent))                                          # a live different process
    assert sm_record.acquire_lock(lock) is (not R.pid_alive(parent))


def test_watchdog_decisions():
    sys.path.insert(0, str(ROOT / "tools"))
    import sm_watchdog as WD
    now = 10_000.0
    assert WD.decide(False, None, True, now) == "exit"
    assert WD.decide(True, now - 30, False, now) == "ok"
    assert WD.decide(True, now - 600, False, now) == "restart_stale"
    assert WD.decide(False, now - 30, False, now) == "start"


# ---------------------------------------------------------------- stream gaps / reconnects (RPC gap)
class FakeWS:
    def __init__(self, msgs, fail_after=True):
        self.msgs, self.fail_after = list(msgs), fail_after

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        pass

    async def recv(self):
        if self.msgs:
            return self.msgs.pop(0)
        raise ConnectionError("dropped")


def test_reconnect_is_a_gap_from_the_last_message(tmp_path):
    s = Store(tmp_path / "t.db")
    clock = {"t": 1_000.0}
    stop = asyncio.Event()
    msg = json.dumps({"params": {"result": {"context": {"slot": 1}, "value": {"signature": "S1", "err": None,
                                                                              "logs": []}}}})
    sockets = [FakeWS(["sub", msg]), FakeWS(["sub"])]

    def connect():
        if not sockets:
            stop.set()
            return FakeWS(["sub"])
        clock["t"] += 50                                                  # 50 s pass between connections
        return sockets.pop(0)

    async def go():
        orig = asyncio.sleep

        async def fast(_):
            await orig(0)
        R.asyncio.sleep = fast
        try:
            await R.record(s, stop=stop, log=lambda m: None, connect=connect, clock=lambda: clock["t"])
        finally:
            R.asyncio.sleep = orig
    asyncio.run(go())
    gaps = s.db.execute("SELECT start, end, reason FROM gaps").fetchall()
    assert gaps and gaps[0][2] == "stream reconnect" and gaps[0][0] == 1_050.0
    kinds = [k for k, in s.db.execute("SELECT kind FROM recorder_events")]
    assert kinds.count("connect") >= 2 and "disconnect" in kinds


# ---------------------------------------------------------------- RPC_COMPLETENESS + recovery
def test_completeness_sample_counts_and_recovers_apart_from_trades(tmp_path):
    import base64
    import struct
    s = Store(tmp_path / "t.db")
    sigs = R.SigWindow()
    t = 1_000.0
    for i in (1, 2, 4):                                                   # received: 1, 2, 4; missing: 3, 5
        sigs.add(f"S{i}", 100 + i, t)
    sigs.add("ANCHOR", 106, t + 1)                                        # the newest one at least 60 s old
    items = [{"signature": f"S{i}", "slot": 100 + i} for i in (5, 4, 3, 2, 1)] + [{"signature": "LOW", "slot": 100}]
    items = [{"signature": "TOP", "slot": 106}] + items
    trade = R.TRADE_DISC + bytes(32) + struct.pack("<QQ", 2 * SOL, 7) + b"\x01" + bytes(32) + struct.pack("<q", 1_791_000_000)
    line = "Program data: " + base64.b64encode(trade + bytes(40)).decode()

    def call(method, params):
        if method == "getSignaturesForAddress":
            assert params[1]["before"] == "ANCHOR"
            return {"result": items}
        return {"result": {"slot": 103, "meta": {"logMessages": [line]}}}
    row = R.completeness_check(s, sigs, now=t + R.CHECK_AGE_S + 5, call=call)
    assert (row["expected"], row["received"], row["missing"], row["recovered_tx"], row["recovered_events"]) == \
        (5, 3, 2, 2, 2)                                                   # slots 101-105 (100 and 106 dropped)
    assert s.db.execute("SELECT COUNT(*) FROM recovered_events").fetchone()[0] == 2
    assert s.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0] == 0          # experiment data untouched
    assert R.completeness_summary(s.db)["provisional"] == 0.6
    assert R.completeness_summary(s.db, min_checks=1)["rpc_completeness"] == 0.6


def test_completeness_is_unknown_without_samples_and_failures_are_recorded(tmp_path):
    s = Store(tmp_path / "t.db")
    assert R.completeness_summary(s.db)["rpc_completeness"] == "UNKNOWN"
    row = R.completeness_check(s, R.SigWindow(), now=5_000.0, call=lambda m, p: {})
    assert row["error"].startswith("RuntimeError") and R.completeness_summary(s.db)["rpc_completeness"] == "UNKNOWN"


# ---------------------------------------------------------------- storage estimate
def test_storage_estimate():
    st = R.estimate_storage(size_bytes=300_000_000, recorded_s=86_400, remaining_s=10 * 86_400,
                            free_bytes=16_000_000_000)
    assert st["mb_per_day"] == 300.0 and st["projected_gb"] == 3.3
    assert st["free_gb_after"] == 13.0 and st["margin_ratio"] == pytest.approx(16 / 3, abs=0.01)
