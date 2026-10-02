"""G2 — backtest fills entries and exits on the same liquidity model (no 30 % haircut on every exit) and says the
result is approximate; live paper without a Jupiter client still takes the haircut."""
import time

from test_trading import _row
from test_v12 import opened
from trading.backtest import APPROXIMATION, backtest
from trading.config import TradingConfig


def test_backtest_exits_are_model_fills_and_result_is_approximate():
    t0 = time.time() - 3600
    rows = [_row(t0, 0.0002), _row(t0 + 20, 0.00021), _row(t0 + 40, 0.00019, liq=8_000)]
    res = backtest(rows, TradingConfig(seed=1), allow_legacy=True)
    assert res["approximation"] == APPROXIMATION and "APPROXIMATE" in APPROXIMATION
    assert [t["exit"] for t in res["trades"]] == ["liquidity_collapse"]
    # a model exit on a 5 % lower print loses a few %, not the 30 % haircut
    assert -10 < res["trades"][0]["net"] < 0


def test_live_paper_without_jupiter_still_haircuts():
    b, st, p = opened()
    b.jupiter = None
    assert b.exit_fallback == "haircut"
    st.stamps["market"].updated_at = time.time() + 1
    st.market.price_usd = p.entry_price * 0.8
    b.tick()
    sell = [e for e in b.book.executions if e.side == "SELL"][-1]
    assert sell.model.startswith("PAPER haircut")
