"""Decoder tests on REAL mainnet transactions (tests/fixtures/tx, captured 2026-11-22 by
scripts/capture_fixtures.py). Each expected number below can be checked on an explorer:
https://solscan.io/tx/<signature>
"""
import json
from pathlib import Path

import pytest

from radar.decoder.logs import records_from_logs
from radar.decoder.tx import decode_transaction

FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tx"


def load(prefix: str) -> dict:
    files = list(FIX.glob(prefix + "*.json"))
    assert len(files) == 1, prefix
    return json.loads(files[0].read_text())


def dec(prefix: str):
    return decode_transaction(load(prefix))


def test_all_fixtures_decode_without_error():
    files = sorted(FIX.glob("*.json"))
    assert len(files) >= 30
    n_trades = n_verified = 0
    for f in files:
        d = decode_transaction(json.loads(f.read_text()))
        for t in d.trades:
            n_trades += 1
            n_verified += t.verification_status == "verified"
            assert t.signature == f.stem
            assert t.sol_amount >= 0 and t.token_amount >= 0
            assert t.tx_type in ("buy", "sell")
    assert n_trades >= 30
    assert n_verified >= 20


def test_large_curve_sell_reconciles_to_the_lamport():
    d = dec("6acaFRzs")
    assert not d.failed and len(d.trades) == 1
    t = d.trades[0]
    assert t.tx_type == "sell" and t.venue == "pump_curve"
    assert t.wallet == "3LrZJ3CaJTMNAgxHhp2Pmr33cEnozfLkZg4aMmMC8iPJ"
    assert t.sol_amount == pytest.approx(4.202539, abs=1e-6)
    assert t.verification_status == "verified"
    assert t.reconciliation_residual_sol == 0
    # received = sol - trading fee - network fee - other costs
    assert t.wallet_sol_delta == pytest.approx(t.sol_amount - t.fee_trading_sol - t.fee_network_sol
                                               - t.tip_sol - t.other_cost_sol, abs=1e-9)


def test_large_curve_buy_separates_fees_and_bot_fee():
    t = dec("UNhL3fan").trades[0]
    assert t.tx_type == "buy" and t.sol_amount == pytest.approx(2.933333, abs=1e-6)
    assert t.fee_trading_sol == pytest.approx(0.036667, abs=1e-6)   # 1.25 % pump + creator fee
    assert t.other_cost_sol == pytest.approx(0.035976, abs=1e-6)    # trading-bot fee outside the swap
    assert t.rent_sol == pytest.approx(0.00151384, abs=1e-8)       # refundable token-account rent
    assert t.verification_status == "verified"
    # rent is NOT part of the cost: wallet delta = -(sol + fees + bot fee + network fee)
    assert t.wallet_sol_delta == pytest.approx(-(t.sol_amount + t.fee_trading_sol + t.other_cost_sol
                                                 + t.fee_network_sol + t.tip_sol), abs=1e-9)


def test_failed_transactions_produce_no_trades():
    for p in ("4qLwxNu9", "ZQqKdDSa", "5vdFdVqe"):
        d = dec(p)
        assert d.failed and d.trades == [] and d.token_transfers == []


def test_token_creation_event_and_dev_buy():
    d = dec("58uva935")
    assert len(d.creates) == 1
    c = d.creates[0]
    assert c.mint == "GGX4FQ75CExNbt1xJMA9d66stNfap5aVCmGZLa5Mpump"
    assert c.name == "Weird Cat" and c.symbol == "WEIRDCAT"
    assert c.bonding_curve == "9R81SWgh9YAe8x9ZpP6rNZ9J5RrNvD2S6x27yuvvDWnS"
    t = d.trades[0]
    assert t.wallet == c.creator and t.tx_type == "buy"
    # launch costs (new mint/curve/metadata accounts) are a cost outside the swap
    assert t.other_cost_sol > 0.005
    assert t.verification_status == "verified"


def test_multi_buy_in_one_transaction_creates_separate_records():
    d = dec("2EoViTuJ")
    assert len(d.trades) == 4
    assert [t.event_key for t in d.trades] == ["trade:0", "trade:1", "trade:2", "trade:3"]
    assert len({t.wallet for t in d.trades}) == 4          # bundle: 4 different wallets
    assert all(t.verification_status == "verified" for t in d.trades)


def test_completion_and_migration_events():
    comp = dec("3huhHLkG").completes
    assert comp and comp[0].mint == "2iDdpRNvUywMpcL6aKT75brjm9ehko4z9ss6DnE9pump"
    mig = dec("3rVFqzVD").migrations
    assert mig and mig[0].mint == comp[0].mint
    assert mig[0].pool == "4xykvpKQSUmit5oUAcSezuyq1WoahGujXuSSrj5zKzMJ"
    assert mig[0].sol_amount == 87_599_820_200


def test_pumpswap_sell_and_exact_quote_buy():
    s = dec("3WcEqbRg").trades[0]
    assert s.venue == "pumpswap" and s.tx_type == "sell"
    assert s.verification_status == "verified"
    b = dec("4z7h5KtV").trades[0]
    assert b.venue == "pumpswap" and b.tx_type == "buy"
    # exact-quote-in buy: user paid 15_318_278 lamports of which 129_110 were fees
    assert b.sol_amount == pytest.approx(0.015189168, abs=1e-9)
    assert b.fee_trading_sol == pytest.approx(0.00012911, abs=1e-9)
    assert b.verification_status == "verified"


def test_routed_usdc_purchase_is_only_partially_verified():
    t = dec("3AYipcX6").trades[0]
    assert t.sol_amount == pytest.approx(2.138503, abs=1e-6)
    assert t.verification_status == "partially_verified"
    assert "another asset" in t.verification_note


def test_decoding_is_deterministic_for_idempotent_upserts():
    a = [(t.signature, t.event_key, t.wallet, t.sol_amount) for t in dec("2EoViTuJ").trades]
    b = [(t.signature, t.event_key, t.wallet, t.sol_amount) for t in dec("2EoViTuJ").trades]
    assert a == b


def test_log_stream_decoder_matches_full_transaction_decoder():
    """The live stream (logs only) must produce the same trade keys / amounts as the verifier."""
    checked = 0
    for f in sorted(FIX.glob("*.json")):
        tx = json.loads(f.read_text())
        if tx["meta"]["err"] is not None:
            continue
        full = decode_transaction(tx)
        pools = full.amm_pools
        stream = records_from_logs(f.stem, tx["slot"], tx["meta"]["logMessages"], pools.get)
        if stream["truncated"]:
            continue
        fk = {t.event_key: t for t in full.trades}
        hop_keys = {t.extra.get("hop_event_key") for t in full.trades if t.venue == "pump_curve_multihop"}
        for r in stream["trades"]:
            if r.event_key in hop_keys:
                continue  # SOL leg folded into the multi-hop record by the full decoder
            assert r.event_key in fk, (f.stem, r.event_key)
            assert fk[r.event_key].wallet == r.wallet
            assert fk[r.event_key].mint == r.mint
            assert fk[r.event_key].sol_amount == pytest.approx(r.sol_amount, abs=1e-12)
            assert fk[r.event_key].token_amount_raw == r.token_amount_raw
            assert r.verification_status == "partially_verified"
            checked += 1
    assert checked >= 25


def test_multi_hop_through_a_non_sol_curve_is_one_sol_trade():
    """Real tx: token A sold on a curve quoted in token B, B sold for SOL in the same tx."""
    import json as _j
    tx = _j.loads((FIX.parent / "multihop" / "multi_hop_sell.json").read_text())
    d = decode_transaction(tx)
    assert len(d.trades) == 1
    t = d.trades[0]
    assert t.venue == "pump_curve_multihop" and t.tx_type == "sell"
    assert t.mint == "CkXfLpvTYLiWhytNRLsGyxuVsQ9gH8fPbHf2HH3Ypump"
    assert t.sol_amount == pytest.approx(0.372544205, abs=1e-9)
    assert t.verification_status == "verified" and t.reconciliation_residual_sol == 0


def test_usdc_quoted_curve_bought_through_router_is_valued_in_sol():
    """Real tx (22/11/2026): KOL wallet Xanse paid SOL, a router (FlashX) swapped it to USDC on
    Raydium CLMM and bought a USDC-quoted pump.fun curve (buy_exact_quote_in). The curve event
    has no SOL amount; the trade is valued by the wallet's own SOL change and stays partial."""
    import json, pathlib
    from radar.decoder.tx import decode_transaction
    tx = json.loads((pathlib.Path(__file__).parent.parent / "fixtures/nonsol/usdc_curve_router_buy.json").read_text())
    d = decode_transaction(tx)
    assert len(d.trades) == 1
    t = d.trades[0]
    assert t.wallet == "B9K2wTQcRDLRLhMKFyRh2hPqHrr6VKiCC9yNGpkMUXrh" and t.tx_type == "buy"
    assert t.mint == "GGo31zd8piSnzHe5hJqeFMk18rJpaHZzc4NJwVUspump"
    assert t.venue == "pump_curve_nonsol" and t.verification_status == "partially_verified"
    assert 1.49 < t.sol_amount < 1.504          # wallet paid 1.5035 SOL incl. 0.00007 network fee


def test_route_leg_of_token_quoted_curve_is_not_reported_by_the_stream():
    """Real tx (22/11/2026): KOL wallet trav bought token Ad2y… on a curve quoted in token 7SL2…
    (NIO); the router first bought 7SL2… with 1.036 SOL on PumpSwap. From logs alone the stream
    must not report '7SL2… bought for 1.036 SOL'; the full decoder records the real token."""
    import json, pathlib
    from radar.decoder.logs import records_from_logs
    from radar.decoder.tx import decode_transaction
    tx = json.loads((pathlib.Path(__file__).parent.parent / "fixtures/nonsol/token_quoted_curve_hop_buy.json").read_text())
    pools = {"B4Wmjc6PvjCtU3qSVS3eTT9Mr4M5V5jBkRQctWANTc71":
             ("7SL2VnfYsChvog5oQY9Nmtqu1wceaMiQzWoqoLQpump", "So11111111111111111111111111111111111111112")}
    out = records_from_logs("x", tx["slot"], tx["meta"]["logMessages"], pools.get)
    assert out["trades"] == [] and out["hop_legs_dropped"] == 1
    assert out["nonsol_users"] == ["CXnf4Tt7qFz3KZNwn3Yve5MKaRyxGoAy2eDX3QT8e99m"]
    d = decode_transaction(tx, pool_resolver=pools.get)
    assert [(t.mint[:6], t.tx_type, round(t.sol_amount, 3)) for t in d.trades] == [("Ad2yCH", "buy", 1.036)]
