"""G6 — the holdout hash is STRATEGY_VERSION + parameter fingerprint, not the git commit: a docs-only commit must not
close the holdout, a strategy bump must."""
import core.version as version
from research.replay import frozen_params, lock_holdout, replay
from replay_fixtures import HZ, make_db
from trading import sample_epoch


def test_hash_ignores_the_commit_and_uses_the_strategy_version(monkeypatch):
    monkeypatch.setattr(version, "git_commit", lambda: "aaaaaaa")
    p = frozen_params("1h", 0.6, 7)
    assert "commit" not in p and p["strategy_version"] == sample_epoch.STRATEGY_VERSION
    monkeypatch.setattr(version, "git_commit", lambda: "bbbbbbb")     # a docs commit
    assert frozen_params("1h", 0.6, 7)["hash"] == p["hash"]
    monkeypatch.setattr(sample_epoch, "STRATEGY_VERSION", "s-next")   # a strategy bump
    assert frozen_params("1h", 0.6, 7)["hash"] != p["hash"]


def test_a_docs_commit_keeps_the_holdout_open(tmp_path, monkeypatch):
    db, lock = str(tmp_path / "r.db"), str(tmp_path / "lock.json")
    make_db(tmp_path / "r.db", ["tp30", "sl15"] * 40, ["sl15"] * 50)
    monkeypatch.setattr(version, "git_commit", lambda: "aaaaaaa")
    assert lock_holdout(db, lock, HZ)["status"] == "LOCKED"
    monkeypatch.setattr(version, "git_commit", lambda: "bbbbbbb")
    r = replay(db, HZ, holdout_lock=lock)
    assert "PARAMETERS CHANGED" not in r["verdict"] and r["walk_forward"]["out_of_sample"]["candidates"]["n"] == 32
