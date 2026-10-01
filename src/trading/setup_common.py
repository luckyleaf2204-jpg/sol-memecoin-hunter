"""Shared result type + scoring helper for the lifecycle setup engines (no decision logic lives here).
A component is a value in [0, 1] or None (UNKNOWN). The setup score is the weighted mean of the KNOWN components;
data_confidence is the known-weight share minus explicit penalties. UNKNOWN is never PASS and never FAIL."""
from __future__ import annotations

from dataclasses import asdict, dataclass, field


@dataclass
class SetupScore:
    setup_type: str
    score: float | None                  # 0-100, None = not enough data
    components: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    data_confidence: float = 0.0
    unknown: list = field(default_factory=list)
    critical_unknown: list = field(default_factory=list)   # safety features that must be known before a BUY
    blocks: list = field(default_factory=list)              # setup-level vetoes (e.g. second wave not READY)
    why: list = field(default_factory=list)
    extra: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["score"] = None if self.score is None else round(self.score, 1)
        d["data_confidence"] = round(self.data_confidence, 3)
        d["components"] = {k: (None if v is None else round(v, 3)) for k, v in self.components.items()}
        return d


def clamp(x: float) -> float:
    return max(0.0, min(1.0, x))


def ramp(x: float, lo: float, hi: float) -> float:
    return clamp((x - lo) / (hi - lo)) if hi != lo else (1.0 if x >= hi else 0.0)


def combine(setup_type: str, comps: dict, weights: dict, penalties: list[tuple[str, float]] | None = None,
            critical_unknown: list | None = None, why: list | None = None) -> SetupScore:
    known = {k: v for k, v in comps.items() if v is not None and weights.get(k)}
    wk = sum(weights[k] for k in known)
    total = sum(w for w in weights.values() if w)
    score = 100 * sum(weights[k] * v for k, v in known.items()) / wk if wk else None
    conf = (wk / total if total else 0.0) - sum(p for _, p in penalties or [])
    return SetupScore(setup_type, score, comps, weights, max(0.0, min(1.0, conf)),
                      [k for k, v in comps.items() if v is None and weights.get(k)], critical_unknown or [], [],
                      (why or []) + [f"{n} -{p:.2f} conf" for n, p in penalties or []])
