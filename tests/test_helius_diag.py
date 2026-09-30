"""Helius diagnostics: CONNECTED / FAILED / NO_KEY, HTTP status + endpoint recorded, key never exposed."""
import asyncio
import json

import httpx

from conftest import build_state, dex_pair
from core.config import ApiKeys, Settings
from core.http import HttpClient
from core.models import TokenInfo, TokenState
from intel.holders import holder_reason
from solana_data.rpc import SOURCE_DAS, SolanaRpc

KEY = "11111111-2222-3333-4444-555555555555"


def _client(handler) -> HttpClient:
    h = HttpClient()
    h._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return h


def _run(coro):
    return asyncio.run(coro)


def _ok_handler(request: httpx.Request) -> httpx.Response:
    body = json.loads(request.content)
    assert body["method"] == "getTokenAccounts"
    return httpx.Response(200, json={"jsonrpc": "2.0", "id": 1, "result": {"total": 1, "token_accounts": [
        {"owner": "O", "amount": 1_000_000}]}})


def test_connected_records_status_and_hides_key():
    h = _client(_ok_handler)
    res = _run(SolanaRpc(h, KEY).helius_check())
    assert res["state"] == "CONNECTED" and res["status"] == 200 and res["call"] == "getTokenAccounts"
    assert res["endpoint"] == "https://mainnet.helius-rpc.com/"
    assert KEY not in json.dumps(res) and KEY not in repr(h.health.get(SOURCE_DAS))


def test_failed_401_reports_status_and_error_without_key():
    h = _client(lambda r: httpx.Response(401, text=f"invalid api key {r.url}"))
    res = _run(SolanaRpc(h, KEY).helius_check())
    assert res["state"] == "FAILED" and res["status"] == 401
    assert "401" in res["error"]
    assert KEY not in res["error"], "API key leaked into the error text"


def test_jsonrpc_error_is_reported():
    h = _client(lambda r: httpx.Response(200, json={"jsonrpc": "2.0", "id": 1,
                                                    "error": {"code": -32401, "message": "Invalid API key"}}))
    res = _run(SolanaRpc(h, KEY).helius_check())
    assert res["state"] == "FAILED" and res["status"] == 200 and "Invalid API key" in res["error"]


def test_no_key():
    assert _run(SolanaRpc(HttpClient(), "").helius_check())["state"] == "NO_KEY"


def test_engine_logs_state_without_key(tmp_path):
    from database.db import Database
    from scanner.engine import ScannerEngine
    logs = []
    eng = ScannerEngine(Settings(), Database(tmp_path / "t.db"), keys=ApiKeys(helius=KEY), on_log=logs.append)
    eng.http._client = httpx.AsyncClient(transport=httpx.MockTransport(_ok_handler))
    _run(eng.check_helius(startup=True))
    text = "\n".join(logs)
    assert "HELIUS = CONNECTED" in text and "HTTP 200" in text and "getTokenAccounts" in text
    assert "(36" in text and KEY not in text


def test_unknown_reason_never_blames_key_when_key_is_set():
    st = TokenState(info=TokenInfo(mint="x"))
    st.holder_status = "pending"
    assert holder_reason(st) == "holders_pending"
    st.holder_status = "failed"
    assert holder_reason(st) == "helius_failed"
    st.holder_status = "no_key"
    assert holder_reason(st) == "needs_helius"
    s = build_state(dex_pair(), holders=False)
    s.holder_status = "pending"
    from scanner.pipeline import evaluate
    evaluate(s, Settings())
    assert s.metric("holders").note == "holders_pending"
