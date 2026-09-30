"""EARLY SIGNAL ENGINE — detects the transition LOW ACTIVITY → EARLY MOMENTUM.

Every signal compares the token with ITSELF at an earlier time (validated snapshots only).
A current absolute value alone can never fire a signal.

Reference times: "5m ago" = snapshot closest to now−300 s (±75 s), "10m ago" = now−600 s (±150 s).
D5 — volume / txns / buys / liquidity are PAIR-SPECIFIC: they are only compared with snapshots of the
SAME DexScreener pair. After a graduation / migration (pair change) they are UNKNOWN ("data break")
until the new pair has its own history. Price / MC are token-level and compare across pairs.

Signals (weight, fires when, strength 0-1 = linear ramp):
  volume_accel     25  vol5m(now)/vol5m(5m ago, same pair) ≥ 2.0, vol5m ≥ $2K, and DIRECTION OK (D1)   ramp 1.5 → 5.0
  txn_accel        15  txns5m ratio (same pair) ≥ 1.8, txns ≥ 15, and DIRECTION OK (D1)                  ramp 1.3 → 4.0
  buy_pressure     15  buy share = buys/(buys+sells) (D2): share(now) − share(5m ago) ≥ +10 pp and
                       share(now) ≥ 55 % (≈ the former B/S ≥ 1.2)                                        ramp 5 → 30 pp
  mc_accel         15  MC growth last 5m ≥ 15 % and > MC growth of the 5m before                         ramp 10 → 60 %
  holder_accel     20  holder growth last 5m ≥ 5 %, > growth of the 5m before, and ≥ +10 holders (D7)   ramp 3 → 20 %
                       eligible only with valid holder data (D6) and ≥ 50 holders (D7)
  liquidity_growth 10  liquidity(now)/liquidity(10m ago, same pair) − 1 ≥ 10 %                           ramp 5 → 50 %
  whale_accum      10  whale state ACCUMULATION with valid holder data, ≥ 50 holders and ≥ +10 holders
                       over the same window (D3/D7); never credited if holders are SUSPICIOUS or the
                       token's data is INVALID (D8)                                                      ramp Δ 1 → 5 % supply
  smart_money / social_accel / narrative_accel — NOT AVAILABLE (weight 0, never counted)

DIRECTION OK (D1) = buy share(now) ≥ 50 %  OR  MC growth last 5m ≥ 0 %. A volume/txn spike during a sell-off
(buy share < 50 % AND MC falling) does not fire. If neither can be measured the signal is UNKNOWN.

fired=None (UNKNOWN / not eligible) removes the signal's weight from the denominator; fired=False counts as 0.

TRANSITION (from low activity, unchanged): baseline = median vol5m of SAME-PAIR snapshots 10-30 min ago
  (needs ≥ 3 points); transition = vol5m(now) ≥ 3 × baseline and baseline < $10K.

  STRENGTH = round(100 × Σ weight·strength (fired) / Σ weight (computable))
  EARLY SIGNAL = TRUE iff transition AND ≥ 3 signals fired AND strength ≥ 50 AND data not INVALID
                 AND NOT SUPPRESSED (D4)
  D4 SUPPRESSED (reasons always recorded in `suppressed`, they block EARLY) when: any RUG-category risk flag (liquidity shock, dev dump, recent dev sell),
                      top-10 concentration > 35 % (valid holder data), or risk score > 60.
  UNKNOWN (strength None) when the token has < 10 min of history, or when fewer than 4 of the 7 implemented
  signal groups (volume, txn, buy pressure, MC, holder, liquidity, whale) are computable (MIN_COVERAGE):
  a score built from 1-3 components is not reported — it is UNKNOWN, never 0 and never high.
"""
from __future__ import annotations

import statistics

from core.models import INVALID, EarlySignal, RiskResult, SignalHit, TokenState
from history.store import TokenHistory
from intel.metrics import MetricBuilder, pct_change

MIN_HISTORY_S = 600
MIN_FIRED = 3
MIN_COMPUTABLE_GROUPS = 4   # of the 7 implemented groups (WEIGHTS)
MIN_STRENGTH = 50
BASELINE_MULT = 3.0
BASELINE_LOW_USD = 10_000
MIN_HOLDERS = 50            # D7
MIN_HOLDER_INCREASE = 10    # D3 / D7
BUY_SHARE_DELTA = 0.10      # D2: +10 percentage points
BUY_SHARE_MIN = 0.55
DIRECTION_SHARE = 0.50      # D1
SUPPRESS_TOP10 = 35.0       # D4
SUPPRESS_RISK = 60          # D4
HIST = "snapshots (DexScreener)"

WEIGHTS = {"volume_accel": 25, "txn_accel": 15, "buy_pressure": 15, "mc_accel": 15,
           "holder_accel": 20, "liquidity_growth": 10, "whale_accum": 10}
NOT_AVAILABLE = ("smart_money", "social_accel", "narrative_accel")


def _ramp(x: float, lo: float, hi: float) -> float:
    return 0.0 if x <= lo else 1.0 if x >= hi else (x - lo) / (hi - lo)


def _hit(key, fired, strength, value="", source=HIST, note="", **raw) -> SignalHit:
    return SignalHit(key, fired, round(strength, 3) if strength is not None else None, WEIGHTS.get(key, 0),
                     value, source, note, raw)


def suppression_reasons(st: TokenState, risk: RiskResult | None) -> list[str]:
    """D4 — reasons an otherwise-qualifying EARLY must be held back."""
    out = []
    if risk:
        out += [f"rug risk: {f.key}" for f in risk.factors if f.category == "rug"]
        if risk.score > SUPPRESS_RISK:
            out.append(f"risk {risk.score} > {SUPPRESS_RISK}")
    h = st.holders
    if h and h.valid and h.top10_pct is not None and h.top10_pct > SUPPRESS_TOP10:
        out.append(f"top10 {h.top10_pct:.1f}% > {SUPPRESS_TOP10:.0f}%")
    return out


def _holder_gate(st: TokenState, h: TokenHistory) -> tuple[bool | None, str]:
    """(eligible, reason). None = no holder data at all."""
    if not st.holders or not h.holders:
        reasons = {"no_key": "no holder data: HELIUS_API_KEY not set",
                   "failed": f"Helius call failed: {st.holder_error or 'see HELIUS log'}",
                   "invalid": f"holder data INVALID: {st.holder_error}",
                   "pending": "Helius connected, token not holder-checked yet"}
        return None, reasons.get(st.holder_status, reasons["pending"])
    n = h.holders[-1].count
    if n is None:
        return None, "holder count unknown (RPC top-20 fallback only)"
    if n < MIN_HOLDERS:
        return False, f"not eligible: {n} holders < {MIN_HOLDERS}"
    return True, ""


def compute_early_signal(st: TokenState, h: TokenHistory, now: float, M: MetricBuilder | None = None,
                         holder_intel=None, whale_intel=None, risk: RiskResult | None = None) -> EarlySignal:
    hist_min = h.span_s / 60
    cur = h.latest()
    pair = cur.pair if cur and cur.pair else None
    p5v = h.at(300, now, tol=75, pair=pair)       # same pair: volume / txns / buys
    p10v = h.at(600, now, tol=150, pair=pair)     # same pair: liquidity
    p5 = h.at(300, now, tol=75)                   # token-level: MC
    p10 = h.at(600, now, tol=150)
    brk = h.last_break_ts
    hits: list[SignalHit] = []

    def no_same_pair(label: str, ago: float) -> str:
        if brk and now - brk <= ago + 150:
            return (f"DATA BREAK: pair changed {(now - brk) / 60:.1f} min ago (graduation/migration) — "
                    f"no same-pair snapshot {label}")
        return f"no snapshot {label}"

    # token-level MC growth (also used for D1 direction)
    g_now = pct_change(cur.mc, p5.mc) if cur and p5 else None
    g_prev = pct_change(p5.mc, p10.mc) if p5 and p10 else None
    share_now = cur.buy_share if cur else None
    direction_known = share_now is not None or g_now is not None
    direction_ok = (share_now is not None and share_now >= DIRECTION_SHARE) or (g_now is not None and g_now >= 0)
    direction = {"buy_share_now": round(share_now, 3) if share_now is not None else None,
                 "mc_growth_5m": round(g_now, 2) if g_now is not None else None,
                 "direction_ok": direction_ok if direction_known else None}

    def accel(key, now_v, before_v, thr_ratio, min_now, ramp, rule, fmt_now):
        if now_v is None or not before_v:
            miss = no_same_pair("5m ago (+-75s)", 300) if not p5v else f"{key.split('_')[0]} missing/zero 5m ago"
            return _hit(key, None, None, note="needs_history", now=now_v, before_5m=before_v, missing=miss)
        r = now_v / before_v
        base_ok = r >= thr_ratio and now_v >= min_now
        raw = dict(now=now_v, before_5m=before_v, ratio=round(r, 3), rule=rule, **direction)
        if base_ok and not direction_known:
            return _hit(key, None, None, note="direction_unknown", missing="direction unknown (no buys/sells, no MC)",
                        **raw)
        if base_ok and not direction_ok:
            return _hit(key, False, _ramp(r, *ramp), f"{r:.2f}×", blocked="D1 sell-off: buy share < 50% and MC falling",
                        **raw)
        return _hit(key, base_ok, _ramp(r, *ramp), f"{r:.2f}×", **raw)

    hits.append(accel("volume_accel", cur.vol_5m if cur else None, p5v.vol_5m if p5v else None, 2.0, 2_000,
                      (1.5, 5.0), "ratio>=2.0, now>=$2K, direction ok", None))
    hits.append(accel("txn_accel", cur.txns_5m if cur else None, p5v.txns_5m if p5v else None, 1.8, 15,
                      (1.3, 4.0), "ratio>=1.8, now>=15, direction ok", None))

    # D2 buy pressure = change of buy share
    s5 = p5v.buy_share if p5v else None
    if share_now is not None and s5 is not None:
        d = share_now - s5
        hits.append(_hit("buy_pressure", d >= BUY_SHARE_DELTA and share_now >= BUY_SHARE_MIN, _ramp(d, 0.05, 0.30),
                         f"{s5*100:.0f}% → {share_now*100:.0f}%", buys=cur.buys_5m, sells=cur.sells_5m,
                         buy_share_now=round(share_now, 3), buy_share_5m_ago=round(s5, 3), delta_pp=round(d * 100, 1),
                         rule="buy share +>=10pp and >=55%"))
    else:
        hits.append(_hit("buy_pressure", None, None, note="needs_history",
                         buys=cur.buys_5m if cur else None, sells=cur.sells_5m if cur else None,
                         missing=no_same_pair("5m ago (+-75s)", 300) if not p5v else "no transactions now or 5m ago"))

    # MC acceleration (token-level)
    if g_now is not None and g_prev is not None:
        hits.append(_hit("mc_accel", g_now >= 15 and g_now > g_prev, _ramp(g_now, 10, 60),
                         f"{g_prev:+.1f}% → {g_now:+.1f}%", mc_now=cur.mc, mc_5m_ago=p5.mc, mc_10m_ago=p10.mc,
                         growth_last_5m=round(g_now, 2), growth_prev_5m=round(g_prev, 2),
                         rule="growth_last_5m>=15% and > growth_prev_5m"))
    else:
        hits.append(_hit("mc_accel", None, None, note="needs_history",
                         missing="no snapshot 5m and 10m ago" if not (p5 and p10) else "MC missing/invalid"))

    # holder acceleration (D6 validity is enforced upstream; D7 eligibility here)
    hi = holder_intel
    eligible, why = _holder_gate(st, h)
    if eligible is None or eligible is False:
        hits.append(_hit("holder_accel", None, None, source="Helius DAS snapshots",
                         note="holder_ineligible" if eligible is False else "needs_holder_history", missing=why))
    elif hi and hi.growth_5m_pct is not None and hi.prev_growth_5m_pct is not None and hi.abs_growth_5m is not None:
        base = hi.growth_5m_pct >= 5 and hi.growth_5m_pct > hi.prev_growth_5m_pct
        fired = base and hi.abs_growth_5m >= MIN_HOLDER_INCREASE
        extra = {} if fired or not base else {"blocked": f"D7 only +{hi.abs_growth_5m} holders (< {MIN_HOLDER_INCREASE})"}
        hits.append(_hit("holder_accel", fired, _ramp(hi.growth_5m_pct, 3, 20),
                         f"{hi.prev_growth_5m_pct:+.1f}% → {hi.growth_5m_pct:+.1f}%", source="Helius DAS snapshots",
                         now=hi.count_now, abs_increase_5m=hi.abs_growth_5m, growth_last_5m=round(hi.growth_5m_pct, 2),
                         growth_prev_5m=round(hi.prev_growth_5m_pct, 2),
                         rule="growth>=5%, > previous 5m, >= +10 holders, >= 50 holders", **extra))
    else:
        hits.append(_hit("holder_accel", None, None, source="Helius DAS snapshots", note="needs_holder_history",
                         missing=f"only {len(h.holders)} valid holder snapshot(s); need counts at now, -5m and -10m"))

    # liquidity growth (same pair)
    if cur and p10v and cur.liq is not None and p10v.liq:
        g = 100 * (cur.liq / p10v.liq - 1)
        hits.append(_hit("liquidity_growth", g >= 10, _ramp(g, 5, 50), f"{g:+.1f}%", now=cur.liq,
                         before_10m=p10v.liq, change_pct=round(g, 2), source_now=cur.liq_src, rule="change>=+10% same pair"))
    else:
        hits.append(_hit("liquidity_growth", None, None, note="needs_history",
                         now=cur.liq if cur else None, before_10m=p10v.liq if p10v else None,
                         missing=no_same_pair("10m ago (+-150s)", 600) if not p10v
                         else "liquidity UNKNOWN (failed validation)"))

    # whale accumulation (D3, D7, D8)
    wi = whale_intel
    if eligible is None or eligible is False:
        hits.append(_hit("whale_accum", None, None, source="holder snapshots",
                         note="holder_ineligible" if eligible is False else "needs_holder_history", missing=why))
    elif not wi or wi.state == "UNKNOWN" or wi.delta_pct is None:
        hits.append(_hit("whale_accum", None, None, source="holder snapshots", note="needs_holder_history",
                         missing="needs 2 valid holder snapshots >=2 min apart"))
    else:
        raw = dict(state=wi.state, delta_supply_pct=wi.delta_pct, holders=wi.holder_count,
                   holder_increase=wi.holder_increase, rule="ACCUMULATION, >=50 holders, >= +10 holders, not suspicious")
        blocked = None
        if st.quality is not None and st.quality.status == INVALID:
            blocked = "D8 token data INVALID"
        elif hi and hi.organic == "SUSPICIOUS":
            blocked = "D8 holder growth SUSPICIOUS"
        elif wi.holder_increase is None or wi.holder_increase < MIN_HOLDER_INCREASE:
            blocked = f"D3 holder increase {wi.holder_increase} < {MIN_HOLDER_INCREASE}"
        fired = wi.state == "ACCUMULATION" and blocked is None
        if blocked and wi.state == "ACCUMULATION":
            raw["blocked"] = blocked
        hits.append(_hit("whale_accum", fired, _ramp(wi.delta_pct, 1, 5), f"{wi.delta_pct:+.2f}% supply",
                         source="holder snapshots", **raw))
    for key in NOT_AVAILABLE:
        hits.append(SignalHit(key, None, None, 0, "", "", "not_implemented",
                              {"missing": "module not implemented - no data source"}))

    # transition from a low-activity baseline (same pair; rule unchanged)
    base_pts = [p.vol_5m for p in h.window(1800, 600, now, pair=pair) if p.vol_5m is not None]
    transition = None
    if len(base_pts) >= 3 and cur and cur.vol_5m is not None:
        baseline = statistics.median(base_pts)
        transition = baseline < BASELINE_LOW_USD and cur.vol_5m >= BASELINE_MULT * max(baseline, 1.0)

    computable = [x for x in hits if x.fired is not None]
    fired_hits = [x for x in computable if x.fired]
    denom = sum(x.weight for x in computable)
    all_w = sum(WEIGHTS.values())
    groups = sum(1 for x in computable if x.key in WEIGHTS)
    if hist_min * 60 < MIN_HISTORY_S or not denom:
        es = EarlySignal(None, None, transition, len(fired_hits), round(100 * denom / all_w), round(hist_min, 1),
                         hits, note="needs_history")
    elif groups < MIN_COMPUTABLE_GROUPS:
        es = EarlySignal(None, None, transition, len(fired_hits), round(100 * denom / all_w), round(hist_min, 1),
                         hits, note="insufficient_coverage")
    else:
        strength = round(100 * sum(x.weight * (x.strength or 0) for x in fired_hits) / denom)
        invalid = st.quality is not None and st.quality.status == INVALID
        qualifies = bool(transition) and len(fired_hits) >= MIN_FIRED and strength >= MIN_STRENGTH and not invalid
        suppressed = suppression_reasons(st, risk)     # always recorded (transparency); gates EARLY only
        es = EarlySignal(strength, qualifies and not suppressed, transition, len(fired_hits),
                         round(100 * denom / all_w), round(hist_min, 1), hits, suppressed=suppressed)
    es.groups_computable = groups
    if M:
        M.add("early_strength", es.strength, "int", "overview", "early-signal engine",
              st.stamps["market"].updated_at if "market" in st.stamps else None, derived=True, note="needs_history")
    return es
