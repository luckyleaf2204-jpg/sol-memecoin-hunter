"""Step E — sample-size warnings, replay without look-ahead, frozen walk-forward parameters / one-shot holdout."""
import sqlite3

import pytest

from research.dataset import SCHEMA
from research.replay import load, replay
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
    assert a["walk_forward"]["out_of_sample"] != b["walk_forward"]["out_of_sample"]


# ---------------------------------------------------------------- frozen parameters / one-shot holdout
def test_holdout_is_evaluated_once_per_parameter_set(tmp_path):
    _db(tmp_path / "r.db", ["tp30", "sl15"] * 20, ["sl15"] * 50)
    log = str(tmp_path / "holdout.json")
    first = replay(str(tmp_path / "r.db"), "1h", holdout_log=log)
    assert first["warnings"] == [] and len(first["frozen_params"]["hash"]) == 10
    again = replay(str(tmp_path / "r.db"), "1h", holdout_log=log)
    assert again["warnings"] == []                                         # same frozen parameters: fine
    tuned = replay(str(tmp_path / "r.db"), "1h", bought_only=True, holdout_log=log)
    assert tuned["warnings"] and "HOLDOUT ALREADY USED" in tuned["warnings"][0]
    assert "first 60 %" in first["protocol"]


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
