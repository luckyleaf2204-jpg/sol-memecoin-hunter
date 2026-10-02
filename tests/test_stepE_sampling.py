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
from replay_fixtures import HZ, make_db  # noqa: E402


def test_bought_comes_only_from_the_decision_episode(tmp_path):
    p = tmp_path / "r.db"
    make_db(p, ["tp30"], [])
    db = sqlite3.connect(p)
    db.execute("UPDATE candidates SET bought=0")
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought, engine) VALUES ('C0','C0', 10000 + 7200, 1, 'lifecycle')")
    db.commit()
    cand, _ = load(db, HZ)
    assert cand[0]["bought"] is False                     # a later episode's BUY does not leak back
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought, engine) VALUES ('C0','C0', 10000 + 60, 1, 'lifecycle')")
    db.commit()
    cand, _ = load(db, HZ)
    assert cand[0]["bought"] is True
    db.close()


def test_in_sample_result_ignores_everything_after_the_in_sample_window(tmp_path):
    hits, base = ["tp30", "sl15"] * 20, (["tp30"] + ["sl15"] * 3) * 25          # 40 candidates, t 10000 .. 13900
    make_db(tmp_path / "a.db", hits, base)
    make_db(tmp_path / "b.db", hits[:24] + ["sl15"] * 16, base)                 # holdout outcomes changed ...
    db = sqlite3.connect(tmp_path / "b.db")                                     # ... and later baseline prices too
    db.execute("UPDATE price_path SET price=1.35 WHERE ca IN (SELECT ca FROM token_snapshots WHERE ca LIKE 'B%' "
               "AND ts >= 12400)")
    db.commit()
    db.close()
    a, b = replay(str(tmp_path / "a.db"), HZ), replay(str(tmp_path / "b.db"), HZ)
    assert a["walk_forward"]["in_sample"] == b["walk_forward"]["in_sample"]
    la, lb = str(tmp_path / "la.json"), str(tmp_path / "lb.json")
    lock_holdout(str(tmp_path / "a.db"), la, HZ, min_n=10)
    lock_holdout(str(tmp_path / "b.db"), lb, HZ, min_n=10)
    oa = replay(str(tmp_path / "a.db"), HZ, holdout_lock=la, min_n=10)["walk_forward"]["out_of_sample"]
    ob = replay(str(tmp_path / "b.db"), HZ, holdout_lock=lb, min_n=10)["walk_forward"]["out_of_sample"]
    assert oa != ob


def test_embargo_drops_decisions_inside_the_last_in_sample_outcome_window(tmp_path):
    make_db(tmp_path / "r.db", ["tp30", "sl15"] * 10, ["sl15"] * 30, step=25.0)    # 25 s apart, horizon 60 s
    r = replay(str(tmp_path / "r.db"), HZ)
    assert r["n_in_sample"] == 12 and r["n_embargoed"] == 2 and r["n_holdout"] == 6
    assert r["walk_forward"]["embargo_s"] == 60


def test_only_lifecycle_candidates_count(tmp_path):
    engines = ["lifecycle", "experimental", "old", None] * 5
    make_db(tmp_path / "r.db", ["tp30"] * 20, ["sl15"] * 20, engines=engines)
    r = replay(str(tmp_path / "r.db"), HZ)
    assert r["selection"]["excluded_other_engine"] == 15 and r["selection"]["all_candidates"] == 20
    assert r["n_in_sample"] + r["n_embargoed"] + r["n_holdout"] == 5


def test_baseline_matches_age_and_liquidity(tmp_path):
    make_db(tmp_path / "r.db", ["tp30"] * 5, ["sl15"] * 20, base_liq=500_000.0)     # baseline: other liquidity bucket
    r = replay(str(tmp_path / "r.db"), HZ)
    assert r["walk_forward"]["in_sample"]["baseline_random"]["status"] == "NO BASELINE"


# ---------------------------------------------------------------- frozen parameters / holdout lock
def test_holdout_lock_records_hash_and_time_and_refuses_bad_openings(tmp_path):
    db, lock = str(tmp_path / "r.db"), str(tmp_path / "lock.json")
    make_db(tmp_path / "r.db", ["tp30", "sl15"] * 40, ["sl15"] * 50)     # 80 candidates: 48 in / 32 holdout
    closed = replay(db, HZ, holdout_lock=lock)
    assert closed["walk_forward"]["out_of_sample"]["status"] == "REFUSED" and "NOT LOCKED" in closed["verdict"]
    assert closed["all"] is None                                        # no pooled peek at the holdout
    res = lock_holdout(db, lock, HZ, now=12345.0, commit="c1")
    assert res["status"] == "LOCKED" and res["lock"]["locked_at"] == 12345.0 and res["lock"]["n_in_sample"] == 48
    assert len(res["lock"]["params_hash"]) == 10 and "out_of_sample" not in res and res["lock"]["embargo_s"] == 60
    assert lock_holdout(db, lock, HZ, commit="c1")["status"] == "REFUSED"          # one lock per holdout
    opened = replay(db, HZ, holdout_lock=lock, commit="c1")
    assert opened["walk_forward"]["out_of_sample"]["candidates"]["n"] == 32 and opened["holdout_lock"]["locked_at"] == 12345.0
    for kw in ({"commit": "c2"}, {"commit": "c1", "sample_id": "other"}, {"commit": "c1", "min_n": 20},
               {"commit": "c1", "bought_only": True}):
        changed = replay(db, HZ, holdout_lock=lock, **kw)                 # any frozen choice differs from the hash
        assert "PARAMETERS CHANGED" in changed["verdict"] and changed["all"] is None, kw
    assert "PARAMETERS CHANGED" in replay(db, "30m", holdout_lock=lock, commit="c1")["verdict"]


def test_holdout_stays_closed_below_threshold(tmp_path):
    db, lock = str(tmp_path / "r.db"), str(tmp_path / "lock.json")
    make_db(tmp_path / "r.db", ["tp30", "sl15"] * 30, ["sl15"] * 20)        # 60: 36 in / 24 holdout
    assert lock_holdout(db, lock, HZ, commit="c")["status"] == "LOCKED"
    r = replay(db, HZ, holdout_lock=lock, commit="c")
    assert "holdout n = 24 < 30" in r["verdict"] and r["walk_forward"]["out_of_sample"]["status"] == "REFUSED"


def test_frozen_params_hash_depends_on_every_choice():
    from research.replay import frozen_params
    f = lambda **kw: frozen_params(**{"horizon": "1h", "split": 0.6, "bought_only": False, "seed": 7,  # noqa: E731
                                      "sample_id": "s", "commit": "c", **kw})["hash"]
    variants = [f(), f(horizon="30m"), f(split=0.5), f(bought_only=True), f(seed=8), f(min_n=20), f(engine="old"),
                f(sample_id="s2"), f(commit="c2")]
    assert len(set(variants)) == len(variants) and f() == variants[0]
    from trading.config import production_config
    assert frozen_params("1h", 0.6, False, 7, commit="c")["sample_id"] == production_config().sample_id()


def test_sample_plan_doc_states_the_thresholds():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "sample_plan.md").read_text(encoding="utf-8")
    assert "30" in doc and "200 per arm" in doc and "2026-10-02T05:31:15Z" in doc and "LEGACY" in doc
    assert SCHEMA  # research schema importable
