"""Fix 2 — entry_location reports the pair's price history and how long ago a pullback made its last low;
the gate blocks UNKNOWN, < 300 s of history and a PULLBACK that is still falling."""
from trading.entry_location import MIN_HISTORY_S, PULLBACK_STABLE_S, entry_location

NOW = 10_000.0


def pts(series):
    return [(NOW + dt, p, 1000.0, 5000.0) for dt, p in series]


def test_history_is_measured_from_the_first_point_of_the_pair():
    r = entry_location(pts([(-1700, 1.0), (-5, 1.1)]), NOW)
    assert r["history_s"] == 1700 and r["entry_location"] == "UNKNOWN"        # < 3 points in the window
    assert entry_location([], NOW)["history_s"] == 0.0
    assert entry_location(pts([(-50, 1.0), (60, 2.0)]), NOW)["history_s"] == 50  # future points never count


def test_pullback_last_low_age():
    falling = pts([(-500, 1.0), (-400, 1.6), (-300, 1.4), (-200, 1.3), (-30, 1.2)])
    r = entry_location(falling, NOW)
    assert r["entry_location"] == "PULLBACK" and r["last_low_age_s"] == 30 < PULLBACK_STABLE_S
    based = pts([(-500, 1.0), (-400, 1.6), (-300, 1.2), (-200, 1.25), (-60, 1.3)])
    r = entry_location(based, NOW)
    assert r["entry_location"] == "PULLBACK" and r["last_low_age_s"] == 300 >= PULLBACK_STABLE_S
    assert MIN_HISTORY_S == 300.0
