"""PRE-EARLY — breakout hints for brand-new tokens (1–3 minutes old).

A separate layer. It does NOT change Early Signal / D1–D8 (which needs >= 10 min of history and stays
UNKNOWN for such young tokens): it only reads data the pipeline already validated, and never turns an
UNKNOWN input into a signal.

ELIGIBLE      token age 1.0–3.0 min (created_at, else DexScreener pair creation). Unknown age -> NOT_ELIGIBLE.
BLOCKED       identity not VERIFIED (validation.identity) · Data Quality INVALID because data is WRONG
              (malformed / inconsistent — INVALID only because data is still MISSING, e.g. not yet indexed by
              DexScreener, gives UNKNOWN instead, never PRE_EARLY) · Risk > 60 · any RUG-category
              risk flag · holder data INVALID (D6) · top10 > 35 % (when known)
SIGNALS       from the in-memory history of validated observations (last 3 min); pair-specific values are only
              compared within the same DexScreener pair (D5 rule), MC is token-level:
  mc_velocity      MC +40 % or more over >= 40 s, at >= +25 %/min
  volume_accel     5m-volume growth rate of the recent half >= 1.5× the earlier half and >= $1,000/min
                   (for a < 5 min old token the 5m volume is cumulative, so its growth = new volume)
  txn_accel        same on 5m transaction count, recent rate >= 10 tx/min
  buy_pressure     buy share >= 60 % with >= 20 transactions (fewer txns -> UNKNOWN)
  liquidity_growth liquidity +20 % or more over >= 40 s (same pair)
  holder_growth    >= 30 holders and +15 or more between two valid holder snapshots (Helius)
RESULT        UNKNOWN   < 3 of 6 signals computable ("Chưa đủ dữ liệu")
              PRE_EARLY >= 3 fired, buy_pressure fired, MC not falling
              NOT_YET   otherwise
Thresholds are fixed and shown to the user. PRE-EARLY is a research hint, not a buy recommendation.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.models import INVALID, TokenState
from scoring.groups import _missing_only
from history.store import TokenHistory

MIN_AGE_MIN, MAX_AGE_MIN = 1.0, 3.0
WINDOW_S = 180
MIN_SPAN_S = 40
MIN_COMPUTABLE, MIN_FIRED = 3, 3
RISK_BLOCK, TOP10_BLOCK = 60, 35.0
MC_GROWTH, MC_PER_MIN = 0.40, 0.25
ACCEL_RATIO, VOL_MIN_PER_MIN, TXN_MIN_PER_MIN = 1.5, 1_000.0, 10.0
BUY_SHARE, BUY_MIN_TXNS = 0.60, 20
LIQ_GROWTH = 0.20
HOLDER_MIN, HOLDER_GROWTH = 30, 15
SIGNALS = ("mc_velocity", "volume_accel", "txn_accel", "buy_pressure", "liquidity_growth", "holder_growth")


@dataclass
class PreSignal:
    key: str
    fired: bool | None          # None = UNKNOWN (not enough data) — never counted as fired
    value: str = ""
    note: str = ""              # why UNKNOWN / what the threshold is


@dataclass
class PreEarly:
    status: str                 # PRE_EARLY | NOT_YET | UNKNOWN | BLOCKED | NOT_ELIGIBLE
    age_min: float | None = None
    signals: list[PreSignal] = field(default_factory=list)
    blocked_by: list[str] = field(default_factory=list)
    fired: int = 0
    computable: int = 0
    total: int = len(SIGNALS)

    @property
    def is_pre_early(self) -> bool:
        return self.status == "PRE_EARLY"


def _data_missing(st: TokenState) -> bool:
    return st.dq_status == INVALID and _missing_only(st)


def _blockers(st: TokenState) -> list[str]:
    out = []
    if st.identity.status != "VERIFIED":
        out.append("identity_" + st.identity.status.lower())
    missing = _data_missing(st)
    if st.dq_status == INVALID and not missing:
        out.append("dq_invalid")
    rk = st.risk
    if rk and rk.score > RISK_BLOCK and not missing:     # a data-less token's Risk is mostly "data" points
        out.append("risk_high")
    if rk and any(f.category == "rug" for f in rk.factors):
        out.append("rug_flag")
    if st.holder_status == "invalid":
        out.append("holder_anomaly")
    h = st.holders
    if h and h.valid and h.top10_pct is not None and h.top10_pct > TOP10_BLOCK:
        out.append("top10")
    return out


def _half_rates(pts, attr):
    """(earlier rate, recent rate) per second of a cumulative value, or None if not measurable."""
    pts = [p for p in pts if getattr(p, attr) is not None]
    if len(pts) < 3 or pts[-1].ts - pts[0].ts < 60:
        return None
    mid = pts[len(pts) // 2]
    a, b = mid.ts - pts[0].ts, pts[-1].ts - mid.ts
    if a <= 0 or b <= 0:
        return None
    d1, d2 = getattr(mid, attr) - getattr(pts[0], attr), getattr(pts[-1], attr) - getattr(mid, attr)
    if d1 < 0 or d2 < 0:                       # a cumulative value never falls: inconsistent -> UNKNOWN
        return None
    return d1 / a, d2 / b


def _accel(key, pts, attr, min_per_min, unit):
    r = _half_rates(pts, attr)
    if r is None:
        return PreSignal(key, None, note="need >= 3 same-pair points over >= 60 s")
    before, now = r[0] * 60, r[1] * 60
    fired = now >= min_per_min and (before == 0 or now >= ACCEL_RATIO * before)
    fmt = (lambda v: f"${v:,.0f}") if unit == "usd" else (lambda v: f"{v:,.1f}")
    return PreSignal(key, fired, f"{fmt(before)}/min -> {fmt(now)}/min",
                     f">= {ACCEL_RATIO}× and >= {fmt(min_per_min)}/min")


def compute_pre_early(st: TokenState, h: TokenHistory | None, now: float | None = None) -> PreEarly:
    now = now or time.time()
    age = st.age_minutes
    if age is None or not (MIN_AGE_MIN <= age <= MAX_AGE_MIN):
        return PreEarly("NOT_ELIGIBLE", age_min=round(age, 2) if age is not None else None)
    res = PreEarly("UNKNOWN", age_min=round(age, 2))
    res.blocked_by = _blockers(st)

    pts = [p for p in (h.points if h else []) if p.ts >= now - WINDOW_S]
    last = pts[-1] if pts else None
    pair = last.pair if last else ""
    same = [p for p in pts if p.pair == pair] if pair else pts
    sig: dict[str, PreSignal] = {}

    mcs = [p for p in pts if p.mc is not None]
    if len(mcs) >= 2 and mcs[-1].ts - mcs[0].ts >= MIN_SPAN_S and mcs[0].mc > 0:
        g = mcs[-1].mc / mcs[0].mc - 1
        per_min = g / ((mcs[-1].ts - mcs[0].ts) / 60)
        sig["mc_velocity"] = PreSignal("mc_velocity", g >= MC_GROWTH and per_min >= MC_PER_MIN,
                                       f"{100 * g:+.0f}% in {mcs[-1].ts - mcs[0].ts:.0f}s ({100 * per_min:+.0f}%/min)",
                                       f">= +{100 * MC_GROWTH:.0f}% and >= +{100 * MC_PER_MIN:.0f}%/min")
    else:
        sig["mc_velocity"] = PreSignal("mc_velocity", None, note="need 2 validated MC points over >= 40 s")
    sig["volume_accel"] = _accel("volume_accel", same, "vol_5m", VOL_MIN_PER_MIN, "usd")
    sig["txn_accel"] = _accel("txn_accel", same, "txns_5m", TXN_MIN_PER_MIN, "n")

    if last and last.txns_5m is not None and last.buy_share is not None and last.txns_5m >= BUY_MIN_TXNS:
        sig["buy_pressure"] = PreSignal("buy_pressure", last.buy_share >= BUY_SHARE,
                                        f"{100 * last.buy_share:.0f}% buys of {last.txns_5m} txns",
                                        f">= {100 * BUY_SHARE:.0f}% with >= {BUY_MIN_TXNS} txns")
    else:
        sig["buy_pressure"] = PreSignal("buy_pressure", None, note=f"need >= {BUY_MIN_TXNS} txns with buy/sell split")

    liqs = [p for p in same if p.liq is not None and p.liq > 0]
    if len(liqs) >= 2 and liqs[-1].ts - liqs[0].ts >= MIN_SPAN_S:
        g = liqs[-1].liq / liqs[0].liq - 1
        sig["liquidity_growth"] = PreSignal("liquidity_growth", g >= LIQ_GROWTH,
                                            f"${liqs[0].liq:,.0f} -> ${liqs[-1].liq:,.0f} ({100 * g:+.0f}%)",
                                            f">= +{100 * LIQ_GROWTH:.0f}%")
    else:
        sig["liquidity_growth"] = PreSignal("liquidity_growth", None, note="need 2 same-pair liquidity points over >= 40 s")

    snaps = [s for s in (h.holders if h else []) if s.count is not None and s.ts >= now - WINDOW_S]
    holders_ok = st.holders is not None and st.holders.valid and st.holder_status == "ok"
    if holders_ok and len(snaps) >= 2 and snaps[-1].ts > snaps[0].ts:
        d = snaps[-1].count - snaps[0].count
        sig["holder_growth"] = PreSignal("holder_growth", snaps[-1].count >= HOLDER_MIN and d >= HOLDER_GROWTH,
                                         f"{snaps[0].count} -> {snaps[-1].count} holders ({d:+d})",
                                         f">= {HOLDER_MIN} holders and >= +{HOLDER_GROWTH}")
    else:
        sig["holder_growth"] = PreSignal("holder_growth", None, note="need 2 valid Helius holder snapshots")

    res.signals = [sig[k] for k in SIGNALS]
    res.computable = sum(1 for s in res.signals if s.fired is not None)
    res.fired = sum(1 for s in res.signals if s.fired is True)
    if res.blocked_by:
        res.status = "BLOCKED"
    elif res.computable < MIN_COMPUTABLE or _data_missing(st):     # missing data is never a signal
        res.status = "UNKNOWN"
    else:
        mc_falling = len(mcs) >= 2 and mcs[-1].mc < mcs[0].mc
        ok = res.fired >= MIN_FIRED and sig["buy_pressure"].fired is True and not mc_falling
        res.status = "PRE_EARLY" if ok else "NOT_YET"
    return res
