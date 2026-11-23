"""Curves quoted in another token (USDC) – TradeEvent v2 quote fields (found 23/11/2026).

Fixture: real transactions of mint Cmf39V6X... (pump.fun Mayhem mode, USDC-quoted curve,
BuyV2/SellV2 via program MAyhSmzX...), fetched with getTransactionsForAddress on 23/11/2026.
"""
import json
from pathlib import Path

from radar.decoder.logs import records_from_logs

FIX = Path(__file__).parent.parent / "fixtures" / "nonsol" / "usdc_curve_v2_mayhem.json"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _usdc_delta(tx, owner):
    tot = lambda which: sum(int(b["uiTokenAmount"]["amount"]) for b in tx["meta"].get(which) or []
                            if b["owner"] == owner and b["mint"] == USDC)
    return tot("postTokenBalances") - tot("preTokenBalances")


def test_default_decoding_is_unchanged_for_quote_curves():
    for tx in json.loads(FIX.read_text()):
        out = records_from_logs(tx["transaction"]["signatures"][0], tx["slot"], tx["meta"]["logMessages"])
        assert out["trades"] == [] and out["nonsol_users"]          # stream behaviour as before


def test_quote_amount_equals_the_wallets_usdc_change_and_is_valued_at_the_given_rate():
    txs = json.loads(FIX.read_text())
    assert txs
    for tx in txs:
        out = records_from_logs(tx["transaction"]["signatures"][0], tx["slot"], tx["meta"]["logMessages"],
                                quote_rates={USDC: 1 / 70.0})
        (t,) = [r for r in out["trades"] if r.venue == "pump_curve_quote"]
        assert t.extra["quote_mint"] == USDC and t.mint.startswith("Cmf39V6X")
        q_raw = round(t.extra["quote_amount_ui"] * 1e6)
        assert abs(_usdc_delta(tx, t.wallet)) == q_raw                 # exact, as measured on-chain
        assert t.sol_amount == t.extra["quote_amount_ui"] / 70.0
        assert t.market_cap_sol and t.market_cap_sol > 0
