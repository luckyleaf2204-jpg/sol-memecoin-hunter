"""Liquidity intelligence from validated liquidity snapshots.

State (needs ≥ 2 validated points spanning ≥ 3 min, else UNKNOWN):
  SHOCK    liquidity fell ≥ 30 % between any two points ≤ 5 min apart in the last 15 min
  FALLING  15-min change ≤ -5 %
  GROWING  15-min change ≥ +5 %
  STABLE   otherwise
Slippage is CALCULATED with the constant-product formula (x·y=k) on the reported pool:
  sell impact ≈ X / (Q + X), Q = quote-side depth in USD
  AMM:   Q = liquidity / 2 (DexScreener liquidity counts both sides)
  Curve: Q = virtual SOL reserve × SOL price (Pump.fun curve is constant-product on virtual reserves)
LP concentration / LP add-remove transactions are not observable from these sources -> UNKNOWN.
"""
from __future__ import annotations

from core.models import LiquidityIntel, TokenState
from history.store import TokenHistory
from intel.metrics import MetricBuilder, pct_change

SHOCK_DROP_PCT = 30
TREND_PCT = 5
VIRTUAL_SOL_BASE = 30.0      # Pump.fun classic curve: virtual SOL reserve = 30 SOL + real SOL in the curve
from pumpfun.client import SOL_QUOTES  # noqa: E402  (native SOL "1111…1111" and wrapped SOL)


def depth_points(points, cur, now: float, info=None, sol_price: float | None = None):
    """Liquidity points comparable with the CURRENT one, as the pool's actual depth (audit 2026-10-01):
    * only the same pair AND the same liquidity source (a curve reserve is never compared with an AMM pool, a new
      pair after graduation / migration starts a new baseline);
    * AMM (DexScreener): reported liquidity as is;
    * bonding curve: the 'liquidity' is the REAL SOL in the curve, which every ordinary sell removes; the price /
      slippage depth of a constant-product curve is the VIRTUAL reserve (real + virtual base). A shock is therefore
      measured on (real + base) x SOL price. Base = virtual - real reserves when Pump.fun reports both, else 30 SOL.
      Non-SOL quote curves or no SOL price -> None (UNKNOWN, never a shock by assumption).
    Returns (basis, [(ts, depth)]) or (basis, None)."""
    if cur is None or cur.liq is None:
        return "", None
    same = [p for p in points if p.ts >= now - 900 and p.liq is not None and p.pair == cur.pair
            and p.liq_src == cur.liq_src]
    if cur.liq_src == "dexscreener_amm":
        return "amm", [(p.ts, p.liq) for p in same]
    if cur.liq_src == "pumpfun_curve":
        if not sol_price or (info is not None and info.quote_mint and info.quote_mint not in SOL_QUOTES):
            return "curve_depth", None
        base_sol = VIRTUAL_SOL_BASE
        if info is not None and info.virtual_sol_reserves and info.real_sol_reserves is not None                 and info.virtual_sol_reserves > info.real_sol_reserves:
            base_sol = info.virtual_sol_reserves - info.real_sol_reserves
        base = base_sol * sol_price
        return "curve_depth", [(p.ts, p.liq + base) for p in same]
    return "", None


def impact_pct(size_usd: float, quote_depth_usd: float | None) -> float | None:
    if not quote_depth_usd or quote_depth_usd <= 0:
        return None
    return 100 * size_usd / (quote_depth_usd + size_usd)


def classify_state(points: list[tuple[float, float]]) -> tuple[str, float | None, float | None]:
    """points = [(ts, liquidity)] oldest->newest, last 15 min. Returns (state, change_15m, max_drop_5m)."""
    if len(points) < 2 or points[-1][0] - points[0][0] < 180:
        return "UNKNOWN", None, None
    max_drop = 0.0
    for i, (ti, li) in enumerate(points):
        for tj, lj in points[i + 1:]:
            if tj - ti > 300:
                break
            if li > 0:
                max_drop = max(max_drop, 100 * (li - lj) / li)
    change = pct_change(points[-1][1], points[0][1])
    if max_drop >= SHOCK_DROP_PCT:
        state = "SHOCK"
    elif change is not None and change <= -TREND_PCT:
        state = "FALLING"
    elif change is not None and change >= TREND_PCT:
        state = "GROWING"
    else:
        state = "STABLE"
    return state, change, max_drop


def analyze_liquidity(st: TokenState, h: TokenHistory, M: MetricBuilder, sol_price: float | None) -> LiquidityIntel:
    li = LiquidityIntel()
    m, now = st.market, M.now
    ts = st.stamps["market"].updated_at if "market" in st.stamps else None
    src = "DexScreener" if m and m.liquidity_source == "dexscreener_amm" else "Pump.fun curve reserve"

    cur = h.latest()
    pair = cur.pair if cur and cur.pair else None
    raw = [(p.ts, p.liq) for p in h.points
           if p.ts >= now - 900 and p.liq is not None and (pair is None or p.pair == pair)]
    li.state_raw = classify_state(raw)[0]
    li.basis, pts = depth_points(h.points, cur, now, st.info, sol_price)
    if pts is None:
        li.state, li.change_15m_pct, li.max_drop_5m_pct = "UNKNOWN", None, None
    else:
        li.state, li.change_15m_pct, li.max_drop_5m_pct = classify_state(pts)
    p5 = h.at(300, now, pair=pair)
    if cur and p5 and cur.liq is not None and p5.liq is not None:
        li.change_5m_pct = pct_change(cur.liq, p5.liq)

    if m and m.liquidity_usd:
        if m.liquidity_source == "dexscreener_amm":
            depth = m.liquidity_usd / 2
            li.exit_liquidity_usd = depth
        else:
            virt = st.info.virtual_sol_reserves
            depth = virt * sol_price if virt and sol_price else None
            li.exit_liquidity_usd = m.liquidity_usd  # SOL actually in the curve
        li.slippage_1k_pct = impact_pct(1_000, depth)
        li.slippage_5k_pct = impact_pct(5_000, depth)
        if m.vol_1h is not None:
            li.vol_liq_ratio = m.vol_1h / m.liquidity_usd
    if m and m.pair_created_at:
        li.pool_age_min = (now - m.pair_created_at) / 60

    M.add("liq_state", li.state if li.state != "UNKNOWN" else None, "text", "liquidity",
          "snapshots", ts, derived=True, note="needs_history")
    M.add("liquidity", m.liquidity_usd if m else None, "usd", "liquidity", src, ts)
    M.add("liq_change_5m", li.change_5m_pct, "pct", "liquidity", "snapshots", ts, derived=True, note="needs_history")
    M.add("liq_change_15m", li.change_15m_pct, "pct", "liquidity", "snapshots", ts, derived=True, note="needs_history")
    M.add("liq_max_drop_5m", li.max_drop_5m_pct, "pct", "liquidity", "snapshots", ts, derived=True, note="needs_history")
    M.add("slippage_1k", li.slippage_1k_pct, "pct", "liquidity", "x*y=k", ts, derived=True, note="calc_cp")
    M.add("slippage_5k", li.slippage_5k_pct, "pct", "liquidity", "x*y=k", ts, derived=True, note="calc_cp")
    M.add("exit_liquidity", li.exit_liquidity_usd, "usd", "liquidity", src, ts, derived=True)
    M.add("pool_age", li.pool_age_min, "min", "liquidity", "DexScreener", ts)
    M.add("vol_liq", li.vol_liq_ratio, "ratio", "liquidity", "DexScreener", ts, derived=True)
    M.add("pool_type", ("curve" if m.is_curve else "amm") if m else None, "text", "liquidity", "DexScreener", ts)
    M.unknown("lp_concentration", "pct", "liquidity", "lp_not_observable")
    return li
