"""Durable paper sample on an ephemeral host: snapshot + restore, GAP flags, keep-alive, report summary line."""
import asyncio
import gzip
import json
import sqlite3

import pytest

from core import snapshot as S
from trading.book import PaperBook
from trading.gaps import GAP_MIN_S, GapTracker, overlaps
from trading.sample_epoch import SampleEpoch
from trading.sample_report import report, summary_line
from web import keepalive as K
from test_stepB_report import epoch, row


# ---------------------------------------------------------------- 1. snapshot / restore
def _data_dir(d):
    d.mkdir()
    book = PaperBook(1000.0)
    book.journal = [{"trade_id": "1:A", "epoch": "e", "entry_ts": 10.0}]
    book.heartbeat = 1000.0
    book.save(d / "paper_bot.json")
    e = SampleEpoch(d / "sample_epoch.json")
    e.start("02e5bdcc03", "abc", now=500.0)
    db = sqlite3.connect(d / "research.db")
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("CREATE TABLE t (x)")
    db.execute("INSERT INTO t VALUES (42)")
    db.commit()                                           # WAL not checkpointed: the backup API must still see it
    (d / "truth_ledger.json").write_text(json.dumps({"trades": [1, 2]}), encoding="utf-8")
    return db


def test_snapshot_and_restore_after_a_wiped_container(tmp_path):
    db = _data_dir(tmp_path / "data")
    store = S.LocalStore(tmp_path / "durable")
    man = S.snapshot(tmp_path / "data", store, commit="abc", now=2000.0)
    assert set(man["files"]) == {"paper_bot.json", "sample_epoch.json", "research.db", "truth_ledger.json"}
    db.close()
    fresh = tmp_path / "fresh"                            # a new container: empty DATA_DIR
    fresh.mkdir()
    res = S.restore(fresh, store)
    assert res["status"] == "RESTORED" and set(res["restored"]) == set(man["files"]) and res["snapshot_ts"] == 2000.0
    book = PaperBook.load(fresh / "paper_bot.json", 1000.0)
    assert book.journal[0]["trade_id"] == "1:A" and book.heartbeat == 1000.0
    assert sqlite3.connect(fresh / "research.db").execute("SELECT x FROM t").fetchone() == (42,)
    e = SampleEpoch(fresh / "sample_epoch.json")
    assert e.start("02e5bdcc03", "def", now=9000.0) is False and e.started_at == 500.0     # the sample continues


def test_restore_never_overwrites_local_files_and_checks_integrity(tmp_path):
    _data_dir(tmp_path / "data").close()
    store = S.LocalStore(tmp_path / "durable")
    S.snapshot(tmp_path / "data", store)
    live = tmp_path / "live"
    live.mkdir()
    (live / "paper_bot.json").write_text("{\"cash\": 1}", encoding="utf-8")
    res = S.restore(live, store)
    assert "paper_bot.json" in res["skipped"] and (live / "paper_bot.json").read_text() == "{\"cash\": 1}"
    store.put("truth_ledger.json.gz", gzip.compress(b"tampered"))
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(ValueError):                      # checksum mismatch: nothing silently restored
        S.restore(other, store)
    assert S.restore(tmp_path, S.LocalStore(tmp_path / "empty"))["status"] == "NO SNAPSHOT"


def test_store_from_env(monkeypatch, tmp_path):
    monkeypatch.delenv("SNAPSHOT_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_URL", raising=False)
    assert S.store_from_env() is None                     # NOT CONFIGURED
    monkeypatch.setenv("SNAPSHOT_URL", "https://store.example/prefix/")
    monkeypatch.setenv("SNAPSHOT_TOKEN", "t0k")
    st = S.store_from_env()
    assert st.kind == "http" and st.url == "https://store.example/prefix" and st._headers() == {"Authorization": "Bearer t0k"}
    monkeypatch.setenv("SNAPSHOT_DIR", str(tmp_path))
    assert S.store_from_env().kind == "dir"               # a mounted disk wins


def test_http_store_put_get(monkeypatch):
    import httpx
    blobs = {}

    def put(url, content, headers, timeout):
        blobs[url] = content
        return httpx.Response(200, request=httpx.Request("PUT", url))

    def get(url, headers, timeout):
        return httpx.Response(200 if url in blobs else 404, content=blobs.get(url, b""), request=httpx.Request("GET", url))
    monkeypatch.setattr(httpx, "put", put)
    monkeypatch.setattr(httpx, "get", get)
    st = S.HttpStore("https://s.example/p")
    st.put("a.gz", b"xyz")
    assert st.get("a.gz") == b"xyz" and st.get("missing") is None


# ---------------------------------------------------------------- 2. gaps
def test_gaps_from_pause_restart_and_feed():
    book = PaperBook(1000.0)
    g = GapTracker(book)
    g.beat(1000.0, True)
    g.beat(1005.0, True)
    g.beat(1005.0 + GAP_MIN_S + 1, True)                 # loop paused (slept)
    assert book.gaps[-1]["cause"] == "loop paused"
    t = 2000.0
    g.beat(t, False)                                      # feed down ...
    g.beat(t + 200, False)
    assert g.all_gaps(t + 200) == book.gaps              # not yet 5 min
    g.beat(t + 290, False)
    g.beat(t + 400, False)
    assert g.all_gaps(t + 400)[-1]["cause"] == "feed down (ongoing)"
    g.beat(t + 410, True)                                 # ... recovered after 410 s
    assert book.gaps[-1]["cause"] == "feed down" and book.gaps[-1]["minutes"] == pytest.approx(6.8)
    g2 = GapTracker(book)
    g2.on_start(t + 410 + 3600)                           # restart one hour after the last heartbeat
    assert [x["cause"] for x in book.gaps] == ["loop paused", "loop paused",   # 1005->1306 and 1306->2000
                                               "feed down", "restart / sleep"]


def test_gaps_persist_with_the_book(tmp_path):
    b = PaperBook(1000.0)
    b.gaps, b.heartbeat = [{"start": 1.0, "end": 400.0, "minutes": 6.6, "cause": "restart / sleep"}], 400.0
    b.save(tmp_path / "b.json")
    l = PaperBook.load(tmp_path / "b.json", 1000.0)
    assert l.gaps == b.gaps and l.heartbeat == 400.0


def test_trades_overlapping_a_gap_are_excluded_and_counted():
    e = epoch()                                           # started at 1000
    rows = [row(e, 10, ts=2000), row(e, -10, ts=3000), row(e, 20, ts=4000)]   # held 60 s each
    gaps = [{"start": 3030.0, "end": 3500.0, "minutes": 7.8, "cause": "restart / sleep"},
            {"start": 100.0, "end": 400.0, "minutes": 5.0, "cause": "restart / sleep"}]   # before the epoch
    assert overlaps(rows[1], gaps, 9999) and not overlaps(rows[0], gaps, 9999)
    r = report(rows, e, gaps=gaps, now=9999.0)
    assert r["n"] == 2 and r["gaps"]["count"] == 1 and r["gaps"]["excluded_trades"] == 1
    assert r["by_cost"]["5%"]["expectancy_pct"] == pytest.approx((5 + 15) / 2)
    open_row = {**row(e, 5, ts=3400), "exit_ts": None}
    assert overlaps(open_row, gaps, 9999)


def test_bot_records_a_restart_gap_at_startup(tmp_path):
    from test_v12 import opened
    b, st, p = opened()
    b.book.heartbeat = 1000.0
    b.sample_epoch = SampleEpoch(tmp_path / "e.json")
    b.begin_sample(now=1000.0 + 3600)
    assert b.book.gaps[-1]["cause"] == "restart / sleep" and b.book.gaps[-1]["minutes"] == 60.0
    assert b.sample_report(1000.0 + 3700)["gaps"]["count"] >= 0


# ---------------------------------------------------------------- 3. keep-alive
def test_keepalive_url(monkeypatch):
    monkeypatch.delenv("KEEPALIVE", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    assert K.keepalive_url() is None                      # not on Render
    monkeypatch.setenv("RENDER_EXTERNAL_URL", "https://sol-memecoin-hunter.onrender.com/")
    assert K.keepalive_url() == "https://sol-memecoin-hunter.onrender.com/healthz"
    monkeypatch.setenv("KEEPALIVE", "0")
    assert K.keepalive_url() is None                      # paid plan: off


def test_keepalive_ping_and_loop():
    import httpx
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, json={"ok": True})

    async def go():
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            ok = await K.ping("https://x.example/healthz", c)
            task = asyncio.create_task(K.keepalive_loop("https://x.example/healthz", every=0.01, client=c))
            await asyncio.sleep(0.05)
            task.cancel()
            return ok
    assert asyncio.run(go()) is True and len(calls) >= 3 and all(u.endswith("/healthz") for u in calls)


# ---------------------------------------------------------------- 5. summary line
def test_summary_line_prints_n_ci_and_status():
    e = epoch()
    r = report([row(e, 12.0 if i % 2 else -2.0, ts=2000 + i) for i in range(40)], e)
    line = summary_line(r)
    assert line.startswith("SAMPLE PRELIMINARY n=40") and "30 preliminary / 200 A/B" in line
    assert "@5%" in line and "[" in line and "gaps 0" in line
    assert summary_line(report([], e)).startswith("SAMPLE INSUFFICIENT n=0") and "[CI n/a]" in summary_line(report([], e))
