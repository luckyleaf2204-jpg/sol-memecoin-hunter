"""MetricBuilder — every metric gets VALUE, SOURCE, TIMESTAMP, AGE (derived from ts) and CONFIDENCE.

Confidence (0-1):
  base      1.0 if reported directly by an API, 0.8 if derived by us (history deltas / formulas)
  × freshness   1.0 while data age ≤ 1.5 × scan interval, falling linearly to 0.3 at 5 × interval
  × coverage    optional factor (e.g. how close the history point is to the requested time)
A metric with value None has confidence None: UNKNOWN is never "low-confidence good data".
"""
from __future__ import annotations

from core.models import Metric


def freshness(age_s: float | None, interval: float) -> float:
    if age_s is None:
        return 0.0
    lo, hi = 1.5 * interval, 5 * interval
    if age_s <= lo:
        return 1.0
    if age_s >= hi:
        return 0.3
    return 1.0 - 0.7 * (age_s - lo) / (hi - lo)


class MetricBuilder:
    def __init__(self, now: float, interval: float):
        self.now = now
        self.interval = interval
        self.items: list[Metric] = []

    def add(self, key: str, value, kind: str, section: str, source: str = "", ts: float | None = None,
            derived: bool = False, coverage: float = 1.0, note: str = "") -> Metric:
        if isinstance(value, float) and value != value:  # NaN guard
            value = None
        conf = None
        if value is not None:
            age = (self.now - ts) if ts else None
            conf = round((0.8 if derived else 1.0) * freshness(age, self.interval) * max(0.0, min(1.0, coverage)), 2)
        m = Metric(key, value, kind, section, source, ts, conf, derived, note)
        self.items.append(m)
        return m

    def unknown(self, key: str, kind: str, section: str, note: str, source: str = "") -> Metric:
        return self.add(key, None, kind, section, source, None, note=note)


def pct_change(now_v, then_v) -> float | None:
    if now_v is None or then_v is None or then_v <= 0:
        return None
    return 100 * (now_v - then_v) / then_v
