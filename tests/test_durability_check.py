"""Durability of the paper sample: start-up check ("MẪU KHÔNG BỀN - sẽ mất khi restart"), last snapshot / restore
status, token never exposed, and a full restart simulation (book, journal, gaps, epoch and sample start kept)."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient

from core import snapshot as S
from test_bot_v2 import Eng, FakeJupiter, good
from test_web_api import CODE, env  # noqa: F401  (fixture)
from trading.bot import PaperBot
from trading.config import TradingConfig
from trading.sample_epoch import SampleEpoch


# ---------------------------------------------------------------- 1. start-up check
def test_no_store_is_not_durable(tmp_path):
    c = S.durability_check(None, tmp_path)
    assert c["durable"] is False and c["warning"] == "MẪU KHÔNG BỀN - sẽ mất khi restart"
    assert "not set" in c["reason"]


def test_dir_store_outside_data_dir_is_durable_inside_is_not(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    assert S.durability_check(S.LocalStore(tmp_path / "disk"), data)["durable"] is True
    inside = S.durability_check(S.LocalStore(data / "snap"), data)
    assert inside["durable"] is False and "inside DATA_DIR" in inside["reason"]


class Broken:
    kind, url, token = "http", "https://user:pw@store.example/p?sig=SECRET", "SECRET-TOKEN"

    def put(self, name, data):
        raise ConnectionError(f"cannot reach {self.url} with {self.token}")

    def get(self, name):
        return None


class Lossy(Broken):
    def put(self, name, data):
        pass


def test_failed_write_test_is_not_durable_and_leaks_nothing(tmp_path):
    c = S.durability_check(Broken(), tmp_path)
    assert c["durable"] is False and c["warning"].startswith("MẪU KHÔNG BỀN")
    assert c["reason"] == "write test failed (ConnectionError)"
    text = str(c)
    assert "SECRET" not in text and "pw" not in text and c["target"] == "https://store.example/p"
    assert S.durability_check(Lossy(), tmp_path)["reason"] == "write test failed (read-back differs)"


def test_target_never_contains_credentials():
    st = S.HttpStore("https://alice:hunter2@r2.example.com:8443/bucket/prefix?X-Amz-Signature=abc", "tok")
    assert S.target_of(st) == "https://r2.example.com:8443/bucket/prefix"


# ---------------------------------------------------------------- 2. last snapshot / restore status
def test_snapshot_manifest_has_sizes_and_target(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "paper_bot.json").write_text("{}" * 1000, encoding="utf-8")
    man = S.snapshot(tmp_path / "data", S.LocalStore(tmp_path / "disk"), commit="c", now=123.0)
    assert man["bytes_raw"] == 2000 and 0 < man["bytes_gz"] < 2000 and man["store"] == "dir"
    assert man["target"] == str(tmp_path / "disk") and man["duration_s"] >= 0
    assert S.fmt_bytes(2000) == "2.0 kB" and S.fmt_bytes(5_000_000) == "5.0 MB"
    assert S.restore(tmp_path / "data", S.LocalStore(tmp_path / "disk"))["at"] > 0


def test_healthz_and_api_snapshot_show_the_warning(env):  # noqa: F811
    c, _, _ = env
    h = c.get("/healthz").json()["snapshot"]
    assert h["durable"] is False and h["warning"] == "MẪU KHÔNG BỀN - sẽ mất khi restart"
    assert set(h) <= {"durable", "store", "last_utc", "last_restore_utc", "warning",
                      "research_db_carried_in_a_row"}                       # no target on /healthz
    assert c.get("/api/snapshot").status_code == 401                                     # details need the code
    d = c.get("/api/snapshot", headers={"X-Access-Code": CODE}).json()
    assert d["durable"] is False and d["warning"].startswith("MẪU KHÔNG BỀN") and d["last_snapshot"] is None


def test_report_shows_the_warning_first():
    b = PaperBot(Eng([good()]), TradingConfig(seed=4))
    r = b.sample_report()
    assert r["warnings"][0].startswith("MẪU KHÔNG BỀN - sẽ mất khi restart") and r["durability"]["durable"] is False
    b.snapshot_status = {"durable": True, "store": "http", "reason": "write + read-back OK", "last_ts": 1.0}
    assert not any("KHÔNG BỀN" in w for w in b.sample_report()["warnings"])


# ---------------------------------------------------------------- 4. snapshot -> restart -> restore
def _bot(data_dir, st):
    b = PaperBot(Eng([st]), TradingConfig(seed=4), state_path=data_dir / "paper_bot.json")
    b.exec.rng.random = lambda: 0.99
    b.jupiter = FakeJupiter()
    return b


def test_full_restart_keeps_journal_gaps_epoch_and_sample_start(tmp_path):
    a, store = tmp_path / "container_a", S.LocalStore(tmp_path / "durable")
    a.mkdir()
    st = good()
    b = _bot(a, st)
    t0 = time.time()
    b.begin_sample(now=t0 - 10)                                   # epoch starts before the trade
    b.tick()
    asyncio.run(b.execute_intents())
    p = b.book.positions[st.mint]
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = b.jupiter.sell_price = p.entry_price * 0.8
    b.tick()
    asyncio.run(b.execute_sells())                               # stop loss: one closed trade in the journal
    b.book.gaps.append({"start": t0 - 9, "end": t0 - 8, "minutes": 0.0, "cause": "feed down"})
    b.persist()
    before = b.sample_report()
    assert before["n"] == 1 and len(b.book.journal) == 1
    epoch_before, journal_before = b.sample_epoch.as_dict(), list(b.book.journal)
    S.snapshot(a, store, commit="c1")

    fresh = tmp_path / "container_b"                              # restart on a new, empty container
    fresh.mkdir()
    res = S.restore(fresh, store)
    assert {"paper_bot.json", "sample_epoch.json"} <= set(res["restored"])
    b2 = _bot(fresh, good())
    restart_at = time.time() + 3600                               # one hour later
    ep = b2.begin_sample(now=restart_at)
    assert ep["started_at"] == epoch_before["started_at"] and b2.sample_epoch.id == epoch_before["id"]
    assert b2.book.journal == journal_before
    causes = [g["cause"] for g in b2.book.gaps]
    assert causes[0] == "feed down" and causes[-1] == "restart / sleep" and b2.book.gaps[-1]["end"] == restart_at
    after = b2.sample_report(restart_at)
    assert after["n"] == before["n"] == 1                         # the trade closed before the restart still counts
    assert after["epoch"]["started_at_utc"] == epoch_before["started_at_utc"]
    assert after["gaps"]["count"] >= 1 and after["gaps"]["excluded_trades"] == 0


def test_restart_without_restore_starts_a_new_sample(tmp_path):
    b = _bot(tmp_path, good())
    b.begin_sample(now=1000.0)
    other = tmp_path / "other"
    other.mkdir()
    b2 = _bot(other, good())                                      # nothing restored
    b2.begin_sample(now=5000.0)
    assert b2.sample_epoch.started_at == 5000.0 and b2.book.journal == []
    assert isinstance(SampleEpoch().as_dict(), dict)


# ---------------------------------------------------------------- 5. free instance hours / docs
def test_free_hours_estimate():
    import sys
    from datetime import datetime, timezone
    from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
    from free_hours import estimate
    oct2 = datetime(2026, 10, 2, tzinfo=timezone.utc)
    one = estimate(oct2, 1)
    assert one["month_hours_if_always_on"] == 744 and one["spare_at_month_end"] == 6 and not one["runs_out"]
    assert one["used_so_far_if_always_on"] == 24.0 and one["remaining_now"] == 726.0
    two = estimate(oct2, 2)
    assert two["runs_out"] and two["runs_out_on_day"] == 16
    assert estimate(datetime(2026, 11, 1, tzinfo=timezone.utc), 1)["spare_at_month_end"] == 30


def test_docs_cover_token_hygiene_and_free_hours():
    from pathlib import Path
    doc = (Path(__file__).resolve().parents[1] / "docs" / "sample_plan.md").read_text(encoding="utf-8")
    for needle in ("MẪU KHÔNG BỀN - sẽ mất khi restart", "least-privilege", "Never in `render.yaml`",
                   "750 free instance hours", "Monthly Included Usage", "tools/free_hours.py", "/api/snapshot"):
        assert needle in doc, needle
