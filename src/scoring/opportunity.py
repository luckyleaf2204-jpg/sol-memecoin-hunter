"""OPPORTUNITY ENGINE — upside ACTIVITY only (per the rule: liquidity / concentration / dev belong to Risk).

Parts and weights:
  momentum       45   MOMENTUM sub-score (volume level + acceleration, buy pressure, txn acceleration, price)
  holder_growth  25   the growth factors of the HOLDER sub-score only: growth_15m, holder_accel, holder_quality
                      (top-10 concentration and retention are NOT opportunity; they inform Risk / Holder score)
  whale          10   WHALE sub-score (accumulation vs distribution)
  smart_money    10   NOT AVAILABLE
  social          5   NOT AVAILABLE
  narrative       5   NOT AVAILABLE

  Opportunity = round(Σ w·S / Σ w) over parts that are NOT None;  coverage = Σ w (available) / 100
Rules
  * INVALID data quality or missing market data -> Opportunity = None (not rankable)
  * momentum must be available (the only market-validated dimension)
  * risk / liquidity / dev are NOT inputs: a low risk score can never lift Opportunity
  * early signal is a separate engine (its own ranking), per the required pipeline order
Explainability: factor contribution = factor.points / Σ max(available factors of its part) × 100 × w / Σ w,
so listed contributions add up to the total.
"""
from __future__ import annotations

from core.models import INVALID, Factor, OpportunityResult, SubScore, TokenState

WEIGHTS = {"momentum": 45, "holder_growth": 25, "whale": 10, "smart_money": 10, "social": 5, "narrative": 5}
HOLDER_GROWTH_FACTORS = ("growth_15m", "holder_accel", "holder_quality")


def holder_growth_part(holder: SubScore | None) -> SubScore:
    factors = [f for f in (holder.factors if holder else []) if f.key in HOLDER_GROWTH_FACTORS]
    avail = [f for f in factors if f.available]
    mx = sum(f.max_points for f in avail)
    if not avail or not mx:
        return SubScore("holder_growth", None, factors, "needs_holder_history")
    return SubScore("holder_growth", round(100 * sum(f.points for f in avail) / mx), factors)


def compute_opportunity(st: TokenState, subs: dict[str, SubScore]) -> OpportunityResult | None:
    if st.market is None or st.quality is None or st.quality.status == INVALID:
        return None
    mom = subs.get("momentum")
    if not mom or mom.score is None:
        return None
    parts_subs = {k: subs.get(k) for k in WEIGHTS}
    parts_subs["holder_growth"] = holder_growth_part(subs.get("holder"))
    parts = [(k, w, parts_subs[k].score if parts_subs[k] else None) for k, w in WEIGHTS.items()]
    avail = [(k, w, sc) for k, w, sc in parts if sc is not None]
    wsum = sum(w for _, w, _ in avail)
    total = round(sum(w * sc for _, w, sc in avail) / wsum)
    contributions = []
    for k, w, _ in avail:
        sub = parts_subs[k]
        mx = sum(f.max_points for f in sub.factors if f.available)
        for f in sub.factors:
            if f.available and mx:
                contributions.append((f.key, round(f.points / mx * 100 * w / wsum, 1), f.value, f.source))
    contributions.sort(key=lambda c: -c[1])
    return OpportunityResult(total=int(max(0, min(100, total))), coverage_pct=round(100 * wsum / sum(WEIGHTS.values())),
                             parts=parts, contributions=contributions)
