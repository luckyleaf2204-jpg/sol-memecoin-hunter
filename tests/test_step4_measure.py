"""Step 4 — measurement: per-trade journal (entry, MFE / MAE, exit reason, real cost, engine), one frozen entry engine
(lifecycle) with the others shadow-only, parameter-fingerprinted samples, TP/SL replay vs a random baseline 60/40."""
import asyncio
import sqlite3
import time

import pytest

from research.dataset import SCHEMA, DatasetRecorder
from research.replay import lock_holdout, rates, replay
from test_jupiter_exec import ScriptedJupiter
from test_lifecycle import SECOND_WAVE, Store, history, post_tok
from test_bot_v2 import bot
from test_v12 import MINT, opened
from trading import jupiter as J
from trading.book import PaperBook
from trading.config import TradingConfig
from trading.models import TRADE


def close_on_stop(b, st, p, factor=0.8):
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = b.jupiter.sell_price = p.entry_price * factor
    b.tick()
    asyncio.run(b.execute_sells())


# ---------------------------------------------------------------- journal
def test_journal_row_per_closed_trade(tmp_path):
    b, st, p = opened()
    b.recorder = DatasetRecorder(tmp_path / "r.db")
    close_on_stop(b, st, p)
    r = b.book.journal[-1]
    assert r["trade_id"] == f"{p.id}:{MINT}" and r["exit_reason"] == "stop_loss" and r["entry_price"] == p.entry_price
    assert r["engine"] == "old" and r["sample_id"] == b.cfg.sample_id()
    assert r["mfe_pct"] is not None and r["mae_pct"] == pytest.approx(-20, abs=0.5)
    assert r["real_cost_pct"] == pytest.approx(r["gross_move_pct"] - r["net_pnl_pct"], abs=1e-3) and r["real_cost_pct"] > 0
    row = b.recorder.db.execute("SELECT engine, exit_reason, real_cost_pct FROM trade_journal").fetchone()
    assert row[0] == "old" and row[1] == "stop_loss" and row[2] == pytest.approx(r["real_cost_pct"])
    b.book.save(tmp_path / "book.json")
    assert PaperBook.load(tmp_path / "book.json", 1000.0).journal[-1]["trade_id"] == r["trade_id"]


def test_samples_never_mix_parameters():
    c = TradingConfig()
    sid = c.sample_id()
    assert TradingConfig(kill_switch=True).sample_id() == sid             # operational switches: same sample
    assert TradingConfig(stop_loss_pct=20).sample_id() != sid
    assert TradingConfig(time_stop_min=25).sample_id() != sid
    b = PaperBook(1000.0)
    b.journal = [{"sample_id": "a", "engine": "lifecycle", "net_pnl_usd": 2.0, "gross_move_pct": 5.0,
                  "real_cost_pct": 3.0, "noquote": False},
                 {"sample_id": "a", "engine": "lifecycle", "net_pnl_usd": -1.0, "gross_move_pct": -2.0,
                  "real_cost_pct": 3.0, "noquote": False},
                 {"sample_id": "b", "engine": "lifecycle", "net_pnl_usd": 5.0, "gross_move_pct": 9.0,
                  "real_cost_pct": 3.0, "noquote": False},
                 {"sample_id": "a", "engine": "experimental", "net_pnl_usd": 9.0, "noquote": True}]
    s = b.stats()["samples"]
    assert s["a"]["n"] == 2 and s["a"]["net_usd"] == 1.0 and s["a"]["win_rate"] == 50.0 and s["b"]["n"] == 1
    assert s["a"]["sample"].startswith("INSUFFICIENT") and s["a"]["engines"] == {"lifecycle": 2}


# ---------------------------------------------------------------- one frozen entry engine
def _lifecycle_bot(engine_name, monkeypatch):
    st = post_tok()
    b = bot([st], ScriptedJupiter([J.OK]))
    b.cfg.experimental, b.cfg.lifecycle = True, True
    b.engine.history = Store({st.mint: history(SECOND_WAVE)})
    real = b._lifecycle_decide

    def decide(st_, v, sc, rec, xd, now):
        d = real(st_, v, sc, rec, xd, now)
        rec["engine"] = engine_name
        return d
    monkeypatch.setattr(b, "_lifecycle_decide", decide)
    b.tick()
    return b, st


def test_only_the_frozen_engine_opens_positions(monkeypatch):
    b, st = _lifecycle_bot("experimental", monkeypatch)
    assert b.decisions[st.mint]["decision"] == TRADE and b.decisions[st.mint]["state"] == "SHADOW_ONLY"
    assert not b.intents
    b2, st2 = _lifecycle_bot("lifecycle", monkeypatch)
    assert st2.mint in b2.intents
    asyncio.run(b2.execute_intents())
    assert b2.book.positions[st2.mint].entry_engine == "lifecycle"
    assert TradingConfig().entry_engine == "lifecycle"


# ---------------------------------------------------------------- replay
from replay_fixtures import HZ, make_db as _db  # noqa: E402


def test_rates():
    r = rates([{"first_hit": "tp30", "ret": 5.0}, {"first_hit": "sl15", "ret": -15.0}, {"first_hit": None, "ret": 1.0}])
    assert (r["n"], r["tp_first"], r["sl_first"], r["neither"]) == (3, 1, 1, 1) and r["tp_minus_sl_rate"] == 0.0


def _locked(tmp_path, db, min_n=10, **kw):
    """Lock the holdout on the in-sample part (frozen parameters), then open it with the same parameters."""
    lock = str(tmp_path / "lock.json")
    assert lock_holdout(str(db), lock, HZ, min_n=min_n, **kw)["status"] == "LOCKED"
    return replay(str(db), HZ, holdout_lock=lock, min_n=min_n, **kw)


def test_replay_small_sample_keeps_the_holdout_closed(tmp_path):
    _db(tmp_path / "r.db", ["tp30", "sl15", None], ["sl15"] * 20)
    res = replay(str(tmp_path / "r.db"), HZ)
    assert res["walk_forward"]["out_of_sample"]["status"] == "REFUSED" and res["all"] is None
    assert (res["n_in_sample"], res["n_embargoed"], res["n_holdout"]) == (1, 0, 2)   # last in-sample + 60 s < next
    ins = res["walk_forward"]["in_sample"]
    assert ins["candidates"]["n"] == 1 and "age / liquidity" in ins["baseline"]
    assert ins["baseline_random"]["n_pool"] >= 1                      # matched tokens, never the candidates
    assert lock_holdout(str(tmp_path / "r.db"), str(tmp_path / "l.json"), HZ)["status"] == "REFUSED"   # n < 30


def test_replay_edge_vs_random_and_walk_forward(tmp_path):
    _db(tmp_path / "r.db", ["tp30"] * 30 + ["sl15"] * 10, (["tp30"] + ["sl15"] * 19) * 10)   # random share ~5 %
    res = _locked(tmp_path, tmp_path / "r.db")
    a = res["all"]
    assert a["candidates"]["tp_first_rate"] == 0.75 and a["candidates"]["tp_share"] == 0.75
    assert a["baseline_random"]["mean_tp_share"] < 0.2 and a["baseline_random"]["p_random_at_least_as_good"] == 0.0
    oos = res["walk_forward"]["out_of_sample"]["candidates"]
    assert oos["n"] == 16 and oos["tp_first_rate"] == pytest.approx(6 / 16)      # time order: the last 16
    assert oos["tp_share"] == pytest.approx(6 / 16) and res["break_even_tp_share"] == pytest.approx(1 / 3, abs=1e-4)
    assert res["verdict"].startswith("out-of-sample TP share beats random")    # 37.5 % > 33.3 % and >> random
    other = tmp_path / "bought_only"                  # its own lock: a different frozen parameter set
    other.mkdir()
    only = _locked(other, tmp_path / "r.db", min_n=5, bought_only=True)
    assert only["all"]["candidates"]["n"] == 20


def test_volatility_alone_is_not_an_edge(tmp_path):
    """Candidates hit TP first 4x more often than random tokens, but SL even more: TP share below break-even."""
    _db(tmp_path / "r.db", ["tp30"] * 12 + ["sl15"] * 28, (["tp30"] + ["sl15"] * 3 + [None] * 16) * 5)
    res = _locked(tmp_path, tmp_path / "r.db")
    a = res["all"]
    assert a["candidates"]["tp_first_rate"] > 4 * a["baseline_random"]["mean_tp_first_rate"]
    assert a["candidates"]["tp_share"] == pytest.approx(0.3) and "no" in res["verdict"]
