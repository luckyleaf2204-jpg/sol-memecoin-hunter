"""Helius backfill (getTransactionsForAddress), daily credit cap and batch gate.

Inputs are hand-built pages shaped like the live response recorded on 23/11/2026
({"data": [tx...], "paginationToken": "slot:index"}); they are test vectors, never shown in the product.
"""
import asyncio

import pytest

from radar.services import wallet_sync as ws
from radar.solana.rpc import helius_credits
from radar.tools.verify_pnl import wallet_token_after

W = "Wallet1111111111111111111111111111111111111"
NOW = 1_795_400_000


def tx(i: int, bt: int, err=None) -> dict:
    return {"slot": 1000 - i, "blockTime": bt, "transaction": {"signatures": [f"sig{i}"], "message": {}},
            "meta": {"err": err}}


def test_helius_credit_estimate_follows_price_list():
    assert helius_credits("getTransaction", [], {}) == 1
    assert helius_credits("getSignaturesForAddress", [], []) == 1
    full = ["W", {"transactionDetails": "full"}]
    assert helius_credits("getTransactionsForAddress", full, {"data": [{}] * 5}) == 10      # 10 minimum
    assert helius_credits("getTransactionsForAddress", full, {"data": [{}] * 100}) == 10
    assert helius_credits("getTransactionsForAddress", full, {"data": []}) == 10
    assert helius_credits("getTransactionsForAddress", ["W", {"transactionDetails": "signatures"}],
                          {"data": [{}] * 1000}) == 10
    assert helius_credits("getProgramAccounts", [], []) == 10


class FakeRpc:
    def __init__(self, pages, credits=0):
        self.pages, self.calls, self.credits, self.tag = list(pages), 0, credits, "sync"

    def has_helius(self):
        return True

    async def helius_credits_today(self, tag=None):
        return self.credits

    async def get_transactions_for_address(self, wallet, pagination_token=None, limit=100):
        self.calls += 1
        page = self.pages.pop(0)
        return page, (f"tok{self.calls}" if self.pages else None)


class FakeRedis:
    def __init__(self, members=()):
        self.members = set(members)

    async def sismember(self, key, v):
        return v in self.members


class Ctx:
    def __init__(self, rpc, redis=None):
        self.rpc, self.redis = rpc, redis or FakeRedis()


def test_gtfa_listing_stops_at_the_backfill_window_and_keeps_failed_transactions():
    cutoff = NOW - 86400
    p1 = [tx(i, NOW - i * 60, err={"InstructionError": [0, 1]} if i == 3 else None) for i in range(100)]
    p2 = [tx(100 + i, NOW - 7200 - i * 1200) for i in range(100)]     # crosses the cutoff inside the page
    sigs, txs, exhausted, reached, truncated, pages = asyncio.run(
        ws.collect_via_gtfa(Ctx(FakeRpc([p1, p2])), W, cutoff, 3000, 150_000))
    assert pages == 2 and reached and not truncated
    assert all(s["blockTime"] >= cutoff for s in sigs)
    assert [s for s in sigs if s["err"]] == [{"signature": "sig3", "slot": 997, "blockTime": NOW - 180,
                                              "err": {"InstructionError": [0, 1]}}]
    assert set(txs) == {s["signature"] for s in sigs}          # every listed signature has its transaction


def test_gtfa_listing_marks_exhausted_and_truncated():
    one = [tx(i, NOW - i) for i in range(40)]
    sigs, _, exhausted, _, truncated, pages = asyncio.run(
        ws.collect_via_gtfa(Ctx(FakeRpc([one])), W, NOW - 86400, 3000, 150_000))
    assert exhausted and not truncated and len(sigs) == 40 and pages == 1
    full = [[tx(p * 100 + i, NOW - p * 100 - i) for i in range(100)] for p in range(5)]
    sigs, _, exhausted, _, truncated, pages = asyncio.run(
        ws.collect_via_gtfa(Ctx(FakeRpc(full)), W, NOW - 86400, 300, 150_000))
    assert truncated and not exhausted and pages == 3 and len(sigs) == 300


def test_gtfa_listing_stops_before_a_call_once_the_daily_cap_is_reached():
    rpc = FakeRpc([[tx(0, NOW)]], credits=150_000)
    with pytest.raises(ws.CreditCapReached):
        asyncio.run(ws.collect_via_gtfa(Ctx(rpc), W, NOW - 86400, 3000, 150_000))
    assert rpc.calls == 0


def test_backfill_gate_admits_only_todays_batch_under_the_cap():
    s = {"helius_daily_credit_cap": 150_000}
    assert asyncio.run(ws.backfill_gate(Ctx(FakeRpc([]), FakeRedis({W})), s, W)) is None
    assert asyncio.run(ws.backfill_gate(Ctx(FakeRpc([]), FakeRedis()), s, W)) == "not_in_todays_batch"
    assert asyncio.run(ws.backfill_gate(Ctx(FakeRpc([], credits=150_000), FakeRedis({W})), s, W)) == "daily_credit_cap"


def test_token_balance_after_close_reads_the_wallets_own_account():
    M = "Mint11111111111111111111111111111111111111"
    t = {"meta": {"postTokenBalances": [
        {"owner": "Other", "mint": M, "uiTokenAmount": {"uiAmount": 500.0}},
        {"owner": W, "mint": M, "uiTokenAmount": {"uiAmount": 0.25}}]}}
    assert wallet_token_after(t, W, M) == 0.25
    assert wallet_token_after({"meta": {"postTokenBalances": []}}, W, M) == 0.0


def test_stale_backfill_message_for_a_wallet_already_backfilled_becomes_an_incremental_sync():
    import inspect
    src = inspect.getsource(ws.sync_wallet)
    # the guard must run before the backfill gate and before any RPC call
    assert src.index('elif mode == "backfill" and w["backfill_status"] in ("complete", "partial")') < \
        src.index("reason = await backfill_gate(")


def test_token_balance_sums_every_account_of_the_wallet():
    from radar.tools.verify_pnl import wallet_token_balance
    M = "Mint11111111111111111111111111111111111111"
    t = {"meta": {"preTokenBalances": [], "postTokenBalances": [
        {"owner": W, "mint": M, "uiTokenAmount": {"uiAmount": 1.5}},
        {"owner": W, "mint": M, "uiTokenAmount": {"uiAmount": 2.0}}]}}
    assert wallet_token_balance(t, W, M, "postTokenBalances") == 3.5
    assert wallet_token_balance(t, W, M, "preTokenBalances") == 0.0
