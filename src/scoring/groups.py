"""Home-screen grouping — reads the existing results, changes none of them.

Order of checks (first match wins):

  ⛔ excluded     INVALID data · Risk HIGH/EXTREME (> 60) · any RUG-category risk flag (liquidity shock,
                  dev dump, recent dev sell) · liquidity state SHOCK · holder data INVALID (anomaly, D6)
                  · top10 above the user's max_top10_pct filter
  ⏳ nodata       no market data yet, INVALID only because data is MISSING / not yet indexed / stale (no
                  malformed value, no rug evidence), or Early Signal UNKNOWN (< 10 min history or < 4/7 groups).
                  Such a token is still INVALID for every score — it is only displayed as "not enough data".
  🔥 opportunity  data VALID + holder data verified + Early Signal computed and NOT suppressed, and
                  either Early Signal TRUE (engine unchanged, D1–D8) or [>= 3 signals fired AND
                  strength >= 50 AND Opportunity >= 60]. "Several confirming signals at once" — it is
                  NOT a buy recommendation.
  👀 watch        at least one Early Signal fired, or Opportunity >= 40
  ·  quiet        everything else (full data, nothing happening) — not shown on the home screen

UNKNOWN never counts as a signal: fired_count only counts signals that actually fired on valid data.
"""
from __future__ import annotations

from core.config import Settings
from core.models import INVALID, VALID, TokenState

GROUPS = ("opportunity", "watch", "nodata", "excluded")
OPP_MIN_FIRED, OPP_MIN_STRENGTH, OPP_MIN_SCORE = 3, 50, 60
WATCH_MIN_SCORE = 40
RISK_EXCLUDE = 60
# critical issues that mean "we do not have the data (yet)", not "the data is wrong"
MISSING_KEYS = {"no_market", "market_stale", "curve_unavailable", "curve_stale", "sol_price_missing", "pair_missing",
                "txns_missing", "liq_not_reported"}
RAW_MISSING_KEYS = {"price_bad", "mc_bad", "volume_bad", "fdv_bad"}      # missing only when the raw value was None


def _missing_only(st: TokenState) -> bool:
    crit = [i for i in (st.quality.issues if st.quality else []) if i.severity == "critical"]
    return bool(crit) and all(i.key in MISSING_KEYS or (i.key in RAW_MISSING_KEYS and i.params.get("raw") is None)
                              for i in crit)


def classify_group(st: TokenState, s: Settings | None = None) -> tuple[str, list[str]]:
    s = s or Settings()
    ex = []
    missing = st.dq_status == INVALID and _missing_only(st)
    if st.dq_status == INVALID and not missing:
        ex.append("dq_invalid")
    rk = st.risk
    if rk and rk.score > RISK_EXCLUDE and not missing:     # Risk of a data-less token is mostly "data" points
        ex.append("risk_high")
    if rk:
        ex += [f"rug:{f.key}" for f in rk.factors if f.category == "rug"]
    if st.liquidity_intel and st.liquidity_intel.state == "SHOCK" and "rug:liquidity_shock" not in ex:
        ex.append("liq_shock")
    if st.holder_status == "invalid":
        ex.append("holder_anomaly")
    h = st.holders
    if h and h.valid and h.top10_pct is not None and h.top10_pct > s.max_top10_pct:
        ex.append("top10")
    if ex:
        return "excluded", ex

    e = st.early
    nd = []
    if not st.market:
        nd.append("no_market")
    elif missing:
        nd.append("data_missing")
    if not e or e.strength is None:
        nd.append("early_unknown")
    if nd:
        return "nodata", nd

    opp = st.score.total if st.score else None
    holders_ok = st.holder_status == "ok" and st.holders is not None
    confirmed = e.is_early is True or (e.fired_count >= OPP_MIN_FIRED and e.strength >= OPP_MIN_STRENGTH
                                       and opp is not None and opp >= OPP_MIN_SCORE)
    if st.dq_status == VALID and holders_ok and not e.suppressed and confirmed:
        return "opportunity", ["early_true" if e.is_early else "multi_signal"]

    why = []
    if e.fired_count >= 1:
        why.append("signals")
    if opp is not None and opp >= WATCH_MIN_SCORE:
        why.append("opportunity")
    if why:
        if st.dq_status != VALID:
            why.append("dq_partial")
        if not holders_ok:
            why.append("holders_missing")
        if e.suppressed:
            why.append("suppressed")
        return "watch", why
    return "quiet", []


def assign_groups(states: list[TokenState], s: Settings | None = None) -> None:
    for st in states:
        st.group, st.group_reasons = classify_group(st, s)


def confirm_key(st: TokenState):
    """Default order inside 🔥: most confirming signals, then signal strength — not a buy/sell ranking."""
    e = st.early
    return (-(e.fired_count if e else 0), -((e.strength or 0) if e else 0))
