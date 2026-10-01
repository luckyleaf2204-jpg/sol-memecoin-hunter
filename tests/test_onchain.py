"""On-chain shadow research collector: parsing, pool detection, budget / rate limits, UNKNOWN on failure."""
import asyncio
import time

from research.onchain import OnchainResearch, features_at, parse_tx, pool_owners, shadow_v2
from test_experimental import hot

MINT = "Mint" + "1" * 40


def tx(signer, owner_deltas, sol_delta=0.0, slot=100, t=1000, sol_out=()):
    keys = [{"pubkey": signer}]
    pre = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": 1000.0}} for o in owner_deltas]
    post = [{"mint": MINT, "owner": o, "uiTokenAmount": {"uiAmount": 1000.0 + d}} for o, d in owner_deltas.items()]
    ins = [{"program": "system", "parsed": {"type": "transfer", "info": {"source": signer, "destination": d,
                                                                         "lamports": int(a * 1e9)}}} for d, a in sol_out]
    return {"slot": slot, "blockTime": t, "transaction": {"message": {"accountKeys": keys, "instructions": ins}},
            "meta": {"err": None, "preTokenBalances": pre, "postTokenBalances": post,
                     "preBalances": [10_000_000_000], "postBalances": [int(10_000_000_000 + sol_delta * 1e9)]}}


def test_parse_buy_sell_transfer_and_sol_out():
    assert parse_tx(tx("W", {"W": 50, "Pool": -50}, -0.5), MINT)["side"] == "buy"
    assert parse_tx(tx("W", {"W": -50, "Pool": 50}, +0.4), MINT)["side"] == "sell"
    t = parse_tx(tx("W", {"W": -50, "X": 50}, -0.000005), MINT)
    assert t["side"] == "transfer"
    p = parse_tx(tx("Dev", {}, -1.0, sol_out=[("Buyer", 1.0)]), MINT, wallet="Dev")
    assert p["sol_out"] == [("Buyer", 1.0)] and p["side"] is None
    assert parse_tx({"meta": {"err": "x"}}, MINT) is None


def test_pool_owner_is_measured_from_the_transactions():
    txs = [parse_tx(tx(f"W{i}", {f"W{i}": 10, "Curve": -10}), MINT) for i in range(5)]
    assert pool_owners(txs) == {"Curve"}
    assert pool_owners(txs[:2]) == set()                         # too few txs: nothing assumed


class Rpc:
    has_das = True

    def __init__(self, ok=True, sigs=True):
        self.calls, self.ok, self.sigs = [], ok, sigs

        class C:
            daily_budget = 300_000

            def state(_):
                return {"daily_budget": 300_000, "used": 0 if ok else 10**9, "quota_exhausted": False}

            def allow(_, c):
                return ok
        self.credits = C()

    async def signatures(self, address, limit=1000, before=None):
        self.calls.append(("sigs", address))
        if not self.sigs:
            return None
        return [{"signature": f"s{i}", "slot": 100 + i, "blockTime": 1000 + i, "err": None} for i in range(3)]

    async def transaction(self, sig):
        self.calls.append(("tx", sig))
        i = int(sig[1:])
        return tx(f"B{i}", {f"B{i}": 10, "Curve": -10}, -0.1, slot=100 + i, t=1000 + i)


def test_rate_limit_budget_and_unknown_on_failure():
    sts = []
    for i in range(6):
        st = hot()
        st.info.mint = f"Oc{i}" + "1" * 41
        sts.append(st)
    states = {s.mint: s for s in sts}
    dec = {m: {"decision": "TRADE", "opportunity": 70} for m in states}
    oc = OnchainResearch(Rpc(), per_min=3, sample_per_min=0)
    assert asyncio.run(oc.round(states, dec, set(), now=time.time())) == 3       # per-minute cap
    assert oc.n["skipped_rate"] == 1
    poor = OnchainResearch(Rpc(ok=False), per_min=3, sample_per_min=0)
    assert asyncio.run(poor.round(states, dec, set(), now=time.time())) == 0 and poor.n["skipped_budget"] == 1
    fail = OnchainResearch(Rpc(sigs=False), per_min=3, sample_per_min=0)
    asyncio.run(fail.round(states, dec, set(), now=time.time()))
    rec = next(iter(fail.done.values()))
    assert rec["status"].startswith("UNKNOWN") and features_at(rec, time.time())["create_slot_txs"] is None
    assert shadow_v2(features_at(rec, time.time()))[0] is None


def test_collected_record_features_and_lp_unknown():
    oc = OnchainResearch(Rpc(), per_min=5, sample_per_min=0)
    st = hot()
    st.info.mint, st.info.total_supply = MINT, 1_000.0
    rec = asyncio.run(oc.collect(st, None, time.time()))
    f = features_at(rec, 2000)
    assert rec["status"] == "ok" and f["create_slot_txs"] == 1 and f["early_buyers"] == 3
    assert f["lp_events"] is None and f["buyer_shared_funding"] is None          # not collectable: UNKNOWN
