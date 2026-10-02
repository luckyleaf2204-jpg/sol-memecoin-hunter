"""Fix 6 — real transactions per trade, trade size and fixed fees (network + priority) as % of each trade; the
smallest trade size that keeps fixed fees under 2 %."""
import asyncio
import time

import pytest

from core.models import LiquidityIntel
from test_stepB_report import epoch, row
from test_v12 import opened
from trading.sample_report import costs, report


def test_every_transaction_of_a_trade_is_counted():
    b, st, p = opened()
    fee = b.exec.network_fee(b._sol())
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = b.jupiter.sell_price = p.entry_price * 1.35       # TP1: second tx
    b.tick()
    asyncio.run(b.execute_sells())
    b.exec.rng.random = lambda: 0.0                                           # next sell attempt fails (tx 3)
    st.liquidity_intel = LiquidityIntel(state="SHOCK")
    b.tick()
    asyncio.run(b.execute_sells())
    assert p.tx_count == 3 and p.status == "OPEN"
    b.exec.rng.random = lambda: 0.99
    b.tick()
    asyncio.run(b.execute_sells())                                            # tx 4: the exit fills
    r = b.book.journal[-1]
    assert r["n_tx"] == 4 and r["fixed_fee_usd"] == pytest.approx(4 * fee, rel=1e-6)
    assert r["size_usd"] == pytest.approx(p.cost_usd, abs=1e-3)
    assert r["fixed_fee_pct"] == pytest.approx(100 * 4 * fee / p.cost_usd, abs=1e-3)       # rounded to 3 decimals


def test_cost_report_and_minimum_trade_size():
    e = epoch()
    rows = []
    for size, fee in ((15.0, 1.5), (25.0, 1.5), (60.0, 1.5)):
        r = row(e, 5.0)
        r.update(size_usd=size, fixed_fee_usd=fee, fixed_fee_pct=100 * fee / size, n_tx=2, symbol="T")
        rows.append(r)
    c = report(rows, e)["costs"]
    assert c["n"] == 3 and c["n_tx_median"] == 2 and c["size_usd_median"] == 25.0
    assert c["fixed_fee_pct_median"] == pytest.approx(6.0) and c["fixed_fee_pct_max"] == pytest.approx(10.0)
    assert c["share_over_target_pct"] == pytest.approx(100.0)                 # 10 %, 6 %, 2.5 % all > 2 %
    assert c["recommended_min_size_usd"] == pytest.approx(75.0) and ">= 75 $" in c["recommendation"]
    assert [t["fixed_fee_pct"] for t in c["per_trade"]] == pytest.approx([10.0, 6.0, 2.5])
    assert costs([])["recommended_min_size_usd"] is None


def test_bot_report_adds_failed_attempts_and_fee_per_tx():
    b, st, p = opened()
    r = b.sample_report()
    f = r["costs"]["failed_attempts_all_time"]
    assert f["count"] == b.book.failed and f["fee_per_tx_usd_now"] == pytest.approx(b.exec.network_fee(b._sol()))


def test_failed_sells_no_longer_crash():
    """Pre-existing bug found by fix 6: a failed sell built Execution(**base, reason=...) with `reason` already in
    base -> TypeError, so failed sell transactions (and their fees) were never recorded."""
    from test_bot_v2 import good
    from trading.execution import PaperExecutor
    ex = PaperExecutor(seed=1)
    ex.rng.random = lambda: 0.0                                   # every tx fails
    st = good()
    q = {"inputMint": st.mint, "outputMint": "So11111111111111111111111111111111111111112", "outAmount": str(10 ** 9),
         "priceImpactPct": "0.01", "routePlan": []}
    for e in (ex.sell_from_quote(st, 1000.0, q, 150.0, "stop_loss"), ex.sell(st, 1000.0, 0.0002, 150.0, "take_profit_1"),
              ex.sell(st, 1e12, 0.0002, 150.0, "take_profit_1")):
        assert e.status == "FAILED" and e.network_fee_usd > 0 and ":" in e.reason
