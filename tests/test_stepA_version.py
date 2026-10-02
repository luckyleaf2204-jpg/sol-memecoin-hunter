"""Step A — which code and parameters are running; the sample epoch (only trades opened after it count)."""
import asyncio
import time

import pytest

from core import version
from test_v12 import MINT, opened
from trading.config import TradingConfig
from trading.sample_epoch import MIN_SAMPLE_COMMIT, STRATEGY_VERSION, SampleEpoch


def test_strategy_parameters_unchanged_while_counting():
    """Pinned fingerprints (commit 148af2c). If this fails, a strategy parameter changed: bump the sample epoch."""
    assert TradingConfig().sample_id() == "78322bacfd"
    c = TradingConfig(experimental=True, latency_probe=True, lifecycle=True, latency_slippage_model="AUTO")
    assert c.sample_id() == "02e5bdcc03"
    assert (c.entry_max_extension_5m_pct, c.hard_exit_no_quote_haircut_pct, c.stop_loss_pct, c.tp1_pct, c.tp2_pct) == \
        (40.0, 30.0, 15.0, 30.0, 80.0)


def test_git_commit_from_render_env(monkeypatch):
    version.git_commit.cache_clear()
    monkeypatch.setenv("RENDER_GIT_COMMIT", "abc1234def")
    assert version.git_commit() == "abc1234def" and version.short("abc1234def") == "abc1234"
    version.git_commit.cache_clear()
    monkeypatch.delenv("RENDER_GIT_COMMIT")
    assert version.git_commit()                     # local repo: git rev-parse (or "unknown"), never empty
    version.git_commit.cache_clear()


def test_epoch_kept_across_restarts_and_reset_on_change(tmp_path):
    f = tmp_path / "sample_epoch.json"
    e = SampleEpoch(f)
    assert e.start("fp1", "c1", now=100.0) is True and e.started_at == 100.0
    e2 = SampleEpoch(f)
    assert e2.start("fp1", "c2", now=500.0) is False and e2.started_at == 100.0       # new commit, same strategy
    e3 = SampleEpoch(f)
    assert e3.start("fp2", "c2", now=900.0) is True and e3.started_at == 900.0        # parameter change
    e3.strategy_version = "next"
    assert e3.start("fp2", "c2", now=950.0) is True                                    # strategy code change
    assert STRATEGY_VERSION and MIN_SAMPLE_COMMIT == "148af2c"


def test_legacy_rows_are_not_counted():
    e = SampleEpoch()
    e.start("fp", "c", now=1000.0)
    ok = {"epoch": e.id, "entry_ts": 1500.0, "noquote": False}
    assert e.counts(ok)
    assert not e.counts({**ok, "epoch": ""})                    # no epoch tag
    assert not e.counts({**ok, "epoch": "s0:old:1"})            # another epoch
    assert not e.counts({**ok, "entry_ts": 900.0})              # opened before the epoch start
    assert not e.counts({**ok, "noquote": True})


def test_positions_are_tagged_with_the_epoch(tmp_path):
    b, st, p = opened()
    assert p.sample_epoch == ""                                 # bot never started a sample -> LEGACY
    b.sample_epoch = SampleEpoch(tmp_path / "e.json")
    d = b.begin_sample()
    assert d["fingerprint"] == b.cfg.sample_id() and d["commit"]
    b2, st2, p2 = opened()
    b2.sample_epoch = b.sample_epoch
    b2.cfg = b.cfg
    b2._tag_position(MINT, {})
    assert b2.book.positions[MINT].sample_epoch == b.sample_epoch.id
    st2.stamps["market"].updated_at = time.time() + 1
    st2.market.price_usd = b2.jupiter.sell_price = p2.entry_price * 0.8
    b2.tick()
    asyncio.run(b2.execute_sells())
    assert b2.book.journal[-1]["epoch"] == b.sample_epoch.id and b.sample_epoch.counts(b2.book.journal[-1])
