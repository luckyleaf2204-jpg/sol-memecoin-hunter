"""G4 — the failed-buy cooldown (last_buy_attempt) is saved with the paper book and restored after a restart."""
import asyncio
import time

import trading.bot as B
from test_bot_v2 import Eng, FakeJupiter, good
from trading.book import PaperBook
from trading.bot import PaperBot
from trading.config import TradingConfig


def _bot(path, st):
    b = PaperBot(Eng([st]), TradingConfig(seed=4), state_path=path)
    b.exec.rng.random = lambda: 0.99
    b.jupiter = FakeJupiter()
    return b


def test_cooldown_survives_a_restart(tmp_path):
    st = good()
    path = tmp_path / "paper_bot.json"
    b = _bot(path, st)
    t0 = time.time()
    b.exec._slip = lambda s: 0.05                       # FAILED fill
    b.tick(t0)
    asyncio.run(b.execute_intents(t0))
    assert b.last_buy_attempt[st.mint] == t0 and b.book.last_buy_attempt is b.last_buy_attempt
    b.persist()
    b2 = _bot(path, st)                                 # restart
    assert b2.last_buy_attempt == {st.mint: t0}
    for stamp in st.stamps.values():
        stamp.updated_at = t0 + 30
    st.market.updated_at = t0 + 30
    b2.tick(t0 + 30)
    assert not b2.intents and b2.decisions[st.mint]["state"] == "BUY_COOLDOWN"


def test_old_entries_are_not_kept_forever(tmp_path):
    b = PaperBook(1000.0)
    b.last_buy_attempt.update({"OLD": time.time() - 2 * 86_400, "NEW": time.time()})
    b.save(tmp_path / "b.json")
    assert set(PaperBook.load(tmp_path / "b.json", 1000.0).last_buy_attempt) == {"NEW"}
    assert B.FAILED_BUY_COOLDOWN_S == 300.0
