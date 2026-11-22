"""Person-or-bot classification and late-copy simulation."""
from radar.analytics.behavior import classify_behavior, copy_summary, simulate_copy
from radar.analytics.price_hits import Bar

T0 = 1_790_035_200          # 00:00 UTC


def tr(t, mint, kind="buy"):
    return {"block_time": t, "mint": mint, "tx_type": kind}


def test_human_trader_is_human():
    # 2–4 tokens a day over 10 days, minutes apart, enters 2–10 min after launch, holds 10–60 min
    trades, created = [], {}
    for d in range(10):
        for k in range(3):
            m = f"m{d}{k}"
            t = T0 + d * 86400 + 3600 * (9 + 3 * k)
            created[m] = t - 120 - 60 * k
            trades += [tr(t, m), tr(t + 900 + 300 * k, m, "sell")]
    b = classify_behavior(trades, created, [900 + 300 * k for _ in range(10) for k in range(3)])
    assert b.label == "HUMAN", b.reasons
    assert b.metrics["median_entry_after_creation_s"] >= 120
    assert b.metrics["snipe_share"] == 0


def test_sniper_bot_is_bot():
    trades, created = [], {}
    for i in range(200):                                 # every 7 minutes, day and night
        m = f"s{i}"
        t = T0 + i * 420
        created[m] = t - 1                               # bought 1 s after creation
        trades += [tr(t, m), tr(t + 8, m, "sell")]       # flipped 8 s later
    b = classify_behavior(trades, created, [8] * 200)
    assert b.label == "BOT"
    assert b.hits["snipe_share"] == "bot" and b.hits["quick_flip_share"] == "bot"


def test_burst_trader_on_many_tokens_is_bot():
    trades = []
    for i in range(30):
        base = T0 + i * 3600
        trades += [tr(base + j, f"b{i}{j}") for j in range(6)]       # 6 different tokens in 6 s
    b = classify_behavior(trades, {}, [])
    assert b.label == "BOT"
    assert b.hits["max_trades_per_minute"] == "likely" and b.hits["multi_token_burst_share"] == "bot"


def test_no_rest_counts_gaps_across_midnight():
    trades = []
    for d in range(4):        # busy 05:00–20:00 UTC; rests 20:00–05:00 across midnight -> rested
        trades += [tr(T0 + d * 86400 + 5 * 3600 + i * 2400, f"r{d}{i}") for i in range(23)]
    b = classify_behavior(trades, {}, [])
    assert b.metrics["busy_days"] == 4 and b.metrics["no_rest_day_share"] == 0
    trades = [tr(T0 + i * 1800, f"n{i}") for i in range(48 * 4)]          # every 30 min, 4 days
    b = classify_behavior(trades, {}, [])
    assert b.metrics["no_rest_day_share"] == 1.0 and b.hits["no_rest_day_share"] == "bot"


def test_too_few_trades_is_unknown():
    assert classify_behavior([tr(T0, "a"), tr(T0 + 600, "a", "sell")], {}, [600]).label == "UNKNOWN"


def bars(prices):
    return [Bar(ts=T0 + 60 * i, close=p) for i, p in enumerate(prices)]


def test_zero_delay_reproduces_the_wallet():
    b = bars([1.0, 1.5, 1.4, 1.3, 1.25, 1.2, 1.2])
    r = simulate_copy([(T0 + 10, 2.0, 1.0)], [(T0 + 310, 1.0, 1.2)], False, b, 0, 0.0)
    assert abs(r["multiple"] - 1.2) < 1e-9 and r["win"]


def test_copy_late_pays_more_and_can_turn_a_win_into_a_loss():
    # market: 1.0 until 2 min, 1.5 at 3 min, then fades to 1.2; wallet buys at 1.0 (t=120), sells at 1.2 (t=420)
    b = bars([1.0, 1.0, 1.5, 1.4, 1.3, 1.25, 1.2, 1.2])
    buys, sells = [(T0 + 120, 2.0, 1.0)], [(T0 + 420, 1.0, 1.2)]
    fast = simulate_copy(buys, sells, False, b, 0, 0.03)
    late = simulate_copy(buys, sells, False, b, 60, 0.03)
    assert fast["win"] and fast["multiple"] > 1.1
    assert not late["win"] and late["multiple"] < 1.0      # follower entered after the jump to 1.5


def test_sniper_flip_earns_the_follower_nothing():
    b = bars([1.0, 1.0, 1.0, 1.0])
    # wallet buys at 1.0 and sells 4 s later at 2.0 (its own instant execution)
    r0 = simulate_copy([(T0 + 60, 1.0, 1.0)], [(T0 + 64, 1.0, 2.0)], False, b, 0, 0.03)
    r60 = simulate_copy([(T0 + 60, 1.0, 1.0)], [(T0 + 64, 1.0, 2.0)], False, b, 60, 0.03)
    assert r0["win"] and not r60["win"] and abs(r60["multiple"] - 0.97) < 1e-9


def test_curve_interpolates_inside_a_minute():
    b = bars([1.0, 1.0, 2.0])                     # closes: 1.0 at T0+60, 1.0 at T0+120, 2.0 at T0+180
    r = simulate_copy([(T0 + 60, 1.0, 1.0)], [(T0 + 150, 1.0, 1.5)], False, b, 30, 0.0)
    # bought 30 s late at 1.0 x 1.0/1.0; sold 30 s late at 1.5 x 2.0/1.5 (curve 1.5 half-way at T0+150)
    assert abs(r["multiple"] - 2.0) < 1e-4


def test_copy_mirrors_partial_sells_and_write_offs():
    b = bars([1.0, 1.0, 2.0, 2.0, 3.0, 3.0])
    half_half = simulate_copy([(T0, 1.0, 1.0)], [(T0 + 120, 0.5, 2.0), (T0 + 240, 1.0, 3.0)], False, b, 0, 0.0)
    assert abs(half_half["multiple"] - 2.5) < 1e-9          # 0.5 x 2 + 0.5 x 3
    dead = simulate_copy([(T0, 1.0, 1.0)], [(T0 + 120, 0.25, 2.0)], True, b, 0, 0.0)
    assert abs(dead["multiple"] - 0.5) < 1e-9 and not dead["win"]    # 25 % sold at 2x, rest worthless


def test_copy_without_price_is_not_guessed():
    b = bars([1.0, 1.1])
    assert simulate_copy([(T0 + 60, 1.0, 1.0)], [(T0 + 3 * 3600, 1.0, 1.0)], False, b, 60, 0.03) is None
    assert simulate_copy([(T0 + 60, 1.0, 1.0)], [], False, b, 60, 0.03) is None          # still open
    s = copy_summary([None, {"multiple": 1.4, "win": True}, {"multiple": 0.8, "win": False}], 3)
    assert s["positions"] == 2 and s["win_rate"] == 0.5 and abs(s["coverage"] - 0.667) < 1e-9
