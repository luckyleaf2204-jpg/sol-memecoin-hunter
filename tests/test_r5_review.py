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


@pytest.fixture
def two_instances(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    monkeypatch.setenv("SNAPSHOT_DIR", str(store_dir))
    for k in ("RESEARCH_LOG", "LATENCY_PROBE"):
        monkeypatch.setenv(k, "0")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(L, "LEASE_POLL_S", 0.05)
    return store_dir


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


def test_lease_keeps_renewing_during_the_shutdown_snapshot(tmp_path, monkeypatch):
    """The renew loop stays up through the final snapshot and is cancelled only just before release."""
    seen = []

    class Watch(S.LocalStore):
        def put(self, name, data):
            if "paper_bot" in name:
                raw = super().get(L.LEASE)
                seen.append(json.loads(raw)["renewed_at"])
                time.sleep(0.45)
                raw = super().get(L.LEASE)
                seen.append(json.loads(raw)["renewed_at"])
            super().put(name, data)

    store = Watch(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(L, "LEASE_RENEW_S", 0.05)
    monkeypatch.setattr(S, "SHUTDOWN_DEADLINE_S", 2.0)
    app = _app(tmp_path, "a")
    with TestClient(app):
        assert app.state.hunter["role"] == "ACTIVE"
    assert len(seen) >= 2 and seen[-1] > seen[0]


def test_a_hung_renew_put_cannot_clear_the_release(tmp_path):
    """release() stops waiting; the renew put that lands afterwards must not leave released=False."""
    import threading

    class Gate(S.LocalStore):
        def __init__(self, root):
            super().__init__(root)
            self.block = False
            self.in_put = threading.Event()
            self.allow = threading.Event()

        def put(self, name, data):
            if name == L.LEASE and self.block:
                self.block = False
                self.in_put.set()
                assert self.allow.wait(5)
            super().put(name, data)

    store = Gate(tmp_path / "store")
    a = L.Lease(store, "A", settle_s=0)
    assert a.acquire()
    store.block = True
    t = threading.Thread(target=a.renew)
    t.start()
    try:
        assert store.in_put.wait(2)
        t0 = time.time()
        with pytest.raises(TimeoutError):
            a.release(wait_s=0.1)                             # cannot take the lock: the renew put is in flight
        assert time.time() - t0 < 0.6
        store.allow.set()
        t.join(3)
        assert not t.is_alive()
        assert json.loads(store.get(L.LEASE))["released"] is True
    finally:
        store.allow.set()
        t.join(3)


def test_self_fence_is_checked_while_a_renew_hangs(tmp_path, monkeypatch, two_instances):
    """Fencing is not only after a renew attempt: a hung renew must not push it out by another full period."""
    import threading
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    gate = threading.Event()

    class Hanging(S.LocalStore):
        hang = False

        def get(self, name):
            if self.hang and name == L.LEASE:
                gate.wait(10)
            return super().get(name)

    store = Hanging(two_instances)
    monkeypatch.setattr(S, "store_from_env", lambda: store)
    monkeypatch.setattr(L, "LEASE_RENEW_S", 1.0)
    monkeypatch.setattr(L, "LEASE_IO_TIMEOUT_S", 0.1)
    monkeypatch.setattr(L, "LEASE_TTL_S", 2.4)                 # fence_after = 2.4 - 1.0 - 0.1 = 1.3
    monkeypatch.setattr(L, "LEASE_FENCE_CHECK_S", 0.05)
    app = _app(tmp_path, "a")
    try:
        with TestClient(app):
            st = app.state.hunter
            assert st["role"] == "ACTIVE"
            store.hang = True
            # Old loop: first check ~1.1s (not fenced yet), next check ~2.2s. The 5s clock fences at ~1.3s.
            assert _wait(lambda: st.get("role") == "FENCED", 1.6)
            assert st["bot_stop"].is_set()
    finally:
        gate.set()
