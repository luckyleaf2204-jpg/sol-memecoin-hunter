"""Canonical token identity — regression for TSLAx shown as "$APEWIF" (2026-09-30).

CA XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB is Tesla xStock (TSLAx, Token-2022). A Pump.fun curve for
APEWIF (real mint FUgEk…aDtT) is QUOTED in TSLAx; the discovery feed attached "APEWIF" to the TSLAx CA.
Payloads below mirror what the real APIs returned for these addresses.
"""
import asyncio
import time

import httpx
import pytest
from fastapi.testclient import TestClient

from conftest import SOL_USD, build_state, dex_pair
from core.config import ApiKeys, Settings
from core.http import HttpClient
from core.models import TokenIdentity, TokenInfo, TokenState
from database.db import Database
from dex.dexscreener import DexScreenerClient, parse_pair
from pumpfun.client import parse_coin
from pumpfun.stream import parse_event
from scanner.engine import ScannerEngine
from scoring.groups import classify_group
from validation.identity import apply_identity, norm_symbol, record_claim, resolve_identity
from web.app import create_app

TSLAX = "XsDoVfqeBukxuZHWhdvWHBhgEHjGNst4MLodqsJHzoB"
APEWIF = "FUgEkQgzWS71rRmqXDkq1N8hft1ir1nLBxvm3QeiaDtT"
USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
WSOL = "So11111111111111111111111111111111111111112"
T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
CODE = "id-code"

# PumpPortal create event with the WRONG address for the name (quote mint instead of the new mint)
BAD_EVENT = {"signature": "sig", "mint": TSLAX, "traderPublicKey": "SZqGj7R2NFC8DqfaU2tWX1noNbYt3wBDn9xwasRNiLE",
             "txType": "create", "initialBuy": 0, "solAmount": 0, "bondingCurveKey": "DkYJn71KcaqoEj34BH8HsbLCSrwcmnYyodow2QyZm5CQ",
             "vTokensInBondingCurve": 1073000000, "vSolInBondingCurve": 12.16576196, "marketCapSol": 11.338081975768873,
             "name": "apewifdress", "symbol": "APEWIF", "pool": "pump"}


def tslax_pair():
    p = dex_pair(mint=TSLAX, mc=353_569, fdv=353_569, price="435.12", liq=180_000, dex="raydium",
                 pair="TSLAxUSDCpair111111111111111111111111111111")
    p["baseToken"] = {"address": TSLAX, "symbol": "TSLAx", "name": "Tesla xStock"}
    p["quoteToken"] = {"address": USDC, "symbol": "USDC"}
    return p


def apewif_curve_pair():
    """The APEWIF curve pair: base = APEWIF, quote = TSLAx (what DexScreener lists)."""
    p = dex_pair(mint=APEWIF, mc=4_900, fdv=4_900, price="0.0000049", liq=None, dex="pumpfun",
                 pair="CQMTTfLgznnvcb6RSob6Y4eVxuhTLmvs8wN5SnLRn868")
    p["baseToken"] = {"address": APEWIF, "symbol": "APEWIF", "name": "apewifdress"}
    p["quoteToken"] = {"address": TSLAX, "symbol": "TSLAx"}
    return p


APEWIF_PUMP_RECORD = {"mint": APEWIF, "name": "apewifdress", "symbol": "APEWIF",
                      "bonding_curve": "DkYJn71KcaqoEj34BH8HsbLCSrwcmnYyodow2QyZm5CQ",
                      "creator": "SZqGj7R2NFC8DqfaU2tWX1noNbYt3wBDn9xwasRNiLE", "created_timestamp": 1790763023000,
                      "complete": False, "total_supply": 1000000000000000, "real_token_reserves": 793100000000000,
                      "token_program": T22, "quote_mint": TSLAX, "base_decimals": 6, "quote_decimals": 8,
                      "virtual_quote_reserves": 1216576196, "real_quote_reserves": 0,
                      "market_cap_quote": 11.338081975768873}
TSLAX_ASSET = {"symbol": "TSLAx", "name": "Tesla xStock", "token_program": T22, "interface": "FungibleToken",
               "extensions": ["default_account_state", "metadata", "metadata_pointer", "pausable_config",
                              "permanent_delegate", "transfer_hook"]}


# ---------------------------------------------------------------- unit rules
def test_symbol_normalisation():
    assert norm_symbol(" $TSLAx ") == norm_symbol("tslax") == norm_symbol("ＴＳＬＡｘ")
    assert norm_symbol("APEWIF") != norm_symbol("TSLAx")


def test_feed_claim_alone_is_unverified_and_conflict_needs_two_symbols():
    ident = TokenIdentity()
    record_claim(ident, "pumpportal", "APEWIF", "apewifdress")
    assert resolve_identity(ident).status == "UNVERIFIED"         # a feed is never trusted on its own
    record_claim(ident, "dexscreener", "TSLAx", "Tesla xStock")
    r = resolve_identity(ident)
    assert r.status == "CONFLICT" and r.symbol == "TSLAx" and "APEWIF" in r.reason and "TSLAx" in r.reason
    ok = TokenIdentity()
    record_claim(ok, "pumpportal", "$apewif", "")
    record_claim(ok, "pumpfun", "APEWIF", "apewifdress")
    record_claim(ok, "dexscreener", "APEWIF", "apewifdress")
    assert resolve_identity(ok).status == "VERIFIED"
    record_claim(ok, "helius", "", "")                           # empty metadata is not a claim
    assert resolve_identity(ok).status == "VERIFIED"


def test_dexscreener_never_assigns_a_pair_to_its_quote_token():
    """/tokens/v1 returns pairs where the CA is base OR quote. The APEWIF/TSLAx curve must not become TSLAx."""
    def handler(req):
        return httpx.Response(200, json=[apewif_curve_pair(), tslax_pair()])

    async def go():
        h = HttpClient(transport=httpx.MockTransport(handler))
        r = await DexScreenerClient(h).tokens([TSLAX])
        await h.aclose()
        return r
    res = asyncio.run(go())
    m, _ = res[TSLAX]
    assert m.base_symbol == "TSLAx" and m.dex_id == "raydium" and m.market_cap == 353_569
    assert APEWIF not in res


def test_pumpfun_record_keeps_mint_and_quote_apart():
    info = parse_coin(APEWIF_PUMP_RECORD)
    assert info.mint == APEWIF and info.symbol == "APEWIF" and info.quote_mint == TSLAX
    assert info.virtual_sol_reserves is None                     # TSLAx reserves are never read as SOL


# ---------------------------------------------------------------- end-to-end regression on the exact CA
@pytest.fixture
def eng(tmp_path):
    e = ScannerEngine(Settings(), Database(tmp_path / "id.db"), keys=ApiKeys(helius="k"), on_log=lambda m: None)
    e.sol_price = 150.0
    e.holder_calls = []

    async def asset(mint):
        return dict(TSLAX_ASSET) if mint == TSLAX else None

    async def holders(*a, **k):
        e.holder_calls.append(a[0])
        return None

    async def coin(mint):
        return parse_coin(APEWIF_PUMP_RECORD) if mint == APEWIF else None     # Pump.fun has no TSLAx record

    async def dex(mints):
        out = {}
        if TSLAX in mints:
            out[TSLAX] = (parse_pair(tslax_pair()), {})
        if APEWIF in mints:
            out[APEWIF] = (parse_pair(apewif_curve_pair()), {})
        return out

    async def none(*a, **k):
        return None
    e.rpc.das_get_asset = asset
    e.holders.analyze = holders
    e.dev.analyze = none
    e.pump.coin = coin
    e.dex.tokens = dex
    e.rpc.token_supply = none
    return e


def _run(e, mint):
    asyncio.run(e.refresh_market([mint]))
    asyncio.run(e._deep(e.tracked[mint]))
    e._evaluate(e.tracked[mint])
    return e.tracked[mint]


def test_tslax_ca_labelled_apewif_is_caught(eng):
    info = parse_event(BAD_EVENT)
    assert info.mint == TSLAX and info.symbol == "APEWIF"             # the feed's (wrong) claim
    eng._add(info)
    st = eng.tracked[TSLAX]
    assert st.identity.status == "UNVERIFIED"                        # nothing trusted yet
    st = _run(eng, TSLAX)
    ident = st.identity
    assert ident.status == "CONFLICT"
    assert ident.claims["pumpportal"][0] == "APEWIF"
    assert ident.claims["dexscreener"][0] == "TSLAx" and ident.claims["helius"][0] == "TSLAx"
    assert st.info.symbol == "TSLAx" and st.info.name == "Tesla xStock"      # canonical metadata of THIS CA
    assert ident.token_program == T22 and "permanent_delegate" in ident.extensions
    assert st.dq_status == "INVALID" and any(i.key == "identity_conflict" for i in st.quality.issues)
    assert st.score is None and not (st.early and st.early.is_early)
    g, why = classify_group(st, eng.settings)
    assert g == "excluded" and "identity_conflict" in why
    assert eng.holder_calls == []                                    # never analysed as a memecoin
    assert st.mc_track.initial_mc is None and st.mc_track.pending is None    # no anchor from a mislabelled CA


def test_real_apewif_mint_is_verified_and_quote_mc_not_used(eng):
    ev = dict(BAD_EVENT, mint=APEWIF)                               # correct address this time
    eng._add(parse_event(ev))
    st = eng.tracked[APEWIF]
    assert st.mc_track.pending is not None                          # PumpPortal MC waits for the quote
    eng._merge_info(st, parse_coin(APEWIF_PUMP_RECORD))            # Pump.fun: quote_mint = TSLAx
    assert st.mc_track.pending is None and st.mc_track.initial_mc is None   # 11.3 TSLAx × SOL price discarded
    st = _run(eng, APEWIF)
    assert st.identity.status == "VERIFIED" and st.info.symbol == "APEWIF"
    # a TSLAx-quoted curve has no SOL reserve: liquidity UNKNOWN (warning, not a rejection); the anchor is the
    # validated USD market cap from DexScreener — never the 11.3 TSLAx × SOL price PumpPortal value
    assert any(i.key == "curve_unavailable" and i.severity == "warning" for i in st.market_issues)
    assert st.market.liquidity_usd is None
    assert st.mc_track.initial_mc == 4_900 and st.mc_track.initial_source.endswith("DexScreener")


def test_ui_shows_canonical_symbol_and_conflict(eng):
    eng._add(parse_event(BAD_EVENT))
    st = _run(eng, TSLAX)
    eng.published = [st]
    with TestClient(create_app(engine=eng, start_scanner=False, access_code=CODE)) as c:
        r = c.get(f"/api/token/{TSLAX}", headers={"X-Access-Code": CODE})
        v = r.json()
        home = c.get("/api/home", headers={"X-Access-Code": CODE}).json()
    assert "api-key" not in r.text
    card = v["card"]
    assert card["symbol"] == "TSLAx" and card["identity"] == "CONFLICT" and card["group"] == "excluded"
    assert {x["symbol"] for x in card["identity_claims"]} == {"APEWIF", "TSLAx"}
    assert [x["mint"] for x in home["groups"]["excluded"]] == [TSLAX]
    assert home["groups"]["opportunity"] == []


def test_opportunity_requires_verified_identity():
    st = build_state(dex_pair(mint="OPPID"))
    from core.models import EarlySignal
    st.holder_status = "ok"
    st.early = EarlySignal(strength=72, is_early=True, transition=True, fired_count=4, groups_computable=5)
    record_claim(st.identity, "pumpportal", "OTHER", "")
    apply_identity(st)
    assert classify_group(st)[0] == "watch"                           # unverified -> never 🔥
    record_claim(st.identity, "dexscreener", "TKN", "")
    apply_identity(st)
    assert st.identity.status == "CONFLICT"
    from scanner.pipeline import evaluate
    from history.store import TokenHistory
    evaluate(st, Settings(), TokenHistory(), time.time(), SOL_USD)
    assert st.dq_status == "INVALID" and classify_group(st)[0] == "excluded"


# ---------------------------------------------------------------- live (real APIs): python -m pytest --live
@pytest.mark.live
def test_live_tslax_identity():
    import os
    from solana_data.rpc import SolanaRpc

    async def go():
        h = HttpClient()
        try:
            dex = await DexScreenerClient(h).tokens([TSLAX])
            rpc = SolanaRpc(h, os.environ.get("HELIUS_API_KEY", ""))
            asset = await rpc.das_get_asset(TSLAX)
            return dex, asset, rpc.credits.state()["quota_exhausted"]
        finally:
            await h.aclose()
    dex, asset, quota = asyncio.run(go())
    assert dex[TSLAX][0].base_symbol == "TSLAx"
    if os.environ.get("HELIUS_API_KEY"):
        if asset is None and quota:
            pytest.skip("Helius quota exhausted (HTTP 429 'max usage reached') — DexScreener part passed")
        assert asset["symbol"] == "TSLAx" and asset["token_program"] == T22
