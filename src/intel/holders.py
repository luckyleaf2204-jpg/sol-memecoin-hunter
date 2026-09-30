"""Holder intelligence from successive on-chain holder snapshots (Helius DAS full lists).

Growth / acceleration use holder COUNTS at different times (never a single absolute value).
Churn / new holders / retention compare OWNER SETS between snapshots and are only computed
when both snapshots are complete lists (DAS); with the top-20 RPC fallback they stay UNKNOWN.

ORGANIC vs SUSPICIOUS holder growth (needs two complete snapshots ≥ 2 min apart):
  SUSPICIOUS if any flag:
    dust_new        ≥ 60 % of new holders hold < 0.001 % of supply (airdrop / sybil pattern)
    growth_gt_txns  new holders > 1.5 × buy transactions reported in the last 5 min while the
                    snapshots are ≤ 6 min apart (holders cannot appear faster than buys without transfers)
    high_churn      ≥ 30 % of the previous holders left while holder count still grew
  ORGANIC if holders grew and no flag fired; UNKNOWN otherwise.
"""
from __future__ import annotations

from core.models import HolderIntel, TokenState
from history.store import TokenHistory
from intel.metrics import MetricBuilder, pct_change

WHALE_PCT = 1.0
DUST_PCT = 0.001


def holder_reason(st: TokenState) -> str:
    """i18n note key explaining why holder data is missing — never blames the key when it is set."""
    return {"no_key": "needs_helius", "failed": "helius_failed", "pending": "holders_pending",
            "invalid": "holders_invalid"}.get(
        st.holder_status, "holders_pending" if st.holder_status else "needs_helius")


def _abs_growth(h: TokenHistory, ago_old: float) -> int | None:
    base = h.holders[-1]
    b = h.holders_at(ago_old, base.ts)
    if not b or b is base or base.count is None or b.count is None:
        return None
    return base.count - b.count


def _count_growth(h: TokenHistory, ago_new: float, ago_old: float) -> float | None:
    """Holder-count change between two moments measured back from the LATEST holder snapshot."""
    base = h.holders[-1].ts
    a = h.holders[-1] if ago_new == 0 else h.holders_at(ago_new, base)
    b = h.holders_at(ago_old, base)
    if not a or not b or a is b or a.count is None or b.count is None:
        return None
    return pct_change(a.count, b.count)


def analyze_holders(st: TokenState, h: TokenHistory, M: MetricBuilder) -> HolderIntel:
    hi = HolderIntel()
    hs, now = st.holders, M.now
    src = hs.source if hs else "Helius DAS"
    ts = hs.fetched_at if hs else None
    supply = st.info.total_supply

    if hs and h.holders:
        hi.growth_5m_pct = _count_growth(h, 0, 300)
        hi.growth_15m_pct = _count_growth(h, 0, 900)
        hi.prev_growth_5m_pct = _count_growth(h, 300, 600)
        if hi.growth_5m_pct is not None and hi.prev_growth_5m_pct is not None:
            hi.accel = hi.growth_5m_pct - hi.prev_growth_5m_pct
        hi.count_now = h.holders[-1].count
        hi.abs_growth_5m = _abs_growth(h, 300)
        hi.abs_growth_15m = _abs_growth(h, 900)
        if supply and hs.owner_amounts:
            hi.whale_count = sum(1 for a in hs.owner_amounts.values() if 100 * a / supply >= WHALE_PCT)

        cur, prev = h.holders[-1], h.previous_holders(120)
        if prev and cur.complete and prev.complete:
            new = set(cur.amounts) - set(prev.amounts)
            lost = set(prev.amounts) - set(cur.amounts)
            mins = max(0.5, (cur.ts - prev.ts) / 60)
            hi.new_holders, hi.lost_holders = len(new), len(lost)
            hi.new_per_min = len(new) / mins
            hi.churn_pct = 100 * len(lost) / len(prev.amounts) if prev.amounts else None
            if new and supply:
                dust = sum(1 for o in new if 100 * cur.amounts[o] / supply < DUST_PCT)
                hi.dust_share_new_pct = 100 * dust / len(new)
            # organic vs suspicious
            if hi.dust_share_new_pct is not None and hi.dust_share_new_pct >= 60 and len(new) >= 10:
                hi.flags.append("dust_new")
            m = st.market
            if m and m.buys_5m is not None and cur.ts - prev.ts <= 360 and len(new) >= 10 \
                    and len(new) > 1.5 * max(1, m.buys_5m):
                hi.flags.append("growth_gt_txns")
            if hi.churn_pct is not None and hi.churn_pct >= 30 and len(new) > len(lost):
                hi.flags.append("high_churn")
            grew = cur.count is not None and prev.count is not None and cur.count > prev.count
            hi.organic = "SUSPICIOUS" if hi.flags else ("ORGANIC" if grew else "UNKNOWN")
        first = h.first_holders
        if first and cur.complete and first is not cur and first.amounts:
            hi.early_retention_pct = 100 * len(set(first.amounts) & set(cur.amounts)) / len(first.amounts)
            hi.early_snapshot_age_min = (now - first.ts) / 60

    nh = "needs_holder_history"
    why = holder_reason(st)
    M.add("holders", hs.holder_count if hs else None, "int", "holders", src, ts, note=why)
    M.add("top10", hs.top10_pct if hs else None, "pct", "holders", src, ts, note=why)
    M.add("top20", hs.top20_pct if hs else None, "pct", "holders", src, ts, note=why)
    M.add("top50", hs.top50_pct if hs and hs.complete_list else None, "pct", "holders", src, ts, note=why)
    M.add("dev_pct_holders", hs.creator_pct if hs else None, "pct", "holders", src, ts, note=why)
    M.add("holder_growth_5m", hi.growth_5m_pct, "pct", "holders", src, ts, derived=True, note=nh)
    M.add("holder_growth_15m", hi.growth_15m_pct, "pct", "holders", src, ts, derived=True, note=nh)
    M.add("holder_accel", hi.accel, "pct", "holders", src, ts, derived=True, note=nh)
    M.add("new_holders", hi.new_holders, "int", "holders", src, ts, derived=True, note=nh)
    M.add("new_holder_rate", hi.new_per_min, "ratio", "holders", src, ts, derived=True, note=nh)
    M.add("holder_churn", hi.churn_pct, "pct", "holders", src, ts, derived=True, note=nh)
    M.add("early_retention", hi.early_retention_pct, "pct", "holders", src, ts, derived=True, note=nh)
    M.add("holder_quality", hi.organic if hi.organic != "UNKNOWN" else None, "text", "holders", src, ts,
          derived=True, note=nh)
    M.unknown("holder_retention_cohort", "pct", "holders", "not_implemented")
    return hi
