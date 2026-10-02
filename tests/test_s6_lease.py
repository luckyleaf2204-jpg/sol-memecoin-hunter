"""Part 2 — one ACTIVE instance per snapshot store (Render zero-downtime deploys overlap two instances for >= 60 s):
the lease, the deploy hand-over (final snapshot -> release -> the new instance restores), a lost lease stops the
bot, and after a restore open positions are re-evaluated on the first tick with the downtime recorded as a GAP."""
import json
import time

import pytest
from fastapi.testclient import TestClient

import core.lease as L
import web.app as webapp
from core import snapshot as S
from core.config import ApiKeys, Settings
from database.db import Database
from scanner.engine import ScannerEngine


class Clock:
    def __init__(self, t=1000.0):
        self.t = t

    def __call__(self):
        return self.t


# ---------------------------------------------------------------- the lease itself
def test_only_one_instance_holds_the_lease(tmp_path):
    store, clk = S.LocalStore(tmp_path / "store"), Clock()
    a, b = L.Lease(store, "A", clock=clk, settle_s=0), L.Lease(store, "B", clock=clk, settle_s=0)
    assert a.acquire() and not b.acquire() and b.status.startswith("STANDBY: held by A")
    assert a.held() and not b.held()
    clk.t += 60
    assert a.renew() and not b.acquire()
    a.release()
    assert not a.held() and b.acquire() and b.held()
    assert not a.renew() and a.status == "LOST to B"                   # the old holder learns it lost


def test_an_expired_lease_is_free(tmp_path):
    store, clk = S.LocalStore(tmp_path / "store"), Clock()
    a, b = L.Lease(store, "A", clock=clk, settle_s=0), L.Lease(store, "B", clock=clk, settle_s=0)
    assert a.acquire()
    clk.t += L.LEASE_TTL_S - 1
    assert not b.acquire()
    clk.t += 2                                                           # A crashed: never renewed
    assert b.acquire()


def test_simultaneous_acquire_last_writer_wins(tmp_path):
    store, clk = S.LocalStore(tmp_path / "store"), Clock()
    b = L.Lease(store, "B", clock=clk, settle_s=0)

    def a_sleep(_):                                                      # B writes while A waits to read back
        store.put(L.LEASE, json.dumps({"owner": "B", "acquired_at": clk.t, "renewed_at": clk.t,
                                       "expires_at": clk.t + 180, "released": False}).encode())
    a = L.Lease(store, "A", clock=clk, settle_s=1.0, sleep=a_sleep)
    assert not a.acquire() and a.status == "STANDBY: lost the acquire race" and b.held()


def test_corrupt_lease_counts_as_free(tmp_path):
    store = S.LocalStore(tmp_path / "store")
    store.put(L.LEASE, b"{garbage")
    assert L.Lease(store, "A", settle_s=0).acquire()


# ---------------------------------------------------------------- the deploy hand-over (two instances, one store)
def _app(tmp_path, name):
    eng = ScannerEngine(Settings(), Database(tmp_path / f"{name}.db"), keys=ApiKeys(), on_log=lambda m: None)

    async def idle():
        return None
    eng.run = idle
    return webapp.create_app(engine=eng, start_scanner=True, access_code="c0de")


def _wait(cond, s=5.0):
    end = time.time() + s
    while time.time() < end:
        if cond():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def two_instances(tmp_path, monkeypatch):
    store_dir = tmp_path / "store"
    monkeypatch.setenv("SNAPSHOT_DIR", str(store_dir))
    for k in ("RESEARCH_LOG", "LATENCY_PROBE"):
        monkeypatch.setenv(k, "0")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    monkeypatch.setattr(L, "LEASE_POLL_S", 0.05)
    return store_dir


def test_deploy_hand_over_standby_then_restore_the_final_snapshot(tmp_path, monkeypatch, two_instances):
    d1, d2 = tmp_path / "d1", tmp_path / "d2"
    d1.mkdir()
    d2.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d1)
    old = _app(tmp_path, "old")
    c1 = TestClient(old)
    c1.__enter__()                                                     # the running instance
    assert c1.get("/healthz").json()["role"] == "ACTIVE"
    new = _app(tmp_path, "new")
    c2 = TestClient(new)
    c2.__enter__()                                                     # deploy: the new instance starts beside it
    h = c2.get("/healthz").json()
    assert h["role"] == "STANDBY" and h["ok"] is True                  # passes Render's health check, does nothing
    assert new.state.hunter["bot_task"] is None and new.state.hunter.get("placeholder_bot")
    assert not list(d2.iterdir())
    old.state.hunter["bot"].book.cash = 1234.5                         # the old instance keeps trading meanwhile
    gen_before = json.loads(S.LocalStore(two_instances).get(S.MANIFEST) or b'{"gen": 0}')["gen"]
    orig = S.restore                                                   # one process plays both machines: the
    monkeypatch.setattr(S, "restore", lambda data_dir, store: orig(d2, store))   # new one restores into its own disk
    c1.__exit__(None, None, None)                                      # SIGTERM: final snapshot, then release
    man = json.loads(S.LocalStore(two_instances).get(S.MANIFEST))
    assert man["gen"] > gen_before
    assert _wait(lambda: new.state.hunter.get("role") == "ACTIVE"      # the standby took over ...
                 and new.state.hunter.get("bot_task") is not None)      # (read from another thread: wait for both)
    assert new.state.hunter["snapshot"]["restore"]["status"] == "RESTORED"
    assert json.loads((d2 / "paper_bot.json").read_text())["cash"] == 1234.5     # ... from the FINAL state
    assert new.state.hunter["bot_task"] is not None
    c2.__exit__(None, None, None)


def test_a_lost_lease_stops_the_bot_and_snapshots(tmp_path, monkeypatch, two_instances):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    monkeypatch.setattr(L, "LEASE_RENEW_S", 0.05)
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        st = app.state.hunter
        assert st["role"] == "ACTIVE"
        S.LocalStore(two_instances).put(L.LEASE, json.dumps({"owner": "intruder", "acquired_at": 1, "renewed_at": 1,
                                                             "expires_at": time.time() + 999,
                                                             "released": False}).encode())
        assert _wait(lambda: st.get("role") == "LEASE LOST")
        assert st["bot_stop"].is_set() and "lease lost" in st["halted"]
        h = c.get("/healthz").json()
        assert h["ok"] is False and h["role"] == "LEASE LOST"
        gen = (json.loads(S.LocalStore(two_instances).get(S.MANIFEST) or b'{"gen": 0}'))["gen"]
    assert (json.loads(S.LocalStore(two_instances).get(S.MANIFEST) or b'{"gen": 0}'))["gen"] == gen   # no shutdown write
    assert json.loads(S.LocalStore(two_instances).get(L.LEASE))["owner"] == "intruder"           # not released by us


def test_without_a_store_there_is_no_lease_and_a_loud_warning(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    for k in ("SNAPSHOT_DIR", "SNAPSHOT_URL", "RENDER_EXTERNAL_URL"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESEARCH_LOG", "0")
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        h = c.get("/healthz").json()
        assert h["role"] == "ACTIVE" and "lease" not in h and h["snapshot"]["warning"] == S.NOT_DURABLE
        r = app.state.hunter["bot"].sample_report()
        assert r["warnings"][0].startswith("NO NEW ENTRIES") and r["warnings"][1].startswith(S.NOT_DURABLE)


# ---------------------------------------------------------------- 2.3 after a restore: first tick re-evaluates
def test_after_restore_open_positions_are_re_evaluated_and_the_downtime_is_a_gap(tmp_path):
    from test_v12 import MINT, opened
    from trading.bot import PaperBot
    from trading.config import TradingConfig
    b, st, p = opened()
    b.book.heartbeat = time.time() - 900                               # the old instance stopped 15 min ago
    b.book.save(tmp_path / "paper_bot.json")
    b2 = PaperBot(b.engine, TradingConfig(seed=4), state_path=tmp_path / "paper_bot.json")
    b2.jupiter = b.jupiter
    now = time.time()
    b2.begin_sample(now)
    gaps = b2.gap_tracker.all_gaps(now)
    assert gaps and gaps[-1]["minutes"] >= 14                           # downtime recorded as a GAP
    st.stamps["market"].updated_at = now + 1
    st.market.price_usd = p.entry_price * 0.8                          # the price now: below the stop
    b2.tick(now + 1)                                                   # FIRST tick after the restore
    assert b2.sell_intents[MINT]["reason"] == "stop_loss"
    r = b2.sample_report(now + 2)
    assert r["gaps"]["count"] >= 1
