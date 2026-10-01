"""EXPERIMENTAL decision engine — Implementation Spec Part 3 (soft EarlyScore, age-dependent thresholds, holder rule,
prior risk). PAPER only. The OLD engine (decision.vet / score / intel.early_signal D1-D8) is untouched and still runs on
every token, so each decision carries OLD and NEW side by side for A/B.

EarlyScore (0-1) = weighted mean of the AVAILABLE soft components; weights depend on age:
                     S_price  S_vol  S_buy  S_holder  S_whale  S_liq  S_lifecycle
    < 90 s            .20      .20    .25     .05       .00      .15     .15
    90 s - 5 m        .20      .20    .20     .15       .05      .10     .10
    > 5 m             .15      .20    .15     .20       .10      .10     .10
Confidence (0-1)  = readiness (weight share with data) - penalties (holders NULL -0.25, < 20 txns -0.10, market data
                    stale -0.15, data PARTIAL -0.05). Missing data lowers Confidence; it never FAILs by itself.
PASS              : <90s score >= .55 & conf >= .40 · 90s-5m .65 / .55 · >5m .75 / .70
Holder rule       : NULL -> component 0 + conf -0.25 · < 15 -> 0.1 · 15-40 -> growth x top10 · >= 40 -> normal;
                    whale component unused (weight 0) below 30 holders. Never a hard fail for a low count.
Prior risk        : unobserved risk of very new tokens (age < 60 s & liq < $8K -> 35, & top10 unknown -> 40,
                    < 2 min on a curve -> 25, liquidity unknown -> 30, < 5 min without holder data -> 15);
                    final_risk = max(observed, prior). Risk 0 on a token seconds old is never read as "safe".
HARD GATES -> REJECT: identity CONFLICT · observed Risk > 60 · rug flag / liquidity SHOCK · mint/freeze authority active
                    · dangerous Token-2022 extension · dev dump (VET dev FAIL) · top10 > 92 % and age > 3 m ·
                    liquidity below the protection floor · invalid CA · data confirmed wrong.
TRADE (all)       : identity VERIFIED · no hard gate · gate data CHECKED (authorities, Token-2022, rug/Risk, liquidity,
                    fresh market data — a gate that was never evaluated is not a PASS) · EarlyScore PASS for the age ·
                    Opportunity >= 65 · Confidence >= 60 · final risk <= 60.
Anything else is WATCH (with what it waits for) or PENDING_IDENTITY.
"""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field

from core.models import TokenState
from trading.config import TradingConfig
from trading.decision import FAIL, PASS, PENDING_IDENTITY, REJECT, TRADE, WATCH, Score, Vet
from trading.decision import _no_activity_only

COMPONENTS = ("S_price", "S_vol", "S_buy", "S_holder", "S_whale", "S_liq", "S_lifecycle")
WEIGHTS = {
    "<90s": {"S_price": .20, "S_vol": .20, "S_buy": .25, "S_holder": .05, "S_whale": .00, "S_liq": .15, "S_lifecycle": .15},
    "90s-5m": {"S_price": .20, "S_vol": .20, "S_buy": .20, "S_holder": .15, "S_whale": .05, "S_liq": .10, "S_lifecycle": .10},
    ">5m": {"S_price": .15, "S_vol": .20, "S_buy": .15, "S_holder": .20, "S_whale": .10, "S_liq": .10, "S_lifecycle": .10},
}
THRESHOLDS = {"<90s": (0.55, 0.40), "90s-5m": (0.65, 0.55), ">5m": (0.75, 0.70)}
LIFECYCLE = {"EARLY": .8, "EARLY_MOMENTUM": .9, "BREAKOUT": .8, "MOMENTUM": .6, "MATURE": .3, "DISTRIBUTION": .1,
             "DECLINING": 0.0}
TOP10_EXTREME, TOP10_EXTREME_AGE_S = 92.0, 180
HOLDER_NULL_PENALTY, LOW_TXN_PENALTY, STALE_PENALTY, PARTIAL_PENALTY = 0.25, 0.10, 0.15, 0.05
HARD_VET = {"authorities", "token_2022", "dev", "ca", "liquidity", "rug"}      # VET FAIL here = TRUE FAIL
MUST_CHECK = ("authorities", "token_2022", "rug", "liquidity", "data_quality", "ca")


def ramp(x: float, lo: float, hi: float) -> float:
    return max(0.0, min(1.0, (x - lo) / (hi - lo)))


def age_seconds(st: TokenState, now: float) -> float:
    created = st.info.created_at or (st.market.pair_created_at if st.market else None) or st.info.discovered_at or now
    return max(0.0, now - created)


def age_bucket(age_s: float) -> str:
    return "<90s" if age_s < 90 else "90s-5m" if age_s < 300 else ">5m"


@dataclass
class EarlyScore:
    score: float | None
    confidence: float
    age_s: float
    bucket: str
    theta: float
    gamma: float
    passed: bool
    components: dict = field(default_factory=dict)
    weights: dict = field(default_factory=dict)
    missing: list = field(default_factory=list)
    penalties: list = field(default_factory=list)
    observed_risk: int | None = None
    prior_risk: int = 0
    final_risk: int | None = None
    prior_reasons: list = field(default_factory=list)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["score"] = None if self.score is None else round(self.score, 3)
        d["confidence"] = round(self.confidence, 3)
        d["components"] = {k: (None if v is None else round(v, 3)) for k, v in self.components.items()}
        return d


# ---------------------------------------------------------------- components (None = no data)
def s_price(st: TokenState) -> float | None:
    m = st.market
    pc = m.price_change_5m if m else None
    if pc is None and m and m.market_cap and st.mc_track and st.mc_track.initial_mc:
        pc = 100 * (m.market_cap / st.mc_track.initial_mc - 1)
    return None if pc is None else ramp(pc, 0, 60)


def s_vol(st: TokenState) -> float | None:
    m = st.market
    if not m or m.vol_5m is None or not m.market_cap:
        return None
    s = ramp(m.vol_5m / m.market_cap, 0.05, 0.6) * min(1.0, m.vol_5m / 1000)
    hit = _hit(st, "volume_accel")
    return max(s, hit) if hit is not None else s


def s_buy(st: TokenState) -> float | None:
    m = st.market
    if not m or m.buys_5m is None or m.sells_5m is None or m.buys_5m + m.sells_5m < 5:
        return None                                       # < 5 trades: no sample
    s = ramp(m.buys_5m / (m.buys_5m + m.sells_5m), 0.50, 0.75)
    hit = _hit(st, "buy_pressure")
    return max(s, hit) if hit is not None else s


def _hit(st: TokenState, key: str) -> float | None:
    for h in (st.early.signals if st.early else []):
        if h.key == key and h.fired:
            return h.strength
    return None


def _top_factor(top10: float | None) -> float:
    return 0.7 if top10 is None else 1.0 - ramp(top10, 35, 80)


def holder_component(st: TokenState) -> tuple[float, bool]:
    """(component, holders_null). Spec 3.5."""
    h = st.holders if st.holder_status == "ok" and st.holders and st.holders.valid else None
    n = h.holder_count if h else None
    if n is None:
        return 0.0, True
    hi = st.holder_intel
    growth = None
    if hi is not None and hi.new_per_min is not None:
        growth = ramp(hi.new_per_min, 1, 8)
    elif hi is not None and hi.growth_5m_pct is not None:
        growth = ramp(hi.growth_5m_pct, 3, 20)
    tf = _top_factor(h.top10_pct)
    if n < 15:
        return 0.1, False
    if n < 40:
        return ((0.3 + 0.7 * growth) if growth is not None else 0.3) * tf, False
    count = ramp(n, 40, 300)
    return ((0.4 * count + 0.6 * growth) if growth is not None else 0.6 * count) * tf, False


def s_whale(st: TokenState) -> tuple[float | None, bool]:
    """(component, used). Unused (weight 0) below 30 holders or without holder data."""
    h = st.holders if st.holder_status == "ok" and st.holders else None
    if h is None or h.holder_count is None or h.holder_count < 30:
        return None, False
    wi = st.whale_intel
    if wi is None or wi.state in ("", "UNKNOWN"):
        return None, True
    if wi.state == "ACCUMULATION":
        return max(0.5, ramp(wi.delta_pct or 0, 1, 5)), True
    return (0.4 if wi.state == "NEUTRAL" else 0.0), True


def s_liq(st: TokenState) -> float | None:
    m = st.market
    if not m or m.liquidity_usd is None:
        return None
    s = 0.6 * ramp(m.liquidity_usd, 5_000, 50_000)
    s += 0.4 * (ramp(m.liquidity_usd / m.market_cap, 0.05, 0.3) if m.market_cap else 0.0)
    li = st.liquidity_intel
    if li is not None:
        s += 0.1 if li.state == "GROWING" else (-0.2 if li.state == "FALLING" else 0.0)
        if li.state == "SHOCK":
            s = 0.0
    return max(0.0, min(1.0, s))


def s_lifecycle(st: TokenState) -> float | None:
    vals = []
    if st.lifecycle in LIFECYCLE:
        vals.append(LIFECYCLE[st.lifecycle])
    cp = st.info.curve_progress
    if cp is not None and not st.info.complete:
        vals.append(ramp(cp, 2, 30) if cp <= 85 else 0.6)
    elif st.info.complete:
        vals.append(0.7)
    return max(vals) if vals else None


# ---------------------------------------------------------------- risk prior
def prior_risk(st: TokenState, age_s: float) -> tuple[int, list[str]]:
    m = st.market
    liq = m.liquidity_usd if m else None
    h = st.holders if st.holder_status == "ok" and st.holders else None
    top10 = h.top10_pct if h else None
    p, why = 0, []

    def at_least(v, r):
        nonlocal p
        if v > p:
            p = v
        why.append(r)
    if age_s < 60:
        at_least(20, "age < 60s")
        if liq is None or liq < 8_000:
            at_least(35, "age < 60s & liq < $8K")
        if top10 is None:
            at_least(40, "age < 60s & top10 unknown")
    if age_s < 120 and m is not None and m.is_curve:
        at_least(25, "< 2 min on bonding curve")
    if liq is None:
        at_least(30, "liquidity unknown")
    if age_s < 300 and h is None:
        at_least(15, "< 5 min without holder data")
    if m is None or not m.pair_address:
        at_least(25, "single source (no DEX pair data)")
    return p, why


# ---------------------------------------------------------------- EarlyScore
def early_score(st: TokenState, now: float | None = None, cfg: TradingConfig | None = None) -> EarlyScore:
    now = now or time.time()
    cfg = cfg or TradingConfig()
    age = age_seconds(st, now)
    b = age_bucket(age)
    w = dict(WEIGHTS[b])
    holder, holder_null = holder_component(st)
    whale, whale_used = s_whale(st)
    comps = {"S_price": s_price(st), "S_vol": s_vol(st), "S_buy": s_buy(st), "S_holder": holder,
             "S_whale": whale, "S_liq": s_liq(st), "S_lifecycle": s_lifecycle(st)}
    if not whale_used:
        w["S_whale"] = 0.0                                 # < 30 holders: whale component not used at all
    total = sum(w.values())
    have = {k: v for k, v in comps.items() if v is not None and w[k] > 0}
    wh = sum(w[k] for k in have)
    score = (sum(w[k] * v for k, v in have.items()) / wh) if wh else None
    readiness = wh / total if total else 0.0
    pen = []
    if holder_null:
        pen.append(("holders_null", HOLDER_NULL_PENALTY))
    m = st.market
    if m is None or m.txns_5m is None or m.txns_5m < 20:
        pen.append(("txns_5m < 20", LOW_TXN_PENALTY))
    if m is None or now - m.updated_at > cfg.max_data_age_s:
        pen.append(("market data stale", STALE_PENALTY))
    if st.dq_status == "PARTIAL":
        pen.append(("data PARTIAL", PARTIAL_PENALTY))
    conf = max(0.0, min(1.0, readiness - sum(x for _, x in pen)))
    theta, gamma = THRESHOLDS[b]
    obs = st.risk.score if st.risk else None
    pr, pr_why = prior_risk(st, age)
    return EarlyScore(score, conf, age, b, theta, gamma, score is not None and score >= theta and conf >= gamma,
                      comps, w, [k for k in COMPONENTS if comps[k] is None and w[k] > 0], [k for k, _ in pen], obs, pr,
                      None if obs is None else max(obs, pr), pr_why)


# ---------------------------------------------------------------- decision
@dataclass
class ExpDecision:
    decision: str
    es: EarlyScore
    blocked_by: list = field(default_factory=list)
    waiting: list = field(default_factory=list)
    rejected: list = field(default_factory=list)
    why: list = field(default_factory=list)


def hard_gates(st: TokenState, v: Vet, es: EarlyScore) -> list[str]:
    checks = {c.key: c for c in v.checks}
    out = []
    if st.identity.status == "CONFLICT":
        out.append("identity_conflict")
    rk = st.risk
    if rk is not None and rk.score > 60:
        out.append("risk_gt_60")
    if rk is not None and any(f.category == "rug" for f in rk.factors):
        out.append("rug")
    if st.liquidity_intel is not None and st.liquidity_intel.state == "SHOCK":
        out.append("liquidity_shock")
    for k in ("authorities", "token_2022", "dev", "ca", "liquidity"):
        c = checks.get(k)
        if c is not None and c.result == FAIL:
            out.append(k)
    h = st.holders if st.holder_status == "ok" and st.holders and st.holders.valid else None
    if h is not None and h.top10_pct is not None and h.top10_pct > TOP10_EXTREME and es.age_s > TOP10_EXTREME_AGE_S:
        out.append("top10_extreme")
    dq = checks.get("data_quality")
    if dq is not None and dq.result == FAIL and st.dq_status == "INVALID" and not _no_activity_only(st):
        out.append("data_invalid")
    return out


def evaluate(st: TokenState, v: Vet, sc: Score, cfg: TradingConfig, now: float | None = None) -> ExpDecision:
    now = now or time.time()
    es = early_score(st, now, cfg)
    checks = {c.key: c for c in v.checks}
    hard = hard_gates(st, v, es)
    blocked, waiting = [], []
    ident = st.identity.status
    if ident != "VERIFIED" and ident != "CONFLICT":
        blocked.append("identity_pending")
        waiting.append("identity")
    blocked += ["hard:" + h for h in hard]
    for k in MUST_CHECK:
        c = checks.get(k)
        if c is not None and c.result != PASS and k not in hard and not (k == "rug" and {"risk_gt_60", "rug"} & set(hard)):
            blocked.append("gate_unknown:" + k)
            waiting.append({"authorities": "vet_onchain", "token_2022": "vet_onchain", "rug": "risk",
                            "liquidity": "market_data", "data_quality": "market_data", "ca": "identity"}[k])
    if es.score is None:
        blocked.append("early_score_unknown")
        waiting.append("early_signal")
    else:
        if es.score < es.theta:
            blocked.append(f"early_score_low:{es.score:.2f}<{es.theta:.2f}")
        if es.confidence < es.gamma:
            blocked.append(f"early_conf_low:{es.confidence:.2f}<{es.gamma:.2f}")
            waiting.append("early_signal")
    opp, conf = sc.opportunity, sc.confidence
    if opp is None:
        blocked.append("opportunity_unknown")
        waiting.append("scores")
    elif opp < cfg.trade_min_opportunity:
        blocked.append("opportunity")
    if conf < cfg.trade_min_confidence:
        blocked.append("confidence")
    if es.final_risk is not None and es.final_risk > 60:
        blocked.append("final_risk")
    why = [f"EarlyScore {'—' if es.score is None else f'{es.score:.2f}'} (≥{es.theta}) · conf {es.confidence:.2f} "
           f"(≥{es.gamma}) · age {es.age_s:.0f}s [{es.bucket}] · risk obs {es.observed_risk} prior {es.prior_risk}"]
    waiting = list(dict.fromkeys(waiting))
    if hard:
        return ExpDecision(REJECT, es, blocked, waiting, hard, ["reject: " + ", ".join(hard)] + why)
    if ident != "VERIFIED":
        return ExpDecision(PENDING_IDENTITY, es, blocked, waiting, [], why)
    if not blocked:
        return ExpDecision(TRADE, es, [], [], [], why)
    return ExpDecision(WATCH, es, blocked, waiting, [], ["waiting: " + ", ".join(blocked[:4])] + why)


def old_candidate(st: TokenState, rec: dict) -> bool:
    """What the OLD engine would call a Trade Candidate (before the Risk Engine), for A/B."""
    return (st.early is not None and st.early.is_early is True and st.identity.status == "VERIFIED"
            and rec.get("old_decision", rec.get("decision")) == TRADE and bool(rec.get("vet_passed")))
