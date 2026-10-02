"""Fix 4 — after a FAILED BUY fill the same mint is not retried for FAILED_BUY_COOLDOWN_S (each try pays fees)."""
import asyncio
import time

import trading.bot as B
from test_bot_v2 import FakeJupiter, bot, good


def fresh(st, t):
    for stamp in st.stamps.values():                     # keep the test token's data fresh at tick time t
        stamp.updated_at = t
    st.market.updated_at = t


def test_failed_fill_starts_a_cooldown_then_buys_again():
    st = good()
    b = bot([st], FakeJupiter())
    t0 = time.time()
    b.exec._slip = lambda s: 0.05                       # impact + slip > 3 % -> FAILED fill
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    assert not b.book.positions and b.last_buy_attempt[st.mint] == t0
    b.exec._slip = lambda s: 0.001
    fresh(st, t0 + 30)
    b.tick(t0 + 30)
    rec = b.decisions[st.mint]
    assert not b.intents and rec["state"] == "BUY_COOLDOWN"
    assert any(r.startswith("buy_cooldown: last BUY fill FAILED 30s ago") for r in rec["blocked_by"])
    fresh(st, t0 + B.FAILED_BUY_COOLDOWN_S + 1)
    b.tick(t0 + B.FAILED_BUY_COOLDOWN_S + 1)
    assert st.mint in b.intents                          # cooldown over: a new attempt
    asyncio.run(b.execute_intents(t0 + B.FAILED_BUY_COOLDOWN_S + 1))
    assert st.mint in b.book.positions


def test_quote_failure_is_not_a_failed_fill():
    st = good()
    b = bot([st], FakeJupiter(fail=True))               # no quote: nothing was sent, no fee, no cooldown
    b.tick()
    asyncio.run(b.execute_intents())
    assert st.mint not in b.last_buy_attempt and B.FAILED_BUY_COOLDOWN_S == 300.0
