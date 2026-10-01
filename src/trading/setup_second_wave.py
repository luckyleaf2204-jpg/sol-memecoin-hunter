"""POST-MIGRATION / SECOND-WAVE setup engine. Never chase the first pump.

Lifecycle after migration (state machine, only points of the post-migration AMM pair with ts in
[migration_ts, decision time] — pre-migration curve prices are never used as K-lines):
  POST_MIGRATED        not enough post-migration history, or no first pump yet
  FIRST_PUMP           price rose >= first_pump_min above the post-migration base, no meaningful pullback yet -> WATCH
  PULLBACK             retraced >= pullback_min from the first-pump peak, no support yet
  SUPPORT              no new low for >= support_min_s
  SECOND_WAVE_READY    support + buyers back: bounce >= bounce_min off the low with buy share >= reentry_buy_share,
                       volume and liquidity retained                                     -> setup may BUY
  SECOND_WAVE_INVALID  pullback deeper than pullback_max, liquidity or volume gone, or Risk > 60
Price up + volume up alone is NOT a setup.
"""
from __future__ import annotations

from dataclasses import dataclass

from trading.setup_common import SetupScore, clamp, combine, ramp

SETUP = "SECOND_WAVE"
POST_MIGRATED, FIRST_PUMP, PULLBACK, SUPPORT, READY, INVALID = (
    "POST_MIGRATED", "FIRST_PUMP", "PULLBACK", "SUPPORT", "SECOND_WAVE_READY", "SECOND_WAVE_INVALID")
WEIGHTS = {"structure": 0.20, "support": 0.15, "volume_retention": 0.15, "liquidity_retention": 0.15,
           "buy_pressure": 0.15, "reentry": 0.10, "smart_money": 0.05, "whale": 0.05}


@dataclass
class PostState:
    state: str = POST_MIGRATED
    base_price: float | None = None
    first_pump_peak: float | None = None
    peak_ts: float | None = None
    pullback_low: float | None = None
    low_ts: float | None = None
    pullback_pct: float | None = None
    pullback_duration_s: float | None = None
    volume_retention: float | None = None
    liquidity_retention: float | None = None
    buy_pressure_after_pullback: float | None = None
    bounce_pct: float | None = None
    support_s: float | None = None
    reason: str = ""


def post_state(points: list, now: float, cfg, risk: int | None) -> PostState:
    """points: [(ts, price, liq, vol_5m, buy_share)] of the post-migration pair, any order; only ts <= now used."""
    pts = sorted(p for p in points if p[0] <= now and p[1])
    s = PostState()
    if len(pts) < 3 or pts[-1][0] - pts[0][0] < cfg.second_wave_min_history_s:
        s.reason = "not enough post-migration history"
        return s
    s.base_price = pts[0][1]
    i_peak = max(range(len(pts)), key=lambda i: pts[i][1])
    peak_ts, peak = pts[i_peak][0], pts[i_peak][1]
    s.first_pump_peak, s.peak_ts = peak, peak_ts
    if peak < s.base_price * (1 + cfg.first_pump_min_pct / 100):
        s.reason = "no first pump yet"
        return s
    after = pts[i_peak:]
    i_low = min(range(len(after)), key=lambda i: after[i][1])
    low_ts, low = after[i_low][0], after[i_low][1]
    cur = pts[-1]
    s.pullback_low, s.low_ts = low, low_ts
    s.pullback_pct = 100 * (1 - low / peak)
    s.pullback_duration_s = low_ts - peak_ts
    pre_vol = [p[3] for p in pts[: i_peak + 1] if p[3] is not None]
    if pre_vol and cur[3] is not None and max(pre_vol) > 0:
        s.volume_retention = cur[3] / max(pre_vol)
    peak_liq = pts[i_peak][2]
    if peak_liq and cur[2] is not None:
        s.liquidity_retention = cur[2] / peak_liq
    s.buy_pressure_after_pullback = cur[4]
    s.bounce_pct = 100 * (cur[1] / low - 1) if low else None
    s.support_s = now - low_ts
    if s.pullback_pct < cfg.pullback_min_pct:
        s.state, s.reason = FIRST_PUMP, f"near the first-pump peak (pullback {s.pullback_pct:.1f}%)"
        return s
    bad = []
    if s.pullback_pct > cfg.pullback_max_pct:
        bad.append(f"pullback {s.pullback_pct:.0f}% > {cfg.pullback_max_pct:.0f}% (structure broken)")
    if s.liquidity_retention is not None and s.liquidity_retention < cfg.liquidity_retention_min:
        bad.append(f"liquidity retention {s.liquidity_retention:.2f}")
    if s.volume_retention is not None and s.volume_retention < cfg.volume_retention_min:
        bad.append(f"volume retention {s.volume_retention:.2f}")
    if risk is not None and risk > 60:
        bad.append(f"Risk {risk} > 60")
    if bad:
        s.state, s.reason = INVALID, "; ".join(bad)
        return s
    if s.support_s < cfg.support_min_s:
        s.state, s.reason = PULLBACK, f"pulling back, low {s.support_s:.0f}s ago"
        return s
    reentry = (s.bounce_pct or 0) >= cfg.bounce_min_pct and (s.buy_pressure_after_pullback or 0) >= cfg.reentry_buy_share
    if reentry and s.volume_retention is not None and s.liquidity_retention is not None:
        s.state, s.reason = READY, f"support {s.support_s:.0f}s, bounce {s.bounce_pct:.1f}%, buyers back"
    else:
        s.state, s.reason = SUPPORT, "holding the low, no buyer re-entry yet"
    return s


def score(ps: PostState, f: dict) -> SetupScore:
    comps = {"structure": None, "support": None, "volume_retention": None, "liquidity_retention": None,
             "buy_pressure": None, "reentry": None, "smart_money": None, "whale": None}
    if ps.pullback_pct is not None and ps.state != FIRST_PUMP:
        comps["structure"] = clamp(1 - abs(ps.pullback_pct - 33) / 33)
    if ps.support_s is not None and ps.state not in (POST_MIGRATED, FIRST_PUMP):
        comps["support"] = ramp(ps.support_s, 60, 300)
    if ps.volume_retention is not None:
        comps["volume_retention"] = ramp(ps.volume_retention, 0.15, 0.6)
    if ps.liquidity_retention is not None:
        comps["liquidity_retention"] = ramp(ps.liquidity_retention, 0.5, 1.0)
    if ps.buy_pressure_after_pullback is not None:
        comps["buy_pressure"] = ramp(ps.buy_pressure_after_pullback, 0.5, 0.75)
    if ps.bounce_pct is not None and ps.state not in (POST_MIGRATED, FIRST_PUMP):
        comps["reentry"] = ramp(ps.bounce_pct, 3, 25)
    ws = f.get("whale_state")
    comps["whale"] = None if ws in (None, "UNKNOWN", "") else {"ACCUMULATION": 1.0, "NEUTRAL": 0.5,
                                                               "DISTRIBUTION": 0.0}.get(ws, 0.5)
    s = combine(SETUP, comps, WEIGHTS)
    if ps.state != READY:
        s.blocks.append(f"second_wave_state:{ps.state}")
    s.extra = {"post_state": ps.__dict__, "smart_money_reentry": None}
    return s
