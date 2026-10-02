"""Fix 7 — the backtest runs the PRODUCTION strategy (gated lifecycle engine) by default and refuses another config
unless explicitly marked legacy; its price history grows with the replayed frames (no look-ahead)."""
import time

import pytest

from test_trading import _row
from trading.backtest import _History, backtest
from trading.config import TradingConfig, production_config


def test_production_config_is_the_server_fingerprint():
    c = production_config()
    assert c.lifecycle and c.entry_location_gate and c.experimental and c.sample_id() == "02e5bdcc03"


def test_backtest_default_is_the_gated_lifecycle_and_legacy_is_refused():
    t0 = time.time() - 3600
    rows = [_row(t0 + 20 * i, 0.0002 * (1 + 0.01 * i)) for i in range(5)]
    res = backtest(rows)
    assert res["config"]["lifecycle"] and res["config"]["entry_location_gate"] and not res["config"]["legacy"]
    with pytest.raises(ValueError):
        backtest(rows, TradingConfig(seed=1))                   # the old engine is not what the bot trades
    with pytest.raises(ValueError):
        backtest(rows, production_config(entry_location_gate=False))
    assert backtest(rows, TradingConfig(seed=1), allow_legacy=True)["config"]["legacy"] is True


def test_history_has_no_future_points():
    h = _History()
    for ts, p in ((100.0, 1.0), (120.0, 1.1)):
        h.add({"mint": "M", "ts": ts, "price": p})
    assert [pt.ts for pt in h.get("M").points] == [100.0, 120.0] and h.get("X") is None
