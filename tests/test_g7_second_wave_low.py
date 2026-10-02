"""G7 — SECOND_WAVE gets the same "no new low within 180 s" check as PULLBACK."""
import pytest

from test_fix2_gate import NOW, pts
from test_step2_chasing import gate
from trading.entry_location import entry_location
from trading.lifecycle_decision import TRADE, WATCH


@pytest.mark.parametrize("low_age, passes", [(30.0, False), (179.0, False), (180.0, True), (600.0, True), (None, True)])
def test_second_wave_blocked_while_making_new_lows(low_age, passes):
    d, rec = gate({"entry_location": "SECOND_WAVE", "entry_extension": 3.0,
                   "entry_location_detail": {"history_s": 900.0, "last_low_age_s": low_age}})
    assert (d == TRADE) is passes and rec["entry_pullback_stable"] is passes
    if not passes:
        assert d == WATCH and any("SECOND_WAVE still falling" in r for r in rec["blocked_by"])


def test_second_wave_location_reports_the_last_low():
    falling = pts([(-500, 1.0), (-400, 1.6), (-300, 1.4), (-200, 1.3), (-40, 1.25)])
    r = entry_location(falling, NOW, post_state="SECOND_WAVE_READY")
    assert r["entry_location"] == "SECOND_WAVE" and r["last_low_age_s"] == 40
    rising = pts([(-500, 1.0), (-400, 1.1), (-300, 1.2), (-20, 1.3)])        # the high is now: no low after it
    r = entry_location(rising, NOW, post_state="SECOND_WAVE_READY")
    assert r["entry_location"] == "SECOND_WAVE" and r["last_low_age_s"] is None
