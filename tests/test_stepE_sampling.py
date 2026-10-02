"""Step E — sample-size warnings, replay without look-ahead, frozen walk-forward parameters / one-shot holdout."""
import sqlite3

import pytest

from research.dataset import SCHEMA
from research.replay import load, lock_holdout, replay
from test_stepB_report import epoch, row
from test_step4_measure import _db
from trading.sample_report import report


# ---------------------------------------------------------------- warnings
@pytest.mark.parametrize("n, status", [(0, "INSUFFICIENT"), (29, "INSUFFICIENT"), (30, "PRELIMINARY"),
                                       (199, "PRELIMINARY"), (200, "OK")])
def test_sample_status(n, status):
    e = epoch()
    r = report([row(e, 12.0 if i % 2 else -2.0, ts=2000 + i) for i in range(n)], e)
    assert r["sample_status"] == status
    if status == "INSUFFICIENT":
        assert any("no conclusion" in w for w in r["warnings"])
    if status == "PRELIMINARY":
        assert any(">= 200 trades per arm" in w for w in r["warnings"])


def test_ci_including_zero_is_flagged():
    e = epoch()
    r = report([row(e, 7.0 + (5 if i % 2 else -5), ts=2000 + i) for i in range(40)], e)   # net ~0 at 7 %
    assert any("at 7% cost" in w and "includes 0" in w for w in r["warnings"])


# ---------------------------------------------------------------- replay: no look-ahead
def test_bought_comes_only_from_the_decision_episode(tmp_path):
    p = tmp_path / "r.db"
    _db(p, ["tp30"], [])
    db = sqlite3.connect(p)
    db.execute("UPDATE candidates SET bought=0")
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought) VALUES ('C0','C0', 1000 + 7200, 1)")   # 2 h later
    db.commit()
    cand, _ = load(db, "1h")
    assert cand[0]["bought"] is False                     # a later episode's BUY does not leak back
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought) VALUES ('C0','C0', 1000 + 60, 1)")     # same episode
    db.commit()
    cand, _ = load(db, "1h")
    assert cand[0]["bought"] is True
    db.close()


def test_in_sample_result_ignores_everything_after_the_in_sample_window(tmp_path):
    hits = ["tp30", "sl15"] * 20                          # 40 candidates, t = 1000 .. 1390
    _db(tmp_path / "a.db", hits, ["tp30", "sl15", None, "sl15"] * 25)
    _db(tmp_path / "b.db", hits[:24] + ["sl15"] * 16, ["tp30", "sl15", None, "sl15"] * 25)   # holdout outcomes changed
    db = sqlite3.connect(tmp_path / "b.db")              # and the baseline after the in-sample window changed
    db.execute("UPDATE forward_returns SET first_hit='tp30' WHERE anchor='discovery' AND ca LIKE 'B%' AND anchor_ts > 1230")
    db.commit()
    db.close()
    a, b = replay(str(tmp_path / "a.db")), replay(str(tmp_path / "b.db"))
    assert a["walk_forward"]["in_sample"] == b["walk_forward"]["in_sample"]
    la, lb = str(tmp_path / "la.json"), str(tmp_path / "lb.json")
    lock_holdout(str(tmp_path / "a.db"), la, min_n=10)
    lock_holdout(str(tmp_path / "b.db"), lb, min_n=10)
    oa = replay(str(tmp_path / "a.db"), holdout_lock=la, min_n=10)["walk_forward"]["out_of_sample"]
    ob = replay(str(tmp_path / "b.db"), holdout_lock=lb, min_n=10)["walk_forward"]["out_of_sample"]
    assert oa != ob


# ---------------------------------------------------------------- holdout lock
def test_holdout_lock_records_hash_and_time_and_refuses_bad_openings(tmp_path):
    db, lock = str(tmp_path / "r.db"), str(tmp_path / "lock.json")
    _db(tmp_path / "r.db", ["tp30", "sl15"] * 40, ["sl15"] * 50)        # 80 candidates: 48 in / 32 holdout
    closed = replay(db, holdout_lock=lock)
    assert closed["walk_forward"]["out_of_sample"]["status"] == "REFUSED" and "NOT LOCKED" in closed["verdict"]
    assert closed["all"] is None                                        # no pooled peek at the holdout
    res = lock_holdout(db, lock, now=12345.0)
    assert res["status"] == "LOCKED" and res["lock"]["locked_at"] == 12345.0 and res["lock"]["n_in_sample"] == 48
    assert len(res["lock"]["params_hash"]) == 10 and "out_of_sample" not in res
    assert lock_holdout(db, lock)["status"] == "REFUSED"                # one lock per holdout
    opened = replay(db, holdout_lock=lock)
    assert opened["walk_forward"]["out_of_sample"]["candidates"]["n"] == 32 and opened["holdout_lock"]["locked_at"] == 12345.0
    changed = replay(db, "30m", holdout_lock=lock)                     # any parameter differs from the hash
    assert "PARAMETERS CHANGED" in changed["verdict"] and changed["all"] is None
    tuned = replay(db, bought_only=True, holdout_lock=lock)
    assert tuned["walk_forward"]["out_of_sample"]["status"] == "REFUSED"


def test_holdout_stays_closed_below_threshold(tmp_path):
    db, lock = str(tmp_path / "r.db"), str(tmp_path / "lock.json")
    _db(tmp_path / "r.db", ["tp30", "sl15"] * 30, ["sl15"] * 20)        # 60: 36 in / 24 holdout
    assert lock_holdout(db, lock)["status"] == "LOCKED"
    r = replay(db, holdout_lock=lock)
    assert "holdout n = 24 < 30" in r["verdict"] and r["walk_forward"]["out_of_sample"]["status"] == "REFUSED"


def test_frozen_params_hash_depends_on_every_choice():
    from research.replay import frozen_params
    base = frozen_params("1h", 0.6, False, 7)["hash"]
    assert len({base, frozen_params("30m", 0.6, False, 7)["hash"], frozen_params("1h", 0.5, False, 7)["hash"],
                frozen_params("1h", 0.6, True, 7)["hash"], frozen_params("1h", 0.6, False, 8)["hash"]}) == 5
    assert frozen_params("1h", 0.6, False, 7)["hash"] == base


def test_sample_plan_doc_states_the_thresholds():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "sample_plan.md").read_text(encoding="utf-8")
    assert "30" in doc and "200 per arm" in doc and "2026-10-02T05:31:15Z" in doc and "LEGACY" in doc
    assert SCHEMA  # research schema importable
