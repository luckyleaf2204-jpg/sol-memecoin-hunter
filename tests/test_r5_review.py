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


class _BudgetStore(S.LocalStore):
    """Critical-file puts, research.db, and manifest puts each sleep their own delay."""

    def __init__(self, root):
        super().__init__(root)
        self.critical = self.db = self.manifest = 0.0

    def put(self, name, data):
        if "research.db" in name:
            time.sleep(self.db)
        elif str(name).startswith("manifest"):
            time.sleep(self.manifest)
        elif str(name).endswith(".gz"):
            time.sleep(self.critical)
        super().put(name, data)


def test_slow_file_leaves_the_publish_reserve(tmp_path, monkeypatch):
    """Critical uploads take most of the deadline. research.db must leave PUBLISH_RESERVE_S for the two
    manifest puts — the old 1s grace is not enough, and the whole shutdown stays under ~110s."""
    assert S.PUBLISH_RESERVE_S >= 10
    assert S.SHUTDOWN_DEADLINE_S >= S.PUBLISH_RESERVE_S
    assert S.SHUTDOWN_TOTAL_S < 110
    assert 10 + S.SHUTDOWN_DEADLINE_S + L.LEASE_RELEASE_DEADLINE_S <= S.SHUTDOWN_TOTAL_S + 1e-9
    monkeypatch.setattr(S, "PUBLISH_RESERVE_S", 1.5)
    d, store = tmp_path / "d", _BudgetStore(tmp_path / "store")
    _files(d, 1)
    first = S.snapshot(d, store, now=1.0)
    _files(d, 2)
    store.critical, store.db, store.manifest = 1.0, 30.0, 0.55   # two critical files, then a DB that would eat the tail
    m = S.snapshot(d, store, now=2.0, slow_timeout_s=60.0, deadline_s=4.0)
    assert m["gen"] == first["gen"] + 1
    assert m["files"]["research.db"]["carried_from"] == first["gen"]
    assert m["files"]["paper_bot.json"]["sha256"] != first["files"]["paper_bot.json"]["sha256"]
    assert json.loads(store.get(S.MANIFEST))["gen"] == m["gen"]


def test_a_second_snapshot_waits_inside_its_deadline(tmp_path):
    """Shutdown must not start a second generation while an hourly snapshot still holds the store."""
    import threading
    d = tmp_path / "d"

    class Gate(S.LocalStore):
        def __init__(self, root):
            super().__init__(root)
            self.block = False
            self.entered = threading.Event()
            self.release = threading.Event()

        def put(self, name, data):
            if self.block and name == S.MANIFEST:
                self.entered.set()
                assert self.release.wait(3)
            super().put(name, data)

    store = Gate(tmp_path / "store")
    _files(d, 1)
    S.snapshot(d, store, now=1.0)
    store.block = True
    _files(d, 2)
    box = {}

    def run():
        try:
            box["m"] = S.snapshot(d, store, now=2.0)
        except Exception as e:
            box["e"] = e

    th = threading.Thread(target=run)
    th.start()
    assert store.entered.wait(2)
    t0 = time.time()
    with pytest.raises(S.SnapshotError, match="another snapshot"):
        S.snapshot(d, store, now=3.0, deadline_s=0.25)
    assert time.time() - t0 < 0.8
    store.release.set()
    th.join(3)
    assert "e" not in box
    man = json.loads(store.get(S.MANIFEST))
    assert man["gen"] == box["m"]["gen"]
    assert man["files"]["paper_bot.json"]["sha256"] == box["m"]["files"]["paper_bot.json"]["sha256"]


def test_abandoned_manifest_put_cannot_overwrite_a_newer_snapshot(tmp_path):
    """A manifest put that outlives its deadline must not replace the snapshot that ran after it."""
    import threading
    d = tmp_path / "d"

    class DelayedManifest(S.LocalStore):
        def __init__(self, root):
            super().__init__(root)
            self.delay = 0.0

        def put(self, name, data):
            if self.delay and name == S.MANIFEST:
                time.sleep(self.delay)
            super().put(name, data)

    store = DelayedManifest(tmp_path / "store")
    _files(d, 1)
    first = S.snapshot(d, store, now=1.0)
    store.delay = 1.0
    _files(d, 2)
    box = {}

    def late():
        try:
            S.snapshot(d, store, now=2.0, deadline_s=0.35)
        except S.SnapshotError as e:
            box["e"] = e

    th = threading.Thread(target=late)
    th.start()
    th.join(3)
    assert "e" in box
    store.delay = 0.0
    _files(d, 3)
    newer = S.snapshot(d, store, now=3.0)
    time.sleep(1.1)                                            # the abandoned put finishes, if it still can
    man = json.loads(store.get(S.MANIFEST))
    assert man["gen"] == newer["gen"]
    assert man["files"]["paper_bot.json"]["sha256"] == newer["files"]["paper_bot.json"]["sha256"]
    assert man["files"]["paper_bot.json"]["sha256"] != first["files"]["paper_bot.json"]["sha256"]


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


def _research_db(d):
    import sqlite3
    db = sqlite3.connect(d / "research.db")
    db.execute("CREATE TABLE IF NOT EXISTS t (v INT)")
    db.execute("INSERT INTO t VALUES (1)")
    db.commit()
    db.close()
    return (d / "research.db").stat().st_size


def _manifest(store):
    raw = store.get(S.MANIFEST)
    return json.loads(raw) if raw else None


def test_hourly_snapshot_carries_a_slow_research_db(tmp_path, monkeypatch, capsys):
    """A slow research.db is carried from the previous generation; it does not fail the snapshot or block orders,
    and the carried object is not overwritten by the late upload."""
    store = SlowFile(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    size = _research_db(tmp_path / "d")
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    monkeypatch.setattr(S, "HOURLY_SLOW_TIMEOUT_S", 0.15)
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        assert c.get("/healthz").status_code == 200
        assert _wait(lambda: (_manifest(store) or {}).get("files", {}).get("research.db"))
        m1 = _manifest(store)
        obj = m1["files"]["research.db"]["object"]
        blob = store.get(obj)
        store.slow, store.delay = "research.db", 1.0

        def carried():
            m = _manifest(store)
            meta = (m or {}).get("files", {}).get("research.db") or {}
            return m and m["gen"] > m1["gen"] and "carried_from" in meta and meta["object"] == obj
        assert _wait(carried, 4)
        st = app.state.hunter
        assert st["snapshot"].get("fails_in_a_row", 0) == 0
        assert st["bot"].entry_block is None
        time.sleep(1.2)                                        # the abandoned upload finishes
        assert store.get(obj) == blob                          # the carried object was not overwritten
    assert f"[snapshot] research.db {size} bytes" in capsys.readouterr().out


class _Boom(S.LocalStore):
    def __init__(self, root, bad):
        super().__init__(root)
        self.bad = bad

    def put(self, name, data):
        if self.bad in name:
            raise RuntimeError("upload failed")
        super().put(name, data)


def test_an_error_carries_only_research_db(tmp_path):
    """truth_ledger.json is restored with the book and the epoch, so an upload error must fail the snapshot
    instead of publishing a stale ledger next to a new epoch. price_history.json is optional scanner state;
    an error fails the snapshot too. Only research.db is carried."""
    d = tmp_path / "d"
    _files(d, 1)
    (d / "truth_ledger.json").write_text('{"n": 1}', encoding="utf-8")
    (d / "price_history.json").write_text('{"n": 1}', encoding="utf-8")

    class Boom(S.LocalStore):
        def __init__(self, root):
            super().__init__(root)
            self.bad = ""

        def put(self, name, data):
            if self.bad and self.bad in name:
                raise RuntimeError("upload failed")
            super().put(name, data)

    store = Boom(tmp_path / "store")
    first = S.snapshot(d, store, now=1.0)
    (d / "paper_bot.json").write_text(json.dumps({"cash": 2}), encoding="utf-8")
    (d / "truth_ledger.json").write_text('{"n": 2}', encoding="utf-8")
    (d / "price_history.json").write_text('{"n": 2}', encoding="utf-8")
    store.bad = "truth_ledger"
    with pytest.raises(RuntimeError, match="upload failed"):
        S.snapshot(d, store, now=2.0, slow_timeout_s=60)
    assert json.loads(store.get(S.MANIFEST))["gen"] == 1
    store.bad = "price_history"
    with pytest.raises(RuntimeError, match="upload failed"):
        S.snapshot(d, store, now=3.0, slow_timeout_s=60)
    assert json.loads(store.get(S.MANIFEST))["gen"] == 1
    store.bad = "research.db"
    m = S.snapshot(d, store, now=4.0, slow_timeout_s=60)
    # Failed attempts claim a generation (their uploads may still be in flight) so this one is newer than gen 2.
    assert m["gen"] > first["gen"] and m["files"]["research.db"]["carried_from"] == first["gen"]
    assert "carried_from" not in m["files"]["truth_ledger.json"]
    assert "carried_from" not in m["files"]["price_history.json"]
    assert m["files"]["truth_ledger.json"]["sha256"] != first["files"]["truth_ledger.json"]["sha256"]


def test_research_db_carried_in_a_row_is_surfaced(tmp_path, monkeypatch, capsys):
    """Three carried research.db snapshots in a row show up on /healthz and /api/snapshot and log a warning."""

    class Toggle(S.LocalStore):
        def __init__(self, root):
            super().__init__(root)
            self.fail = False

        def put(self, name, data):
            if self.fail and "research.db" in name:
                raise RuntimeError("db")
            super().put(name, data)

    store = Toggle(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    _research_db(tmp_path / "d")
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    monkeypatch.setattr(S, "HOURLY_SLOW_TIMEOUT_S", 0.5)
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        st = app.state.hunter
        assert _wait(lambda: (st["snapshot"].get("last") or {}).get("reason") == "hourly", 3)
        assert st["snapshot"].get("research_db_carried_in_a_row", 0) == 0
        store.fail = True
        assert _wait(lambda: st["snapshot"].get("research_db_carried_in_a_row", 0) >= 3, 4)
        body = c.get("/healthz").json()
        assert body["ok"] is True
        assert body["snapshot"]["research_db_carried_in_a_row"] >= 3
        view = c.get("/api/snapshot", headers={"X-Access-Code": "c0de"}).json()
        assert view["research_db_carried_in_a_row"] >= 3
        assert st["bot"].entry_block is None
    assert "WARNING: research.db carried" in capsys.readouterr().out


def test_a_failing_research_db_does_not_block_orders_a_critical_file_does(tmp_path, monkeypatch):
    store = _Boom(tmp_path / "store", "research.db")
    _setup(tmp_path, monkeypatch, store)
    _research_db(tmp_path / "d")
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    monkeypatch.setattr(S, "HOURLY_SLOW_TIMEOUT_S", 0.2)
    app = _app(tmp_path, "a")
    with TestClient(app):
        st = app.state.hunter
        assert _wait(lambda: st["snapshot"].get("last") is not None, 3)
        time.sleep(0.3)
        assert st["snapshot"].get("fails_in_a_row", 0) == 0
        assert st["bot"].entry_block is None
    store2 = _Boom(tmp_path / "store2", "paper_bot")
    monkeypatch.setattr(S, "store_from_env", lambda: store2)
    app = _app(tmp_path, "b")
    with TestClient(app):
        st = app.state.hunter
        assert _wait(lambda: st["snapshot"].get("fails_in_a_row", 0) >= S.SNAPSHOT_FAIL_BLOCK_N, 3)
        assert "snapshots in a row" in (st["bot"].entry_block or "")


def test_standby_activate_failure_is_logged(tmp_path, monkeypatch, capsys, two_instances):
    from trading.config import TradingConfig
    d1 = tmp_path / "d1"
    d1.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d1)
    old = _app(tmp_path, "old")
    c1 = TestClient(old)
    c1.__enter__()
    new = _app(tmp_path, "new")
    c2 = TestClient(new)
    c2.__enter__()
    try:
        assert c2.get("/healthz").json()["role"] == "STANDBY"

        def boom(*_a, **_k):
            raise RuntimeError("activate boom")
        monkeypatch.setattr(TradingConfig, "load", boom)
        c1.__exit__(None, None, None)
        c1 = None
        time.sleep(0.8)                                    # the standby loop polls, then activate raises
        out = capsys.readouterr().out
        assert "[lease] activate after STANDBY failed: RuntimeError" in out
        body = c2.get("/healthz").json()
        assert body["ok"] is False and body["role"] == "ACTIVATE FAILED"
        assert "activate after STANDBY failed" in (body.get("halted") or "")
        lease = json.loads((two_instances / "instance_lease.json").read_text())
        assert lease.get("released") is True                 # the renew loop must not keep the lease
        task = new.state.hunter.get("lease_task")
        assert task is None or task.done()
    finally:
        if c1 is not None:
            c1.__exit__(None, None, None)
        c2.__exit__(None, None, None)


def test_stale_s_forgets_mints_with_no_open_position():
    from test_v12 import MINT, opened
    b, _st, _p = opened()
    b.stale_s["not-a-position"] = 80.0
    b.stale_s[MINT] = 40.0
    b.book.positions.pop(MINT)
    b.tick()
    assert "not-a-position" not in b.stale_s and MINT not in b.stale_s


def test_uvicorn_bounds_connection_drain_before_lifespan_shutdown(monkeypatch):
    """Connection draining runs before the lifespan snapshot and is not part of the 105s budget."""
    import uvicorn

    from main import cmd_web
    seen = {}

    def fake_run(*_a, **k):
        seen.update(k)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    cmd_web("127.0.0.1", 9)
    assert seen["timeout_graceful_shutdown"] == 5


def test_healthz_ok_is_computed_before_calls_that_can_fail(monkeypatch):
    """A broken git_commit import must not leave ok true while the instance is halted or the probe is retrying."""
    import sys

    from test_v12 import opened
    b, _, _ = opened()
    app = webapp.create_app(engine=b.engine, start_scanner=False, access_code="c0de", bot=b)
    app.state.hunter["halted"] = "activate after STANDBY failed (RuntimeError)"
    app.state.hunter["role"] = "ACTIVATE FAILED"
    monkeypatch.setitem(sys.modules, "core.version", None)
    with TestClient(app) as c:
        body = c.get("/healthz").json()
        assert body["ok"] is False and body["role"] == "ACTIVATE FAILED"
        assert "activate after STANDBY failed" in body["halted"]
        assert "health_error" in body
    b2, _, _ = opened()
    app2 = webapp.create_app(engine=b2.engine, start_scanner=False, access_code="c0de", bot=b2)
    app2.state.hunter["snapshot"]["probe_retrying"] = True
    with TestClient(app2) as c:
        body = c.get("/healthz").json()
        assert body["ok"] is False
        assert "retrying" in body["durability"]


def test_healthz_survives_any_call_or_import_error(monkeypatch):
    import sys

    from test_v12 import opened
    b, _, _ = opened()

    def boom():
        raise RuntimeError("params broke")
    b.cfg.sample_id = boom
    app = webapp.create_app(engine=b.engine, start_scanner=False, access_code="c0de", bot=b)
    with TestClient(app) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json()["ok"] is True and r.json()["health_error"] == "RuntimeError"
    b2, _, _ = opened()
    app2 = webapp.create_app(engine=b2.engine, start_scanner=False, access_code="c0de", bot=b2)
    monkeypatch.setitem(sys.modules, "core.version", None)
    with TestClient(app2) as c:
        r = c.get("/healthz")
        assert r.status_code == 200 and r.json()["health_error"] in ("TypeError", "ImportError", "ModuleNotFoundError")
