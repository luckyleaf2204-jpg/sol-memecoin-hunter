"""KOL copy test (docs/kol_plan.md) on a synthetic recorder database (no network, no real data)."""
import json

import pytest

from smartmoney import kol as K
from smartmoney.analysis import LAMPORTS as SOL, net_pct
from smartmoney.recorder import Store


def put(s, ts, mint, wallet, buy, sol, tok):
    s.buf.append((ts, None, s._id("mint", mint), s._id("wallet", wallet), int(buy), int(sol), int(tok)))


def round_trip(s, t, mint, wallet, exit_mult):
    """wallet buys at price 1.0, a follower trade +3 s at 1.0 (entry), wallet sells, a trade +3 s at exit_mult."""
    put(s, t, mint, wallet, True, 1 * SOL, 1 * SOL)
    put(s, t + 3, mint, "crowd", True, 0.1 * SOL, 0.1 * SOL)
    put(s, t + 60, mint, wallet, False, exit_mult * SOL, 1 * SOL)
    put(s, t + 63, mint, "crowd", False, 0.1 * exit_mult * SOL, 0.1 * SOL)


def build(tmp_path, kol_mult, other_mult, n_kols=3, per_wallet=40, n_others=30):
    s = Store(tmp_path / "t.db")
    t = 1_000
    for i in range(n_kols):
        for j in range(per_wallet):
            round_trip(s, t, f"K{i}_{j}", f"KOL{i}", kol_mult)
            t += 100
    for i in range(n_others):
        for j in range(4):
            round_trip(s, t, f"O{i}_{j}", f"W{i}", other_mult)
            t += 100
    for j in range(2):                                   # roster wallet with too few tokens: not eligible
        round_trip(s, t, f"F{j}", "KOLFEW", kol_mult)
        t += 100
    s.flush()
    roster = tmp_path / "roster.json"
    roster.write_text(json.dumps({"wallets": [{"wallet": w} for w in
                                              [f"KOL{i}" for i in range(n_kols)] + ["KOLFEW", "NEVER_SEEN"]]}))
    return str(tmp_path / "t.db"), str(roster)


def test_pass_when_kols_beat_random(tmp_path):
    db, roster = build(tmp_path, kol_mult=1.5, other_mult=0.8)
    r = K.run(db, roster, draws=200)
    assert r["roster"] == 5 and r["kols_seen"] == 4 and r["kols_eligible"] == 3
    assert r["test"]["n"] == 120
    assert r["test"]["mean_net_pct"] == pytest.approx(net_pct(1.0, 1.5), abs=1e-3)
    assert r["random_baseline"]["p_random_ge_observed"] == 0
    assert r["kol_own"]["n"] == 120
    assert r["verdict"] == "PASS"


def test_reject_when_kols_lose(tmp_path):
    db, roster = build(tmp_path, kol_mult=0.9, other_mult=1.2)
    r = K.run(db, roster, draws=200)
    assert r["test"]["n"] == 120 and r["verdict"] == "REJECT"


def test_inconclusive_below_100_copies(tmp_path):
    db, roster = build(tmp_path, kol_mult=1.5, other_mult=0.8, per_wallet=10)
    r = K.run(db, roster, draws=200)
    assert r["test"]["n"] == 30 and r["verdict"] == "INCONCLUSIVE"
