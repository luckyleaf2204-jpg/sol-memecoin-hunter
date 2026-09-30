"""Live API checks — run with:  python -m pytest --live"""
import asyncio

import pytest

from core.http import HttpClient
from dex.dexscreener import DexScreenerClient
from pumpfun.client import PumpFunClient
from solana_data.rpc import SolanaRpc

SICAT = "9pMXEbTjQ5HHiYifGbNBwkMkrQxGyhuSmB8ndKNXpump"
pytestmark = pytest.mark.live


async def _with_http(fn):
    h = HttpClient()
    try:
        return await fn(h)
    finally:
        await h.aclose()


def test_pumpfun_coin_and_list():
    async def go(h):
        p = PumpFunClient(h)
        return await p.coin(SICAT), await p.latest(10)
    coin, latest = asyncio.run(_with_http(go))
    assert coin and coin.symbol == "SICAT"
    assert latest


def test_dexscreener_tokens():
    r = asyncio.run(_with_http(lambda h: DexScreenerClient(h).tokens([SICAT])))
    assert SICAT in r and r[SICAT][0].market_cap


def test_rpc_supply():
    supply, dec = asyncio.run(_with_http(lambda h: SolanaRpc(h).token_supply(SICAT)))
    assert supply > 0 and dec == 6
