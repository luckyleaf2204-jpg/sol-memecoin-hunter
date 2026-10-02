"""Review round 3 (A1): the snapshot / lease path never dies silently and never skips the release + engine stop."""
import time

from fastapi.testclient import TestClient

import core.lease as L
import web.app as webapp
from core import snapshot as S
from test_s6_lease import _app, _wait


class Flaky(S.LocalStore):
    """A store whose lease reads fail like an HTTP store answering 5xx / timing out."""
    def __init__(self, root, exc=OSError):
        super().__init__(root)
        self.exc, self.fail = exc, False

    def get(self, name):
        if self.fail and name == L.LEASE:
            raise self.exc("store 503 / timeout")
        return super().get(name)


def _setup(tmp_path, monkeypatch, store):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    monkeypatch.setattr(S, "store_from_env", lambda: store)
    monkeypatch.setenv("RESEARCH_LOG", "0")
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)


def test_held_error_is_logged_and_the_hourly_loop_keeps_going(tmp_path, monkeypatch, capsys):
    store = Flaky(tmp_path / "store")
    _setup(tmp_path, monkeypatch, store)
    monkeypatch.setattr(S, "SNAPSHOT_EVERY_S", 0.05)
    app = _app(tmp_path, "a")
    with TestClient(app):
        st = app.state.hunter
        assert st["role"] == "ACTIVE"
        store.fail = True
        assert _wait(lambda: (st["snapshot"].get("last_error") or {}).get("reason") == "hourly")
        time.sleep(0.3)
        n_fail = capsys.readouterr().out.count("[snapshot] hourly FAILED")
        assert n_fail >= 2                                    # the loop survived the first failure
        store.fail = False
        assert _wait(lambda: st["snapshot"].get("last_error") is None)      # and recovers by itself


def test_shutdown_still_releases_and_stops_the_engine_when_held_fails(tmp_path, monkeypatch, capsys):
    store = Flaky(tmp_path / "store", exc=TimeoutError)
    _setup(tmp_path, monkeypatch, store)
    app = _app(tmp_path, "a")
    stopped = []
    eng = app.state.hunter["engine"]
    real_stop = eng.stop
    eng.stop = lambda: (stopped.append(1), real_stop())
    c = TestClient(app)
    c.__enter__()
    store.fail = True                                         # held() and release() both fail at SIGTERM
    c.__exit__(None, None, None)
    out = capsys.readouterr().out
    assert "[snapshot] shutdown FAILED: TimeoutError" in out and "[lease] release failed: TimeoutError" in out
    assert stopped == [1]


# ---------------------------------------------------------------- A2: a short shutdown snapshot
class SlowDB(S.LocalStore):
    """research.db uploads take `delay` seconds (slow HTTP store); everything else is fast."""
    def __init__(self, root, delay=0.0):
        super().__init__(root)
        self.delay = delay

    def put(self, name, data):
        if "research.db" in name and self.delay:
            time.sleep(self.delay)
        super().put(name, data)


def _files(d, cash):
    import json
    import sqlite3
    d.mkdir(parents=True, exist_ok=True)
    (d / "paper_bot.json").write_text(json.dumps({"cash": cash}), encoding="utf-8")
    (d / "sample_epoch.json").write_text("{}", encoding="utf-8")
    db = sqlite3.connect(d / "research.db")
    db.execute("CREATE TABLE IF NOT EXISTS t (v INT)")
    db.execute("INSERT INTO t VALUES (?)", (cash,))
    db.commit()
    db.close()


def test_book_first_research_db_carried_when_the_store_is_slow(tmp_path):
    import json
    import sqlite3
    d, store = tmp_path / "d", SlowDB(tmp_path / "store")
    _files(d, 1)
    m1 = S.snapshot(d, store, now=1.0)                                # normal hourly snapshot
    assert list(m1["files"])[:2] == ["paper_bot.json", "sample_epoch.json"] and list(m1["files"])[-1] == "research.db"
    assert set(m1["steps"]) == {"paper_bot.json", "sample_epoch.json", "research.db"}
    _files(d, 2)
    store.delay = 1.0
    t0 = time.time()
    m2 = S.snapshot(d, store, now=2.0, slow_timeout_s=0.2)           # SIGTERM with a slow store
    assert time.time() - t0 < 0.9
    assert m2["files"]["research.db"]["carried_from"] == 1 and "research.db (timeout, carried)" in m2["steps"]
    time.sleep(1.1)                                                   # the late upload lands in the NEW slot only
    fresh = tmp_path / "fresh"
    r = S.restore(fresh, S.LocalStore(tmp_path / "store"))
    assert r["gen"] == 2 and json.loads((fresh / "paper_bot.json").read_text()) == {"cash": 2}   # newest book
    db = sqlite3.connect(fresh / "research.db")
    assert db.execute("SELECT MAX(v) FROM t").fetchone()[0] == 1      # the previous verified research.db
    db.close()


def test_render_yaml_shutdown_delay():
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "render.yaml").read_text(encoding="utf-8")
    import re
    m = re.search(r"maxShutdownDelaySeconds:\s*(\d+)", text)
    assert m and 120 <= int(m.group(1)) <= 300 and S.SHUTDOWN_SLOW_TIMEOUT_S + 10 + 30 <= int(m.group(1))


# ---------------------------------------------------------------- A3: no durable store -> no new entries
def test_not_durable_blocks_new_entries_but_counts_them():
    import asyncio

    from test_bot_v2 import FakeJupiter, bot, good
    st = good()
    b = bot([st], FakeJupiter())
    b.entry_block = "MẪU KHÔNG BỀN - sẽ mất khi restart (no store)"
    b.tick()
    asyncio.run(b.execute_intents())
    assert not b.book.positions and not b.intents and b.decisions[st.mint]["state"] == "NOT_DURABLE"
    assert st.mint in b.entry_block_seen
    r = b.sample_report()
    assert r["warnings"][0].startswith("NO NEW ENTRIES") and r["entries_blocked"]["tokens_not_bought"] == 1
    b.entry_block = None                                       # store configured -> trades again
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions


def test_open_positions_still_exit_while_entries_are_blocked():
    import asyncio

    from test_v12 import MINT, opened
    b, st, p = opened()
    b.entry_block = "not durable"
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.8
    b.tick()
    asyncio.run(b.execute_sells())
    assert MINT not in b.book.positions


def test_server_without_store_blocks_entries_and_says_so(tmp_path, monkeypatch):
    d = tmp_path / "d"
    d.mkdir()
    monkeypatch.setattr(webapp, "DATA_DIR", d)
    for k in ("SNAPSHOT_DIR", "SNAPSHOT_URL", "RENDER_EXTERNAL_URL", "ALLOW_NOT_DURABLE"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("RESEARCH_LOG", "0")
    app = _app(tmp_path, "a")
    with TestClient(app) as c:
        h = c.get("/healthz").json()
        assert h["entries"].startswith("BLOCKED") and h["snapshot"]["warning"] == S.NOT_DURABLE
        assert app.state.hunter["bot"].entry_block.startswith(S.NOT_DURABLE)
    monkeypatch.setenv("ALLOW_NOT_DURABLE", "1")                 # local development override
    app = _app(tmp_path, "b")
    with TestClient(app) as c:
        assert "entries" not in c.get("/healthz").json() and app.state.hunter["bot"].entry_block is None


# ---------------------------------------------------------------- B5: gate evaluation never blocks the tick
def test_slow_gate_evaluation_runs_beside_the_tick(monkeypatch):
    import asyncio

    import research.gate_eval as GE
    import trading.bot as B
    from test_v12 import opened
    monkeypatch.setattr(B, "TICK_S", 0.02)
    b, st, p = opened()

    class Rec:
        path = "unused.db"

        async def run_followups(self):
            return None
    b.recorder = Rec()
    b._rec = lambda *a, **k: None
    seen = {}

    def slow_eval(path, seed, since):
        seen["ticks_at_start"] = b.ticks
        time.sleep(0.5)                                            # a big research.db
        seen["ticks_at_end"] = b.ticks
        return {"n_blocked": 0, "n_entered": 0}
    monkeypatch.setattr(GE, "evaluate", slow_eval)
    stop = asyncio.Event()

    async def scenario():
        task = asyncio.create_task(b.run(stop))
        for _ in range(100):
            await asyncio.sleep(0.02)
            if b.gate_eval_cache:
                break
        stop.set()
        await task
    asyncio.run(scenario())
    assert seen["ticks_at_end"] - seen["ticks_at_start"] >= 10 and b.gate_eval_cache["n_blocked"] == 0
