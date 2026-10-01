"""State machine: DISCOVER -> WATCH -> WAITING FOR CONFIRMATION -> EARLY SIGNAL -> VET -> RISK -> TRADE CANDIDATE -> BUY.

WATCH is wide, BUY is exactly as strict as before:
  1. UNKNOWN / PENDING data never means REJECT
  2. a WATCH token can never BUY
  3. only when every current BUY condition passes does a Trade Candidate (and a paper BUY) appear
  4. D1–D8 / Early Signal / Risk / VET / exits are byte-identical (hash-frozen) and the TRADE gate is unchanged
"""
import hashlib
import inspect
import random
import time
from pathlib import Path

import pytest

from conftest import build_state, dex_pair
from core.models import DevReport, EarlySignal, HolderStats, RiskFactor, RiskResult, SourceStamp
from test_bot_v2 import FakeJupiter, bot, run
from trading import decision as D
from trading.config import TradingConfig
from trading.models import PASS, REJECT, TRADE, WATCH
from validation.identity import apply_identity, record_claim

SRC = Path(D.__file__).resolve().parents[1]
CFG = TradingConfig()


def napoleon(mint="NapoLeonMint1111111111111111111111111111111"):
    """High Opportunity / Momentum, low Risk, verified — but Early Signal and holders still UNKNOWN."""
    st = build_state(dex_pair(mint=mint))
    record_claim(st.identity, "dexscreener", "NAPO", "Napoleon")
    apply_identity(st)
    st.early = EarlySignal(None, None, None)                    # Early Signal UNKNOWN (< 10 min history)
    st.holders, st.holder_status = None, "pending"              # holder data UNKNOWN
    st.identity.helius_checked = False                          # authorities / Token-2022 UNKNOWN
    return st


def complete(st):
    """The missing data arrives: every BUY condition now passes."""
    st.early = EarlySignal(72, True, True, 5, groups_computable=6)
    st.holders = HolderStats(holder_count=1200, top10_pct=12, source="helius_das", complete_list=True)
    st.holder_status = "ok"
    st.stamps["holders"] = SourceStamp("helius_das", time.time())
    st.identity.helius_checked = True
    st.dev = DevReport(creator="Dev111", balance_verified=True, current_pct=1.5, status="HOLD")
    return st


def decide(st):
    return D.score(st, D.vet(st, CFG), CFG)


# ---------------------------------------------------------------- 1. UNKNOWN is never REJECT
def test_unknown_data_is_watch_waiting_not_reject():
    sc = decide(napoleon())
    assert sc.decision == WATCH and sc.rejected == []
    assert {"early_signal", "holders", "vet_onchain"} <= set(sc.waiting)
    assert sc.why[0].startswith("waiting: ")
    assert sc.opportunity >= 65 and sc.components["momentum"] >= 70


@pytest.mark.parametrize("make_unknown", [
    lambda st: setattr(st, "early", None),
    lambda st: setattr(st, "early", EarlySignal(None, None, None)),
    lambda st: (setattr(st, "holders", None), setattr(st, "holder_status", "failed")),
    lambda st: setattr(st, "dev", None),
    lambda st: setattr(st.identity, "helius_checked", False),
    lambda st: (st.identity.claims.clear(), apply_identity(st)),          # identity not verified yet
    lambda st: st.stamps["market"].__setattr__("updated_at", time.time() - 120),   # stale market data
])
def test_each_unknown_alone_waits(make_unknown):
    st = complete(napoleon())
    make_unknown(st)
    sc = decide(st)
    assert sc.decision in (WATCH, "PENDING_IDENTITY") and not sc.rejected and sc.waiting
    assert (sc.decision == "PENDING_IDENTITY") == (st.identity.status != "VERIFIED")


# ---------------------------------------------------------------- real reasons still REJECT
@pytest.mark.parametrize("make_bad,reason", [
    (lambda st: (record_claim(st.identity, "pumpportal", "OTHER", ""), apply_identity(st)), "identity_conflict"),
    (lambda st: setattr(st, "early", EarlySignal(40, False, False, 2, groups_computable=7)), "early_signal"),
    (lambda st: setattr(st.market, "liquidity_usd", 2_000), "liquidity"),
    (lambda st: setattr(st, "risk", RiskResult(75, "HIGH")), "rug"),
    (lambda st: setattr(st, "risk", RiskResult(20, "LOW", [RiskFactor("dev_dump", 10, "rug")])), "rug"),
    (lambda st: setattr(st.identity, "mint_authority", "7pt9tkctJPK7PPNQJ77GKg8ZffSF6QxoMiCFYHxrtaCj"), "authorities"),
])
def test_real_reasons_still_reject(make_bad, reason):
    st = complete(napoleon())
    make_bad(st)
    sc = decide(st)
    assert sc.decision == REJECT and reason in sc.rejected


def test_low_known_scores_reject_but_missing_scores_wait():
    st = complete(napoleon())
    st.subscores["momentum"].score = 10
    for k in ("holder", "onchain", "liquidity"):
        if k in st.subscores:
            st.subscores[k].score = 5
    st.risk = RiskResult(55, "MEDIUM")
    st.early = EarlySignal(10, False, False, 0, groups_computable=6)
    assert decide(st).decision == REJECT


# ---------------------------------------------------------------- 2. WATCH can never BUY
def test_watch_token_never_buys_and_is_never_a_trade_candidate():
    st = napoleon()
    b = bot([st], FakeJupiter())
    for _ in range(3):
        run(b)
    rec = b.decisions[st.mint]
    assert rec["decision"] == WATCH and rec["waiting"]
    assert not b.book.positions and b.trade_candidates() == [] and not b.intents
    assert not [e for e in b.book.executions if e.side == "BUY"]


# ---------------------------------------------------------------- 3. full PASS -> Trade Candidate -> paper BUY
def test_state_updates_automatically_and_buys_only_when_everything_passes():
    st = napoleon()
    b = bot([st], FakeJupiter())
    run(b)
    assert b.decisions[st.mint]["decision"] == WATCH and not b.book.positions
    complete(st)
    st.early = EarlySignal(None, None, None)                    # everything except Early Signal
    run(b)
    assert b.decisions[st.mint]["decision"] == WATCH and b.decisions[st.mint]["waiting"] == ["early_signal"]
    assert not b.book.positions
    complete(st)                                                # Early Signal TRUE arrives
    run(b)
    rec = b.decisions[st.mint]
    assert rec["decision"] == TRADE and rec["vet_passed"] and all(c["result"] in (PASS, "N/A") for c in rec["checks"])
    assert st.mint in b.book.positions                          # paper BUY through the unchanged strategy


# ---------------------------------------------------------------- 4. byte-identical core + unchanged TRADE gate
FROZEN = {
    "intel/early_signal.py": "c5e6ecbdb38a95299e064046daa22631564d17d67bb14c35fc13f9a041ff8fe6",
    "risk/engine.py": "5ec4d856f92893bb6dd9299cb86038446cfc8f2df3a0991c07a7a8de192c652c",
    "trading/risk.py": "04d41b755a3ce9fe6d18df651b15493fbec0dba4359eeefa9a7ce398e19994a5",
    "trading/exits.py": "b44f8d610c85b041f1112a644bcf572103ae00c3a09fe12b71be85834c2769fa",
    "validation/quality.py": "ed81bfde10a21888b86409b45dd474daa0c3e953cd940a87a9330e8935281e1e",
    "validation/identity.py": "7b2cbab6cfbf6b5801760e62296b0b9f0c2c399c7577ea4089c7c48132c50f99",
    "validation/market.py": "d71d62db593d8d83209c8a698d2f04bad82e1773b6249aaddc02e0fece67c507",
}
VET_SOURCE_SHA = "5ab66e87354cd1aa411c4081766fe7d6f5b6f93c300620d12316a1e0478e0f08"


def test_core_files_are_byte_identical():
    for rel, sha in FROZEN.items():
        data = (SRC / rel).read_bytes().replace(b"\r\n", b"\n")
        assert hashlib.sha256(data).hexdigest() == sha, rel
    assert hashlib.sha256(inspect.getsource(D.vet).replace("\r\n", "\n").encode()).hexdigest() == VET_SOURCE_SHA


def _old_trade(st, v, opp, conf):
    """The TRADE rule exactly as before this change (reference copy)."""
    return D.vet_passed(v) and opp is not None and opp >= CFG.trade_min_opportunity and conf >= CFG.trade_min_confidence


def test_trade_gate_is_unchanged_on_random_states():
    rnd = random.Random(11)
    seen = {TRADE: 0, WATCH: 0, REJECT: 0}
    for i in range(400):
        st = complete(napoleon(f"Rand{i:04d}".replace("0", "A") + "1" * 34))
        if rnd.random() < 0.3:
            st.early = rnd.choice([None, EarlySignal(None, None, None), EarlySignal(40, False, False, 2, groups_computable=6)])
        if rnd.random() < 0.3:
            st.holders, st.holder_status = None, rnd.choice(["pending", "failed"])
        if rnd.random() < 0.2:
            st.identity.helius_checked = False
        if rnd.random() < 0.2:
            st.risk = RiskResult(rnd.randint(0, 90), "X")
        if rnd.random() < 0.2:
            st.market.liquidity_usd = rnd.choice([None, 3_000, 50_000])
        if rnd.random() < 0.3:
            st.subscores["momentum"].score = rnd.randint(0, 100)
        v = D.vet(st, CFG)
        sc = D.score(st, v, CFG)
        seen[sc.decision] += 1
        assert (sc.decision == TRADE) == _old_trade(st, v, sc.opportunity, sc.confidence)
        if sc.decision == WATCH:
            assert not sc.rejected
    assert all(seen.values()), seen                              # every state was exercised



def test_early_false_with_missing_groups_waits_and_confirmed_false_rejects():
    st = complete(napoleon())
    st.early = EarlySignal(40, False, False, 2, groups_computable=5)      # FALSE but 2 groups still missing
    sc = decide(st)
    assert sc.decision == WATCH and "early_signal" in sc.waiting and not sc.rejected
    st.early = EarlySignal(40, False, False, 2, groups_computable=7)      # FALSE on complete data
    sc = decide(st)
    assert sc.decision == REJECT and "early_signal" in sc.rejected
