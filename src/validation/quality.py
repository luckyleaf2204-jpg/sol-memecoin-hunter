"""Data Quality score (0-100) and badge.

  INVALID  any critical issue -> excluded from Opportunity, ranking, alerts, early-signal TRUE, backtest signals
  VALID    no critical issue and score >= 80
  PARTIAL  everything else (scored, visible in tables, NOT in Top Opportunities)

Deductions:
  critical market issue                     -40 each (forces INVALID)
  token identity CONFLICT (validation.identity)  critical: feeds/sources disagree on what this CA is
  market warning                            -10 each
  market data older than 1.5 × scan interval  -10 ; older than max(60 s, 3 × interval) = critical
  holder data unavailable                   -10
  dev balance not verified on-chain         -10
  X / Telegram activity source unavailable  -5
"""
from __future__ import annotations

import time

from core.models import INVALID, PARTIAL, VALID, DataQuality, Issue, TokenState

VALID_MIN = 80


def assess_quality(st: TokenState, scan_interval: float, now: float | None = None) -> DataQuality:
    now = now or time.time()
    issues: list[Issue] = []
    if not st.market:
        issues.append(Issue("critical", "market", "no_market"))
    issues += st.market_issues
    if st.identity.status == "CONFLICT":
        issues.append(Issue("critical", "identity", "identity_conflict", {"detail": st.identity.reason}))

    score = 100
    for i in issues:
        score -= 40 if i.severity == "critical" else 10
    m_stamp = st.stamps.get("market")
    if m_stamp:
        age = m_stamp.age(now)
        if age > max(60, 3 * scan_interval):
            issues.append(Issue("critical", "market", "market_stale", {"age": round(age)}))
            score -= 40
        elif age > 1.5 * scan_interval:
            issues.append(Issue("warning", "market", "market_aging", {"age": round(age)}))
            score -= 10
    if not st.holders:
        if st.holder_status == "invalid":
            issues.append(Issue("warning", "holders", "holders_invalid", {"reason": st.holder_error}))
        else:
            issues.append(Issue("warning", "holders", "holders_unavailable"))
        score -= 10
    if not (st.dev and st.dev.balance_verified):
        issues.append(Issue("warning", "dev", "dev_unverified"))
        score -= 10
    issues.append(Issue("warning", "social", "social_unavailable"))
    score -= 5

    score = max(0, min(100, score))
    if any(i.severity == "critical" for i in issues):
        status = INVALID
        score = min(score, 30)
    elif score >= VALID_MIN:
        status = VALID
    else:
        status = PARTIAL
    return DataQuality(score=score, status=status, issues=issues)
