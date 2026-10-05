"""Trend research T1 (docs/trend_plan.md): no look-ahead, costs as registered, split, baseline, verdict, data plumbing.
Synthetic bars only — no network."""
import json
import math
import random

import pytest

from trend import backtest as T
from trend.data import Api, fetch_ohlcv, select_pool


def bars_from(closes, vol=1000.0, t0=1_700_000_000):
    out = []
    for i, c in enumerate(closes):
        o = closes[i - 1] if i else c
        out.append((t0 + 3600 * i, o, max(o, c) * 1.001, min(o, c) * 0.999, c, vol))
    return out


def test_entry_fills_at_the_next_open_after_a_7_day_breakout_with_volume():
    closes = [1.0] * 200 + [1.2] + [1.25] * 10
    bars = bars_from(closes)
    bars = [b if i != 200 else (*b[:5], 5000.0) for i, b in enumerate(bars)]       # volume surge on the breakout
    tr = T.signals_t1(bars)
    assert tr and tr[0][0] == 201                                                  # signal on bar 200, fill at 201


def test_no_entry_without_volume_and_no_signal_from_future_bars():
    closes = [1.0] * 200 + [1.2] + [1.25] * 10
    flat = bars_from(closes)
    flat = [b if i != 200 else (*b[:5], 500.0) for i, b in enumerate(flat)]       # breakout on LOW volume
    assert T.signals_t1(flat) == []
    # changing a FUTURE bar never changes an earlier entry
    a = bars_from([1.0] * 200 + [1.2] + [1.25] * 50)
    a = [b if i != 200 else (*b[:5], 5000.0) for i, b in enumerate(a)]
    b2 = list(a)
    b2[230] = (*b2[230][:4], 9.9, b2[230][5])
    assert T.signals_t1(a)[0][0] == T.signals_t1(b2)[0][0]


def test_exit_on_the_3_day_low_at_the_next_open_and_end_of_data():
    up = [1.0] * 200 + [1.2] + [1.3 + 0.001 * i for i in range(100)] + [0.9] + [0.9] * 5
    bars = bars_from(up)
    bars = [b if i != 200 else (*b[:5], 5000.0) for i, b in enumerate(bars)]
    (ei, xi, kind), = T.signals_t1(bars)
    assert kind == "channel" and xi == 302 and bars[301][4] == 0.9
    still = bars_from([1.0] * 200 + [1.2] + [1.3] * 20)
    still = [b if i != 200 else (*b[:5], 5000.0) for i, b in enumerate(still)]
    assert T.signals_t1(still)[-1][2] == "end"


def test_costs_as_registered():
    c = T.Costs(size_usd=100.0, priority_fee_sol=0.005, reserve_usd=1_000_000.0)
    fixed = 2 * (0.00011 + 0.005) * 150 / 100
    assert c.round_trip_pct() == pytest.approx(100 * (2 * (0.003 + 0.005 + 0.0002) + fixed))
    assert c.net(1.0, 1.0) == pytest.approx(100 * ((1 - 0.0082) ** 2 - 1 - fixed))     # flat trade loses the costs
    assert T.Costs(50.0).round_trip_pct() > c.round_trip_pct()                           # fixed fee weighs more


def test_split_baseline_and_verdict_on_noise_rejects():
    rng = random.Random(3)
    data, res = {}, {}
    for k in range(12):
        p, closes = 1.0, []
        for _ in range(2000):
            p *= math.exp(rng.gauss(0, 0.02))
            closes.append(p)
        data[f"T{k}"] = [(b[0], b[1], b[2], b[3], b[4], 1000.0 * (1 + rng.random())) for b in bars_from(closes)]
        res[f"T{k}"] = 2_000_000.0
    r = T.run(data, res, baseline_iters=200)
    assert r["verdict"] == "REJECT"                                   # random walks + costs: no edge
    assert r["holdout"].get("n", 0) + r["in_sample"].get("n", 0) == len(r["trades"])
    cut = r["window"]["cut"]
    assert r["holdout"].get("n", 0) == sum(1 for t in r["trades"] if t["entry_ts"] >= cut)    # split by ENTRY time
    assert r["in_sample"].get("n", 0) == sum(1 for t in r["trades"] if t["entry_ts"] < cut)


def test_cluster_ci_needs_two_tokens():
    assert T.cluster_ci([{"symbol": "A", "net_pct": 1.0}]) is None
    assert T.cluster_ci([{"symbol": "A", "net_pct": 1.0}, {"symbol": "B", "net_pct": 3.0}]) is not None


# ---------------------------------------------------------------- data plumbing (fake API)
class FakeApi(Api):
    def __init__(self, routes):
        super().__init__(spacing_s=0, sleep=lambda s: None)
        self.routes = routes

    def get(self, path):
        self.calls += 1
        for k, v in self.routes.items():
            if path.startswith(k):
                return v(path) if callable(v) else v
        raise AssertionError(path)


def test_pool_selection_checks_symbol_quote_reserve_and_age():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    pool = lambda addr, q, res, created: {"attributes": {"address": addr, "reserve_in_usd": str(res),  # noqa: E731
                                                         "pool_created_at": created},
                                          "relationships": {"base_token": {"data": {"id": "solana_MINT"}},
                                                            "quote_token": {"data": {"id": "solana_" + q}},
                                                            "dex": {"data": {"id": "orca"}}}}
    sol = "So11111111111111111111111111111111111111112"
    api = FakeApi({"/networks/solana/tokens/MINT/pools": {"data": [
        pool("young", sol, 9e6, "2026-08-01T00:00:00Z"), pool("small", sol, 5e5, "2024-01-01T00:00:00Z"),
        pool("other", "XYZ", 9e6, "2024-01-01T00:00:00Z"), pool("good", sol, 3e6, "2024-01-01T00:00:00Z")]},
        "/networks/solana/tokens/MINT": {"data": {"attributes": {"symbol": "ABC"}}}})
    sel = select_pool(api, "ABC", "MINT", now)
    assert sel["pool"] == "good" and sel["quote"] == "SOL"
    assert "symbol mismatch" in select_pool(api, "XXX", "MINT", now)["dropped"]


def test_ohlcv_pages_backwards_and_dedupes():
    from datetime import datetime, timezone
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    end = int(now.timestamp())

    def page(path):
        before = int(path.split("before_timestamp=")[1])
        rows = [[before - 3600 * k, 1, 1, 1, 1, 1] for k in range(1, 1001)]
        return {"data": {"attributes": {"ohlcv_list": rows}}}
    bars = fetch_ohlcv(FakeApi({"/networks/solana/pools/P/ohlcv": page}), "P", now, days=100)
    ts = [b[0] for b in bars]
    assert ts == sorted(set(ts)) and ts[-1] == end - 3600 and len(ts) == 100 * 24 - 1
    assert json.dumps(bars[0])


def test_pool_where_the_token_is_the_quote_side_is_accepted_and_404_drops():
    import urllib.error
    from datetime import datetime, timezone
    now = datetime(2026, 10, 5, tzinfo=timezone.utc)
    sol = "So11111111111111111111111111111111111111112"
    p = {"attributes": {"address": "flip", "reserve_in_usd": "4000000", "pool_created_at": "2024-01-01T00:00:00Z"},
         "relationships": {"base_token": {"data": {"id": "solana_" + sol}}, "quote_token": {"data": {"id": "solana_MINT"}},
                           "dex": {"data": {"id": "orca"}}}}
    api = FakeApi({"/networks/solana/tokens/MINT/pools": {"data": [p]},
                   "/networks/solana/tokens/MINT": {"data": {"attributes": {"symbol": "$ABC"}}}})
    sel = select_pool(api, "ABC", "MINT", now)
    assert sel["pool"] == "flip" and sel["quote"] == "SOL"

    def boom(path):
        raise urllib.error.HTTPError(path, 404, "nf", {}, None)
    assert select_pool(FakeApi({"/networks/solana/tokens/": boom}), "X", "BAD", now)["dropped"].startswith("mint not found")
