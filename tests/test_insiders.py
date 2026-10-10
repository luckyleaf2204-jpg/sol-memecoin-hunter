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
