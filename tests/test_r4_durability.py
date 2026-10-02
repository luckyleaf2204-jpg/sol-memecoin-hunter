"""Review round 4 (C2): a SNAPSHOT_DIR on the same disk is not durable; repeated snapshot failures block new entries
and a success lifts it; the block is also checked at execution and approval; a failed start-up probe is retried with
/healthz ok=false."""
import asyncio
import time

from fastapi.testclient import TestClient

import web.app as webapp
from core import snapshot as S
from test_r3_robust import Flaky, _setup
from test_s6_lease import _app, _wait


def test_local_dir_on_the_same_disk_is_not_durable(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_LOCAL_DIR", raising=False)
    d = tmp_path / "data"
    chk = S.durability_check(S.LocalStore(tmp_path / "store"), d)
    assert not chk["durable"] and "same disk" in chk["reason"] and not chk["transient"]
    monkeypatch.setenv("ALLOW_LOCAL_DIR", "1")
    assert S.durability_check(S.LocalStore(tmp_path / "store"), d)["durable"]


def test_other_device_counts_as_a_separate_mount(tmp_path, monkeypatch):
    monkeypatch.delenv("ALLOW_LOCAL_DIR", raising=False)
    store_root = (tmp_path / "mnt").resolve()
    real = S._dev
    monkeypatch.setattr(S, "_dev", lambda p: 999 if str(store_root) in str(__import__("pathlib").Path(p).resolve())
                        else real(p))
    assert S.same_disk_as(store_root, tmp_path / "data") == []
    assert S.durability_check(S.LocalStore(store_root), tmp_path / "data")["durable"]


def test_failing_snapshots_block_entries_and_recovery_lifts_it(tmp_path, monkeypatch):
    class Breakable(S.LocalStore):
        broken = False

        def put(self, name, data):
            if self.broken and name.startswith("gen-"):
                raise OSError("store 503")
            super().put(name, data)
    store = Breakable(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    app = _app(tmp_path, "a")
    with TestClient(app):
        st = app.state.hunter
        assert st["bot"].entry_block is None
        store.broken = True
        assert _wait(lambda: st["bot"].entry_block and "3 snapshots in a row" in st["bot"].entry_block)
        store.broken = False
        assert _wait(lambda: st["bot"].entry_block is None)


def test_entry_block_is_checked_at_execution_and_approval():
    from test_bot_v2 import FakeJupiter, bot, good
    st = good()
    b = bot([st], FakeJupiter())
    b.tick()
    assert b.intents                                                    # intent made while durable
    b.entry_block = "snapshot store failing"
    asyncio.run(b.execute_intents())
    assert not b.book.positions and st.mint in b.entry_block_seen
    b.pending["o1"] = {"id": "o1", "mint": st.mint, "usd": 5.0, "ts": time.time(), "expires": time.time() + 60}
    assert b.approve("o1") is False and not b.intents


def test_transient_probe_failure_is_retried_with_healthz_not_ok(tmp_path, monkeypatch):
    store = Flaky(tmp_path / "store")

    def bad_probe(name):
        if store.fail and name == S.PROBE:
            raise OSError("503")
        return S.LocalStore.get(store, name)
    store.get = bad_probe
    store.fail = True
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(S, "DURABILITY_RETRY_S", 0.05)
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        h = c.get("/healthz").json()
        assert h["ok"] is False and "retrying" in h["durability"]
        assert app.state.hunter["bot"].entry_block
        store.fail = False
        assert _wait(lambda: c.get("/healthz").json()["ok"] is True)
        assert app.state.hunter["bot"].entry_block is None
