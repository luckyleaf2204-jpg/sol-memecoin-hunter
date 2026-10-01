"""👀 EARLY WATCH — tokens 3–10 minutes old: the gap between PRE-EARLY (≤ 3 min) and Early Signal (≥ 10 min).

A ranking layer only: it reads scanner results and never feeds Early Signal / D1–D8 or any score.

ELIGIBLE    age 3.0–10.0 min (created_at, else DexScreener pair creation); unknown age -> not eligible
EXCLUDED    identity CONFLICT · Data Quality INVALID because data is WRONG (missing-only INVALID is allowed and
            labelled) · any RUG-category risk flag · holder data INVALID (D6)
COMPONENTS  (None = not available, never 0)
  momentum     scanner sub-score "momentum"
  opportunity  Opportunity score (None for INVALID)
  risk         100 − Risk
RANK        weighted mean of the AVAILABLE components (momentum 40, opportunity 35, risk 25)
            × (0.5 + 0.5 × confidence / 100)
CONFIDENCE  share of the 6 data facets present: fresh market data · verified identity · valid holder data ·
            verified dev balance · momentum sub-score · Opportunity score. Every missing facet is listed.
Only the top TOP_N (50) are shown; PARTIAL data is allowed but its gaps are always displayed.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from core.models import INVALID, TokenState
from scoring.groups import _missing_only

MIN_AGE_MIN, MAX_AGE_MIN = 3.0, 10.0
WEIGHTS = {"momentum": 40, "opportunity": 35, "risk": 25}
TOP_N = 50
FRESH_S = 60


@dataclass
class EarlyWatch:
    eligible: bool
    age_min: float | None = None
    excluded_by: list[str] = field(default_factory=list)
    components: dict[str, int | None] = field(default_factory=dict)
    confidence: int = 0
    missing: list[str] = field(default_factory=list)
    rank: float | None = None


def compute_early_watch(st: TokenState, now: float | None = None) -> EarlyWatch:
    now = now or time.time()
    age = st.age_minutes
    if age is None or not (MIN_AGE_MIN <= age <= MAX_AGE_MIN):
        return EarlyWatch(False, round(age, 2) if age is not None else None)
    w = EarlyWatch(True, round(age, 2))
    if st.identity.status == "CONFLICT":
        w.excluded_by.append("identity_conflict")
    if st.dq_status == INVALID and not _missing_only(st):
        w.excluded_by.append("dq_invalid")
    if st.risk and any(f.category == "rug" for f in st.risk.factors):
        w.excluded_by.append("rug_flag")
    if st.holder_status == "invalid":
        w.excluded_by.append("holder_anomaly")

    mom = st.subscores.get("momentum")
    w.components = {"momentum": mom.score if mom and mom.score is not None else None,
                    "opportunity": st.score.total if st.score else None,
                    "risk": (100 - st.risk.score) if st.risk else None}
    stamp = st.stamps.get("market")
    facets = {"market_fresh": bool(stamp and now - stamp.updated_at <= FRESH_S),
              "identity_verified": st.identity.status == "VERIFIED",
              "holders": bool(st.holders and st.holders.valid and st.holder_status == "ok"),
              "dev_verified": bool(st.dev and st.dev.balance_verified),
              "momentum": w.components["momentum"] is not None,
              "opportunity": w.components["opportunity"] is not None}
    w.missing = [k for k, ok in facets.items() if not ok]
    w.confidence = round(100 * sum(facets.values()) / len(facets))
    avail = {k: v for k, v in w.components.items() if v is not None}
    if avail and not w.excluded_by:
        aw = sum(WEIGHTS[k] for k in avail)
        base = sum(WEIGHTS[k] * v for k, v in avail.items()) / aw
        w.rank = round(base * (0.5 + 0.5 * w.confidence / 100), 1)
    return w


def select_watch(states: list[TokenState], top_n: int = TOP_N) -> list[TokenState]:
    """The 👀 list: eligible, not excluded, ranked; at most top_n."""
    rows = [s for s in states if s.early_watch is not None and s.early_watch.eligible and s.early_watch.rank is not None]
    rows.sort(key=lambda s: -s.early_watch.rank)
    return rows[:top_n]
