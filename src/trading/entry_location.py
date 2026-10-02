"""ENTRY LOCATION (SHADOW). Not "is the token good?" but "where in the move is this entry?".

From the token's own price history (current pair, points with ts <= decision time) over the last LOOKBACK_S:
  distance from the local low / recent high, 5-min extension, pullback depth from the recent high, volume and
  liquidity retention (now vs the window max), acceleration (last minute vs the minute before), buyer retention
  (from money flow when measured).
Classes: EARLY_ENTRY · MID_MOVE · EXTENDED · PULLBACK · SECOND_WAVE (post-migration state READY) · UNKNOWN.
"""
from __future__ import annotations

LOOKBACK_S = 600.0
MIN_HISTORY_S = 300.0          # gate: less price history than this in the lookback -> no entry (location unreliable)
PULLBACK_STABLE_S = 180.0      # gate: a PULLBACK enters only if it made no new low for this long
SECOND_WAVE_STABLE_S = 180.0   # gate: the same for a SECOND_WAVE (G7)
EARLY, MID, EXTENDED, PULLBACK, SECOND_WAVE, UNKNOWN = ("EARLY_ENTRY", "MID_MOVE", "EXTENDED", "PULLBACK",
                                                       "SECOND_WAVE", "UNKNOWN")


def _at(pts, t):
    best = None
    for p in pts:
        if p[0] <= t:
            best = p
    return best


def entry_location(points: list, now: float, post_state: str | None = None, buyer_acceleration: float | None = None) -> dict:
    """points: [(ts, price, vol_5m, liq)] (any order). Only ts <= now and ts >= now - LOOKBACK_S are used."""
    pts = sorted(p for p in points if p[1] and now - LOOKBACK_S <= p[0] <= now)
    out = {"entry_location": UNKNOWN, "dist_from_low_pct": None, "dist_from_high_pct": None, "extension_5m_pct": None,
           "pullback_depth_pct": None, "volume_retention": None, "liquidity_retention": None, "acceleration": None,
           "buyer_retention": buyer_acceleration,
           "history_s": round(now - min(seen), 1) if (seen := [p[0] for p in points if p[1] and p[0] <= now]) else 0.0,
           "last_low_age_s": None}                 # history_s: price history of this pair up to now (any age)
    if post_state == "SECOND_WAVE_READY":
        out["entry_location"] = SECOND_WAVE
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < 60:
        return out
    cur = pts[-1]
    low, high = min(p[1] for p in pts), max(p[1] for p in pts)
    i_high = max(range(len(pts)), key=lambda i: pts[i][1])
    out["dist_from_low_pct"] = round(100 * (cur[1] / low - 1), 2)
    out["dist_from_high_pct"] = round(100 * (1 - cur[1] / high), 2)
    p5 = _at(pts, now - 300)
    if p5:
        out["extension_5m_pct"] = round(100 * (cur[1] / p5[1] - 1), 2)
    after = pts[i_high:]
    out["pullback_depth_pct"] = round(100 * (1 - min(p[1] for p in after) / high), 2)
    low_after = min(after, key=lambda p: (p[1], -p[0]))           # latest point at the lowest price after the high
    if low_after[1] < high:                                       # None: nothing below the high since the high
        out["last_low_age_s"] = round(now - low_after[0], 1)
    vols = [p[2] for p in pts if p[2] is not None]
    if vols and cur[2] is not None and max(vols):
        out["volume_retention"] = round(cur[2] / max(vols), 3)
    liqs = [p[3] for p in pts if p[3] is not None]
    if liqs and cur[3] is not None and max(liqs):
        out["liquidity_retention"] = round(cur[3] / max(liqs), 3)
    p1, p2 = _at(pts, now - 60), _at(pts, now - 120)
    if p1 and p2 and p2[1] and p1[1]:
        out["acceleration"] = round((cur[1] / p1[1] - 1) - (p1[1] / p2[1] - 1), 4)
    if out["entry_location"] == SECOND_WAVE:
        return out
    dl, dh, ext = out["dist_from_low_pct"], out["dist_from_high_pct"], out["extension_5m_pct"]
    if dh >= 15 and i_high < len(pts) - 1:
        out["entry_location"] = PULLBACK
    elif dl >= 100 or (ext is not None and ext >= 100) or (dl >= 60 and dh < 5):
        out["entry_location"] = EXTENDED
    elif dl < 30 and (ext is None or ext < 30):
        out["entry_location"] = EARLY
    else:
        out["entry_location"] = MID
    return out
