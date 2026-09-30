from dex.dexscreener import parse_pair, parse_socials, pick_best_pair
from pumpfun.client import parse_coin
from pumpfun.stream import parse_event

# trimmed real responses captured 2026-09-29
PUMP_COIN = {
    "mint": "9pMXEbTjQ5HHiYifGbNBwkMkrQxGyhuSmB8ndKNXpump", "name": "Super Intelligent Cat", "symbol": "SICAT",
    "twitter": "https://x.com/sicatcoin", "bonding_curve": "4Lr1oD5v5eKZ3Po99QpJYQifVo9fjSBEva5BVNWCZ85D",
    "creator": "Ee74HnN114swkYnwfnZruPxxz8rXnmXaEeR3sH3qGJmZ", "created_timestamp": 1790608864000,
    "complete": True, "total_supply": 1000000000000000, "market_cap": 2950.0, "real_sol_reserves": 0,
    "real_token_reserves": 0, "pump_swap_pool": "B4kJcppu2bxh7srJmEJq8NVM9SrpZVLum5YVjGq6VChH",
    "quote_mint": "Xsc9qvGR1efVDFGLrVsmkzv3qi45LTBjeUKSPmx9qEh", "base_decimals": 6, "quote_decimals": 8,
    "usd_market_cap": 347571.33, "ath_market_cap": 917422.3,
}
PUMP_NEW = {
    "mint": "Cy9jGVec9G6XV5m9m1GN46UGiZ1znKNJkaNyawkDpump", "symbol": "DWPJ", "creator": "83FE",
    "created_timestamp": 1790656051000, "complete": False, "total_supply": 1000000000000000,
    "real_sol_reserves": 197530863, "real_token_reserves": 786081193828046, "market_cap": 28.328,
    "quote_mint": "11111111111111111111111111111111", "base_decimals": 6, "quote_decimals": 9,
    "usd_market_cap": 3337.82,
}
PAIR = {
    "chainId": "solana", "dexId": "pumpswap", "pairAddress": "P1", "baseToken": {"address": "M"},
    "priceUsd": "0.0003477",
    "txns": {"m5": {"buys": 30, "sells": 32}, "h1": {"buys": 415, "sells": 308}},
    "volume": {"h24": 3143105.85, "h6": 395144.99, "h1": 41674.15, "m5": 4218.67},
    "priceChange": {"m5": 12.89, "h1": 14.37}, "liquidity": {"usd": 63307.96},
    "fdv": 336391, "marketCap": 336391, "pairCreatedAt": 1790608872000,
    "info": {"websites": [{"url": "https://sicatcoin.com/"}],
             "socials": [{"url": "https://x.com/sicatcoin", "type": "twitter"},
                         {"url": "https://t.me/sicatsol", "type": "telegram"}]},
}


def test_parse_graduated_coin():
    t = parse_coin(PUMP_COIN)
    assert t.symbol == "SICAT" and t.complete and t.curve_progress == 100
    assert t.total_supply == 1e9
    assert t.pool == "B4kJcppu2bxh7srJmEJq8NVM9SrpZVLum5YVjGq6VChH"
    assert t.sol_price is None  # quote is not SOL -> no SOL price inferred
    assert abs(t.created_at - 1790608864) < 1


def test_parse_curve_coin():
    t = parse_coin(PUMP_NEW)
    assert not t.complete
    assert abs(t.real_sol_reserves - 0.1975) < 1e-3
    assert 0.8 < t.curve_progress < 1.0
    assert 100 < t.sol_price < 150


def test_parse_ws_event():
    t = parse_event({"txType": "create", "mint": "M", "traderPublicKey": "C", "initialBuy": 7035992.7,
                     "solAmount": 0.198, "name": "o", "symbol": "O", "bondingCurveKey": "B"})
    assert t.creator == "C" and t.dev_initial_buy == 7035992.7 and t.complete is False
    assert parse_event({"message": "Successfully subscribed"}) is None


def test_parse_pair():
    m = parse_pair(PAIR)
    assert m.market_cap == 336391 and m.liquidity_usd == 63307.96 and m.liquidity_source == "dexscreener_amm"
    assert m.buys_5m == 30 and m.sells_5m == 32 and m.txns_5m == 62
    m.updated_at = m.pair_created_at + 7200          # pair older than 1h -> full 12-slice window
    assert abs(m.vol_accel - 4218.67 / (41674.15 / 12)) < 1e-9
    assert parse_socials(PAIR) == {"twitter": "https://x.com/sicatcoin", "telegram": "https://t.me/sicatsol",
                                   "website": "https://sicatcoin.com/"}


def test_missing_fields_stay_none_not_zero():
    m = parse_pair({"chainId": "solana", "dexId": "pumpfun", "baseToken": {"address": "M"}})
    for fld in ("price_usd", "market_cap", "liquidity_usd", "vol_5m", "vol_1h", "buys_5m", "sells_5m"):
        assert getattr(m, fld) is None, fld
    assert m.txns_5m is None and m.buy_sell_ratio_5m is None and m.vol_accel is None


def test_parse_curve_virtual_reserves():
    t = parse_coin({**PUMP_NEW, "virtual_sol_reserves": 30197530863})
    assert abs(t.virtual_sol_reserves - 30.1975) < 1e-3 and t.pump_updated_at


def test_curve_pair_without_liquidity():
    p = {**PAIR, "dexId": "pumpfun", "liquidity": None}
    m = parse_pair(p)
    assert m.liquidity_usd is None and m.liquidity_source == ""
    assert pick_best_pair([p, PAIR]) is PAIR


def test_young_pair_has_no_fake_acceleration():
    """A 4-minute-old pair: DexScreener h1 volume == m5 volume. Must NOT read as a 12x acceleration."""
    import time
    now = time.time()
    p = {**PAIR, "volume": {"m5": 46_730, "h1": 46_730}, "pairCreatedAt": int((now - 240) * 1000)}
    m = parse_pair(p)
    m.updated_at = now
    assert m.vol_accel is None and m.txn_accel is None and m.buy_accel is None
    p2 = {**PAIR, "volume": {"m5": 10_000, "h1": 40_000}, "pairCreatedAt": int((now - 20 * 60) * 1000)}
    m2 = parse_pair(p2)
    m2.updated_at = now
    assert abs(m2.vol_accel - 10_000 / (40_000 / 4)) < 1e-6      # 20-min life = 4 five-minute slices
