"""Winner capture (stream buffer) and winner-reverse discovery.

Hand-built trade sequences that exercise the rules; test vectors, never shown in the product.
"""
import time

from radar.decoder.models import TradeRecord
from radar.services import winner_reverse as wr
from radar.services.stream import StreamService

S = {"winner_min_peak_mc_sol": 400, "winner_min_multiple": 5, "winner_hold_fraction": 0.3,
     "winner_early_mc_fraction": 0.3, "winner_min_buy_sol": 0.2, "winner_min_tokens": 3}
T0 = 1_795_400_000
CREATED_SLOT = 1000


def tr(mint, sol, mc, wallet="W1", bt=None, sig="s", slot=2000):
    return TradeRecord(signature=sig, event_key="trade:0", wallet=wallet, mint=mint, slot=slot,
                       block_time=bt or int(time.time()), tx_type="buy", venue="pump_curve", sol_amount=sol,
                       token_amount=1000.0, token_amount_raw=10 ** 9, decimals=6, fee_trading_sol=0.0,
                       market_cap_sol=mc)


def svc():
    s = StreamService(ctx=None)
    s.capture_mc, s.capture_min, s.capture_hours, s.buffer_minutes = 150.0, 0.05, 24.0, 120.0
    s.creation_slot["M"] = CREATED_SLOT          # M was launched while the stream listened
    return s


def test_small_trades_are_buffered_and_written_when_the_token_starts_rising():
    s = svc()
    assert s._capture(tr("M", 0.3, 40.0, sig="a")) is False          # buffered, not stored
    assert s._capture(tr("M", 0.01, 40.0, sig="dust")) is False      # dust: neither
    assert [t.signature for t in s.buffer["M"]] == ["a"]
    s._maybe_start_capture(tr("M", 0.5, 160.0, sig="b"))              # market cap crosses 150 SOL
    assert [t.signature for t in s.pending_trades] == ["a"]           # buffer flushed for storage
    assert "M" in s.capture_until and "M" not in s.buffer and "M" in s.new_captures
    assert s._capture(tr("M", 0.1, 170.0, sig="c")) is True           # every later trade is kept
    assert s._capture(tr("M", 0.01, 170.0, sig="d")) is False         # except dust


def test_mispriced_market_cap_never_starts_a_capture():
    s = svc()
    s._maybe_start_capture(tr("M", 0.5, 800_000_000.0))
    assert "M" not in s.capture_until


def test_established_tokens_are_never_captured():
    s = svc()
    s._maybe_start_capture(tr("OLD", 5.0, 9_000.0))       # not launched while the stream listened
    assert "OLD" not in s.capture_until


def test_buffer_drops_trades_older_than_the_window():
    s = svc()
    s._capture(tr("M", 0.3, 40.0, bt=int(time.time()) - 3 * 3600, sig="old"))
    s._capture(tr("M", 0.3, 40.0, sig="new"))
    s.prune_buffer()
    assert [t.signature for t in s.buffer["M"]] == ["new"]


def series(first_mc, peak_mc, mc_1h, n=60):
    """first 10 minutes at first_mc, peak at 2 h, then mc_1h one hour after the peak."""
    out = [{"bt": T0 + 60 * i, "mc": first_mc} for i in range(10)]
    out += [{"bt": T0 + 3600 + 60 * i, "mc": first_mc + (peak_mc - first_mc) * i / n} for i in range(n + 1)]
    peak_t = T0 + 3600 + 60 * n
    out += [{"bt": peak_t + 60 * i, "mc": peak_mc} for i in range(5)]
    out += [{"bt": peak_t + 3600 + 30 * i, "mc": mc_1h} for i in range(10)]
    return out


def test_winner_needs_multiple_peak_and_holding_after_the_peak():
    assert wr.evaluate_token(series(50, 600, 300), T0, S)["winner"] is True
    assert wr.evaluate_token(series(50, 600, 60), T0, S)["reason"] == "did_not_hold_after_peak"   # pump and dump
    assert wr.evaluate_token(series(50, 200, 150), T0, S)["reason"] == "peak_below_min"
    assert wr.evaluate_token(series(200, 600, 500), T0, S)["reason"] == "multiple_below_min"


def t(w, typ, sol, tok, mc, slot, bt, sig):
    return {"wallet": w, "tx_type": typ, "sol": sol, "tok": tok, "fee": 0.01, "mc": mc, "slot": slot, "bt": bt, "signature": sig}


def test_early_buyers_exclude_snipers_bundlers_creator_insiders_losers_and_open_positions():
    trades = [
        t("SNIPER", "buy", 1, 100, 30, CREATED_SLOT + 1, T0, "s1"), t("SNIPER", "sell", 5, 100, 300, 9000, T0 + 900, "s2"),
        t("B1", "buy", 1, 100, 32, CREATED_SLOT + 4, T0, "b1"), t("B2", "buy", 1, 100, 32, CREATED_SLOT + 4, T0, "b2"),
        t("B3", "buy", 1, 100, 32, CREATED_SLOT + 4, T0, "b3"), t("B1", "sell", 4, 100, 300, 9000, T0 + 900, "b4"),
        t("DEV", "buy", 1, 100, 30, CREATED_SLOT + 50, T0, "d1"), t("DEV", "sell", 4, 100, 300, 9000, T0 + 900, "d2"),
        t("FRIEND", "buy", 1, 100, 40, CREATED_SLOT + 60, T0, "f1"), t("FRIEND", "sell", 4, 100, 300, 9000, T0 + 900, "f2"),
        t("GOOD", "buy", 2, 200, 60, CREATED_SLOT + 300, T0 + 120, "g1"), t("GOOD", "sell", 7, 200, 400, 9000, T0 + 2000, "g2"),
        t("LOSER", "buy", 2, 200, 100, CREATED_SLOT + 400, T0 + 200, "l1"), t("LOSER", "sell", 1, 200, 60, 9000, T0 + 300, "l2"),
        t("HOLDER", "buy", 2, 200, 100, CREATED_SLOT + 500, T0 + 200, "h1"),
        t("LATE", "buy", 2, 200, 500, CREATED_SLOT + 9000, T0 + 9000, "x1"), t("LATE", "sell", 3, 200, 550, 9500, T0 + 9500, "x2"),
    ]
    out, excl = wr.early_buyers(trades, CREATED_SLOT, "DEV", 600.0, {"FRIEND"}, S)
    assert [b["wallet"] for b in out] == ["GOOD"]
    g = out[0]
    assert g["pnl"] == round(7 - 2 - 0.02, 4) and g["buy_sig"] == "g1" and g["sell_sig"] == "g2"
    assert excl["sniper"] == 1 and excl["bundle"] == 3 and excl["creator"] == 1 and excl["linked_to_creator"] == 1
    assert excl["loss"] == 1 and excl["not_closed_in_tokens"] == 1          # LATE is not early at all


def test_a_single_lucky_win_is_not_a_candidate():
    b = {"cost": 1.0, "pnl": 3.0, "entry_mc": 50.0, "buy_sig": "x", "sell_sig": "y"}
    wins = [("M1", {"wallet": "A", **b}), ("M2", {"wallet": "A", **b}), ("M3", {"wallet": "A", **b}),
            ("M1", {"wallet": "C", **b}), ("M1", {"wallet": "C", **b})]
    c = wr.repeated(wins, 3)
    assert list(c) == ["A"] and c["A"]["tokens"] == 3 and c["A"]["pnl"] == 9.0


def test_historical_winner_test_uses_hourly_closes_and_ignores_wicks():
    import asyncio
    from radar.tools import winner_history as wh

    class Http:
        def __init__(self, candles):
            self.candles = candles

        async def request_json(self, *a, **k):
            return {"data": {"attributes": {"ohlcv_list": self.candles}}}

    class C:
        def __init__(self, candles):
            self.http = Http(candles)

    s = {"winner_hist_min_peak_usd": 300000, "winner_min_multiple": 5, "winner_hold_fraction": 0.3}
    # [t, open, high, low, close, volume]; price x 1e9 supply = market cap (USD)
    held = [[3600 * i, 0, 0.09, 0, c, 1] for i, c in enumerate([8e-5, 2e-4, 6e-4, 4e-4, 3e-4])]
    r = asyncio.run(wh.winner_test(C(held), {"pool": "P"}, s, 150.0))
    assert r["winner"] is True and r["peak_mc_usd"] == 600000          # the 0.09 wick is ignored
    dumped = [[3600 * i, 0, 0, 0, c, 1] for i, c in enumerate([8e-5, 2e-4, 6e-4, 5e-5])]
    assert asyncio.run(wh.winner_test(C(dumped), {"pool": "P"}, s, 150.0))["reason"] == "did_not_hold_after_peak"
    small = [[3600 * i, 0, 0, 0, c, 1] for i, c in enumerate([8e-5, 1e-4, 2e-4, 1.5e-4])]
    assert asyncio.run(wh.winner_test(C(small), {"pool": "P"}, s, 150.0))["reason"] == "peak_below_min"


def test_priority_prefers_growth_that_held_with_depth_and_enough_data():
    from radar.tools.winner_history import priority
    base = {"graduation_mc_usd": 100_000, "peak_mc_usd": 1_000_000, "hold_ratio": 0.6,
            "volume_first_24h_usd": 5_000_000, "candles": 48}
    p = priority(base)
    assert priority({**base, "hold_ratio": 0.3}) < p                    # held less
    assert priority({**base, "peak_mc_usd": 400_000}) < p               # grew less
    assert priority({**base, "volume_first_24h_usd": 50_000}) < p       # thin trading
    assert priority({**base, "candles": 6}) == round(p * 0.5, 4) or priority({**base, "candles": 6}) < p
