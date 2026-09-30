"""Market-cap journey, short-term trend deltas and the MC reference scenario.

All three are DISPLAY / PRIORITY data built from validated observations only. None of them is an input to
Opportunity, Risk, Data Quality or Early Signal.

MC reference scenario ("MC KỊCH BẢN / THAM CHIẾU")
  The next standard MC levels above the current validated MC, each with the multiple it would take from
  here, plus real reference points already observed: the ATH we saw, Pump.fun's reported ATH and the
  creator's best previous token (verified history only). It is NOT a prediction and carries no
  probability. Without a validated current MC (or with INVALID data) the scenario is None -> UI shows
  "Chưa đủ dữ liệu".
"""
from __future__ import annotations

import time

from core.models import INVALID, McTrack, TokenState
from history.store import TokenHistory

PATH_STEP = 0.30          # record a milestone when MC moved >= 30 % from the last milestone
PATH_MIN_GAP_S = 30
PATH_MAX = 40
LADDER = (10e3, 25e3, 50e3, 100e3, 200e3, 300e3, 500e3, 1e6, 2e6, 5e6, 10e6, 25e6, 50e6, 100e6, 250e6, 500e6, 1e9)
SCENARIO_LEVELS = 3


def validated_mc(st: TokenState) -> float | None:
    """Current MC only when the market observation passed validation without a critical issue."""
    m = st.market
    if not m or m.market_cap is None or m.market_cap <= 0:
        return None
    if any(i.severity == "critical" for i in st.market_issues):
        return None
    return m.market_cap


def update_mc_track(st: TokenState, now: float | None = None) -> McTrack:
    """Record the first validated MC (once, never overwritten), the ATH and >=30 % milestones."""
    now = now or time.time()
    tr = st.mc_track
    if tr is None:
        tr = st.mc_track = McTrack(first_seen=st.info.discovered_at)
    mc = validated_mc(st)
    if mc is None:
        return tr
    if tr.initial_mc is None:
        tr.initial_mc, tr.initial_ts = mc, now
        tr.initial_source = "Pump.fun curve (DexScreener)" if st.market.is_curve else "DexScreener"
        tr.path.append((now, mc))
        tr.dirty = True
    if tr.ath_mc is None or mc > tr.ath_mc:
        tr.ath_mc, tr.ath_ts, tr.dirty = mc, now, True
    last_ts, last_mc = tr.path[-1] if tr.path else (0.0, None)
    if last_mc and abs(mc / last_mc - 1) >= PATH_STEP and now - last_ts >= PATH_MIN_GAP_S:
        tr.path.append((now, mc))
        del tr.path[:-PATH_MAX]
        tr.dirty = True
    return tr


def compact_path(tr: McTrack | None, current: float | None, keep: int = 6) -> list[float]:
    """First milestone, the most recent ones, and the current MC — e.g. $8K → $25K → $67K → $125K."""
    if not tr or not tr.path:
        return []
    vals = [mc for _, mc in tr.path]
    if current is not None and (not vals or abs(current / vals[-1] - 1) >= 0.05):
        vals = vals + [current]
    if len(vals) <= keep:
        return vals
    return [vals[0]] + vals[-(keep - 1):]


def compute_trend(st: TokenState, h: TokenHistory, now: float | None = None) -> dict:
    """Short-term deltas vs ~5 min ago, same-pair for pair-specific values (D5). None when not measurable."""
    now = now or time.time()
    out: dict = {"mc_chg_5m_pct": None, "vol_chg_5m_pct": None, "buy_share": None, "buy_pp_5m": None,
                 "txns_5m": None}
    last = h.latest() if h else None
    if not last:
        return out
    pair = last.pair or None
    p5 = h.at(300, now, tol=75)
    p5s = h.at(300, now, tol=75, pair=pair)
    if p5 and p5.mc and last.mc is not None:
        out["mc_chg_5m_pct"] = round(100 * (last.mc / p5.mc - 1), 1)
    if p5s and p5s.vol_5m and last.vol_5m is not None:
        out["vol_chg_5m_pct"] = round(100 * (last.vol_5m / p5s.vol_5m - 1), 1)
    if last.buy_share is not None:
        out["buy_share"] = round(100 * last.buy_share, 1)
        if p5s and p5s.buy_share is not None:
            out["buy_pp_5m"] = round(100 * (last.buy_share - p5s.buy_share), 1)
    out["txns_5m"] = last.txns_5m
    return out


def mc_scenario(st: TokenState) -> dict | None:
    mc = validated_mc(st)
    if mc is None or st.dq_status == INVALID:
        return None
    levels = [x for x in LADDER if x > mc * 1.1][:SCENARIO_LEVELS]
    tr = st.mc_track
    refs = []
    if tr and tr.ath_mc and tr.ath_mc > mc * 1.05:
        refs.append({"key": "observed_ath", "mc": tr.ath_mc, "multiple": round(tr.ath_mc / mc, 2),
                     "source": "scanner (validated DexScreener)"})
    if st.info.ath_usd_mc and st.info.ath_usd_mc > mc * 1.05:
        refs.append({"key": "pump_ath", "mc": st.info.ath_usd_mc, "multiple": round(st.info.ath_usd_mc / mc, 2),
                     "source": "Pump.fun"})
    d = st.dev
    if d and d.history_verified and d.prev_best_ath:
        refs.append({"key": "dev_best", "mc": d.prev_best_ath, "multiple": round(d.prev_best_ath / mc, 2),
                     "source": "Pump.fun (creator history)"})
    ath_seen = max([r["mc"] for r in refs if r["key"] in ("observed_ath", "pump_ath")] + [0])
    return {
        "current": mc,
        "levels": [{"mc": x, "multiple": round(x / mc, 2), "reached_before": ath_seen >= x} for x in levels],
        "refs": refs,
        "liquidity": st.market.liquidity_usd if st.market else None,
    }
