"""Insider scan of the call channel (src/insiders): parsing, wallet filtering, aggregation, service and API.
Synthetic transactions and a fake chain only — no network."""
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
    assert WT.analyse(wtx(WALLET, "f", 1, NEWMINT, (5, 0), (sol, 2 * sol)), WALLET) == []   # a sell: no alert


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
    txs = {"b1": wtx(WALLET, "b1", 1_500, NEWMINT, (0, 5), (2 * sol, sol)),
           "b2": wtx(CEX, "b2", 1_600, NEWMINT, (0, 9), (3 * sol, sol))}
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
    txs = {"f1": wtx(WALLET, "f1", 1_500, transfer=(BOSS, 10 ** 9))}
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
