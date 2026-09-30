import asyncio

from core.models import TokenInfo
from dev.analyzer import DevAnalyzer, classify_status, find_incoming_sol
from holders.analyzer import compute_stats


def test_concentration_excludes_curve_and_pool():
    amounts = {"CURVE": 700e6, "POOL": 50e6, "creator": 20e6}
    amounts.update({f"w{i}": 10e6 - i * 1e5 for i in range(30)})
    s = compute_stats(amounts, 1e9, {"CURVE": "bonding curve", "POOL": "AMM pool"}, creator="creator",
                      holder_count=len(amounts))
    assert s.excluded_pct == 75.0
    assert all(h.owner not in ("CURVE", "POOL") for h in s.top)
    assert s.top[0].owner == "creator" and "CREATOR" in s.top[0].tags
    assert s.creator_pct == 2.0
    assert abs(s.top10_pct - (2.0 + sum(1.0 - i * 0.01 for i in range(9)))) < 0.01


def test_1000_holders_concentrated_vs_distributed():
    concentrated = {f"big{i}": 55e6 for i in range(10)}
    concentrated.update({f"s{i}": 0.45e6 for i in range(990)})
    spread = {f"big{i}": 12e6 for i in range(10)}
    spread.update({f"s{i}": 0.889e6 for i in range(990)})
    assert compute_stats(concentrated, 1e9, {}, holder_count=1000).top10_pct == 55.0
    assert compute_stats(spread, 1e9, {}, holder_count=1000).top10_pct == 12.0


def test_dev_status():
    assert classify_status(100, 100) == ("HOLD", 0.0)
    assert classify_status(100, 80)[0] == "PARTIAL SELL"
    assert classify_status(100, 30)[0] == "MAJOR SELL"
    assert classify_status(100, 0) == ("SOLD ALL", 100.0)
    assert classify_status(None, 0)[0] == "ZERO BALANCE"
    assert classify_status(None, 5)[0] == "HOLDING"
    assert classify_status(None, None)[0] == "UNKNOWN"


def test_funding_from_system_transfer():
    tx = {"meta": {}, "transaction": {"message": {"instructions": [
        {"program": "system", "parsed": {"type": "transfer", "info": {
            "source": "FUNDER", "destination": "DEV", "lamports": 2_000_000_000}}}]}}}
    assert find_incoming_sol(tx, "DEV") == ("FUNDER", 2.0)


def test_funding_from_balance_delta():
    tx = {"meta": {"preBalances": [5_000_000_000, 0, 1], "postBalances": [1_000_000_000, 3_999_000_000, 1]},
          "transaction": {"message": {"accountKeys": [{"pubkey": "EXCHANGE"}, {"pubkey": "DEV"},
                                                      {"pubkey": "PROG"}], "instructions": []}}}
    funder, sol = find_incoming_sol(tx, "DEV")
    assert funder == "EXCHANGE" and abs(sol - 3.999) < 1e-9


def test_dust_is_not_funding():
    tx = {"meta": {"preBalances": [10, 0], "postBalances": [0, 2_000_000]},
          "transaction": {"message": {"accountKeys": ["X", "DEV"], "instructions": []}}}
    assert find_incoming_sol(tx, "DEV") is None


class _FailingRpc:
    """RPC that errors on every call (returns None, like SolanaRpc does on failure)."""
    async def owner_token_balance(self, owner, mint): return None
    async def sol_balance(self, a): return None
    async def signatures(self, a, limit=1000, before=None): return None


class _Pump:
    async def creator_coins(self, creator, limit=50): return None


def test_rpc_failure_gives_unknown_not_zero():
    info = TokenInfo(mint="M", creator="DEV", total_supply=1e9)
    rep = asyncio.run(DevAnalyzer(_FailingRpc(), _Pump()).analyze(info))
    assert rep.balance_verified is False
    assert rep.status == "UNKNOWN" and rep.current_pct is None and rep.current_tokens is None
    assert rep.history_verified is False and rep.prev_tokens_count is None


def test_unknown_supply_is_not_assumed():
    class Rpc(_FailingRpc):
        async def owner_token_balance(self, owner, mint): return 5_000_000.0
    rep = asyncio.run(DevAnalyzer(Rpc(), _Pump()).analyze(TokenInfo(mint="M", creator="DEV", total_supply=None)))
    assert rep.balance_verified is False and rep.current_pct is None
