"""LIFECYCLE engine decision: IDENTITY -> LIFECYCLE -> LIFECYCLE SETUP -> SETUP SCORE -> RISK / VET -> (execution).

Opportunity, EarlyScore and D1-D8 are computed and LOGGED but are no longer a universal BUY gate here; each lifecycle
has its own setup engine and threshold (cfg.new_setup_threshold / premigration_setup_threshold /
second_wave_setup_threshold, cfg.min_setup_confidence). The existing safety gates are reused unchanged
(experimental.safety_pre / safety_post): identity conflict, Risk > 60, rug / liquidity shock, authorities,
Token-2022, dev dump, top10 extreme, liquidity floor ($10K, AMM-equivalent model), invalid CA / data, gates never
evaluated, final risk and the entry risk buffer. Lifecycle never lowers Risk and never bypasses VET or execution.
UNKNOWN lifecycle / migration conflict / LOW confidence -> no BUY.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from research.antirug import features as antirug_features
from trading import experimental as X
from trading import lifecycle as LC
from trading import setup_new, setup_premigration, setup_second_wave
from trading.decision import PENDING_IDENTITY, REJECT, TRADE, WATCH

SETUP_FOR = {LC.NEW: "NEW", LC.PRE_MIGRATION: "PRE_MIGRATION", LC.POST_MIGRATION: "SECOND_WAVE"}


@dataclass
class LifecycleDecision:
    decision: str
    lifecycle: LC.LifecycleInfo
    setup: object | None = None              # SetupScore
    blocked_by: list = field(default_factory=list)
    waiting: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    why: list = field(default_factory=list)
    es: object | None = None                 # EarlyScore (logged; also carries the liquidity model)
    threshold: float | None = None
    pre_shadow_would_buy: bool = False       # PRE-MIGRATION in shadow mode: what it WOULD have done


def threshold_for(setup_type: str, cfg) -> float:
    return {"NEW": cfg.new_setup_threshold, "PRE_MIGRATION": cfg.premigration_setup_threshold,
            "SECOND_WAVE": cfg.second_wave_setup_threshold}[setup_type]


def curve_points(h, pair: str, now: float) -> list:
    if h is None:
        return []
    return [(p.ts, p.liq) for p in h.points if p.ts <= now and p.pair == pair and p.liq_src == "pumpfun_curve"]


def post_points(h, pair: str, since: float | None, now: float) -> list:
    if h is None or not pair:
        return []
    out = []
    for p in h.points:
        if p.pair != pair or p.ts > now or (since is not None and p.ts < since - 1):
            continue
        out.append((p.ts, p.price, p.liq, p.vol_5m, p.buy_share))
    return out


def evaluate(st, v, sc, cfg, now: float | None = None, tr=None, h=None, onchain_rec: dict | None = None,
             li: LC.LifecycleInfo | None = None) -> LifecycleDecision:
    now = now or time.time()
    li = li or LC.classify(st, cfg, now)
    es = X.prepare(st, v, cfg, now)
    hard, blocked, waiting = X.safety_pre(st, v, cfg, es)
    why = [f"lifecycle {li.lifecycle} ({li.confidence}) · {'; '.join(li.reasons[:2])}"]
    setup = None
    # ---- lifecycle
    if li.migration_status == "conflict":
        blocked.append("migration_conflict")
    elif li.lifecycle == LC.UNKNOWN:
        blocked.append("lifecycle_unknown")
    elif not li.active:
        blocked.append(f"lifecycle_confidence:{li.confidence}")
    # ---- setup (always computed when the lifecycle is known, for research even when not active)
    f = antirug_features(st, now)
    oc = None
    if onchain_rec is not None:
        from research.onchain import features_at
        oc = features_at(onchain_rec, now)
    if li.lifecycle == LC.NEW:
        setup = setup_new.score(f, oc)
    elif li.lifecycle == LC.PRE_MIGRATION:
        setup = setup_premigration.score(f, oc, curve_points(h, li.pair_address, now), now)
    elif li.lifecycle == LC.POST_MIGRATION:
        pair = (tr.post_pair if tr is not None and tr.post_pair else li.pair_address)
        since = tr.migration_ts if tr is not None else None
        ps = setup_second_wave.post_state(post_points(h, pair, since, now), now, cfg,
                                          st.risk.score if st.risk else None)
        setup = setup_second_wave.score(ps, f)
    thr = None
    if setup is not None:
        thr = threshold_for(setup.setup_type, cfg)
        blocked += setup.blocks
        if setup.score is None:
            blocked.append("setup_score_unknown")
            waiting.append("setup_data")
        elif setup.score < thr:
            blocked.append(f"setup_score_low:{setup.score:.0f}<{thr:.0f}")
        if setup.data_confidence < cfg.min_setup_confidence:
            blocked.append(f"setup_confidence_low:{setup.data_confidence:.2f}<{cfg.min_setup_confidence:.2f}")
            waiting.append("setup_data")
        why.append(f"{setup.setup_type} setup {('—' if setup.score is None else f'{setup.score:.0f}')} (≥{thr:.0f})"
                   f" · data conf {setup.data_confidence:.2f}")
    # ---- risk / VET / buffer (unchanged gates)
    b2, w2 = X.safety_post(st, cfg, es, hard)
    blocked += b2
    waiting = list(dict.fromkeys(waiting + w2))
    why.append(f"Opportunity {sc.opportunity} · Confidence {sc.confidence} · EarlyScore "
               f"{'—' if es.score is None else f'{es.score:.2f}'} (logged, not a gate)")
    ident = st.identity.status
    if hard:
        return LifecycleDecision(REJECT, li, setup, blocked, waiting, hard, ["reject: " + ", ".join(hard)] + why, es, thr)
    if ident != "VERIFIED":
        return LifecycleDecision(PENDING_IDENTITY, li, setup, blocked, waiting, [], why, es, thr)
    if not blocked:
        if setup is not None and setup.setup_type == "PRE_MIGRATION" and getattr(cfg, "pre_migration_shadow", False):
            d = LifecycleDecision(WATCH, li, setup, ["pre_migration_shadow"], [], [],
                                  ["PRE-MIGRATION SHADOW: would BUY (no order)"] + why, es, thr)
            d.pre_shadow_would_buy = True
            return d
        return LifecycleDecision(TRADE, li, setup, [], [], [], why, es, thr)
    return LifecycleDecision(WATCH, li, setup, blocked, waiting, [], ["waiting: " + ", ".join(blocked[:4])] + why, es, thr)
