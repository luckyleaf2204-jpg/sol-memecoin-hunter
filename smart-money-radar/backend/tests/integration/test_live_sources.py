"""Live checks against the real external sources (run with --live). They verify that the
endpoints and response shapes the system depends on still exist."""
import json
from pathlib import Path

import pytest

from radar.config import load_config
from radar.decoder.tx import decode_transaction
from radar.net.http import HttpClient
from radar.solana.rpc import SolanaRpc

pytestmark = pytest.mark.live
FIX = Path(__file__).resolve().parents[1] / "fixtures" / "tx"


async def test_rpc_returns_identical_transaction_to_fixture():
    rpc = SolanaRpc(load_config().rpc_endpoints)
    try:
        f = next(FIX.glob("6acaFRzs*.json"))
        stored = json.loads(f.read_text())
        live = await rpc.get_transaction(f.stem)
        assert live is not None
        assert live["meta"]["preBalances"] == stored["meta"]["preBalances"]
        assert live["meta"]["postBalances"] == stored["meta"]["postBalances"]
        a = decode_transaction(live).trades[0]
        b = decode_transaction(stored).trades[0]
        assert (a.wallet, a.sol_amount, a.token_amount_raw) == (b.wallet, b.sol_amount, b.token_amount_raw)
    finally:
        await rpc.close()


async def test_pump_global_account_constants():
    import base64
    from radar.decoder.events import Reader
    from radar.solana.constants import (PUMP_GLOBAL, PUMP_INITIAL_REAL_TOKEN, PUMP_INITIAL_VIRTUAL_SOL,
                                        PUMP_INITIAL_VIRTUAL_TOKEN, PUMP_PROGRAM)
    rpc = SolanaRpc(load_config().rpc_endpoints)
    try:
        acc = await rpc.get_account_info(PUMP_GLOBAL)
        assert acc["owner"] == PUMP_PROGRAM
        r = Reader(base64.b64decode(acc["data"][0])[8:])
        r.u8(); r.pubkey(); r.pubkey()
        assert (r.u64(), r.u64(), r.u64()) == (PUMP_INITIAL_VIRTUAL_TOKEN, PUMP_INITIAL_VIRTUAL_SOL, PUMP_INITIAL_REAL_TOKEN)
    finally:
        await rpc.close()


async def test_market_data_sources_respond():
    from radar.sources import dexscreener, geckoterminal, jupiter
    http = HttpClient()
    try:
        p = await jupiter.sol_usd(http)
        assert p and 1 < p < 10000
        mint = "9BB6NFEcjBCtnNLFko2FqVQBq8HHM13kCyYcdQbgpump"   # long-lived migrated pump.fun token
        pairs = await dexscreener.token_pairs(http, [mint])
        assert mint in pairs
        pools = await geckoterminal.token_pools(http, mint)
        assert isinstance(pools, list)
    finally:
        await http.close()
