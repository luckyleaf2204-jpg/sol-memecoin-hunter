"""Fix 3 — generational snapshots (verify, then switch the manifest), all-or-nothing restore, the bot STOPS when the
restore fails, holdout lock + sample epoch in the snapshot, atomic writes."""
import gzip
import json

import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from core import snapshot as S
from core.config import ApiKeys, Settings
from database.db import Database
from scanner.engine import ScannerEngine


def _data(d):
    d.mkdir(parents=True, exist_ok=True)
    for name, body in (("paper_bot.json", {"cash": 1000}), ("sample_epoch.json", {"started_at": 1.0}),
                       ("holdout_lock.json", {"params_hash": "abc", "locked_at": 2.0})):
        (d / name).write_text(json.dumps(body), encoding="utf-8")
    return d


def test_holdout_lock_and_epoch_are_snapshotted():
    assert "holdout_lock.json" in S.FILES and "sample_epoch.json" in S.FILES


def test_generations_rotate_and_never_overwrite_the_live_one(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    gens = [S.snapshot(d, store, now=float(i)) for i in range(1, 5)]
    assert [g["gen"] for g in gens] == [1, 2, 3, 4] and [g["slot"] for g in gens] == [1, 2, 0, 1]
    man = json.loads(store.get(S.MANIFEST))
    assert man["gen"] == 4 and man["files"]["paper_bot.json"]["object"] == "gen-1--paper_bot.json.gz"
    assert store.get("gen-2--paper_bot.json.gz") is not None and store.get("gen-0--paper_bot.json.gz") is not None


class Corrupting(S.LocalStore):
    """Stores the bytes, but reads back garbage for one object (bad disk / partial upload)."""
    def __init__(self, root, bad):
        super().__init__(root)
        self.bad = bad

    def get(self, name):
        data = super().get(name)
        return gzip.compress(b"garbage") if data is not None and self.bad in name else data


def test_failed_verify_keeps_the_previous_manifest(tmp_path):
    d = _data(tmp_path / "data")
    good = S.LocalStore(tmp_path / "store")
    S.snapshot(d, good, now=1.0)
    bad = Corrupting(tmp_path / "store", "sample_epoch")
    with pytest.raises(S.SnapshotError):
        S.snapshot(d, bad, now=2.0)
    man = json.loads(good.get(S.MANIFEST))
    assert man["gen"] == 1 and man["ts"] == 1.0                     # still the verified generation
    fresh = tmp_path / "fresh"
    assert S.restore(fresh, good)["status"] == "RESTORED"


def test_restore_is_all_or_nothing(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    S.snapshot(d, store)
    broken = Corrupting(tmp_path / "store", "holdout_lock")
    fresh = tmp_path / "fresh"
    fresh.mkdir()
    with pytest.raises(S.SnapshotError):
        S.restore(fresh, broken)
    assert sorted(p.name for p in fresh.iterdir()) == []             # nothing moved in, staging cleaned up
    ok = S.restore(fresh, store)
    assert ok["status"] == "RESTORED" and set(ok["restored"]) == {"paper_bot.json", "sample_epoch.json",
                                                                  "holdout_lock.json"}
    assert json.loads((fresh / "holdout_lock.json").read_text())["params_hash"] == "abc"


def test_complete_local_data_restores_nothing_partial_local_data_halts(tmp_path):
    d, store = _data(tmp_path / "data"), S.LocalStore(tmp_path / "store")
    S.snapshot(d, store)
    r = S.restore(d, store)                               # warm restart: all files there, local is newer
    assert r["status"].startswith("LOCAL DATA PRESENT") and r["restored"] == []
    live = tmp_path / "live"
    live.mkdir()
    (live / "sample_epoch.json").write_text("{\"started_at\": 99}", encoding="utf-8")
    with pytest.raises(S.SnapshotError, match="partial local data"):
        S.restore(live, store)
    assert not (live / "paper_bot.json").exists()


def test_write_atomic(tmp_path):
    S.write_atomic(tmp_path / "x.json", b"{}")
    assert (tmp_path / "x.json").read_bytes() == b"{}" and not (tmp_path / "x.json.tmp").exists()


def test_server_stops_the_bot_when_the_restore_fails(tmp_path, monkeypatch):
    store_dir, data = tmp_path / "store", tmp_path / "data"
    src = _data(tmp_path / "src")
    S.snapshot(src, S.LocalStore(store_dir))
    man = json.loads(S.LocalStore(store_dir).get(S.MANIFEST))
    S.LocalStore(store_dir).put(man["files"]["paper_bot.json"]["object"], gzip.compress(b"corrupt"))
    data.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", data)
    monkeypatch.setenv("SNAPSHOT_DIR", str(store_dir))
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    eng = ScannerEngine(Settings(), Database(tmp_path / "e.db"), keys=ApiKeys(), on_log=lambda m: None)

    async def no_run():                                   # the scanner must not be started either
        raise AssertionError("scanner started while halted")
    eng.run = no_run
    app = webapp.create_app(engine=eng, start_scanner=True, access_code="c0de")
    with TestClient(app) as c:
        h = c.get("/healthz").json()
        assert h["ok"] is False and "restore failed" in h["halted"] and "checksum mismatch for paper_bot.json" in h["halted"]
        d = c.get("/api/snapshot", headers={"X-Access-Code": "c0de"}).json()
        assert d["halted"] and d["last_restore"]["status"].startswith("RESTORE FAILED")
    assert not (data / "paper_bot.json").exists() and not (data / "sample_epoch.json").exists()
    assert json.loads(S.LocalStore(store_dir).get(S.MANIFEST))["gen"] == man["gen"]   # no snapshot over the good one
