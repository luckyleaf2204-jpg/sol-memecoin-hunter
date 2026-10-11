"""Insider scan of the call channel (src/insiders): parsing, wallet filtering, aggregation, service and API.
Synthetic transactions and a fake chain only — no network."""
import asyncio
import json
import threading
import time

import pytest
from fastapi.testclient import TestClient

import web.app as webapp
from insiders import scan as S
from insiders.service import InsiderService
from web.app import create_app

WALLET = "9WzDXwBbmkg8ZTbNMqUxvQRAyrZzDsGYdLVL9zYtAWWM"      # a normal (on-curve) key
PDA = "4wTV1YmiEkRvAtNtsSGPtUrqRYQMe5SKy2uB4Jjaxnjf"         # pump.fun global PDA (off-curve)
MINT = "CAjtTHvC878f8cZ4zEwdvgjkjFM7rbYN8Mb1go1cpump"


def bal(owner, amount, mint=MINT):
    return {"owner": owner, "mint": mint, "uiTokenAmount": {"amount": str(amount)}}


def tx(ts, payer, pre, post, transfers=(), err=None, sig="s"):
    return {"blockTime": ts, "meta": {"err": err, "preTokenBalances": pre, "postTokenBalances": post,
                                      "innerInstructions": [{"instructions": [
                                          {"programId": S.SYSTEM, "parsed": {"type": "transfer", "info": {
                                              "source": a, "destination": b, "lamports": lam}}}
                                          for a, b, lam in transfers]}]},
            "transaction": {"signatures": [sig], "message": {"accountKeys": [{"pubkey": payer}], "instructions": []}}}


def test_on_curve_separates_wallets_from_program_accounts():
    assert S.on_curve(WALLET) and not S.on_curve(PDA)
    assert S.is_solana_address(MINT) and not S.is_solana_address("xBEEA1D618e533a387D941F58a7d4c9b7bD377777")


def test_load_calls_dedups_and_converts_utc7():
    calls = S.load_calls()
    mints = [c["mint"] for c in calls]
    assert len(mints) == len(set(mints)) == 97
    cr7 = next(c for c in calls if c["ticker"] == "CR7")
    assert cr7["call_ts"] == 1776989095                     # 24.04.2026 07:04:55 UTC+7 = 00:04:55 UTC


def test_token_deltas_and_sol_transfers():
    t = tx(1, WALLET, [bal(PDA, 1000)], [bal(PDA, 400), bal(WALLET, 600)],
           transfers=[("F" * 44, WALLET, 5 * 10 ** 9)])
    assert S.token_deltas(t, MINT) == {PDA: -600, WALLET: 600}
    assert S.sol_transfers(t) == [("F" * 44, WALLET, 5 * 10 ** 9)]


class FakeChain:
    def __init__(self, pages: dict, mint_ok=True):
        self.pages, self.mint_ok, self.calls, self.cached = pages, mint_ok, 0, 0

    def rpc(self, method, params, cache=True):
        self.calls += 1
        if self.mint_ok:
            return {"result": {"value": {"owner": next(iter(S.TOKEN_PROGRAMS)),
                                         "data": {"parsed": {"type": "mint"}}}}}
        return {"result": {"value": None}}

    def history(self, address, gte=None, token=None, order="asc", limit=100):
        self.calls += 1
        return self.pages.get((address, order, gte if address != MINT else 0), {"data": []})


def test_scan_token_deployer_receivers_pre_call_and_pda_excluded():
    dep, b1, b2 = WALLET, "8qNzt6DxDtZYj8jhrm8NP7sNsMz1QzudymKxS5GPCX4Q", "CD5YMZ8Kkwn7Q41a2Wo2re4t61DaD2j6MMYc4i5kW8Vb"
    page = {"data": [
        tx(100, dep, [], [bal(PDA, 900), bal(dep, 100), bal(b1, 50)]),          # create + bundle to b1
        tx(150, b2, [bal(PDA, 900)], [bal(PDA, 800)], err={"x": 1}),            # failed: ignored
        tx(200, b2, [bal(PDA, 900)], [bal(PDA, 800), bal(b2, 100)]),            # after the call
    ]}
    r = S.scan_token(FakeChain({(MINT, "asc", 0): page}), {"mint": MINT, "call_ts": 180, "ticker": "X"})
    assert r["status"] == "ok" and r["deployer"] == dep and r["created_ts"] == 100
    got = {b["wallet"]: b["pre_call"] for b in r["buyers"]}
    assert got == {b1: True, b2: False}                                         # PDA (curve) never a buyer


def test_scan_token_rejects_non_mints_and_evm_addresses():
    assert S.scan_token(FakeChain({}), {"mint": "xc6E8C393d46B685C2Fb2177F759F2b16eB7A7D54", "call_ts": 0})["status"] \
        == "not_solana"
    assert S.scan_token(FakeChain({}, mint_ok=False), {"mint": MINT, "call_ts": 0})["status"] == "not_a_mint"


BOSS = "G2YxRa6wt1qePMwfJzdXZG62ej4qaTC7YURzuh2Lwd3t"     # real on-curve wallets (used as fixtures only)
CEX = "DQ5JWbJyWdJeyBxZuuyu36sUBud6L6wo3aN1QC1bRmsR"
SINK = "21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv"


def test_trace_wallet_first_funder_and_cash_out_sinks():
    hist = {"data": [tx(10, CEX, [], [], transfers=[(BOSS, WALLET, 2 * 10 ** 9)]),
                     tx(20, CEX, [], [], transfers=[(CEX, WALLET, 10 ** 9)]),
                     tx(50, WALLET, [], [], transfers=[(WALLET, SINK, 3 * 10 ** 9), (WALLET, CEX, 1000),
                                                       (WALLET, PDA, 5 * 10 ** 9)])]}
    tr = S.trace_wallet(FakeChain({(WALLET, "asc", 0): hist}), WALLET, after_ts=40)
    assert tr["first_funder"]["address"] == BOSS
    assert tr["funders"] == {BOSS: 2 * 10 ** 9, CEX: 10 ** 9}
    assert tr["sinks"] == {SINK: 3 * 10 ** 9}           # dust < 0.01 SOL and program accounts (a buy) ignored


def test_run_links_fresh_wallets_to_one_funder_across_tokens(monkeypatch):
    """Two tokens, a different fresh wallet in each, both funded by the same address -> one entity, 2 tokens."""
    boss = BOSS
    calls = [{"mint": "M1", "call_ts": 0, "ticker": "A"}, {"mint": "M2", "call_ts": 0, "ticker": "B"}]
    monkeypatch.setattr(S, "scan_token", lambda ch, c, n=40: {**c, "status": "ok", "deployer": None,
                                                                "created_ts": 1, "buyers": [
                                                                    {"wallet": "w_" + c["mint"], "ts": 5,
                                                                     "pre_call": True, "rank": 1}]})
    monkeypatch.setattr(S, "trace_wallet", lambda ch, w, ts: {"wallet": w, "first_funder": None,
                                                              "funders": {boss: 10 ** 9}, "sinks": {}})
    monkeypatch.setattr(S, "activity", lambda ch, a: {"hub": False, "per_min": 0.1, "txs": 3})
    r = S.run(FakeChain({}), calls)
    top = r["entities"][0]
    assert top["address"] == boss and top["n_tokens"] == 2 and top["funded_wallets"] == 2 and not top["hub"]


def test_small_fee_like_receivers_are_not_the_wallet_behind(monkeypatch):
    fee = "axmWxBPqgRmcBN2cV12quqaQzsk16SazVXq8397KFKu"
    calls = [{"mint": m, "call_ts": 0, "ticker": m} for m in ("M1", "M2")]
    monkeypatch.setattr(S, "scan_token", lambda ch, c, n=40: {**c, "status": "ok", "deployer": None, "created_ts": 1,
                                                                "buyers": [{"wallet": "w" + c["mint"], "ts": 5,
                                                                            "pre_call": True, "rank": 1}]})
    monkeypatch.setattr(S, "trace_wallet", lambda ch, w, ts: {"wallet": w, "funders": {}, "sinks": {fee: 5 * 10 ** 7}})
    monkeypatch.setattr(S, "activity", lambda ch, a: {"hub": False, "per_min": 0.0, "txs": 5})
    e = S.run(FakeChain({}), calls)["entities"][0]
    assert e["kind"] == "fee" and e["hub"] is True


def test_hubs_are_flagged_not_reported_as_the_wallet_behind(monkeypatch):
    hub = CEX
    calls = [{"mint": m, "call_ts": 0, "ticker": m} for m in ("M1", "M2")]
    monkeypatch.setattr(S, "scan_token", lambda ch, c, n=40: {**c, "status": "ok", "deployer": None, "created_ts": 1,
                                                                "buyers": [{"wallet": "w" + c["mint"], "ts": 5,
                                                                            "pre_call": True, "rank": 1}]})
    monkeypatch.setattr(S, "trace_wallet", lambda ch, w, ts: {"wallet": w, "funders": {hub: 10 ** 9}, "sinks": {}})
    monkeypatch.setattr(S, "activity", lambda ch, a: {"hub": True, "per_min": 400.0, "txs": 100})
    r = S.run(FakeChain({}), calls)
    assert r["entities"][0]["hub"] is True


def test_service_runs_in_background_and_keeps_the_result(tmp_path):
    svc = InsiderService(result_path=tmp_path / "r.json", key="k")
    assert svc.start(runner=lambda log: (log("x"), {"tokens": [], "ok": 1})[1])
    for _ in range(100):
        if not svc.running:
            break
        time.sleep(0.02)
    assert svc.status()["result"] == {"tokens": [], "ok": 1} and (tmp_path / "r.json").exists()
    assert not InsiderService(result_path=tmp_path / "n.json", key="").start()   # no key: cannot run


def test_api_requires_the_access_code_and_never_returns_the_key(tmp_path, monkeypatch):
    secret = "HELIUS-SECRET-insiders-0000"
    monkeypatch.setattr(webapp, "_INSIDERS", InsiderService(result_path=tmp_path / "r.json", key=secret))
    app = create_app(engine=None, start_scanner=False, access_code="code-1")
    with TestClient(app) as c:
        assert c.get("/api/insiders").status_code == 401
        r = c.get("/api/insiders", headers={"X-Access-Code": "code-1"})
        assert r.status_code == 200 and r.json()["can_run"] is True and secret not in r.text


# ---------------------------------------------------------------- deep trace (3-4 hops)
from insiders import deep as D  # noqa: E402


def graph_flows(g_back, g_fwd, busy=()):
    """Fake flows(): g_back[a] = {funder: lamports}, g_fwd[a] = {sink: lamports}."""
    def flows(chain, a, after_ts):
        f = {k: (v, 1) for k, v in g_back.get(a, {}).items()}
        s = {k: (v, 2) for k, v in g_fwd.get(a, {}).items()}
        return {"busy": a in busy, "first_funder": next(iter(f), None), "funders": f, "sinks": s}
    return flows


def test_deep_trace_finds_the_common_funder_three_hops_back(monkeypatch):
    """seed A <- fa <- ga <- BOSS and seed B <- fb <- BOSS: fresh wallets per token, one boss 2-3 hops back."""
    sol = 10 * 10 ** 9
    back = {"A": {"fa": sol}, "fa": {"ga": sol}, "ga": {"BOSS": sol}, "B": {"fb": sol}, "fb": {"BOSS": sol}}
    monkeypatch.setattr(D, "flows", graph_flows(back, {}))
    seeds = {"A": {"tokens": {"M1"}, "ts": 0}, "B": {"tokens": {"M2"}, "ts": 0}}
    d = D.deep_trace(FakeChain({}), seeds, max_hops=3, workers=1)
    boss = d["nodes"]["BOSS"]
    assert boss["tok"]["back"] == {"M1", "M2"} and boss["back"] == 2
    top = D.summarize(d, seeds, {})["top"][0]
    assert top["address"] == "BOSS" and top["n_tokens"] == 2
    path = next(p["path"] for p in top["paths"] if p["token"] == "M1")
    assert [x["from"] for x in path] == ["ga", "fa", "A"]                   # BOSS <- ga <- fa <- seed A


def test_deep_trace_stops_at_exchanges_and_marks_deposits(monkeypatch):
    sol = 10 * 10 ** 9
    cex = "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9"                    # Binance 2 (labelled)
    back = {"A": {cex: sol}, "B": {cex: sol}, cex: {"WHALE": sol}}
    fwd = {"A": {"dep": sol}, "dep": {cex: sol}}
    monkeypatch.setattr(D, "flows", graph_flows(back, fwd))
    seeds = {"A": {"tokens": {"M1"}, "ts": 0}, "B": {"tokens": {"M2"}, "ts": 0}}
    d = D.deep_trace(FakeChain({}), seeds, max_hops=4, workers=1)
    assert "WHALE" not in d["nodes"]                                         # never traced through an exchange
    assert d["nodes"]["dep"]["pays_into"] == "Binance 2"
    s = D.summarize(d, seeds, {})
    assert all(r["address"] != cex for r in s["top"]) and any(r["address"] == cex for r in s["hubs"])


def test_deep_trace_does_not_go_through_busy_wallets(monkeypatch):
    sol = 10 * 10 ** 9
    back = {"A": {"fa": sol}, "fa": {"BOT": sol}, "BOT": {"X": sol}, "B": {"fb": sol}, "fb": {"BOT": sol}}
    monkeypatch.setattr(D, "flows", graph_flows(back, {}, busy={"BOT"}))
    seeds = {"A": {"tokens": {"M1"}, "ts": 0}, "B": {"tokens": {"M2"}, "ts": 0}}
    d = D.deep_trace(FakeChain({}), seeds, max_hops=4, workers=1)
    assert "X" not in d["nodes"] and d["nodes"]["BOT"]["busy"]


def test_seeds_skip_sniper_bots():
    r = {"tokens": [{"mint": "M1", "status": "ok", "deployer": "DEP", "created_ts": 1,
                     "buyers": [{"wallet": "BOTW", "ts": 2, "pre_call": True, "rank": 1},
                                {"wallet": "W2", "ts": 3, "pre_call": True, "rank": 2}]}],
         "repeat_wallets": [{"wallet": "BOTW", "bot_like": True}]}
    assert set(D.seeds_from(r)) == {"DEP", "W2"}


def test_deep_trace_never_mixes_directions(monkeypatch):
    """X funded seed A's funder (back) and paid Y (forward): Y must NOT inherit A's token."""
    sol = 10 * 10 ** 9
    back = {"A": {"fa": sol}, "fa": {"X": sol}}
    fwd = {"B": {"X": sol}, "X": {"Y": sol}}
    monkeypatch.setattr(D, "flows", graph_flows(back, fwd))
    seeds = {"A": {"tokens": {"M1"}, "ts": 0}, "B": {"tokens": {"M2"}, "ts": 0}}
    d = D.deep_trace(FakeChain({}), seeds, max_hops=4, workers=1)
    assert d["nodes"]["X"]["tok"]["back"] == {"M1"} and d["nodes"]["X"]["tok"]["fwd"] == {"M2"}
    assert d["nodes"]["Y"]["tok"]["fwd"] == {"M2"} and not d["nodes"]["Y"]["tok"]["back"]


# ---------------------------------------------------------------- profit per wallet (pool-side pricing)
from insiders import pnl as PN  # noqa: E402

POOL = "6zZHQtPqiBzwm4qR21BHaKp5VJEYhzjccamfd6x8cjsP"   # a PumpSwap pool (program account)
W2 = "9qhkWvC7K2wtsn3H8sbEWAGv82LD12uKxmyN7YdmxS8D"


def ptx(ts, wallet_tok, pool_tok, pool_wsol, extra=(), wallet_sol=(0, 0)):
    """pre/post token balances: (owner, mint, pre, post)."""
    rows = [(WALLET, MINT, *wallet_tok), (POOL, MINT, *pool_tok), (POOL, PN.WSOL, *pool_wsol), *extra]
    pre = [{"owner": o, "mint": m, "uiTokenAmount": {"amount": str(a)}} for o, m, a, _ in rows]
    post = [{"owner": o, "mint": m, "uiTokenAmount": {"amount": str(b)}} for o, m, _, b in rows]
    return {"blockTime": ts, "meta": {"err": None, "preTokenBalances": pre, "postTokenBalances": post,
                                      "preBalances": [wallet_sol[0]], "postBalances": [wallet_sol[1]]},
            "transaction": {"signatures": ["x"], "message": {"accountKeys": [{"pubkey": WALLET}]}}}


def test_pnl_prices_trades_on_the_pool_side_even_when_proceeds_go_to_a_bot_vault():
    sol = 10 ** 9
    txs = [ptx(1, (0, 100), (1000, 900), (50 * sol, 52 * sol), wallet_sol=(10 * sol, 8 * sol)),     # buy 2 SOL
           ptx(2, (100, 0), (900, 1000), (60 * sol, 50 * sol), wallet_sol=(8 * sol, 8 * sol))]      # sell 10 SOL,
    ch = FakeChain({(PN.ata(WALLET, MINT), "asc", 0): {"data": txs}})                               # paid elsewhere
    r = PN.pair_pnl(ch, WALLET, MINT)
    assert (r["spent_sol"], r["received_sol"], r["net_sol"], r["n_buy"], r["n_sell"]) == (2.0, 10.0, 8.0, 1, 1)


def test_pnl_splits_a_bundle_by_token_amounts_and_tracks_transfers():
    sol = 10 ** 9
    bundle = ptx(1, (0, 100), (1000, 700), (0, 3 * sol), extra=[(W2, MINT, 0, 200)])        # 3 SOL for 300 tokens
    out = ptx(2, (100, 40), (700, 700), (3 * sol, 3 * sol), extra=[(W2, MINT, 200, 260)])  # 60 tokens to W2
    ch = FakeChain({(PN.ata(WALLET, MINT), "asc", 0): {"data": [bundle, out]}})
    r = PN.pair_pnl(ch, WALLET, MINT)
    assert r["spent_sol"] == 1.0 and r["tokens_out"] == 60 and r["sent_to"] == [W2] and r["tokens_left"] == 40


def test_ata_derivation_matches_a_known_account():
    # CfkigDD8... DUMBMONEY (Token-2022) ATA seen on chain: DbT81ECHUd...
    a = PN.ata("CfkigDD8ig77boQNw9pqXMYaBu14XekugqzgeupGoJU6", "CAjtTHvC878f8cZ4zEwdvgjkjFM7rbYN8Mb1go1cpump",
               "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
    assert a.startswith("DbT81ECHUd") and not S.on_curve(a)


def test_pnl_prices_the_dev_buy_inside_the_create_transaction():
    """Create: the curve is minted the supply (its tokens go UP) while the dev buys; the dev's cost is the curve's
    SOL increase shared by the buyers' token amounts."""
    sol = 10 ** 9
    create = ptx(1, (0, 100), (0, 800), (0, 0), extra=[(W2, MINT, 0, 100)])
    create["meta"]["preBalances"], create["meta"]["postBalances"] = [0, 0], [0, 4 * sol]   # curve lamports +4 SOL
    create["transaction"]["message"]["accountKeys"] = [{"pubkey": WALLET}, {"pubkey": POOL}]
    del create["meta"]["preTokenBalances"][2], create["meta"]["postTokenBalances"][2]     # no WSOL on the curve
    ch = FakeChain({(PN.ata(WALLET, MINT), "asc", 0): {"data": [create]}})
    r = PN.pair_pnl(ch, WALLET, MINT)
    assert r["spent_sol"] == 2.0 and r["n_buy"] == 1 and r["tokens_in"] == 0


# ---------------------------------------------------------------- deployer watch (5 min)
from insiders import watch as WT  # noqa: E402

NEWMINT = "7uqfEqdd9strJVYiju6kasjCj6AzjBF2S1GmWRhdpump"


def wtx(wallet, sig, ts, mint=None, tok=(0, 0), sol=(0, 0), create=False, transfer=None):
    keys = [{"pubkey": wallet}] + ([{"pubkey": WT.PUMP}] if create else [])
    pre = [{"owner": wallet, "mint": mint, "uiTokenAmount": {"amount": str(tok[0])}}] if mint else []
    post = [{"owner": wallet, "mint": mint, "uiTokenAmount": {"amount": str(tok[1])}}] if mint else []
    ins = [{"parsed": {"type": "transfer", "info": {"source": wallet, "destination": transfer[0],
                                                     "lamports": transfer[1]}}}] if transfer else []
    return {"blockTime": ts, "meta": {"err": None, "preTokenBalances": pre, "postTokenBalances": post,
                                      "preBalances": [sol[0]] + [0] * (len(keys) - 1),
                                      "postBalances": [sol[1]] + [0] * (len(keys) - 1),
                                      "logMessages": ["Program log: Instruction: Create"] if create else [],
                                      "innerInstructions": []},
            "transaction": {"signatures": [sig], "message": {"accountKeys": keys, "instructions": ins}}}


def test_analyse_create_buy_receive_and_fund():
    sol = 10 ** 9
    assert WT.analyse(wtx(WALLET, "a", 1, NEWMINT, (0, 5), (2 * sol, sol), create=True), WALLET)[0]["kind"] == "CREATE"
    assert WT.analyse(wtx(WALLET, "b", 1, NEWMINT, (0, 5), (2 * sol, sol)), WALLET)[0]["kind"] == "BUY"
    assert WT.analyse(wtx(WALLET, "c", 1, NEWMINT, (0, 5), (sol, sol)), WALLET)[0]["kind"] == "RECEIVE"
    ev = WT.analyse(wtx(WALLET, "d", 1, transfer=(BOSS, sol)), WALLET)
    assert ev[0]["kind"] == "FUND" and ev[0]["to"] == BOSS
    assert WT.analyse(wtx(WALLET, "e", 1, transfer=(PDA, sol)), WALLET) == []        # program account: a trade
    sell = WT.analyse(wtx(WALLET, "f", 1, NEWMINT, (5, 0), (sol, 2 * sol)), WALLET)
    assert sell[0]["kind"] == "SELL" and sell[0]["sol"] == 1.0                     # recorded, never alerted
    assert WT.analyse(wtx(WALLET, "g", 1, NEWMINT, (5, 0), (sol, sol)), WALLET)[0]["kind"] == "SEND"


class FakeRpc:
    def __init__(self, sigs, txs, mint_sigs=1, fresh=True):
        self.sigs, self.txs, self.mint_sigs, self.fresh, self.calls = sigs, txs, mint_sigs, fresh, 0

    def __call__(self, method, params):
        self.calls += 1
        if method == "getTransaction":
            return self.txs[params[0]]
        a, opts = params
        if a in self.sigs:
            out = self.sigs[a]
            if opts.get("until"):
                out = out[:[s["signature"] for s in out].index(opts["until"])]
            return out
        if a == NEWMINT:
            return [{"signature": "m", "blockTime": 1_000}] * self.mint_sigs
        return [{"signature": "x"}] * (1 if self.fresh else 5)


def watcher(tmp_path, rpc, now=2_000.0, wallets=(WALLET, CEX)):
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": w} for w in wallets], "tickers": {}}
    return WT.Watcher(r, tmp_path / "w.json", rpc=rpc, now=lambda: now)


def test_first_poll_only_records_then_new_coin_buys_alert_and_two_wallets_are_strong(tmp_path):
    sol = 10 ** 9
    txs = {"b1": wtx(WALLET, "b1", 1_500, NEWMINT, (0, 5), (12 * sol, 6 * sol)),
           "b2": wtx(CEX, "b2", 1_600, NEWMINT, (0, 9), (13 * sol, 8 * sol))}
    rpc = FakeRpc({WALLET: [{"signature": "old"}], CEX: [{"signature": "old2"}]}, txs)
    w = watcher(tmp_path, rpc)
    assert w.poll_once() == []                                              # baseline
    rpc.sigs = {WALLET: [{"signature": "b1"}, {"signature": "old"}], CEX: [{"signature": "b2"}, {"signature": "old2"}]}
    al = w.poll_once()
    assert {a["kind"] for a in al} == {"BUY"} and all(a["level"] == "strong" and a["n_wallets"] == 2 for a in al)
    assert al[0]["age_h"] == round(1_000 / 3600, 2)
    assert WT.Watcher({"tokens": []}, tmp_path / "w.json", rpc=rpc).state["polls"] == 2      # state persisted


def test_old_or_channel_coins_do_not_alert(tmp_path):
    sol = 10 ** 9
    txs = {"b1": wtx(WALLET, "b1", 1_500, NEWMINT, (0, 5), (2 * sol, sol)),
           "c1": wtx(WALLET, "c1", 1_500, MINT, (0, 5), (2 * sol, sol))}
    rpc = FakeRpc({WALLET: [{"signature": "old"}]}, txs, mint_sigs=1000)          # >= 1000 sigs: an old coin
    w = watcher(tmp_path, rpc, wallets=(WALLET,))
    w.poll_once()
    rpc.sigs = {WALLET: [{"signature": "c1"}, {"signature": "b1"}, {"signature": "old"}]}
    assert w.poll_once() == []


def test_funding_a_fresh_wallet_adds_it_to_the_watch_for_72h(tmp_path):
    txs = {"f1": wtx(WALLET, "f1", 1_500, transfer=(BOSS, 5 * 10 ** 9))}
    rpc = FakeRpc({WALLET: [{"signature": "old"}]}, txs)
    w = watcher(tmp_path, rpc, wallets=(WALLET,))
    w.poll_once()
    rpc.sigs = {WALLET: [{"signature": "f1"}, {"signature": "old"}]}
    al = w.poll_once()
    assert al[0]["kind"] == "FUND" and BOSS in w.wallets()
    w.now = lambda: 2_000.0 + WT.CHILD_TTL_S + 1
    assert BOSS not in w.wallets()


def test_tier_b_rotates_and_snipers_are_muted(tmp_path):
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": WALLET,
                     "buyers": [{"wallet": w, "rank": i, "pre_call": True} for i, w in enumerate([CEX, BOSS, SINK], 1)]}],
         "tickers": {}}
    asked = []

    def rpc(method, params):
        asked.append(params[0])
        return []
    w = WT.Watcher(r, tmp_path / "w.json", rpc=rpc, now=lambda: 2_000.0)
    assert set(w.base) == {WALLET} and set(w.tier_b) == {CEX, BOSS, SINK}
    w.poll_once()
    assert asked.count(WALLET) == 1 and len(set(asked) & {CEX, BOSS, SINK}) == 1      # one slice of tier B
    for _ in range(WT.B_SLICES - 1):
        w.poll_once()
    assert asked.count(WALLET) == WT.B_SLICES and set(asked) >= {CEX, BOSS, SINK}   # all of B within B_SLICES polls
    for i in range(WT.BOT_NEW_COINS):
        muted = w._is_bot(CEX, f"m{i}")
    assert muted and CEX in w.state["muted"]


def test_family_tree_one_direction_ancestors_and_pruning(monkeypatch):
    """BOSS -> fa -> A (coin M1), BOSS -> fb -> B (coin M2), GRAND -> BOSS; a dead branch is pruned."""
    sol = 10 * 10 ** 9
    back = {"A": {"fa": sol}, "fa": {"BOSS": sol}, "B": {"fb": sol}, "fb": {"BOSS": sol}, "BOSS": {"GRAND": sol}}
    monkeypatch.setattr(D, "flows", graph_flows(back, {}))
    seeds = {"A": {"tokens": {"M1"}, "ts": 0}, "B": {"tokens": {"M2"}, "ts": 0}}
    d = D.deep_trace(FakeChain({}), seeds, max_hops=4, workers=1)
    roles = {"A": [["M1", "dev", 5.0]], "B": [["M2", "#3", None]]}
    trees = D.build_trees(d["nodes"], seeds, ["BOSS", "GRAND"], roles)
    kept = D.dedupe_trees(trees)
    assert len(kept) == 1 and kept[0]["a"] in ("BOSS", "GRAND") and kept[0]["n_coins"] == 2
    t = kept[0] if kept[0]["a"] == "BOSS" else kept[0]["c"][0]
    assert {c["a"] for c in t["c"]} == {"fa", "fb"} and t["c"][0]["c"][0]["coins"]


def test_alerts_are_pushed_by_level_and_capped(tmp_path):
    sent = []
    w = WT.Watcher({"tokens": [], "tickers": {NEWMINT: "NEW"}}, tmp_path / "w.json", rpc=lambda m, p: [],
                   notify=lambda txt: sent.append(txt) or True, notify_levels=("strong", "high"))
    al = [{"level": "medium", "kind": "BUY", "mint": NEWMINT, "wallet": WALLET, "sig": "s"},
          {"level": "strong", "kind": "BUY", "mint": NEWMINT, "wallet": WALLET, "sig": "s", "n_wallets": 2, "age_h": 0.3}]
    w._send(al)
    assert len(sent) == 1 and "MẠNH" in sent[0] and "×2" in sent[0] and NEWMINT in sent[0] and "dexscreener" in sent[0]
    sent.clear()
    w._send([{"level": "high", "kind": "CREATE", "mint": NEWMINT, "wallet": WALLET, "sig": str(i)} for i in range(20)])
    assert len(sent) == 16 and "5 cảnh báo khác" in sent[-1] and w.status()["notified"] == 1 + 15
    assert w.status()["telegram"] is True


def test_no_notifier_without_telegram_env(tmp_path, monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.delenv("TELEGRAM_CHAT_ID", raising=False)
    monkeypatch.setenv("INSIDER_VIP", "0")                                 # no live websocket in a unit test
    svc = InsiderService(result_path=tmp_path / "r.json", key="")
    svc.result = {"tokens": [], "tickers": {}}
    monkeypatch.setattr(WT.Watcher, "run_forever", lambda self, stop, progress=print, every_s=0: None)
    assert svc.start_watch(tmp_path / "w.json") and svc.watcher.notify is None
    assert svc.status()["watch"]["telegram"] is False


def test_new_wallet_funded_then_buying_right_away_makes_a_strong_alert(tmp_path):
    """Group wallet funds a fresh wallet (seen in poll 2); the fresh wallet had ALREADY bought the new coin before
    poll 3 looked at it: the buy is still read (from the funding signature) and with the group's own buy -> STRONG."""
    sol = 10 ** 9
    txs = {"f1": wtx(WALLET, "f1", 1_500, transfer=(BOSS, 6 * sol)),
           "kb": wtx(BOSS, "kb", 1_550, NEWMINT, (0, 7), (6 * sol, sol)),
           "wb": wtx(WALLET, "wb", 1_560, NEWMINT, (0, 5), (9 * sol, sol))}
    rpc = FakeRpc({WALLET: [{"signature": "old"}]}, txs)
    w = watcher(tmp_path, rpc, wallets=(WALLET,))
    w.poll_once()                                                            # baseline
    rpc.sigs = {WALLET: [{"signature": "f1"}, {"signature": "old"}]}
    assert w.poll_once()[0]["kind"] == "FUND"
    rpc.sigs = {WALLET: [{"signature": "wb"}, {"signature": "f1"}, {"signature": "old"}],
                BOSS: [{"signature": "kb"}, {"signature": "f1"}]}             # BOSS bought before we looked at it
    al = w.poll_once()
    strong = [a for a in al if a["level"] == "strong"]
    assert {a["wallet"] for a in strong} == {WALLET, BOSS} and strong[0]["n_wallets"] == 2


# ---------------------------------------------------------------- paper $500 on STRONG alerts
from insiders import paper as PP  # noqa: E402


def test_paper_opens_once_per_strong_coin_and_values_at_the_marks():
    clock = {"t": 10_000.0}
    price = {"v": 1.0}                                                 # USDC per 1e6 raw tokens

    def quote(inp, out, amount):
        if inp == PP.USDC:
            return int(amount / price["v"])                           # tokens for $amount
        return int(amount * price["v"] * 0.95)                       # selling loses 5 % (impact)
    st = {}
    book = PP.PaperBook(st, usd=500, quote=quote, now=lambda: clock["t"])
    assert book.open({"level": "medium", "mint": NEWMINT}) is None   # only STRONG
    p = book.open({"level": "strong", "mint": NEWMINT, "n_wallets": 2})
    assert p["status"] == "open" and p["entry_value"] == round(500 * 0.95 - PP.FEE_USD, 2)
    assert book.open({"level": "strong", "mint": NEWMINT}) is None   # once per coin
    for dt, px in ((900, 2.0), (3600, 3.0), (6 * 3600, 0.5), (24 * 3600, 0.1)):
        clock["t"] = 10_000.0 + dt
        price["v"] = px
        book.update()
    m = p["marks"]
    assert m["15m"] == round(500 * 2 * 0.95 - 1, 2) and m["24h"] == round(500 * 0.1 * 0.95 - 1, 2)
    assert p["status"] == "closed"
    s = book.summary()
    assert s["positions"] == 1 and s["marks"]["1h"]["wins"] == 1 and s["marks"]["24h"]["avg_pct"] < 0


def test_paper_no_route_is_recorded_not_traded():
    book = PP.PaperBook({}, usd=500, quote=lambda i, o, a: None)
    assert book.open({"level": "strong", "mint": NEWMINT})["status"] == "no_route"
    assert book.summary()["positions"] == 0 and book.summary()["no_route"] == 1


def test_hold_confirmation_opens_the_paper_trade_and_flippers_do_not_count(tmp_path):
    """Two group wallets buy >= 4 SOL; 10 minutes later both still hold -> one MẠNH · ĐANG GIỮ alert + paper.
    A third wallet that sold half within 5 minutes is a flip: no confirmation from it, and a second flip mutes it."""
    sol = 10 ** 9
    clock = {"t": 2_000.0}
    txs = {"b1": wtx(WALLET, "b1", 1_990, NEWMINT, (0, 5), (12 * sol, 6 * sol)),
           "b2": wtx(CEX, "b2", 1_995, NEWMINT, (0, 9), (13 * sol, 8 * sol)),
           "b3": wtx(BOSS, "b3", 1_990, NEWMINT, (0, 10), (12 * sol, 6 * sol)),
           "x3": wtx(BOSS, "x3", 2_050, NEWMINT, (10, 2), (6 * sol, 14 * sol))}           # flips in 60 s
    rpc = FakeRpc({WALLET: [{"signature": "o1"}], CEX: [{"signature": "o2"}], BOSS: [{"signature": "o3"}]}, txs)
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": w} for w in (WALLET, CEX, BOSS)], "tickers": {}}
    w = WT.Watcher(r, tmp_path / "w.json", rpc=rpc, now=lambda: clock["t"], paper_quote=lambda i, o, a: a)
    w.poll_once()
    rpc.sigs = {WALLET: [{"signature": "b1"}, {"signature": "o1"}], CEX: [{"signature": "b2"}, {"signature": "o2"}],
                BOSS: [{"signature": "x3"}, {"signature": "b3"}, {"signature": "o3"}]}
    al = w.poll_once()
    assert not any(a["level"] == "hold" for a in al)                                # not 10 minutes yet
    assert w.status()["paper"]["summary"]["positions"] == 0
    ledger = w.state["coins"][NEWMINT]["wallets"]
    assert ledger[BOSS]["flip"] is True and ledger[BOSS]["half_s"] == 60
    clock["t"] = 2_000.0 + WT.HOLD_CONFIRM_S + 5
    rpc.sigs = {WALLET: [{"signature": "b1"}], CEX: [{"signature": "b2"}], BOSS: [{"signature": "x3"}]}
    al = w.poll_once()
    hold = [a for a in al if a["level"] == "hold"]
    assert len(hold) == 1 and hold[0]["n_wallets"] == 2 and set(hold[0]["holders"]) == {WALLET, CEX}
    assert w.status()["paper"]["summary"]["positions"] == 1
    assert not [a for a in w.poll_once() if a["level"] == "hold"]                   # once per coin
    w._ledger({"ts": 3_000, "kind": "BUY", "mint": "OTHERpump", "wallet": BOSS, "sol": 5.0, "tokens": 4}, "x")
    w._ledger({"ts": 3_030, "kind": "SELL", "mint": "OTHERpump", "wallet": BOSS, "sol": 6.0, "tokens": 4}, "x")
    assert BOSS in w.state["muted"]                                                 # second flip: muted


def test_wallet_styles_from_history():
    r = {"pnl": {"rows": [
        {"wallet": "F", "trades": [[0, "buy", 1, 10], [30, "sell", 2, 10]]},
        {"wallet": "H", "trades": [[0, "buy", 1, 10], [900, "sell", 2, 6]]},
        {"wallet": "H", "trades": [[0, "buy", 1, 10], [20, "sell", 2, 6]]}]}}
    st = WT.wallet_styles(r)
    assert st["F"]["style"] == "flip" and st["H"]["style"] == "hold" and st["H"]["positions"] == 2


def test_alerts_carry_name_links_age_and_busy_new_coins_are_not_missed(tmp_path):
    """RPC cannot date a busy coin (>= 1000 signatures): the creation time from DexScreener / Jupiter is used."""
    sol = 10 ** 9
    txs = {"b1": wtx(WALLET, "b1", 1_500, NEWMINT, (0, 5), (12 * sol, 6 * sol))}
    rpc = FakeRpc({WALLET: [{"signature": "old"}]}, txs, mint_sigs=1000)
    meta = lambda m: {"name": "Frog Coin", "symbol": "FROG", "mcap": 12345.0, "created_ts": 2_000 - 1_800,   # noqa: E731
                      "chart": "https://dexscreener.com/solana/pair1"}
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": WALLET}], "tickers": {}}
    w = WT.Watcher(r, tmp_path / "w.json", rpc=rpc, now=lambda: 2_000.0, meta=meta)
    w.poll_once()
    rpc.sigs = {WALLET: [{"signature": "b1"}, {"signature": "old"}]}
    a = w.poll_once()[0]
    assert a["symbol"] == "FROG" and a["age_h"] == 0.5 and a["mcap"] == 12345.0
    txt = WT.alert_text({**a, "level": "strong"})
    assert "$FROG" in txt and "Frog Coin" in txt and "pump.fun/coin/" + NEWMINT in txt and "pair1" in txt
    assert "0.5 giờ tuổi" in txt and "MC $12,345" in txt


def test_coin_meta_falls_back_to_jupiter():
    def get(url):
        if "dexscreener" in url:
            return []
        return [{"id": NEWMINT, "name": "Frog", "symbol": "FRG", "mcap": 9.0, "createdAt": "2026-10-10T22:33:50Z"}]
    m = WT.coin_meta(NEWMINT, get=get)
    assert m["symbol"] == "FRG" and m["src"] == "jupiter" and m["created_ts"] > 0
    assert WT.coin_meta(NEWMINT, get=lambda u: (_ for _ in ()).throw(OSError("down"))) == {}



def test_only_buys_of_at_least_min_sol_alert_but_every_trade_is_in_the_coin_tab(tmp_path):
    """A 1 SOL buy stays out of the alert list; the 6 SOL buy alerts and opens the coin tab, whose ledger then has
    both wallets' buys and the later sell."""
    sol = 10 ** 9
    txs = {"s1": wtx(CEX, "s1", 1_500, NEWMINT, (0, 3), (2 * sol, sol)),              # 1 SOL: no alert
           "b1": wtx(WALLET, "b1", 1_600, NEWMINT, (0, 5), (12 * sol, 6 * sol)),       # 6 SOL: alert
           "x1": wtx(WALLET, "x1", 1_700, NEWMINT, (5, 1), (6 * sol, 15 * sol)),       # sells for 9 SOL
           "f1": wtx(WALLET, "f1", 1_800, transfer=(BOSS, 2 * sol))}                   # 2 SOL fund: watched, hidden
    rpc = FakeRpc({WALLET: [{"signature": "old"}], CEX: [{"signature": "old2"}]}, txs)
    w = watcher(tmp_path, rpc)
    w.poll_once()
    rpc.sigs = {WALLET: [{"signature": s} for s in ("f1", "x1", "b1", "old")],
                CEX: [{"signature": "s1"}, {"signature": "old2"}]}
    al = w.poll_once()
    assert [(a["kind"], a["wallet"]) for a in al] == [("BUY", WALLET)]
    assert BOSS in w.wallets()                                                    # the 2 SOL wallet is still watched
    c = w.status()["coins"][0]
    assert c["mint"] == NEWMINT and c["n_wallets"] == 2 and c["buy_sol"] == 7.0 and c["sell_sol"] == 9.0
    me = next(x for x in c["wallets"] if x["wallet"] == WALLET)
    assert (me["n_buy"], me["n_sell"], me["tok_in"], me["tok_out"]) == (1, 1, 5, 4)


def test_received_tokens_are_valued_before_they_can_alert(tmp_path):
    sol = 10 ** 9
    txs = {"r1": wtx(WALLET, "r1", 1_500, NEWMINT, (0, 1000), (sol, sol))}           # tokens in, no SOL out
    rpc = FakeRpc({WALLET: [{"signature": "old"}]}, txs)
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": WALLET}], "tickers": {}}
    for value, alerted in ((3 * sol, False), (5 * sol, True)):
        w = WT.Watcher(r, tmp_path / f"w{value}.json", rpc=rpc, now=lambda: 2_000.0,
                       paper_quote=lambda i, o, a, v=value: v if o == WT.WSOL else a)
        rpc.sigs = {WALLET: [{"signature": "old"}]}
        w.poll_once()
        rpc.sigs = {WALLET: [{"signature": "r1"}, {"signature": "old"}]}
        al = w.poll_once()
        assert bool(al) is alerted and (not al or (al[0]["kind"] == "RECEIVE" and al[0]["sol"] == value / sol))



# ---------------------------------------------------------------- real-time special wallets (45 holders + successors)
from insiders import vip as VP  # noqa: E402


def vip_watcher(tmp_path, quote=None):
    r = {"tokens": [{"mint": MINT, "status": "ok", "deployer": CEX}], "tickers": {},
         "vip": {"holders": [{"wallet": WALLET, "profit_sol": 120.0, "coins": ["CR7"]}],
                 "successors": [{"wallet": SINK, "kind": "deposit", "sol": 500, "from": [WALLET]},
                                {"wallet": BOSS, "kind": "person", "sol": 300, "token_tx": 0, "from": [WALLET]}]}}
    w = WT.Watcher(r, tmp_path / "w.json", rpc=lambda m, p: [], now=lambda: 2_000.0, paper_quote=quote)
    return w, r


def test_vip_list_is_holders_plus_personal_successors(tmp_path):
    w, r = vip_watcher(tmp_path)
    lst = VP.vip_list(r)
    assert set(lst) == {WALLET, BOSS} and "held >= 10 min" in lst[WALLET]       # a deposit address is not followed
    assert WALLET in w.base and w.base[WALLET].startswith("special:")             # also polled as a fallback


def test_vip_pushes_every_trade_or_transfer_of_at_least_min_sol_and_adopts_new_wallets(tmp_path):
    sol = 10 ** 9
    sent = []
    w, r = vip_watcher(tmp_path)
    w.notify, w.notify_levels = (lambda txt: sent.append(txt) or True), {"vip"}
    fresh = "8xvHWupToGmcv5NoWek7J11LFGGJ7Nkx9Myp6n9LcSLH"
    txs = {"big": wtx(WALLET, "big", 1_900, NEWMINT, (0, 5), (20 * sol, 14 * sol)),           # buys for 6 SOL
           "small": wtx(WALLET, "small", 1_910, NEWMINT, (0, 5), (3 * sol, 2 * sol)),         # 1 SOL: ignored
           "fund": wtx(WALLET, "fund", 1_920, transfer=(fresh, 9 * sol))}                       # 9 SOL to a person
    vs = VP.VipStream(w, r, rpc=lambda m, p: txs[p[0]], now=lambda: 2_000.0)
    vs.retry_sleep = False
    assert vs.handle(WALLET, "big")[0]["kind"] == "BUY"
    assert vs.handle(WALLET, "small") == [] and vs.handle(WALLET, "big") == []                # dedup
    al = vs.handle(WALLET, "fund")
    assert al[0]["kind"] == "FUND" and al[0]["to"] == fresh and fresh in vs.wallets()        # followed from now on
    assert len(sent) == 2 and "VÍ ĐẶC BIỆT" in sent[0]
    assert {a["level"] for a in w.state["alerts"]} == {"vip"}


def test_vip_counts_sol_received():
    sol = 10 ** 9
    t = wtx(WALLET, "in", 1, transfer=(WALLET, 0))
    t["transaction"]["message"]["instructions"] = [{"parsed": {"type": "transfer", "info": {
        "source": CEX, "destination": WALLET, "lamports": 7 * sol}}}]
    assert VP.sol_in(t, WALLET) == (7.0, CEX)


class StreamOnce:
    """Confirms every subscription, pushes one notification for the first wallet, then the test stops it."""
    def __init__(self, stop, sig):
        self.stop, self.sig, self.out = stop, sig, []
        self.ids = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def send(self, m):
        self.ids.append(json.loads(m)["id"])

    async def recv(self):
        if self.ids:
            k = self.ids.pop(0)
            return json.dumps({"jsonrpc": "2.0", "id": k, "result": 100 + k})
        if self.sig:
            s, self.sig = self.sig, None
            return json.dumps({"params": {"subscription": 101, "result": {"value": {"signature": s, "err": None}}}})
        self.stop.set()
        raise ConnectionError("closed")


def test_vip_websocket_subscribes_and_handles_a_push(tmp_path):
    sol = 10 ** 9
    w, r = vip_watcher(tmp_path)
    txs = {"big": wtx(WALLET, "big", 1_900, NEWMINT, (0, 5), (20 * sol, 14 * sol))}
    stop = threading.Event()
    vs = VP.VipStream(w, r, rpc=lambda m, p: txs[p[0]], connect=lambda: StreamOnce(stop, "big"), now=lambda: 2_000.0)
    asyncio.run(vs.run(stop))
    assert vs.stat["subscribed"] == 2 and w.state["vip"]["events"] == 1
    assert w.state["alerts"][0]["wallet"] == WALLET and w.state["alerts"][0]["level"] == "vip"


# ---------------------------------------------------------------- balances of the special wallets
from insiders import portfolio as PO  # noqa: E402


def test_portfolio_keeps_only_holdings_worth_more_than_one_sol_and_groups_by_coin():
    good, junk, mid = "GoodMint1111", "JunkMint2222", "MidMint33333"

    def rpc(m, p):
        if m == "getBalance":
            return {"value": 3 * 10 ** 9}
        if p[1]["programId"] != PO.TOKEN_PROGRAMS[0]:
            return {"value": []}
        acc = lambda mint, amt: {"account": {"data": {"parsed": {"info": {"mint": mint, "tokenAmount": {"amount": "1", "uiAmountString": str(amt)}}}}}}  # noqa: E731
        return {"value": [acc(good, 1000), acc(junk, 5_000_000), acc(mid, 10)]}

    def get(url):
        if "price" in url:
            return {PO.SOL: {"usdPrice": 100.0}, good: {"usdPrice": 0.5}, mid: {"usdPrice": 5.0}}   # junk: no price
        return [{"baseToken": {"address": good, "symbol": "GOOD", "name": "Good"}, "url": "https://dexscreener.com/x"}]
    pf = PO.Portfolio(lambda: {WALLET: "held", CEX: "received"}, rpc, get=get, now=lambda: 1.0)
    v = pf.refresh()
    w = v["wallets"][0]
    assert w["sol"] == 3.0 and [t["symbol"] for t in w["tokens"]] == ["GOOD"]   # 500 $ = 5 SOL kept
    assert w["tokens"][0]["sol"] == 5.0 and w["dust"] == 2                         # junk (no price) + 50 $ (0.5 SOL)
    c = v["coins"][0]
    assert c["mint"] == good and c["n_holders"] == 2 and v["total_sol"] == 6.0
    assert junk in pf.prices.dead                                                  # not asked again for 6 h


def test_two_special_wallets_on_one_coin_fire_once_and_open_the_paper_trade(tmp_path):
    sol = 10 ** 9
    w, r = vip_watcher(tmp_path, quote=lambda i, o, a: a)
    txs = {"a": wtx(WALLET, "a", 1_900, NEWMINT, (0, 5), (20 * sol, 14 * sol)),
           "b": wtx(BOSS, "b", 1_950, NEWMINT, (0, 7), (20 * sol, 12 * sol)),
           "c": wtx(BOSS, "c", 1_960, NEWMINT, (7, 9), (12 * sol, 5 * sol))}
    vs = VP.VipStream(w, r, rpc=lambda m, p: txs[p[0]], now=lambda: 2_000.0)
    vs.retry_sleep = False
    assert not [a for a in vs.handle(WALLET, "a") if a["level"] == "vipstrong"]
    strong = [a for a in vs.handle(BOSS, "b") if a["level"] == "vipstrong"]
    assert len(strong) == 1 and strong[0]["n_wallets"] == 2 and strong[0]["paper"] == "open"
    assert not [a for a in vs.handle(BOSS, "c") if a["level"] == "vipstrong"]    # once per coin
    assert w.status()["coins"][0]["mint"] == NEWMINT                               # the coin got its tab


# ---------------------------------------------------------------- early-signal board
from insiders import chart as CH  # noqa: E402


def test_new_wallet_funded_with_half_a_sol_is_followed_without_an_alert(tmp_path):
    sol = 10 ** 9
    w, r = vip_watcher(tmp_path)
    fresh = "8xvHWupToGmcv5NoWek7J11LFGGJ7Nkx9Myp6n9LcSLH"
    txs = {"f": wtx(WALLET, "f", 1_900, transfer=(fresh, sol))}                    # 1 SOL: below the alert level
    vs = VP.VipStream(w, r, rpc=lambda m, p: txs.get(p[0]), now=lambda: 2_000.0)
    vs.retry_sleep = False
    assert vs.handle(WALLET, "f") == [] and fresh in vs.wallets()


def test_board_lists_every_trade_with_supply_and_what_is_still_held(tmp_path):
    sol = 10 ** 9
    w, r = vip_watcher(tmp_path)
    txs = {"a": wtx(WALLET, "a", 1_900, NEWMINT, (0, 5_000_000), (20 * sol, 14 * sol)),
           "b": wtx(WALLET, "b", 1_960, NEWMINT, (5_000_000, 2_000_000), (14 * sol, 18 * sol))}

    def rpc(m, p):
        if m == "getTokenSupply":
            return {"value": {"uiAmountString": "1000000000", "decimals": 6}}
        return txs[p[0]]
    vs = VP.VipStream(w, r, rpc=rpc, now=lambda: 2_000.0)
    vs.retry_sleep = False
    w.vip = vs
    vs.handle(WALLET, "a")
    vs.handle(WALLET, "b")

    class PF:
        raw = {WALLET: (1.0, [(NEWMINT, 2.0)])}
        updated = 2_000.0
    w.portfolio = PF()
    b = w.board_view()[0]
    assert b["mint"] == NEWMINT and b["supply"] == {"ui": 1e9, "decimals": 6}
    me = b["wallets"][0]
    assert [x[1] for x in me["trades"]] == ["BUY", "SELL"] and me["trades"][0][2] == 6.0 and me["held_ui"] == 2.0


def test_charts_merge_curve_and_amm_candles_and_cache():
    calls = []

    def get(url):
        calls.append(url)
        if url.endswith("/pools?page=1"):
            return {"data": [
                {"attributes": {"address": "CURVE", "reserve_in_usd": "10"}, "relationships": {
                    "dex": {"data": {"id": "pump-fun"}}, "base_token": {"data": {"id": "solana_M"}}}},
                {"attributes": {"address": "AMM", "reserve_in_usd": "900"}, "relationships": {
                    "dex": {"data": {"id": "pumpswap"}}, "base_token": {"data": {"id": "solana_M"}}}}]}
        if "/CURVE/" in url:
            return {"data": {"attributes": {"ohlcv_list": [[60, 1, 2, 1, 2, 10], [120, 2, 3, 2, 3, 10]]}}}
        return {"data": {"attributes": {"ohlcv_list": [[120, 2, 4, 2, 4, 99], [180, 4, 5, 4, 5, 50]]}}}
    ch = CH.Charts(get=get, now=lambda: 1_000.0)
    d = ch.candles("M", created_ts=0)
    assert [c[0] for c in d["candles"]] == [60, 120, 180] and d["candles"][1][5] == 99   # overlap: larger volume
    n = len(calls)
    ch.candles("M", created_ts=0)
    assert len(calls) == n                                                          # cached
