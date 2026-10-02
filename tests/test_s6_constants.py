"""Part 4.2 — strategy module constants are pinned with STRATEGY_VERSION: change one without bumping the version and
this fails (a sample must never mix two rule sets). To change a constant: change it, bump STRATEGY_VERSION, and add
the new (version, hash) pair here — never edit an old pair."""
import pytest

import trading.entry_location as EL
from trading import sample_epoch
from trading.strategy_constants import constants_hash, strategy_constants

PINNED = {"s7-sellguard-budget": "d61e3a6eb7", "s8-protective-exits": "f8695d45a5", "s9-stale-timeout": "380073928c", "s10-http-sell-priority": "380073928c"}


def test_constants_unchanged_for_this_strategy_version():
    v = sample_epoch.STRATEGY_VERSION
    assert v in PINNED, f"new STRATEGY_VERSION {v}: pin its constants hash {constants_hash()} in PINNED"
    assert constants_hash() == PINNED[v], (
        f"a strategy constant changed ({constants_hash()} != {PINNED[v]}) but STRATEGY_VERSION is still {v}: bump it")


def test_the_values_the_review_asked_for():
    c = strategy_constants()
    assert c["entry_location.MIN_HISTORY_S"] == 300.0 and c["entry_location.PULLBACK_STABLE_S"] == 180.0
    assert c["entry_location.SECOND_WAVE_STABLE_S"] == 180.0 and c["bot.FAILED_BUY_COOLDOWN_S"] == 300.0
    assert c["bot.QUOTE_RETRY_WINDOW_S"] == 60.0 and c["quote_budget.PER_MIN"] == 50
    assert c["history.persist.MAX_RESTORE_GAP_S"] == 120.0


def test_a_changed_constant_is_caught(monkeypatch):
    before = constants_hash()
    monkeypatch.setattr(EL, "SECOND_WAVE_STABLE_S", 120.0)
    assert constants_hash() != before
    with pytest.raises(AssertionError, match="bump it"):
        test_constants_unchanged_for_this_strategy_version()
