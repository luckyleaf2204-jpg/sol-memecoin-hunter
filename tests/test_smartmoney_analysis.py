"""Smart-money copy test (docs/smart_money_plan.md): selection, copy rule, costs, exclusions, baseline, verdict —
on a synthetic recorder database (no network, no real data)."""
import random

import pytest

from smartmoney import analysis as A
from smartmoney.recorder import Store

SOL = A.LAMPORTS


def _db(tmp_path):
    s = Store(tmp_path / "t.db")
    return s


def put(s, ts, mint, wallet, buy, sol, tok):
    s.buf.append((ts, None, s._id("mint", mint), s._id("wallet", wallet), int(buy), int(sol), int(tok), None))


def test_net_cost_as_registered():
    assert A.net_pct(1.0, 1.0) == pytest.approx(100 * ((1 - 0.0125) ** 2 - 1 - 2 * 0.00511 / 0.33))


def test_selection_eligibility_and_profit(tmp_path):
    s = _db(tmp_path)
    t = 1000
    for k in range(6):                                   # GOOD: 6 tokens, 1 SOL buys, sells at 2x
        put(s, t + k * 10, f"M{k}", "GOOD", True, 1 * SOL, 1000)
        put(s, t + k * 10 + 5, f"M{k}", "GOOD", False, 2 * SOL, 1000)
    for k in range(6):                                   # SMALL: profitable but avg buy 0.1 SOL -> not eligible
        put(s, t + k * 10, f"M{k}", "SMALL", True, 0.1 * SOL, 100)
        put(s, t + k * 10 + 4, f"M{k}", "SMALL", False, 1 * SOL, 100)      # before GOOD's sell
    for k in range(3):                                   # FEW: only 3 tokens
        put(s, t + k, f"M{k}", "FEW", True, 2 * SOL, 1000)
    for k in range(6):                                   # HOLDER: never sells; holdings valued at the last price
        put(s, t + k * 10 + 1, f"M{k}", "HOLD", True, 1 * SOL, 250)
    put(s, 10_000, "M0", "X", True, 0.01 * SOL, 1)       # end of window far away (cut at ~5500)
    s.flush()
    w = A.window(s.db, 9_000 / 86_400)                  # window 1000 -> 10000, cut 5500 (fixed window, amendment 6)
    sel = A.select_wallets(s.db, w)
    gid, hid = s._id("wallet", "GOOD"), s._id("wallet", "HOLD")
    assert set(sel["eligible"]) == {gid, hid} and sel["selected"][0] == gid
    assert sel["profit_sol"][gid] == pytest.approx(6.0)
    # HOLD bought 250 tokens for 1 SOL per mint; valued at the last price before the cut (GOOD's sell: 2 SOL / 1000)
    assert sel["profit_sol"][hid] == pytest.approx(6 * (0.5 - 1))


def _test_period_db(tmp_path):
    s = _db(tmp_path)
    put(s, 0, "Z", "Z", True, 0.01 * SOL, 1)                            # window start
    put(s, 200_000, "Z", "Z", True, 0.01 * SOL, 1)                      # window end; cut = 100_000
    w = "W"
    # T1: wallet buys at 100_100; next trade >= +3 s at price 1.0; wallet sells at 101_000 -> exit at 1.5
    put(s, 100_100, "T1", w, True, 1 * SOL, 1 * SOL)
    put(s, 100_101, "T1", "o", True, 0.1 * SOL, 0.05 * SOL)             # +1 s: too early (price 2.0 ignored)
    put(s, 100_103, "T1", "o", True, 0.1 * SOL, 0.1 * SOL)              # entry price 1.0
    put(s, 101_000, "T1", w, False, 3 * SOL, 2 * SOL)
    put(s, 101_004, "T1", "o", False, 0.15 * SOL, 0.1 * SOL)            # exit price 1.5
    # T2: no sell, curve completes -> last curve price before completion
    put(s, 100_200, "T2", w, True, 1 * SOL, 1 * SOL)
    put(s, 100_205, "T2", "o", True, 0.1 * SOL, 0.1 * SOL)              # entry 1.0
    put(s, 100_900, "T2", "o", True, 0.3 * SOL, 0.1 * SOL)              # 3.0 just before completion
    s.done.append((s._id("mint", "T2"), 100_950, None))
    # T3: no sell, no completion -> 24 h cap
    put(s, 110_000, "T3", w, True, 1 * SOL, 1 * SOL)
    put(s, 110_010, "T3", "o", True, 0.1 * SOL, 0.1 * SOL)              # entry 1.0
    put(s, 110_000 + 86_000, "T3", "o", True, 0.05 * SOL, 0.1 * SOL)    # 0.5 before the cap
    put(s, 110_000 + 90_000, "T3", "o", True, 0.9 * SOL, 0.1 * SOL)     # after the cap: ignored
    # T4: nothing trades after the trigger -> lost
    put(s, 120_000, "T4", w, True, 1 * SOL, 1 * SOL)
    # small buy (< 0.05 SOL) is no trigger
    put(s, 130_000, "T5", w, True, 0.01 * SOL, 1 * SOL)
    s.flush()
    return s


def test_copy_rule_entry_delay_exits_and_lost_trades(tmp_path):
    s = _test_period_db(tmp_path)
    w = A.window(s.db, 200_000 / 86_400)                                # fixed window (amendment 6)
    assert w.cut == 100_000
    rows = {r["mint"]: r for r in A.copies_with_status(s.db, w, [s._id("wallet", "W")])}
    m = lambda k: s._id("mint", k)                                    # noqa: E731
    assert set(rows) == {m("T1"), m("T2"), m("T3"), m("T4")}           # T5 (< 0.05 SOL) is no trigger
    assert rows[m("T1")]["kind"] == "wallet_sold" and rows[m("T1")]["gross_pct"] == pytest.approx(50.0)
    # amendment 5: the curve completed while the copy was open and no PumpSwap data -> unresolved, never the
    # last curve price (the old code returned "migrated" at +200 % here)
    assert rows[m("T2")]["status"] == "unresolved" and rows[m("T2")]["net_pct"] is None
    assert rows[m("T3")]["kind"] == "max_hold" and rows[m("T3")]["gross_pct"] == pytest.approx(-50.0)
    assert rows[m("T4")]["kind"] == "no_trade_after" and rows[m("T4")]["net_pct"] == -100.0
    assert rows[m("T1")]["net_pct"] == pytest.approx(A.net_pct(1.0, 1.5))


def test_gap_overlap_excludes_the_copy(tmp_path):
    s = _test_period_db(tmp_path)
    s.gap(100_150, 100_150 + 600)                                       # 10 min gap inside T1 / T2 holdings
    w = A.window(s.db, 200_000 / 86_400)
    kinds = {r["mint"] for r in A.copies(s.db, w, [s._id("wallet", "W")])}
    assert s._id("mint", "T1") not in kinds and s._id("mint", "T3") in kinds


def test_run_verdict_rejects_noise(tmp_path):
    """Random wallets on random-walk prices: the pre-registered test must REJECT."""
    rng = random.Random(5)
    s = _db(tmp_path)
    price = {f"K{j}": 1.0 for j in range(40)}
    t = 0
    for step in range(6000):
        t += rng.randint(20, 40)
        mint = f"K{rng.randrange(40)}"
        price[mint] *= 2.718 ** rng.gauss(0, 0.05)
        wallet = f"w{rng.randrange(60)}"
        sol = rng.choice([0.6, 0.8, 1.0]) * SOL
        buy = rng.random() < 0.55
        put(s, t, mint, wallet, buy, sol, sol / price[mint])
    s.flush()
    r = A.run(str(tmp_path / "t.db"), days=999, draws=200)
    assert r["verdict"] == "REJECT" and r["selected"] == A.TOP_N
    assert set(r["checks"]) == {"n>=100", "ci_wallet_lower>0", "random_p<0.05"}
