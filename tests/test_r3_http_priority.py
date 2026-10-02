"""Review round 3 (B8): SELL quotes get the next HTTP slot before BUY / truth / probe; the budget counts real HTTP
attempts; a budget refusal is BUDGET (not RATE_LIMITED); 401 / 403 / 408 are transient."""
import asyncio
import time

import httpx
import pytest

from core.http import HttpClient
from trading import jupiter as J
from trading.quote_budget import QuoteBudget


def test_priority_request_takes_the_next_slot():
    http = HttpClient()
    http.set_rate("h", 600)                                    # one slot every 0.1 s
    order = []

    async def req(name, prio, delay=0.0):
        await asyncio.sleep(delay)
        await http._throttle("h", prio)
        order.append(name)

    async def scenario():
        await http._throttle("h")                              # slot taken now
        await asyncio.gather(*(req(f"buy{i}", False) for i in range(4)), req("SELL", True, 0.01))
    asyncio.run(scenario())
    assert order[0] == "SELL"                                  # not behind the 4 queued normal requests


def _client(statuses):
    calls = []

    def handler(request):
        s = statuses[min(len(calls), len(statuses) - 1)]
        calls.append(s)
        if s == 200:
            p = dict(request.url.params)
            return httpx.Response(200, json={"inputMint": p["inputMint"], "outputMint": p["outputMint"],
                                             "inAmount": p["amount"], "outAmount": "1000", "routePlan": []})
        return httpx.Response(s, json={"error": "x"})
    jq = J.JupiterQuotes(HttpClient(transport=httpx.MockTransport(handler)))
    jq.http.set_rate("lite-api.jup.ag", 6000)
    return jq, calls


@pytest.mark.parametrize("status, expected", [(401, J.API_ERROR), (403, J.API_ERROR), (408, J.TIMEOUT),
                                              (400, J.INVALID), (429, J.RATE_LIMITED)])
def test_classify_auth_and_timeout_are_transient(status, expected):
    r = J.classify(status, {"error": "x"}, f"HTTP {status}", "A", "B", 1)
    assert r.status == expected and r.transient == (expected != J.INVALID)


def test_budget_counts_every_http_attempt():
    jq, calls = _client([500, 500, 200])
    jq.budget = QuoteBudget()
    r = asyncio.run(jq.quote_result("A", "B", 1, 50, attempts=3, budget_s=8.0, kind="buy"))
    assert r.ok and len(calls) == 3 and jq.budget.as_dict()["by_kind"] == {"buy": 3}


def test_non_sell_retries_stop_when_the_budget_is_reserved_for_sells():
    jq, calls = _client([500, 500, 200])
    jq.budget = QuoteBudget(per_min=12, sell_reserve=10)       # 2 non-SELL calls per minute
    r = asyncio.run(jq.quote_result("A", "B", 1, 50, attempts=3, budget_s=8.0, kind="buy"))
    assert r.status == J.BUDGET and len(calls) == 2


def test_budget_refusal_is_not_counted_as_rate_limited():
    from test_v12 import opened
    b, st, p = opened()
    b.quote_budget = QuoteBudget(per_min=1, sell_reserve=1)
    b.quote_budget.record("sell")
    qr = asyncio.run(b._buy_quote("X", 10 ** 8))
    assert qr.status == J.BUDGET and qr.transient
    b.quote_stats.clear()
    b.intents["X"] = {"mint": "X", "usd": 5.0, "ts": time.time()}
    asyncio.run(b.execute_intents())
    assert J.RATE_LIMITED not in b.quote_stats


def test_sell_quotes_ask_for_priority():
    from test_v12 import opened
    seen = {}

    class Spy:
        async def quote_result(self, im, om, amount, slip, kind="other", priority=False, attempts=3, budget_s=8.0):
            seen.update(kind=kind, priority=priority, attempts=attempts)
            return J.QuoteResult(J.TIMEOUT)
    b, st, p = opened()
    b.jupiter = Spy()
    asyncio.run(b._sell_quote(p.mint, 1000, 150.0))
    assert seen == {"kind": "sell", "priority": True, "attempts": 2}
    asyncio.run(b._buy_quote(p.mint, 1000))
    assert seen["kind"] == "buy" and seen["priority"] is False
