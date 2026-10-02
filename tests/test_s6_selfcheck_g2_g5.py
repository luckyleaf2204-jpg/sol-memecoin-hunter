"""s6 self-check, G2-G5: evidence the review asked for that the G-round tests did not show yet."""
import json
import sqlite3
import time

import trading.backtest as BT
from replay_fixtures import HZ, make_db
from research.replay import load, replay
from test_trading import _row
from trading.book import PaperBook
from trading.config import TradingConfig
from trading.execution import HAIRCUT_MODEL


# ---------------------------------------------------------------- G2: one fill model for entries and exits
def test_backtest_entry_and_exit_use_the_same_model(monkeypatch):
    bots = []

    class Spy(BT.PaperBot):
        def __init__(self, *a, **kw):
            super().__init__(*a, **kw)
            bots.append(self)
    monkeypatch.setattr(BT, "PaperBot", Spy)
    t0 = time.time() - 3600
    rows = [_row(t0, 0.0002), _row(t0 + 20, 0.00021), _row(t0 + 40, 0.00019, liq=8_000)]
    res = BT.backtest(rows, TradingConfig(seed=1), allow_legacy=True)
    ex = bots[0].book.executions
    buys, sells = [e for e in ex if e.side == "BUY"], [e for e in ex if e.side == "SELL"]
    assert buys and sells and bots[0].jupiter is None and bots[0].exit_fallback == "model"
    assert {e.model for e in buys} == {e.model for e in sells}            # the same liquidity model both ways
    assert HAIRCUT_MODEL not in {e.model for e in ex} and "Jupiter" not in buys[0].model
    assert "APPROXIMATE" in res["approximation"]


# ---------------------------------------------------------------- G3: no look-ahead in the replay population
def test_replay_reads_no_bought_flag_and_no_later_event(tmp_path):
    p = tmp_path / "r.db"
    make_db(p, ["tp30", "sl15"] * 3, ["sl15"] * 10)
    a = replay(str(p), HZ)
    db = sqlite3.connect(p)
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought, engine) VALUES ('C0','C0', 1, 1, 'lifecycle')")
    db.execute("INSERT INTO candidates (ca, symbol, ts, bought, engine) VALUES ('K9','K9', 1, 1, 'lifecycle')")
    db.execute("INSERT INTO gate_events (ca, ts, kind, reasons, age_s, liquidity_usd, price, lifecycle) "
               "VALUES ('C0', 99999, 'entered', '[]', 99999, 1, 1, 'NEW')")         # a LATER re-entry of C0
    db.commit()
    rows, _ = load(db, HZ)
    c0 = [r for r in rows if r["ca"] == "C0"][0]
    assert "K9" not in {r["ca"] for r in rows}                         # 'bought' flags are not the population
    assert c0["ts"] == 10_000.0 and c0["age_s"] == 200.0 and c0["liq"] == 7000.0   # the decision's own values
    db.close()
    assert replay(str(p), HZ)["walk_forward"]["in_sample"] == a["walk_forward"]["in_sample"]


def test_blocked_then_entered_tokens_are_counted(tmp_path):
    p = tmp_path / "r.db"
    make_db(p, ["tp30"] * 4, ["sl15"] * 10, blocked_hits=["sl15"] * 2)
    db = sqlite3.connect(p)
    db.execute("INSERT INTO gate_events (ca, ts, kind, reasons, age_s, liquidity_usd, price, lifecycle) "
               "VALUES ('C1', 9000, 'blocked', '[]', 100, 7000, 1, 'NEW')")
    db.execute("INSERT INTO forward_returns VALUES ('C1','gate_blocked',9000,1.0,?,60,1.1,9000,1,1,0,0,1,'sl15',5,'t')",
               (HZ,))
    db.commit()
    rows, info = load(db, HZ)
    db.close()
    assert info["blocked_then_entered"] == 1 and len(info["gate_blocked"]) == 3


# ---------------------------------------------------------------- G4: an old paper_bot.json still loads
def test_old_book_file_without_the_new_fields_loads(tmp_path):
    f = tmp_path / "paper_bot.json"
    f.write_text(json.dumps({"starting": 1000.0, "cash": 990.0, "positions": [], "closed": []}), encoding="utf-8")
    b = PaperBook.load(f, 1000.0)
    assert b.cash == 990.0 and b.last_buy_attempt == {} and b.gate_seen == {}


# ---------------------------------------------------------------- G5: halted = nothing runs
def test_halted_server_runs_no_bot_loop(tmp_path, monkeypatch):
    import gzip

    from fastapi.testclient import TestClient

    import web.app as webapp
    from core import snapshot as S
    from core.config import ApiKeys, Settings
    from database.db import Database
    from scanner.engine import ScannerEngine
    from test_fix3_snapshot import _data
    from trading.bot import PaperBot
    store_dir, data = tmp_path / "store", tmp_path / "data"
    S.snapshot(_data(tmp_path / "src"), S.LocalStore(store_dir))
    man = json.loads(S.LocalStore(store_dir).get(S.MANIFEST))
    for meta in man["files"].values():
        S.LocalStore(store_dir).put(meta["object"], gzip.compress(b"corrupt"))
    data.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", data)
    monkeypatch.setenv("SNAPSHOT_DIR", str(store_dir))
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    calls = []

    async def no_run(self, stop=None):
        calls.append("bot")
    monkeypatch.setattr(PaperBot, "run", no_run)
    eng = ScannerEngine(Settings(), Database(tmp_path / "e.db"), keys=ApiKeys(), on_log=lambda m: None)

    async def no_scan():
        calls.append("scanner")
    eng.run = no_scan
    app = webapp.create_app(engine=eng, start_scanner=True, access_code="c0de")
    with TestClient(app) as c:
        assert c.get("/healthz").json()["ok"] is False
    assert calls == [] and not list(data.iterdir())                     # no bot, no scanner, no files written
    assert json.loads(S.LocalStore(store_dir).get(S.MANIFEST))["gen"] == man["gen"]   # and no snapshot
