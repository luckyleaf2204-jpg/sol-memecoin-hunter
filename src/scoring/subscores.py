"""Multi-dimension sub-scores (0-100 each). Every sub-score is built from Factors; a factor whose
input is UNKNOWN is marked unavailable and removed from the denominator — never scored as 0 or
as good. A sub-score with no available factor is None (= UNKNOWN / NOT AVAILABLE).

  sub-score = round(100 × Σ points(available) / Σ max_points(available))

MOMENTUM (absolute level and acceleration are separate factors)
  vol_accel_1h      25  vol5m vs 1h average pace          1× → 3×   (D1: 0 during a sell-off)
  vol_trend_5m      15  vol5m now vs 5 min ago, same pair 1× → 3×   (D1: 0 during a sell-off)
  buy_pressure      20  buy share buys/(buys+sells) 5m    50 % → 67 % (0 if < 10 txns)   (D2)
  txn_accel         15  txns 5m vs 1h pace                1× → 3×   (D1: 0 during a sell-off)
  sell-off (D1) = buy share < 50 % AND price change 5m < 0
  price_momentum    15  price change 5m                   0 → +30 %
  abs_volume        10  vol5m (log)                       20 % → 200 % of min-volume filter
HOLDER (needs VALID on-chain holder data — D6 — and ≥ 50 holders — D7)
  distribution      30  top-10 %: <15 → 30, <25 → 20, <35 → 10, <50 → 3, else 0
  growth_15m        25  holder count +0 → +30 % in 15 min (0 unless ≥ +10 holders, D7)
  holder_accel      15  growth last 5m − growth previous 5m: 0 → +10 pct-points
  early_retention   15  earliest observed holders still holding: 30 → 80 %
  holder_quality    15  ORGANIC 15 · SUSPICIOUS 0 · UNKNOWN unavailable
LIQUIDITY
  depth             30  liquidity 30 % → 200 % of the min-liquidity filter
  liq_mc            25  liquidity/MC 3 → 15 % (AMM only; curve = unavailable)
  liq_trend         25  GROWING 25 · STABLE 15 · FALLING 0 · SHOCK 0 · UNKNOWN unavailable
  slippage          20  $1K sell impact: ≤ 1 % → 20, ≥ 10 % → 0
DEV (verified RPC / Pump.fun data only)
  dev_holding       40  ≤3 % → 40, ≤5 % → 30, ≤10 % → 10, else 0
  dev_selling       20  HOLD 20 · PARTIAL 10 · MAJOR/SOLD 0 · initial buy unknown → unavailable
  dev_history       40  first token 20; else graduated share 0 → 33 %+ maps to 0 → 40
WHALE (only with valid data, ≥ 50 holders, holders not SUSPICIOUS, token not INVALID — D3/D7/D8)
  whale_flow       100  ACCUMULATION 80+ (by Δ, only if ≥ +10 holders over the window, else NEUTRAL) ·
                        NEUTRAL 50 · DISTRIBUTION ≤ 20
ON-CHAIN = mean of available HOLDER, DEV, WHALE, LIQUIDITY
EARLY SIGNAL = early-signal strength
SMART MONEY / SOCIAL / NARRATIVE / SECURITY = None (NOT AVAILABLE — modules not implemented)
"""
from __future__ import annotations

import math

from core.config import Settings
from core.models import Factor, SubScore, TokenState

DEX = "DexScreener"
NOT_IMPLEMENTED = ("smart_money", "social", "narrative", "security")


def lin(x: float, lo: float, hi: float, pts: float) -> float:
    if x <= lo:
        return 0.0
    if x >= hi:
        return pts
    return pts * (x - lo) / (hi - lo)


def usd(v: float) -> str:
    return f"${v/1e6:.2f}M" if v >= 1e6 else f"${v/1e3:.1f}K" if v >= 1e3 else f"${v:.0f}"


def na(key: str, max_points: float, note: str, source: str = "") -> Factor:
    return Factor(key, 0, max_points, available=False, value="", source=source, note=note)


def finish(key: str, factors: list[Factor], note_if_none: str = "") -> SubScore:
    for f in factors:
        f.points = round(f.points, 1)
    avail = [f for f in factors if f.available]
    mx = sum(f.max_points for f in avail)
    if not avail or not mx:
        return SubScore(key, None, factors, note_if_none or "no_data")
    return SubScore(key, round(100 * sum(f.points for f in avail) / mx), factors)


def momentum(st: TokenState, s: Settings, mi) -> SubScore:
    m, F = st.market, []
    if not m:
        return SubScore("momentum", None, [], "no_market")
    tx = m.txns_5m
    share = m.buys_5m / tx if tx else None
    selloff = share is not None and share < 0.5 and m.price_change_5m is not None and m.price_change_5m < 0
    tag = " (sell-off: 0)" if selloff else ""
    va = m.vol_accel
    F.append(Factor("vol_accel_1h", 0 if selloff else lin(va, 1, 3, 25), 25, value=f"{va:.2f}×{tag}", source=DEX)
             if va is not None else na("vol_accel_1h", 25, "needs_market", DEX))
    vt = mi.vol_trend_5m if mi else None
    F.append(Factor("vol_trend_5m", 0 if selloff else lin(vt, 1, 3, 15), 15, value=f"{vt:.2f}×{tag}",
                    source="snapshots") if vt is not None else na("vol_trend_5m", 15, "needs_history", "snapshots"))
    if share is None:
        F.append(na("buy_pressure", 20, "needs_market", DEX))
    else:
        F.append(Factor("buy_pressure", lin(share, 0.5, 0.667, 20) if tx >= 10 else 0, 20,
                        value=f"{share*100:.0f}% buys ({m.buys_5m}/{m.sells_5m})", source=DEX))
    ta = m.txn_accel
    F.append(Factor("txn_accel", 0 if selloff else lin(ta, 1, 3, 15), 15, value=f"{ta:.2f}×{tag}", source=DEX)
             if ta is not None else na("txn_accel", 15, "needs_market", DEX))
    pc = m.price_change_5m
    F.append(Factor("price_momentum", lin(pc, 0, 30, 15), 15, value=f"{pc:+.1f}%", source=DEX) if pc is not None
             else na("price_momentum", 15, "needs_market", DEX))
    if m.vol_5m is not None and s.min_volume_5m > 0:
        lvl = lin(math.log10(max(m.vol_5m, 1)), math.log10(s.min_volume_5m * 0.2), math.log10(s.min_volume_5m * 2), 10)
        F.append(Factor("abs_volume", lvl, 10, value=usd(m.vol_5m), source=DEX))
    else:
        F.append(na("abs_volume", 10, "needs_market", DEX))
    return finish("momentum", F)


def holder(st: TokenState, s: Settings) -> SubScore:
    h, hi = st.holders, st.holder_intel
    src = h.source if h else "Helius DAS"
    if not h or h.top10_pct is None:
        from intel.holders import holder_reason
        return SubScore("holder", None, [], holder_reason(st))
    if h.holder_count is not None and h.holder_count < 50:
        return SubScore("holder", None, [], "holder_ineligible")
    t10 = h.top10_pct
    F = [Factor("distribution", 30 if t10 < 15 else 20 if t10 < 25 else 10 if t10 < 35 else 3 if t10 < 50 else 0,
                30, value=f"top10 {t10:.1f}%", source=src)]
    g = hi.growth_15m_pct if hi else None
    g_abs = hi.abs_growth_15m if hi else None
    if g is None or g_abs is None:
        F.append(na("growth_15m", 25, "needs_holder_history", src))
    else:
        F.append(Factor("growth_15m", lin(g, 0, 30, 25) if g_abs >= 10 else 0, 25,
                        value=f"{g:+.1f}% ({g_abs:+d})", source=src))
    a = hi.accel if hi else None
    a_abs = hi.abs_growth_5m if hi else None
    if a is None or a_abs is None:
        F.append(na("holder_accel", 15, "needs_holder_history", src))
    else:
        F.append(Factor("holder_accel", lin(a, 0, 10, 15) if a_abs >= 10 else 0, 15,
                        value=f"{a:+.1f} pp ({a_abs:+d})", source=src))
    r = hi.early_retention_pct if hi else None
    F.append(Factor("early_retention", lin(r, 30, 80, 15), 15, value=f"{r:.0f}%", source=src) if r is not None
             else na("early_retention", 15, "needs_holder_history", src))
    q = hi.organic if hi else "UNKNOWN"
    F.append(Factor("holder_quality", 15 if q == "ORGANIC" else 0, 15, value=q, source=src) if q != "UNKNOWN"
             else na("holder_quality", 15, "needs_holder_history", src))
    return finish("holder", F)


def liquidity(st: TokenState, s: Settings) -> SubScore:
    m, li = st.market, st.liquidity_intel
    if not m or m.liquidity_usd is None:
        return SubScore("liquidity", None, [], "liquidity_unknown")
    src = DEX if m.liquidity_source == "dexscreener_amm" else "Pump.fun curve reserve"
    F = [Factor("depth", lin(m.liquidity_usd, s.min_liquidity * 0.3, s.min_liquidity * 2, 30), 30,
                value=usd(m.liquidity_usd), source=src)]
    if m.liquidity_source == "dexscreener_amm" and m.market_cap:
        r = m.liquidity_usd / m.market_cap
        F.append(Factor("liq_mc", lin(r, 0.03, 0.15, 25), 25, value=f"{r*100:.1f}%", source=DEX))
    else:
        F.append(na("liq_mc", 25, "curve_not_amm", src))
    state = li.state if li else "UNKNOWN"
    if state == "UNKNOWN":
        F.append(na("liq_trend", 25, "needs_history", "snapshots"))
    else:
        F.append(Factor("liq_trend", {"GROWING": 25, "STABLE": 15}.get(state, 0), 25, value=state, source="snapshots"))
    sl = li.slippage_1k_pct if li else None
    F.append(Factor("slippage", 20 - lin(sl, 1, 10, 20), 20, value=f"{sl:.2f}%", source="x*y=k") if sl is not None
             else na("slippage", 20, "needs_market", "x*y=k"))
    return finish("liquidity", F)


def dev(st: TokenState, s: Settings) -> SubScore:
    d = st.dev
    if not d or not d.balance_verified:
        return SubScore("dev", None, [], "dev_unverified")
    src = d.balance_source or "Solana RPC"
    p = d.current_pct
    F = [Factor("dev_holding", 40 if p <= 3 else 30 if p <= 5 else 10 if p <= 10 else 0, 40, value=f"{p:.2f}%", source=src)]
    if d.sold_pct is None:
        F.append(na("dev_selling", 20, "initial_buy_unknown", "PumpPortal"))
    else:
        F.append(Factor("dev_selling", {"HOLD": 20, "PARTIAL SELL": 10}.get(d.status, 0), 20,
                        value=f"{d.status} ({d.sold_pct:.0f}%)", source=src))
    if not d.history_verified or d.prev_tokens_count is None:
        F.append(na("dev_history", 40, "history_unavailable", "Pump.fun"))
    elif d.prev_tokens_count == 0:
        F.append(Factor("dev_history", 20, 40, value="first token", source="Pump.fun"))
    else:
        rate = (d.prev_graduated or 0) / d.prev_tokens_count
        F.append(Factor("dev_history", lin(rate, 0, 0.33, 40), 40,
                        value=f"{d.prev_graduated}/{d.prev_tokens_count} graduated", source="Pump.fun"))
    return finish("dev", F)


def whale(st: TokenState, s: Settings) -> SubScore:
    wi, hi = st.whale_intel, st.holder_intel
    if not wi or wi.state == "UNKNOWN" or wi.delta_pct is None:
        return SubScore("whale", None, [], "needs_holder_history")
    if wi.holder_count is None or wi.holder_count < 50:
        return SubScore("whale", None, [], "holder_ineligible")                        # D7
    if (st.quality and st.quality.status == "INVALID") or (hi and hi.organic == "SUSPICIOUS"):
        return SubScore("whale", None, [], "whale_blocked")                            # D8
    if wi.state == "ACCUMULATION" and (wi.holder_increase is None or wi.holder_increase < 10):
        return finish("whale", [Factor("whale_flow", 50, 100, value=f"ACCUMULATION {wi.delta_pct:+.2f}% "
                                       f"but holders {wi.holder_increase:+d} (< +10) → neutral",
                                       source="holder snapshots")])                    # D3
    if wi.state == "ACCUMULATION":
        pts = 80 + lin(wi.delta_pct, 1, 5, 20)
    elif wi.state == "DISTRIBUTION":
        pts = 20 - lin(-wi.delta_pct, 1, 5, 20)
    else:
        pts = 50
    return finish("whale", [Factor("whale_flow", pts, 100, value=f"{wi.state} {wi.delta_pct:+.2f}%",
                                   source="holder snapshots")])


def onchain(subs: dict[str, SubScore]) -> SubScore:
    parts = [subs[k] for k in ("holder", "dev", "whale", "liquidity") if subs.get(k) and subs[k].score is not None]
    if not parts:
        return SubScore("onchain", None, [], "no_data")
    F = [Factor(f"onchain_{p.key}", p.score, 100, value=str(p.score), source=f"score.{p.key}") for p in parts]
    return finish("onchain", F)


def early(st: TokenState) -> SubScore:
    e = st.early
    if not e or e.strength is None:
        return SubScore("early_signal", None, [], "needs_history")
    F = [Factor(f"sig_{x.key}", round(100 * x.weight * (x.strength or 0) / 100, 1) if x.fired else 0, x.weight,
                value=x.value, source=x.source)
         for x in e.signals if x.fired is not None]
    return SubScore("early_signal", e.strength, F)


def not_available(key: str) -> SubScore:
    return SubScore(key, None, [], "not_implemented")


def compute_subscores(st: TokenState, s: Settings, mi) -> dict[str, SubScore]:
    subs = {"momentum": momentum(st, s, mi), "holder": holder(st, s), "liquidity": liquidity(st, s),
            "dev": dev(st, s), "whale": whale(st, s)}
    subs["onchain"] = onchain(subs)
    subs["early_signal"] = early(st)
    for k in NOT_IMPLEMENTED:
        subs[k] = not_available(k)
    return subs
