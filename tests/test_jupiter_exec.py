"""Jupiter BUY-quote execution: every failure is classified, transient ones are retried, a real "no route" is a final
SKIP, a matching quote leads to the paper BUY. Strategy / BUY conditions are not touched here."""
import asyncio
import time

import httpx
import pytest

from core.http import HttpClient
from test_bot_v2 import bot, good
from trading import jupiter as J
from trading.audit import RunAudit, category

MINT = "Want" + "1" * 40


def ok_body(req):
    p = dict(req.url.params)
    return {"inputMint": p["inputMint"], "outputMint": p["outputMint"], "inAmount": p["amount"],
            "outAmount": str(int(p["amount"]) * 1000), "priceImpactPct": "0.004", "slippageBps": 300,
            "routePlan": [{"swapInfo": {"label": "Pump.fun"}}]}


@pytest.fixture(autouse=True)
def fast_backoff(monkeypatch):
    monkeypatch.setattr(J, "BACKOFF_S", (0.0, 0.0, 0.0))


def quote_with(responses, mint=MINT, amount=10_000_000):
    """responses: list of callables(req) -> httpx.Response, or exceptions to raise; the last one repeats."""
    calls = []

    def handler(req):
        r = responses[min(len(calls), len(responses) - 1)]
        calls.append(req)
        if isinstance(r, Exception):
            raise r
        return r(req)

    async def go():
        h = HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.0)
        jq = J.JupiterQuotes(h)
        h.set_rate("lite-api.jup.ag", 60000)               # production throttle (50/min) is not under test here
        res = await jq.quote_result(J.WSOL, mint, amount, 300)
        await h.aclose()
        return res
    return asyncio.run(go()), calls


R200 = lambda req: httpx.Response(200, json=ok_body(req))                                       # noqa: E731
R429 = lambda req: httpx.Response(429, json={"error": "Too many requests"})                      # noqa: E731
R503 = lambda req: httpx.Response(503, text="unavailable")                                       # noqa: E731
RNOROUTE = lambda req: httpx.Response(400, json={"error": f"The token {MINT} is not tradable",   # noqa: E731
                                                 "errorCode": "TOKEN_NOT_TRADABLE"})
RNOROUTE2 = lambda req: httpx.Response(400, json={"error": "Could not find any route",           # noqa: E731
                                                  "errorCode": "COULD_NOT_FIND_ANY_ROUTE"})


def test_successful_quote_is_a_match():
    r, calls = quote_with([R200])
    assert r.ok and r.quote["outputMint"] == MINT and r.attempts == 1 and len(calls) == 1


def test_429_is_retried_then_matches():
    r, calls = quote_with([R429, R429, R200])
    assert r.ok and r.attempts == 3 and len(calls) == 3


def test_persistent_429_is_rate_limited_transient():
    r, calls = quote_with([R429])
    assert r.status == J.RATE_LIMITED and r.transient and len(calls) == 3


def test_timeout_is_transient_and_retried():
    r, calls = quote_with([httpx.ReadTimeout("slow")])
    assert r.status == J.TIMEOUT and r.transient and len(calls) == 3
    r, _ = quote_with([httpx.ReadTimeout("slow"), R200])
    assert r.ok


def test_api_unavailable_5xx_and_network_errors_are_transient():
    r, _ = quote_with([R503])
    assert r.status == J.API_ERROR and r.transient and r.http == 503
    r, _ = quote_with([httpx.ConnectError("down")])
    assert r.status == J.API_ERROR and r.transient


def test_no_route_is_final_and_not_retried():
    for resp in (RNOROUTE, RNOROUTE2):
        r, calls = quote_with([resp])
        assert r.status == J.NO_ROUTE and not r.transient and len(calls) == 1


def test_mismatched_or_empty_quote_is_never_used():
    bad_mint = lambda req: httpx.Response(200, json=ok_body(req) | {"outputMint": "Other111"})    # noqa: E731
    bad_amount = lambda req: httpx.Response(200, json=ok_body(req) | {"inAmount": "1"})           # noqa: E731
    zero = lambda req: httpx.Response(200, json=ok_body(req) | {"outAmount": "0"})                # noqa: E731
    assert quote_with([bad_mint])[0].status == J.INVALID
    assert quote_with([bad_amount])[0].status == J.INVALID
    assert quote_with([zero])[0].status == J.NO_ROUTE
    assert quote_with([bad_mint])[0].quote is None


def test_legacy_quote_wrapper():
    async def go(handler):
        h = HttpClient(transport=httpx.MockTransport(handler), backoff_base=0.0)
        q = await J.JupiterQuotes(h).quote(J.WSOL, MINT, 10, 300)
        await h.aclose()
        return q
    assert asyncio.run(go(R200))["outputMint"] == MINT
    assert asyncio.run(go(RNOROUTE)) is None


# ---------------------------------------------------------------- bot flow: Candidate -> Quote -> BUY / SKIP
class ScriptedJupiter:
    """quote_result() returns scripted QuoteResults (last one repeats) and builds real-shaped quotes."""
    def __init__(self, statuses):
        self.statuses, self.calls = list(statuses), 0

    async def quote_result(self, input_mint, output_mint, amount_raw, slippage_bps):
        s = self.statuses[min(self.calls, len(self.statuses) - 1)]
        self.calls += 1
        if s != J.OK:
            return J.QuoteResult(s, http=429 if s == J.RATE_LIMITED else 400 if s == J.NO_ROUTE else None, attempts=3)
        out = int(amount_raw / 1e9 * 150 / 0.0002 * 1e6 * 0.997)
        return J.QuoteResult(J.OK, quote={"inputMint": input_mint, "outputMint": output_mint, "inAmount": str(amount_raw),
                                          "outAmount": str(out), "priceImpactPct": "0.004",
                                          "routePlan": [{"swapInfo": {"label": "Pump.fun"}}]}, http=200, attempts=1)


def texts(b):
    return [a.text for a in b.activity]


def test_candidate_quote_match_buy_is_logged_in_order():
    st = good()
    b = bot([st], ScriptedJupiter([J.OK]))
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint in b.book.positions
    t = texts(b)
    i_c = next(i for i, x in enumerate(t) if x.startswith("BUY CANDIDATE"))
    i_q = next(i for i, x in enumerate(t) if x.startswith("BUY → QUOTE → MATCH"))
    i_b = next(i for i, a in enumerate(b.activity) if a.kind == "BUY")
    assert i_c < i_q < i_b
    f = b.audit.funnel
    assert f["buy_candidate"] == 1 and f["quote_ok"] == 1 and f["buy_executed"] == 1 and f["buy_skipped"] == 0


def test_transient_failure_retries_then_buys():
    st = good()
    jup = ScriptedJupiter([J.RATE_LIMITED, J.TIMEOUT, J.OK])
    b = bot([st], jup)
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    assert st.mint in b.intents and not b.book.positions
    asyncio.run(b.execute_intents(t0 + 1))            # retry not due yet: no call
    assert jup.calls == 1
    asyncio.run(b.execute_intents(t0 + 3))
    asyncio.run(b.execute_intents(t0 + 9))
    assert st.mint in b.book.positions and jup.calls == 3
    assert sum("RETRY" in x for x in texts(b)) == 2 and b.quote_stats == {"RATE_LIMITED": 1, "TIMEOUT": 1, "OK": 1}


def test_no_route_skips_without_bypass_and_is_not_requoted():
    st = good()
    jup = ScriptedJupiter([J.NO_ROUTE])
    b = bot([st], jup)
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    assert not b.book.positions and not b.intents
    assert any(a.kind == "FAILED" and "QUOTE FAILED (NO_ROUTE" in a.text and "BUY SKIPPED — JUPITER" in a.text
               for a in b.activity)
    b.tick(t0 + 5)                                    # still a candidate, but Jupiter said "no route" seconds ago
    asyncio.run(b.execute_intents(t0 + 5))
    assert jup.calls == 1 and b.decisions[st.mint]["state"] == "NO_ROUTE"
    assert b.audit.skips == {"jupiter:NO_ROUTE": 1}


def test_transient_failure_past_window_is_a_logged_skip():
    st = good()
    b = bot([st], ScriptedJupiter([J.API_ERROR]))
    t0 = time.time()
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    asyncio.run(b.execute_intents(t0 + 61))
    assert not b.book.positions and b.audit.skips == {"jupiter:API_ERROR": 1}
    assert any("BUY SKIPPED — JUPITER" in x for x in texts(b))


# ---------------------------------------------------------------- audit
def test_audit_categories_and_report(tmp_path):
    assert category("vet:holders") == "holder" and category("vet_unknown:volume_buy_pressure") == "volume_buy_pressure"
    assert category("early_false_partial") == "early_signal" and category("risk_engine: x") == "risk"
    assert category("jupiter: no route") == "jupiter_quote" and category("vet:dev") == "vet"
    st = good()
    b = bot([st], ScriptedJupiter([J.OK]))
    b.audit = RunAudit(tmp_path / "a.json")
    b.tick()
    asyncio.run(b.execute_intents())
    r = b.audit.report()
    assert r["stats"]["discovery"] == 1 and r["stats"]["trade_candidate"] == 1 and r["stats"]["buy_executed"] == 1
    assert r["top"][0]["mint"] == st.mint and r["top"][0]["vet"] == "PASS"
    b.audit.save(force=True)
    again = RunAudit(tmp_path / "a.json")
    assert st.mint in again.tokens and again.funnel["buy_executed"] == 1


def test_status_payload_carries_audit_and_quote_stats():
    import json
    from trading.serialize import bot_status
    st = good()
    b = bot([st], ScriptedJupiter([J.NO_ROUTE]))
    b.tick()
    asyncio.run(b.execute_intents())
    d = json.loads(json.dumps(bot_status(b, b.engine), default=str))
    assert d["version"] == "web-13" and d["jupiter_quotes"] == {"NO_ROUTE": 1}
    a = d["audit"]
    assert a["stats"]["buy_candidate"] == 1 and a["stats"]["buy_skipped"] == 1 and a["blocked_by"]["jupiter_quote"] == 1
    assert len(a["top"]) == 1 and a["events"][0]["kind"] == "skip"
