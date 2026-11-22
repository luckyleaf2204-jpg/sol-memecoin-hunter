"""Smart Money Score (0–100) with data-confidence and official-ranking eligibility.

Weights (PHẦN V): Win Rate 20 · Median Buy SOL 20 · Realised PnL 15 · ROI 15 ·
Buying before the move 20 · Stability & independence 10.

Each component is a number in [0, 1] times its weight; formulas are explicit so every score
can be recomputed by hand from the metrics shown on the wallet page.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any

from .performance import Performance

OFFICIAL_MIN_SCORE = 65.0
WEIGHTS = {"win_rate": 20, "median_buy": 20, "realized_pnl": 15, "roi": 15, "early_entry": 20,
           "stability": 10}


def clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


@dataclass
class ScoreResult:
    score: float
    category: str
    confidence: str
    qualifies: bool
    components: dict[str, Any] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def eligible_official(self) -> bool:
        # passes the mandatory criteria, enough trustworthy data, and at least "Watchlist" (>= 65)
        return self.qualifies and self.confidence in ("HIGH", "MEDIUM") and self.score >= OFFICIAL_MIN_SCORE


def category_for(score: float) -> str:
    if score >= 85:
        return "EXCELLENT"
    if score >= 75:
        return "STRONG"
    if score >= 65:
        return "WATCHLIST"
    return "NOT_PRIORITY"


def score_wallet(perf: Performance, settings: dict[str, Any], cluster_size: int = 1,
                 excluded: bool = False, signer_share: float | None = 1.0, history_read: bool = True) -> ScoreResult:
    s = settings
    min_buy = float(s["min_buy_sol"])
    reasons: list[str] = []
    comp: dict[str, Any] = {}

    closed = perf.closed_qualifying
    # 1) Win rate — Laplace-smoothed so 3/3 does not look like a perfect record
    wr_s = (perf.wins + 1) / (closed + 2) if closed else 0.0
    f_wr = clamp((wr_s - 0.50) / (0.85 - 0.50))
    comp["win_rate"] = {"raw": perf.win_rate, "smoothed": wr_s, "factor": f_wr}

    # 2) Median buy: log scale 2 SOL -> 0.25, 50 SOL -> 1.0, times a consistency factor
    med = perf.median_buy_sol or 0.0
    if med >= min_buy:
        size = 0.25 + 0.75 * clamp(math.log(med / min_buy) / math.log(25))
    else:
        size = 0.0
    share_q = perf.metrics.get("share_positions_qualifying") or 0.0
    m2m = perf.metrics.get("median_to_mean")
    m2m = 1.0 if m2m is None else clamp(m2m)
    rel = perf.metrics.get("median_entry_share_of_liquidity")
    rel_f = clamp(rel / 0.2) if rel is not None else 0.5
    consistency = 0.4 * share_q + 0.4 * m2m + 0.2 * rel_f
    f_mb = size * (0.6 + 0.4 * consistency)
    comp["median_buy"] = {"median_buy_sol": med, "size": size, "share_qualifying": share_q,
                          "median_to_mean": m2m, "entry_share_of_liquidity": rel, "consistency": consistency,
                          "factor": f_mb}

    # 3) Realised PnL (after fees): log10 scale, 100 SOL = full
    pnl = perf.realized_pnl_sol
    f_pnl = clamp(math.log10(1 + pnl) / math.log10(101)) if pnl > 0 else 0.0
    comp["realized_pnl"] = {"sol": pnl, "factor": f_pnl}

    # 4) ROI on closed qualifying positions: 100 % = full
    roi = perf.roi if perf.roi is not None else 0.0
    f_roi = clamp(roi / 1.0)
    comp["roi"] = {"roi": perf.roi, "factor": f_roi}

    # 5) Buying before the move: token hit rates after the wallet's entry
    h2, h5, h10 = perf.hit_rate_2x, perf.hit_rate_5x, perf.hit_rate_10x
    if h2 is None:
        f_early = 0.0
        reasons.append("no historical price data for entries: early-entry component = 0")
    else:
        f_early = 0.45 * clamp(h2 / 0.6) + 0.35 * clamp((h5 or 0) / 0.3) + 0.20 * clamp((h10 or 0) / 0.15)
    comp["early_entry"] = {"hit_2x": h2, "hit_5x": h5, "hit_10x": h10, "factor": f_early}

    # 6) Stability & independence
    tws = perf.top_win_share
    conc = 1.0 - clamp(((tws if tws is not None else 1.0) - 0.4) / 0.5)
    ex_best = 1.0 if perf.pnl_ex_best_sol > 0 else 0.0
    indep = 1.0 / math.sqrt(max(cluster_size, 1))
    f_stab = 0.4 * conc + 0.3 * ex_best + 0.3 * indep
    comp["stability"] = {"top_win_share": tws, "pnl_ex_best_sol": perf.pnl_ex_best_sol,
                         "cluster_size": cluster_size, "factor": f_stab}

    raw = (WEIGHTS["win_rate"] * f_wr + WEIGHTS["median_buy"] * f_mb + WEIGHTS["realized_pnl"] * f_pnl
           + WEIGHTS["roi"] * f_roi + WEIGHTS["early_entry"] * f_early + WEIGHTS["stability"] * f_stab)

    # Preference for few-but-good trades: dampen high-frequency wallets
    mult = 1.0
    pmax = int(s["preferred_positions_max"])
    if closed > pmax:
        mult *= max(0.8, 1.0 - 0.05 * (closed - pmax) / pmax)
        reasons.append(f"{closed} closed qualifying positions > preferred {pmax}: score x{mult:.2f}")
    if perf.positions_total > 300:
        mult *= 0.85
        reasons.append(f"{perf.positions_total} positions in period: high-frequency / bot-like (x0.85)")
    score = round(raw * mult, 2)
    comp["multiplier"] = mult
    comp["weights"] = WEIGHTS

    # Mandatory criteria
    ok = True
    if closed < int(s["min_closed_positions_official"]):
        ok = False
        reasons.append(f"only {closed} closed qualifying positions (< {s['min_closed_positions_official']})")
    if perf.win_rate is None or perf.win_rate < float(s["min_win_rate"]):
        ok = False
        reasons.append(f"win rate {fmt_pct(perf.win_rate)} < {fmt_pct(s['min_win_rate'])}")
    if med < min_buy:
        ok = False
        reasons.append(f"median buy {med:.2f} SOL < {min_buy} SOL")
    if pnl <= 0:
        ok = False
        reasons.append("realised PnL after fees is not positive")
    if perf.roi is None or perf.roi <= 0:
        ok = False
        reasons.append("ROI is not positive")
    if excluded:
        ok = False
        reasons.append("wallet excluded by the user")
    if signer_share is not None and signer_share < 0.5:
        ok = False
        reasons.append("most trades were made by a non-signing (router/PDA) account")

    # Data confidence
    vs = perf.verified_share or 0.0
    priced = perf.metrics.get("positions_with_price_data") or 0
    priced_share = priced / perf.positions_qualifying if perf.positions_qualifying else 0
    if (closed >= int(s["high_confidence_min_closed"]) and vs >= float(s["high_confidence_min_verified_share"])
            and perf.coverage_complete and priced_share >= 0.6):
        conf = "HIGH"
    elif closed >= int(s["min_closed_positions_official"]) and vs >= float(s["medium_confidence_min_verified_share"]):
        conf = "MEDIUM"
    else:
        conf = "LOW"
    if not history_read:
        conf = "LOW"
        reasons.append("wallet history not backfilled yet (only live-stream trades): Low confidence")
    if not perf.coverage_complete:
        reasons.append("history not fully backfilled for the period: confidence capped")
    if vs < float(s["medium_confidence_min_verified_share"]):
        reasons.append(f"only {vs:.0%} of positions fully verified on-chain")
    comp["confidence_inputs"] = {"closed": closed, "verified_share": vs, "coverage_complete": perf.coverage_complete,
                                 "priced_share": priced_share}
    return ScoreResult(score=score, category=category_for(score), confidence=conf, qualifies=ok,
                       components=comp, reasons=reasons)


def fmt_pct(x) -> str:
    return "n/a" if x is None else f"{float(x):.0%}"


def rank_official(results: dict[str, ScoreResult], limit: int = 10) -> list[tuple[str, ScoreResult]]:
    """Official Top list: only wallets that pass the mandatory criteria with HIGH/MEDIUM confidence.
    Ties broken by confidence (HIGH first) then median buy."""
    conf_rank = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
    elig = [(w, r) for w, r in results.items() if r.eligible_official]
    elig.sort(key=lambda x: (-x[1].score, conf_rank[x[1].confidence],
                             -(x[1].components.get("median_buy", {}).get("median_buy_sol") or 0)))
    return elig[:limit]
