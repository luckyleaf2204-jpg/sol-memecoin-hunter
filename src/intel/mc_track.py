"""Market-cap journey, short-term trend deltas and the MC Scenario / Reference.

All three are DISPLAY / PRIORITY data built from sourced observations only. None of them is an input to
Opportunity, Risk, Data Quality or Early Signal.

INITIAL MC (anchor) — immutable
  Set once, at discovery, from the discovery source when it reports a market cap:
    1. PumpPortal create event: marketCapSol × SOL/USD (DexScreener WSOL price)
    2. Pump.fun list/coin: usd_market_cap
  otherwise from the first VALIDATED DexScreener MC. Plausibility: finite and >= MIN_PLAUSIBLE_MC (the same
  floor the market validator uses). Once set it is never changed: later MC, a new pair after
  graduation/migration, restarts (SQLite COALESCE) and re-discovery after pruning (engine stash) all keep it.

MIGRATION / GRADUATION
  MC is a token-level value, so the journey continues across pairs. When the DexScreener pair changes, the
  first MC on the new pair is always recorded as a "migrate" milestone (whatever the % move) and the switch
  is kept in `migrations` (from/to pair, MC before/after), so the history is continuous and explicit.

MC SCENARIO / REFERENCE — not a prediction
  Round-number reference levels above the current validated MC with the multiple each would take, plus
  sourced reference points: the ATH we observed, Pump.fun's reported ATH and the creator's best previous
  token (verified history only). No probability, no target. Without a validated current MC the scenario is
  None -> UI shows "Chưa đủ dữ liệu".
"""
from __future__ import annotations

import math
import time

from core.models import INVALID, McTrack, TokenState
from history.store import TokenHistory
from validation.market import MIN_PLAUSIBLE_MC

PATH_STEP = 0.30          # record a milestone when MC moved >= 30 % from the last milestone
PATH_MIN_GAP_S = 30
PATH_MAX = 40
LADDER = (10e3, 25e3, 50e3, 100e3, 200e3, 300e3, 500e3, 1e6, 2e6, 5e6, 10e6, 25e6, 50e6, 100e6, 250e6, 500e6, 1e9)
SCENARIO_LEVELS = 3
SRC_PUMPPORTAL = "PumpPortal marketCapSol × SOL/USD (DexScreener)"
SRC_PUMPFUN = "Pump.fun usd_market_cap"
SRC_DEX = "DexScreener"


def _plausible(v) -> bool:
    return isinstance(v, (int, float)) and math.isfinite(v) and v >= MIN_PLAUSIBLE_MC


def validated_mc(st: TokenState) -> float | None:
    """Current MC only when the market observation passed validation without a critical issue."""
    m = st.market
    if not m or m.market_cap is None or m.market_cap <= 0:
        return None
    if any(i.severity == "critical" for i in st.market_issues):
        return None
    return m.market_cap


def _track(st: TokenState) -> McTrack:
    if st.mc_track is None:
        st.mc_track = McTrack(first_seen=st.info.discovered_at)
    return st.mc_track


def set_anchor(tr: McTrack, mc: float, ts: float, source: str) -> bool:
    """Write the initial MC once. Returns True only when this call set it."""
    if tr.initial_mc is not None or not _plausible(mc):
        return False
    tr.initial_mc, tr.initial_ts, tr.initial_source = mc, ts, source
    tr.path.insert(0, (ts, mc, "initial"))
    del tr.path[PATH_MAX:]
    if tr.ath_mc is None or mc > tr.ath_mc:
        tr.ath_mc, tr.ath_ts = mc, ts
    tr.dirty = True
    return True


def anchor_at_discovery(st: TokenState, sol_price: float | None) -> bool:
    """Anchor from the discovery source itself (no DexScreener needed). Sourced values only."""
    info, tr = st.info, _track(st)
    if tr.initial_mc is not None:
        return False
    ts = info.discovered_at
    if info.discovery_mc_sol and sol_price and sol_price > 0:
        if set_anchor(tr, info.discovery_mc_sol * sol_price, ts, SRC_PUMPPORTAL):
            return True
    if info.pump_usd_mc:
        return set_anchor(tr, info.pump_usd_mc, ts, SRC_PUMPFUN)
    return False


def update_mc_track(st: TokenState, now: float | None = None) -> McTrack:
    """After a validated market ingest: anchor (if still missing), migrations, ATH, >= 30 % milestones."""
    now = now or time.time()
    tr = _track(st)
    mc = validated_mc(st)
    if mc is None:
        return tr
    if tr.initial_mc is None:
        set_anchor(tr, mc, now, ("Pump.fun curve via " + SRC_DEX) if st.market.is_curve else SRC_DEX)
    pair = st.market.pair_address or ""
    migrated = bool(tr.last_pair and pair and pair != tr.last_pair)
    if migrated:
        tr.migrations.append({"ts": now, "from": tr.last_pair, "to": pair, "mc_before": tr.last_mc, "mc_after": mc})
        del tr.migrations[:-10]
        tr.path.append((now, mc, "migrate"))
        tr.dirty = True
    if pair:
        if pair != tr.last_pair:
            tr.dirty = True
        tr.last_pair = pair
    tr.last_mc = mc
    if tr.ath_mc is None or mc > tr.ath_mc:
        tr.ath_mc, tr.ath_ts, tr.dirty = mc, now, True
    last = tr.path[-1] if tr.path else None
    if not migrated and last and abs(mc / last[1] - 1) >= PATH_STEP and now - last[0] >= PATH_MIN_GAP_S:
        tr.path.append((now, mc, ""))
        tr.dirty = True
    if len(tr.path) > PATH_MAX:                 # keep the anchor, drop the oldest milestones after it
        tr.path = tr.path[:1] + tr.path[-(PATH_MAX - 1):]
    return tr


def compact_path(tr: McTrack | None, current: float | None, keep: int = 6) -> list[tuple[float, str]]:
    """[(mc, tag)]: the anchor, the most recent milestones (migrations always kept) and the current MC."""
    if not tr or not tr.path:
        return []
    pts = [(p[1], p[2] if len(p) > 2 else "") for p in tr.path]
    if current is not None and abs(current / pts[-1][0] - 1) >= 0.05:
        pts.append((current, "now"))
    if len(pts) <= keep:
        return pts
    head, rest = pts[:1], pts[1:]
    mig = [p for p in rest[:-(keep - 1)] if p[1] == "migrate"][-1:]      # last older migration stays visible
    return head + mig + rest[-(keep - 1 - len(mig)):]


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
    """MC Scenario / Reference. None = not enough data. Every number is either a round reference level
    (labelled as such) or a sourced observation; nothing is estimated or predicted."""
    mc = validated_mc(st)
    if mc is None or st.dq_status == INVALID:
        return None
    stamp = st.stamps.get("market")
    levels = [x for x in LADDER if x > mc * 1.1][:SCENARIO_LEVELS]
    tr = st.mc_track
    refs = []
    if tr and tr.ath_mc and tr.ath_mc > mc * 1.05:
        refs.append({"key": "observed_ath", "mc": tr.ath_mc, "multiple": round(tr.ath_mc / mc, 2),
                     "source": "scanner (" + SRC_DEX + " / " + (tr.initial_source or SRC_DEX) + ")"})
    if st.info.ath_usd_mc and st.info.ath_usd_mc > mc * 1.05:
        refs.append({"key": "pump_ath", "mc": st.info.ath_usd_mc, "multiple": round(st.info.ath_usd_mc / mc, 2),
                     "source": "Pump.fun ath_market_cap"})
    d = st.dev
    if d and d.history_verified and d.prev_best_ath:
        refs.append({"key": "dev_best", "mc": d.prev_best_ath, "multiple": round(d.prev_best_ath / mc, 2),
                     "source": "Pump.fun (creator history)"})
    ath_seen = max([r["mc"] for r in refs if r["key"] in ("observed_ath", "pump_ath")] + [0])
    return {
        "kind": "reference",                                    # never a prediction
        "basis": {"mc": mc, "source": stamp.source if stamp else SRC_DEX, "ts": stamp.updated_at if stamp else None},
        "levels": [{"mc": x, "multiple": round(x / mc, 2), "reached_before": ath_seen >= x, "type": "round_level"}
                   for x in levels],
        "refs": refs,
    }
