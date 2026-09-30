"""RANKING — the only place that decides what appears in ranked lists / alerts.

Top Opportunities: data quality VALID and an Opportunity score; sorted by Opportunity,
                   ties -> lower risk, then higher data quality.
Early Signals:     data quality not INVALID and a known early-signal strength; EARLY=TRUE first,
                   then by strength.
"""
from __future__ import annotations

from core.models import INVALID, VALID, TokenState


def is_rankable(st: TokenState) -> bool:
    return st.quality is not None and st.quality.status == VALID and st.score is not None


def rank_opportunities(states: list[TokenState]) -> list[TokenState]:
    return sorted((s for s in states if is_rankable(s)),
                  key=lambda s: (-s.score.total, s.risk.score if s.risk else 100, -s.quality.score))


def rank_early(states: list[TokenState]) -> list[TokenState]:
    ok = [s for s in states if s.quality and s.quality.status != INVALID and s.early and s.early.strength is not None]
    return sorted(ok, key=lambda s: (not s.early.is_early, -s.early.strength))
