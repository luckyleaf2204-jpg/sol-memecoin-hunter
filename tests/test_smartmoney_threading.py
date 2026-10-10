"""Recorder SQLite connection ownership (fix of 2026-10-09). The RPC_COMPLETENESS sampler used to run
completeness_check — which writes through the Store's connection — in asyncio.to_thread; sqlite3 refused
("SQLite objects created in a thread can only be used in that same thread"), the INSERT sat outside the try, the
exception ended asyncio.gather and the recorder exited at its first sample, every ~6 min. Synthetic data only."""
import asyncio
import base64
import json
import sqlite3
import struct
import threading
import time

import pytest

from smartmoney import recorder as R
from smartmoney.recorder import SigWindow, Store

SOL = 1_000_000_000


def sample_rpc(seen_threads=None):
    items = [{"signature": "TOP", "slot": 106}] + [{"signature": f"S{i}", "slot": 100 + i} for i in (5, 4, 3, 2, 1)] \
        + [{"signature": "LOW", "slot": 100}]

    def call(method, params):
        if seen_threads is not None:
            seen_threads.add(threading.get_ident())
        if method == "getSignaturesForAddress":
            return {"result": items}
        return {"result": None}
    return call


def old_sigs(now):
    sigs = SigWindow()
    for i in (1, 2, 4):
        sigs.add(f"S{i}", 100 + i, now - 100)
    sigs.add("ANCHOR", 106, now - 90)
    return sigs


def trade_msg(i):
    ev = R.TRADE_DISC + bytes([1]) * 32 + struct.pack("<QQ", 2 * SOL, 7) + b"\x01" + bytes([2]) * 32 \
        + struct.pack("<q", int(time.time()))
    line = "Program data: " + base64.b64encode(ev + bytes(40)).decode()
    return json.dumps({"params": {"result": {"context": {"slot": i},
                                             "value": {"signature": f"T{i}", "err": None, "logs": [line]}}}})


class StreamWS:
    """A socket that keeps delivering one trade every 10 ms until stop."""
    def __init__(self, stop):
        self.stop, self.i = stop, 0

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        pass

    async def recv(self):
        self.i += 1
        if self.i == 1:
            return "sub"
        await asyncio.sleep(0.01)
        return trade_msg(self.i)


async def until(cond, timeout=5.0):
    """Wait for a condition instead of a fixed sleep (no timing flake on a loaded machine)."""
    t = time.time()
    while not cond():
        assert time.time() - t < timeout, "condition not reached"
        await asyncio.sleep(0.02)


def test_old_pattern_reproduces_the_cross_thread_error(tmp_path):
    """The exact call the old completeness_loop made: completeness_check(store, ...) inside asyncio.to_thread."""
    s = Store(tmp_path / "t.db")
    now = time.time()
    with pytest.raises(sqlite3.ProgrammingError, match="same thread"):
        asyncio.run(asyncio.to_thread(R.completeness_check, s, old_sigs(now), now, sample_rpc()))
    assert s.db.execute("SELECT COUNT(*) FROM rpc_checks").fetchone()[0] == 0


def test_probe_never_receives_the_store():
    """The worker-thread half takes no Store / SigWindow: it cannot touch the connection."""
    import inspect
    params = list(inspect.signature(R.completeness_probe).parameters)
    assert params[:3] == ["anchor", "received", "now"] and "store" not in params and "sigs" not in params


def test_loop_samples_in_a_worker_thread_and_writes_on_the_owner_thread(tmp_path):
    s = Store(tmp_path / "t.db")
    seen = set()

    async def go():
        stop = asyncio.Event()
        sigs = old_sigs(time.time())
        task = asyncio.create_task(R.completeness_loop(s, sigs, stop, every_s=0.05, log=lambda m: None,
                                                       call=sample_rpc(seen)))
        await until(lambda: s.db.execute("SELECT COUNT(*) FROM rpc_checks").fetchone()[0] >= 2)
        stop.set()
        await task
    asyncio.run(go())
    rows = s.db.execute("SELECT expected, received, missing, error FROM rpc_checks").fetchall()
    assert len(rows) >= 2 and all(r == (5, 3, 2, None) for r in rows)
    assert threading.get_ident() not in seen                     # the HTTP half really ran in a worker thread
    summ = R.completeness_summary(s.db)
    assert summ["provisional"] == 0.6 and summ["rpc_completeness"] == "UNKNOWN"   # < MIN_CHECKS samples


def test_a_sampler_bug_is_logged_and_the_recorder_keeps_recording(tmp_path, monkeypatch):
    s = Store(tmp_path / "t.db")
    stop = asyncio.Event()

    def broken(*a, **k):
        raise sqlite3.ProgrammingError("SQLite objects created in a thread can only be used in that same thread")
    monkeypatch.setattr(R, "completeness_probe", broken)
    logs = []

    async def go():
        sigs = SigWindow()
        rec = asyncio.create_task(R.record(s, stop=stop, log=logs.append, connect=lambda: StreamWS(stop), sigs=sigs))
        loop = asyncio.create_task(R.completeness_loop(s, sigs, stop, every_s=0.05, log=logs.append))
        await until(lambda: s.db.execute("SELECT COUNT(*) FROM recorder_events WHERE kind='sampler_error'")
                    .fetchone()[0] >= 2)
        await asyncio.sleep(0.3)                                # keep streaming after the failures
        assert not rec.done() and not loop.done()               # neither task died
        stop.set()
        await asyncio.wait_for(asyncio.gather(rec, loop), 5)
    asyncio.run(go())
    errs = s.db.execute("SELECT ts FROM recorder_events WHERE kind='sampler_error' ORDER BY ts").fetchall()
    assert len(errs) >= 2
    first_err_ms = int(errs[0][0] * 1000)
    after = s.db.execute("SELECT COUNT(*) FROM trades WHERE recv_ms > ?", (first_err_ms,)).fetchone()[0]
    assert after > 5                                             # trades kept arriving after the sampler failed
    bad = s.db.execute("SELECT COUNT(*) FROM rpc_checks WHERE error LIKE 'sampler:%'").fetchone()[0]
    assert bad == len(errs)
    assert R.completeness_summary(s.db)["rpc_completeness"] == "UNKNOWN"
    assert any("recording continues" in m for m in logs)


def test_completeness_stays_unknown_until_enough_samples(tmp_path):
    s = Store(tmp_path / "t.db")
    now = time.time()
    for k in range(R.MIN_CHECKS):
        assert R.completeness_summary(s.db)["rpc_completeness"] == "UNKNOWN"
        R.completeness_check(s, old_sigs(now), now=now + k, call=sample_rpc())
    assert R.completeness_summary(s.db)["rpc_completeness"] == 0.6


def test_failed_samples_never_count_as_evidence(tmp_path):
    s = Store(tmp_path / "t.db")
    for k in range(R.MIN_CHECKS + 3):
        R.completeness_check(s, SigWindow(), now=5_000.0 + k, call=lambda m, p: {})
    summ = R.completeness_summary(s.db)
    assert summ["checks_failed"] == R.MIN_CHECKS + 3 and summ["rpc_completeness"] == "UNKNOWN"
