"""Outcome of a call from real 1-minute candles: ATH, drawdown, time to 2x/3x/5x, rug, pre-call run."""
from __future__ import annotations

import datetime as dt

UTC = dt.timezone.utc
MARKS_MIN = [0, 5, 15, 30, 60, 120, 360, 720, 1440]
RUG_PRICE = 0.10          # price <= 10 % of the entry
RUG_LIQUIDITY = 0.20      # liquidity <= 20 % of the liquidity at the call


def compute(entry: float, call_ts: int, candles: list[list], liq_entry: float | None = None,
            liq_last: float | None = None) -> dict:
    """candles: [[ts, o, h, l, c, v], ...] oldest first, covering call-6h .. call+24h (USD)."""
    before = [c for c in candles if c[0] < call_ts - 59]
    after = [c for c in candles if call_ts - 59 <= c[0] <= call_ts + 86400]
    res: dict = {"candles": len(after), "coverage": round(len(after) / 1440, 3)}
    if not after or not entry:
        return res
    ath, ath_ts, low = entry, call_ts, entry
    hits = {2: None, 3: None, 5: None}
    for c in after:
        if c[2] > ath:
            ath, ath_ts = c[2], c[0]
        low = min(low, c[3])
        for k in hits:
            if hits[k] is None and c[2] >= k * entry:
                hits[k] = c[0]
    last = after[-1]
    res.update(ath_price_usd=ath, ath_at=dt.datetime.fromtimestamp(ath_ts, UTC), ath_multiple=ath / entry,
               min_price_usd=low, max_drawdown=low / entry - 1,
               roi_24h=(last[4] / entry - 1) if last[0] >= call_ts + 86400 - 3600 else None,
               x2_at=dt.datetime.fromtimestamp(hits[2], UTC) if hits[2] else None,
               x3_at=dt.datetime.fromtimestamp(hits[3], UTC) if hits[3] else None,
               x5_at=dt.datetime.fromtimestamp(hits[5], UTC) if hits[5] else None)
    rug_reason = None
    if low <= RUG_PRICE * entry:
        rug_reason = f"giá giảm còn {low / entry:.0%} giá lúc gọi"
    if liq_entry and liq_last is not None and liq_last <= RUG_LIQUIDITY * liq_entry:
        rug_reason = (rug_reason + "; " if rug_reason else "") + f"thanh khoản còn {liq_last / liq_entry:.0%}"
    res.update(rugged=rug_reason is not None, rug_reason=rug_reason)
    h1 = [c for c in before if c[0] >= call_ts - 3600]
    h6 = [c for c in before if c[0] >= call_ts - 6 * 3600]
    if h1:
        res["pre_call_run"] = entry / min(c[3] for c in h1) - 1
    if h6:
        res["pre_call_6h_mult"] = entry / min(c[3] for c in h6)
    return res


def candle_at(candles: list[list], ts: int, max_age_s: int = 1800) -> list | None:
    """Price in force at ts: the candle of ts's minute, else the last candle BEFORE it (a minute with
    no trade keeps the last traded price) if it is at most `max_age_s` old. Never a later candle."""
    m = ts - ts % 60
    best = None
    for c in candles:
        if c[0] > m:
            break
        best = c
    if best is None or m - best[0] > max_age_s:
        return None
    return best
