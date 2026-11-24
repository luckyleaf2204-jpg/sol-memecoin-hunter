"""Caller leaderboard and A-D grade. Only a channel's FIRST call of a token is scored (repeats are
counted apart), only calls with a priced entry and a finalised 24 h outcome enter the rates, and a
channel is never graded A below MIN_SAMPLE scored calls."""
from __future__ import annotations

import statistics

MIN_SAMPLE = 30           # finalised first calls needed before grade A / C / D
WIN_MULTIPLE = 2.0        # a call "wins" when the price reaches 2x within 24 h
EARLY_MAX_RUN = 0.5       # early call: price had risen < 50 % over the 60 min before the call
LATE_MIN_6H = 3.0         # late call: price was already >= 3x its 6 h low ...
LATE_DUMP = -0.3          # ... and fell >= 30 % within 24 h


def _r(x, d=3):
    return round(x, d) if x is not None else None


def channel_stats(calls: list[dict]) -> dict:
    """calls: rows of tg_calls joined with tg_call_outcomes and tg_call_sm (dicts)."""
    total = len(calls)
    valid = [c for c in calls if c["status"] != "invalid"]
    first = [c for c in valid if not c["is_repeat"]]
    scored = [c for c in first if c.get("ath_multiple") is not None and c.get("finalized")]
    n = len(scored)
    rois = [float(c["roi_24h"]) for c in scored if c.get("roi_24h") is not None]
    win = [c for c in scored if float(c["ath_multiple"]) >= WIN_MULTIPLE]
    x3 = [c for c in scored if float(c["ath_multiple"]) >= 3]
    rug = [c for c in scored if c.get("rugged")]
    early_known = [c for c in scored if c.get("pre_call_run") is not None]
    early = [c for c in early_known if float(c["pre_call_run"]) < EARLY_MAX_RUN]
    late_known = [c for c in scored if c.get("pre_call_6h_mult") is not None and c.get("roi_24h") is not None]
    late = [c for c in late_known if float(c["pre_call_6h_mult"]) >= LATE_MIN_6H and float(c["roi_24h"]) <= LATE_DUMP]
    tx2 = [(c["x2_at"] - c["called_at"]).total_seconds() / 60 for c in scored if c.get("x2_at")]
    dd = [float(c["max_drawdown"]) for c in scored if c.get("max_drawdown") is not None]
    sm = [c for c in scored if c.get("wallets_known")]
    sm_before = [c for c in sm if (c.get("buyers_before") or 0) > 0]
    sm_after = [c for c in sm if (c.get("buyers_after") or 0) > 0]
    sm_dump = [c for c in sm if (c.get("sellers_30m") or 0) > 0]
    corr = None
    if sm:
        corr = max(0.0, min(1.0, 0.5 * len(sm_before) / len(sm) + 0.5 * len(sm_after) / len(sm) - 0.5 * len(sm_dump) / len(sm)))
    s = {"total_calls": total, "valid_calls": len(valid), "first_calls": len(first), "repeat_calls": len(valid) - len(first),
         "scored_calls": n, "pending_or_unpriced": len(first) - n,
         "win_rate": _r(len(win) / n) if n else None, "x2_rate": _r(len(win) / n) if n else None,
         "x3_rate": _r(len(x3) / n) if n else None,
         "avg_roi_24h": _r(statistics.mean(rois)) if rois else None, "median_roi_24h": _r(statistics.median(rois)) if rois else None,
         "avg_ath_multiple": _r(statistics.mean(float(c["ath_multiple"]) for c in scored)) if n else None,
         "median_max_drawdown": _r(statistics.median(dd)) if dd else None, "worst_drawdown": _r(min(dd)) if dd else None,
         "rug_rate": _r(len(rug) / n) if n else None,
         "early_call_rate": _r(len(early) / len(early_known)) if early_known else None,
         "late_dump_rate": _r(len(late) / len(late_known)) if late_known else None,
         "avg_minutes_to_x2": _r(statistics.mean(tx2), 1) if tx2 else None,
         "sm_checked": len(sm), "sm_before_rate": _r(len(sm_before) / len(sm)) if sm else None,
         "sm_after_rate": _r(len(sm_after) / len(sm)) if sm else None, "sm_dump_rate": _r(len(sm_dump) / len(sm)) if sm else None,
         "caller_sm_correlation": _r(corr)}
    s["grade"], s["grade_reason"] = grade(s)
    return s


def grade(s: dict) -> tuple[str, str]:
    n = s["scored_calls"]
    if n < MIN_SAMPLE:
        bad = (s["rug_rate"] or 0) >= 0.5 and n >= 10
        if bad:
            return "D", f"{n} kèo đã chấm (chưa đủ {MIN_SAMPLE}) nhưng rug {s['rug_rate']:.0%}"
        return "B", f"chưa đủ mẫu: {n}/{MIN_SAMPLE} kèo có kết quả 24 giờ"
    if ((s["late_dump_rate"] or 0) >= 0.4 or (s["rug_rate"] or 0) >= 0.4
            or (s["median_roi_24h"] is not None and s["median_roi_24h"] < -0.3)):
        return "D", "gọi trễ / rug cao / ROI trung vị < -30 %"
    if ((s["win_rate"] or 0) >= 0.4 and (s["median_roi_24h"] or -1) > 0 and (s["rug_rate"] or 1) <= 0.2
            and (s["early_call_rate"] or 0) >= 0.5):
        return "A", "đủ mẫu, thắng ≥ 40 %, ROI trung vị dương, rug ≤ 20 %, gọi sớm ≥ 50 %"
    return "C", "đủ mẫu, kết quả trung bình hoặc không ổn định"
