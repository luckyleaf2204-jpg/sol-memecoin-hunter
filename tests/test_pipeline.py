"""Pipeline retention + diagnostics: nothing is lost for missing data, and BUY decisions are exactly the old ones."""
import random
import time

import pytest

from conftest import build_state, dex_pair, default_info
from core.config import ApiKeys, Settings
from core.models import EarlySignal, TokenInfo, TokenState
from database.db import Database
from scanner.engine import ScannerEngine
from test_bot_v2 import FakeJupiter, bot, good, run
from test_states import complete, napoleon
from trading import decision as D
from validation.identity import apply_identity, record_claim

B58 = "ABCDEFGHJKLMNPQRSTUVWXYZ"


def mint(i, prefix="Pipe"):
    return prefix + B58[i % 24] + B58[(i // 24) % 24] + "1" * 34


# ---------------------------------------------------------------- classify every token, BUY unchanged
def test_every_token_is_classified_but_only_scanned_tokens_can_trade():
    sts = []
    for i in range(30):
        st = napoleon(mint(i))
        if i % 3 == 0:
            complete(st)                                   # full data, Early TRUE -> scan reason "early_signal"
        sts.append(st)
    b = bot(sts, FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100)
    run(b)
    assert len(b.decisions) == 30                          # nothing silently skipped
    for st in sts:
        rec = b.decisions[st.mint]
        if rec["decision"] == "TRADE":
            assert D.scan_reasons(st), "a TRADE without a scan reason would be a new trade"


def test_buys_identical_to_the_old_scan_scope(monkeypatch):
    rnd = random.Random(3)

    def make():
        out = []
        for i in range(60):
            st = napoleon(mint(i, "Same"))
            if rnd.random() < 0.4:
                complete(st)
            if rnd.random() < 0.2:
                st.early = EarlySignal(None, None, None)
            out.append(st)
        return out
    rnd.seed(3)
    new_bot = bot(make(), FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100)
    for _ in range(3):
        run(new_bot)

    def old_scan(states):                                  # the scope before this change: scan reasons only
        cands = [(st, r) for st in states if (r := D.scan_reasons(st))]
        cands.sort(key=lambda x: (-len(x[1]), -((x[0].score.total if x[0].score else 0))))
        return cands
    rnd.seed(3)
    old_states = make()
    monkeypatch.setattr(D, "scan", old_scan)
    old_bot = bot(old_states, FakeJupiter(), max_open_positions=50, max_total_exposure_pct=100)
    for _ in range(3):
        run(old_bot)
    assert set(new_bot.book.positions) == set(old_bot.book.positions) and new_bot.book.positions
    assert [e.mint for e in new_bot.book.executions] == [e.mint for e in old_bot.book.executions]


def test_pending_identity_never_buys():
    st = complete(napoleon())
    st.identity.claims.clear()
    apply_identity(st)
    b = bot([st], FakeJupiter())
    for _ in range(3):
        run(b)
    assert b.decisions[st.mint]["decision"] == "PENDING_IDENTITY" and not b.book.positions
    assert "identity_pending" in b.decisions[st.mint]["blocked_by"]


# ---------------------------------------------------------------- retention
@pytest.fixture
def eng(tmp_path):
    return ScannerEngine(Settings(max_tracked=5), Database(tmp_path / "r.db"), keys=ApiKeys(), on_log=lambda m: None)


def test_unknown_mc_is_kept_known_dead_coin_is_pruned(eng):
    now = time.time()
    nodata = TokenState(info=TokenInfo(mint="NoData1", created_at=now - 1800, discovered_at=now - 1800))
    dead = build_state(dex_pair(mint="Dead1", mc=2_000, fdv=2_000, price="0.000002"), info=default_info("Dead1", age_s=1800))
    old_nodata = TokenState(info=TokenInfo(mint="OldNoData", created_at=now - 4000, discovered_at=now - 4000))
    eng.tracked = {s.mint: s for s in (nodata, dead, old_nodata)}
    eng.prune()
    assert "NoData1" in eng.tracked                        # 30 min without data: still in the pipeline
    assert "Dead1" not in eng.tracked and "OldNoData" not in eng.tracked
    assert eng.pipe["pruned_low_mc"] == 1 and eng.pipe["pruned_no_data"] == 1


def test_full_pipeline_evicts_instead_of_dropping_new_tokens(eng):
    keep = TokenState(info=TokenInfo(mint="Star1"), watch=True)
    held = TokenState(info=TokenInfo(mint="Held1"))
    excluded = TokenState(info=TokenInfo(mint="Excl1"))
    excluded.group = "excluded"
    others = [TokenState(info=TokenInfo(mint=f"Other{i}", discovered_at=time.time() - 100 + i)) for i in range(2)]
    eng.tracked = {s.mint: s for s in [keep, held, excluded] + others}
    eng.deep_extra = {"Held1"}
    assert eng._add(TokenInfo(mint="Fresh1", created_at=time.time() - 30))
    assert "Fresh1" in eng.tracked and "Excl1" not in eng.tracked and eng.pipe["evicted"] == 1
    assert eng._add(TokenInfo(mint="Fresh2", created_at=time.time() - 30))
    assert {"Star1", "Held1"} <= set(eng.tracked)          # starred / held never evicted


# ---------------------------------------------------------------- diagnostics
def test_pipeline_counts_and_block_summary():
    sts = [napoleon(mint(1)), complete(napoleon(mint(2)))]
    conflict = complete(napoleon(mint(3)))
    record_claim(conflict.identity, "pumpportal", "OTHER", "")
    apply_identity(conflict)
    pend = napoleon(mint(4))
    pend.identity.claims.clear()
    apply_identity(pend)
    b = bot(sts + [conflict, pend], FakeJupiter())
    run(b)
    p = b.pipeline()
    assert p["WATCH"] == 1 and p["PENDING_IDENTITY"] == 1 and p["TRADE_CANDIDATE"] == 1 and p["REJECT"] == 1
    assert p["reject_reasons"] == {"identity_conflict": 1}
    s = p["summary"]
    assert s["reject_by"]["identity"] == 1 and s["unknown_pending"] == 2 and s["trade_candidates"] == 1
    assert s["most_blocking"] in s["first_blocker"]
    w = b.decisions[sts[0].mint]["blocked_by"]
    assert w[0] == "early_unknown" and any(x.startswith("vet_unknown:") for x in w)


# ---------------------------------------------------------------- soft VET fails wait, hard ones reject
@pytest.mark.parametrize("key", sorted(D.SOFT_FAIL))
def test_soft_vet_fail_is_watch_and_never_trades(key, monkeypatch):
    st = complete(napoleon())
    real = D.vet

    def vet(st, cfg, now=None):
        v = real(st, cfg, now)
        for c in v.checks:
            if c.key == key:
                c.result = D.FAIL
        return v
    monkeypatch.setattr(D, "vet", vet)
    b = bot([st], FakeJupiter())
    for _ in range(3):
        run(b)
    rec = b.decisions[st.mint]
    assert rec["decision"] == "WATCH" and not rec.get("rejected") and not b.book.positions
    assert "vet:" + key in rec["blocked_by"]


@pytest.mark.parametrize("key", ["dev", "authorities", "token_2022", "rug", "liquidity"])
def test_hard_vet_fail_still_rejects(key, monkeypatch):
    st = complete(napoleon())
    real = D.vet

    def vet(st, cfg, now=None):
        v = real(st, cfg, now)
        for c in v.checks:
            if c.key == key:
                c.result = D.FAIL
        return v
    monkeypatch.setattr(D, "vet", vet)
    b = bot([st], FakeJupiter())
    run(b)
    rec = b.decisions[st.mint]
    assert rec["decision"] == "REJECT" and key in rec["rejected"] and not b.book.positions


# ---------------------------------------------------------------- missing / no-activity data is not "wrong data"
def _with_issues(issues):
    from core.models import DataQuality, Issue
    st = complete(napoleon())
    st.quality = DataQuality(0, "INVALID", [Issue("critical", "market", k, p) for k, p in issues])
    return st


def test_missing_price_repr_none_counts_as_missing():
    from scoring.groups import _missing_only
    st = _with_issues([("price_bad", {"raw": "None"}), ("mc_bad", {"raw": "None"})])
    assert _missing_only(st)
    assert not _missing_only(_with_issues([("price_bad", {"raw": "-1.0"})]))


def test_zero_volume_waits_implausible_mc_rejects():
    st = _with_issues([("price_bad", {"raw": "None"}), ("volume_bad", {"field": "vol_5m", "raw": "0.0"})])
    assert D._no_activity_only(st)
    assert not D._no_activity_only(_with_issues([("mc_implausible", {"value": 433.0, "min": 1000})]))
    assert not D._no_activity_only(_with_issues([("volume_bad", {"field": "vol_5m", "raw": "-5.0"})]))
