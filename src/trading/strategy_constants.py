"""Strategy constants that are module constants (not TradingConfig fields, so not in the parameter fingerprint)
but change which trades are taken, how they are exited or how they are costed. Their hash is pinned together with
STRATEGY_VERSION in tests/test_s6_constants.py: changing one without bumping STRATEGY_VERSION fails the tests, so a
sample never mixes two rule sets. Shown on /healthz (`constants`)."""
from __future__ import annotations

import hashlib
import json


def strategy_constants() -> dict:
    import history.persist as hp
    import trading.bot as bot
    import trading.entry_location as el
    import trading.exit_policy as ep
    import trading.exits as ex
    import trading.quote_budget as qb
    return {
        "entry_location.LOOKBACK_S": el.LOOKBACK_S, "entry_location.MIN_HISTORY_S": el.MIN_HISTORY_S,
        "entry_location.PULLBACK_STABLE_S": el.PULLBACK_STABLE_S,
        "entry_location.SECOND_WAVE_STABLE_S": el.SECOND_WAVE_STABLE_S,
        "bot.QUOTE_RETRY_WINDOW_S": bot.QUOTE_RETRY_WINDOW_S, "bot.FAILED_BUY_COOLDOWN_S": bot.FAILED_BUY_COOLDOWN_S,
        "bot.STALE_TIMEOUT_S": bot.STALE_TIMEOUT_S,
        "bot.NO_ROUTE_BLOCK_S": bot.NO_ROUTE_BLOCK_S, "bot.MAX_ENTRIES_PER_TICK": bot.MAX_ENTRIES_PER_TICK,
        "bot.TICK_S": bot.TICK_S, "bot.SELL_QUOTE_ATTEMPTS": bot.SELL_QUOTE_ATTEMPTS,
        "bot.SELL_QUOTE_BUDGET_S": bot.SELL_QUOTE_BUDGET_S,
        "exits.HARD": list(ex.HARD), "exit_policy.PROTECTIVE": list(ep.PROTECTIVE),
        "quote_budget.PER_MIN": qb.PER_MIN, "quote_budget.SELL_RESERVE": qb.SELL_RESERVE,
        "history.persist.MAX_RESTORE_GAP_S": hp.MAX_RESTORE_GAP_S, "history.persist.KEEP_S": hp.KEEP_S,
    }


def constants_hash() -> str:
    return hashlib.sha256(json.dumps(strategy_constants(), sort_keys=True).encode()).hexdigest()[:10]
