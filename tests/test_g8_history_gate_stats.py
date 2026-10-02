"""G8 — price history survives a quick restart (snapshot file, loaded only when fresh); gate block rate by reason."""
import json

from core import snapshot as S
from core.models import MarketData
from history import persist
from history.store import HistoryStore
from test_step2_chasing import bot, post_tok
from trading.book import PaperBook
from trading.gate_stats import block_rates, reason_key, record
from trading.lifecycle_decision import TRADE

NOW = 100_000.0


def _store(points):
    h = HistoryStore()
    for mint, ts, price in points:
        h.get(mint).add_market(MarketData(price_usd=price, liquidity_usd=5000.0, vol_5m=100.0, pair_address="P"), ts)
    return h


def test_price_history_round_trip_when_fresh(tmp_path):
    src = _store([("M", NOW - 2000 + 20 * i, 1.0 + i / 100) for i in range(100)])
    info = persist.dump(src, tmp_path / persist.FILE, now=NOW)
    assert info["tokens"] == 1 and info["points"] == len([p for p in src.get("M").points if NOW - p.ts <= persist.KEEP_S])
    dst = HistoryStore()
    r = persist.load(dst, tmp_path / persist.FILE, now=NOW + 60)
    assert r["status"] == "LOADED" and r["points"] == info["points"]
    a, b = list(src.get("M").points)[-1], list(dst.get("M").points)[-1]
    assert (a.ts, a.price, a.liq, a.pair) == (b.ts, b.price, b.liq, b.pair)


def test_stale_or_bad_history_is_not_loaded(tmp_path):
    f = tmp_path / persist.FILE
    persist.dump(_store([("M", NOW - 30, 1.0), ("M", NOW - 5, 1.1)]), f, now=NOW)
    dst = HistoryStore()
    r = persist.load(dst, f, now=NOW + 600)                  # restart 10 min later: a hole the gate cannot see
    assert r["status"].startswith("STALE") and len(dst) == 0
    f.write_text("{oops", encoding="utf-8")
    assert persist.load(dst, f, now=NOW)["status"].startswith("UNREADABLE")
    assert persist.load(dst, tmp_path / "none.json", now=NOW)["status"] == "NONE"


def test_live_points_are_never_mixed_with_loaded_ones(tmp_path):
    f = tmp_path / persist.FILE
    persist.dump(_store([("M", NOW - 60, 1.0), ("M", NOW - 30, 1.1)]), f, now=NOW)
    dst = _store([("M", NOW, 2.0)])
    persist.load(dst, f, now=NOW)
    assert [p.price for p in dst.get("M").points] == [2.0]


def test_price_history_is_snapshotted_but_optional_for_the_partial_check(tmp_path):
    assert "price_history.json" in S.FILES and "price_history.json" in S.OPTIONAL
    d = tmp_path / "data"
    d.mkdir()
    for n in ("paper_bot.json", "sample_epoch.json", "price_history.json"):
        (d / n).write_text("{}", encoding="utf-8")
    store = S.LocalStore(tmp_path / "store")
    S.snapshot(d, store)
    (d / "price_history.json").unlink()                       # warm restart before the history was rewritten
    assert S.restore(d, store)["status"].startswith("LOCAL DATA PRESENT")
    fresh = tmp_path / "fresh"
    assert "price_history.json" in S.restore(fresh, store)["restored"]


def test_reason_keys():
    assert reason_key("entry_location: history 120s < 300s") == "history<300s"
    assert reason_key("entry_location: PULLBACK still falling (new low 30s ago < 180s)") == "PULLBACK new low<180s"
    assert reason_key("entry_location: extension_5m 55% > 40%") == "extension_5m"
    assert reason_key("entry_location: EXTENDED") == "EXTENDED"


def test_block_rates_count_unique_tokens_in_the_epoch():
    seen = {}
    record(seen, "A", ["entry_location: UNKNOWN", "entry_location: history 10s < 300s"], 10.0)
    record(seen, "A", ["entry_location: UNKNOWN"], 20.0)                   # same token again: still one
    record(seen, "A", [], 30.0)                                            # later passed
    record(seen, "B", ["entry_location: EXTENDED"], 40.0)
    record(seen, "C", [], 50.0)
    record(seen, "OLD", ["entry_location: EXTENDED"], 1.0)                 # before the epoch
    r = block_rates(seen, since=5.0)
    assert r["tokens_at_gate"] == 3 and r["passed_once"] == 2 and r["never_passed"] == 1 and r["blocked_any"] == 2
    assert r["by_reason"]["UNKNOWN"] == {"tokens": 1, "rate": round(1 / 3, 4)}
    assert r["by_reason"]["EXTENDED"]["tokens"] == 1 and r["by_reason"]["history<300s"]["tokens"] == 1


def test_gate_records_and_book_persists_and_report_shows_it(tmp_path):
    b = bot([post_tok()])
    st = b.engine.published[0]
    b._entry_location_gate(st, {"entry_location": "EXTENDED", "entry_extension": 10.0,
                                "entry_location_detail": {"history_s": 400.0, "last_low_age_s": 300.0}}, TRADE, 1e9)
    assert b.book.gate_seen[st.mint]["reasons"] == ["EXTENDED"]
    b.sample_epoch.started_at = 0.0
    r = b.sample_report(now=1e9)
    assert r["gate_block_rates"]["by_reason"]["EXTENDED"]["rate"] == 1.0
    from trading.sample_report import summary_line
    assert summary_line(r).startswith("gate blocked 1/1 tokens")
    b.book.save(tmp_path / "pb.json")
    assert PaperBook.load(tmp_path / "pb.json", 1000.0).gate_seen == b.book.gate_seen
    assert json.loads((tmp_path / "pb.json").read_text())["gate_seen"][st.mint]["passed"] is False


def test_server_loads_fresh_price_history_at_start(tmp_path, monkeypatch):
    import time

    from fastapi.testclient import TestClient

    import web.app as webapp
    from core.config import ApiKeys, Settings
    from database.db import Database
    from scanner.engine import ScannerEngine
    data = tmp_path / "data"
    data.mkdir()
    now = time.time()
    persist.dump(_store([("M", now - 60, 1.0), ("M", now - 20, 1.1)]), data / persist.FILE, now=now)
    monkeypatch.setattr(webapp, "DATA_DIR", data)
    monkeypatch.delenv("SNAPSHOT_DIR", raising=False)
    monkeypatch.delenv("SNAPSHOT_URL", raising=False)
    monkeypatch.delenv("RENDER_EXTERNAL_URL", raising=False)
    for k in ("RESEARCH_LOG", "EXPERIMENTAL_MODE", "LATENCY_PROBE"):
        monkeypatch.setenv(k, "0")
    eng = ScannerEngine(Settings(), Database(tmp_path / "e.db"), keys=ApiKeys(), on_log=lambda m: None)

    async def idle():
        return None
    eng.run = idle
    app = webapp.create_app(engine=eng, start_scanner=True, access_code="c0de")
    with TestClient(app) as c:
        assert [p.price for p in eng.history.get("M").points] == [1.0, 1.1]
        s = c.get("/api/snapshot", headers={"X-Access-Code": "c0de"}).json()
        assert s["price_history_at_start"]["status"] == "LOADED"
    assert json.loads((data / persist.FILE).read_text())["tokens"]["M"]          # rewritten at shutdown
