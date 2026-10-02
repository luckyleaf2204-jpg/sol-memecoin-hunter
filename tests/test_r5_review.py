"""Review round 5 — shutdown is one real deadline, the lease stays alive until release, a slow research.db
does not block entries, and three small loops/handlers no longer die quietly."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import core.lease as L
import web.app as webapp
from core import snapshot as S
from test_r3_robust import SlowFile, _files, _setup
from test_s6_lease import _app, _wait


class SlowCalls(S.LocalStore):
    """Every store read and write takes `delay` seconds (a hung HTTP store)."""

    def __init__(self, root, delay=0.0):
        super().__init__(root)
        self.delay = delay

    def get(self, name):
        if self.delay:
            time.sleep(self.delay)
        return super().get(name)

    def put(self, name, data):
        if self.delay:
            time.sleep(self.delay)
        return super().put(name, data)


class SlowManifest(S.LocalStore):
    """Only the manifest objects are slow; file uploads stay fast."""

    def __init__(self, root, delay=0.0):
        super().__init__(root)
        self.delay = delay

    def put(self, name, data):
        if self.delay and str(name).startswith("manifest"):
            time.sleep(self.delay)
        super().put(name, data)


def test_deadline_covers_the_store_reads(tmp_path):
    """The 80s budget starts at snapshot(), so a slow manifest read cannot run before the clock does."""
    d, store = tmp_path / "d", SlowCalls(tmp_path / "store")
    _files(d, 1)
    S.snapshot(d, store, now=1.0)
    store.delay = 0.8                                          # each get would be 0.8s; several run before any file
    t0 = time.time()
    with pytest.raises(S.SnapshotError, match="deadline"):
        S.snapshot(d, store, now=2.0, deadline_s=0.2)
    assert time.time() - t0 < 0.7
    store.delay = 0.0
    assert json.loads(store.get(S.MANIFEST))["gen"] == 1       # nothing was published


def test_deadline_covers_the_manifest_writes(tmp_path):
    """The two manifest puts are inside the same budget, not after it."""
    d, store = tmp_path / "d", SlowManifest(tmp_path / "store")
    _files(d, 1)
    S.snapshot(d, store, now=1.0)
    store.delay = 1.0
    t0 = time.time()
    with pytest.raises(S.SnapshotError, match="deadline"):
        S.snapshot(d, store, now=2.0, deadline_s=0.25)
    assert time.time() - t0 < 0.8                             # old code waits out both manifest puts (~2s)


def test_release_is_bounded_when_every_store_call_is_slow(tmp_path):
    store = SlowCalls(tmp_path / "store")
    a = L.Lease(store, "A", settle_s=0)
    assert a.acquire()
    store.delay = 2.0
    t0 = time.time()
    with pytest.raises(TimeoutError):
        a.release(wait_s=0.2)
    assert time.time() - t0 < 0.8


def test_shutdown_stays_inside_the_total_budget_when_every_store_call_is_slow(tmp_path, monkeypatch, capsys):
    """Bot stop + final snapshot + lease release stay under the shutdown budget even if each call would hang."""
    assert S.SHUTDOWN_TOTAL_S < 110
    assert 10 + S.SHUTDOWN_DEADLINE_S + L.LEASE_RELEASE_DEADLINE_S <= S.SHUTDOWN_TOTAL_S + 1e-9
    store = SlowCalls(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(S, "SHUTDOWN_TOTAL_S", 1.2)
    monkeypatch.setattr(S, "SHUTDOWN_DEADLINE_S", 0.35)
    monkeypatch.setattr(L, "LEASE_RELEASE_DEADLINE_S", 0.25)
    app = _app(tmp_path, "a")
    stopped = []
    eng = app.state.hunter["engine"]
    real_stop = eng.stop
    eng.stop = lambda: (stopped.append(1), real_stop())
    c = TestClient(app)
    c.__enter__()
    store.delay = 5.0                                          # every get and put would take 5s
    t0 = time.time()
    c.__exit__(None, None, None)
    elapsed = time.time() - t0
    out = capsys.readouterr().out
    assert elapsed < 2.0                                       # 0.35 snapshot + 0.25 release + slack, not N * 5s
    assert stopped == [1]
    assert ("[lease] released" in out) or ("[lease] release failed" in out)
