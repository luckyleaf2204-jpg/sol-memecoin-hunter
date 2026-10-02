"""SCAN -> VET -> SCORE -> SIZE. Reads scanner results (TokenState); never modifies them.

SCAN    candidates = tokens the scanner already flags: ⚡ PRE-EARLY, Early Signal TRUE (D1–D8, unchanged),
        🔥 group, or a momentum lifecycle (EARLY_MOMENTUM / MOMENTUM / BREAKOUT) with Opportunity >= 50.
        X Alpha and smart-money wallet signals have NO data source yet -> listed as NOT AVAILABLE, never faked.
VET     every check must PASS — including EARLY SIGNAL = TRUE (Early Signal / D1–D8 exactly as computed by the
        scanner; UNKNOWN or FALSE never trades, whatever Momentum or Opportunity say). UNKNOWN blocks (no data = no trade). "N/A" is only used for checks whose source
        does not exist yet (X Alpha) and is shown as such; it never counts in favour of a trade.
SCORE   component scores 0-100 reuse the scanner's sub-scores (None = not available):
          momentum (subscore momentum) · onchain (holder / whale / onchain) · liquidity (subscore liquidity)
          early (Early Signal strength, else PRE-EARLY fired/6) · risk (100 - Risk) · x_alpha / smart_money: N/A
        Opportunity = weighted mean over AVAILABLE components (weights momentum 30, onchain 20, liquidity 15,
        risk 20, early 15). Confidence = share of the total weight backed by data (×0.8 when DQ is PARTIAL).
        TRADE  vet passed, Opportunity >= trade_min_opportunity and Confidence >= trade_min_confidence
               (UNCHANGED — the only way to a trade)
        WATCH  "waiting for confirmation": no real failure, but data still missing (UNKNOWN / PENDING) or a trade
               threshold not reached yet; `waiting` lists exactly what is missing (Early Signal, holder data, VET,
               Risk, identity verification, market data, opportunity / confidence)
        PENDING_IDENTITY  identity not verified yet and no conflict: classified WATCH / REJECT only once the
               identity is known; never rejected for it, never bought
        Early Signal FALSE rejects only when confirmed on all 7 signal groups; FALSE with groups still missing
               waits (WATCH), because more data may change it. UNKNOWN always waits.
        REJECT only for a REAL reason: identity CONFLICT, a check that really FAILED (incl. Early Signal FALSE,
               liquidity, rug / shock / Risk > 60, dev dump, authorities, Token-2022, migration, volume / buy
               pressure, wrong data), or Opportunity AND Momentum both KNOWN and both below the watch level.
               UNKNOWN / PENDING data is never a reason to reject.
SIZE    risk-based: equity × risk_per_trade / stop distance, then capped by max position %, % of pool liquidity,
        free cash and exposure room; scaled by Opportunity, Confidence and short-term volatility.
"""
from __future__ import annotations

import re
import time

from core.models import INVALID, VALID, TokenState
from trading.config import TradingConfig
from trading.models import FAIL, PASS, PENDING_IDENTITY, REJECT, TRADE, UNKNOWN, WATCH, Check, Score, Size, Vet

NA = "N/A"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
MOMENTUM_STAGES = ("EARLY_MOMENTUM", "MOMENTUM", "BREAKOUT")
DANGEROUS_EXT = {"permanent_delegate", "transfer_hook", "pausable_config", "default_account_state",
                 "transfer_fee_config", "confidential_transfer_mint", "non_transferable"}
T22 = "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"
WEIGHTS = {"momentum": 30, "onchain": 20, "liquidity": 15, "risk": 20, "early": 15, "x_alpha": 0, "smart_money": 0}
MIN_HOLDERS, MAX_TOP10, MAX_DEV_PCT, MIN_VOL_5M, MIN_BUY_SHARE = 50, 35.0, 10.0, 5_000.0, 0.5
MAX_HOLDER_AGE_S = 900          # cached Helius holder data older than this is UNKNOWN for VET
SOFT = {"volume_buy_pressure", "liquidity", "holders", "early_signal"}   # kept for reference (old mapping)
WATCH_MIN_MOMENTUM = 70
# which "waiting for" group an UNKNOWN check belongs to
WAIT_GROUP = {"early_signal": "early_signal", "holders": "holders", "top_holders": "holders", "dev": "vet_dev",
              "authorities": "vet_onchain", "token_2022": "vet_onchain", "ca": "identity", "identity": "identity",
              "liquidity": "market_data", "volume_buy_pressure": "market_data", "data_quality": "market_data",
              "rug": "risk", "migration": "market_data"}
# VET FAILs that are "not yet" rather than "wrong": too little volume / holders, concentrated early supply, a pair change
# in the last 5 min. They still fail VET (so they can never TRADE) but the token is WATCHed, not REJECTed.
SOFT_FAIL = {"volume_buy_pressure", "holders", "top_holders", "migration"}
EARLY_WATCH_MIN_RANK = 60      # failing only these -> WATCH, not REJECT


# ---------------------------------------------------------------- SCAN
def scan_reasons(st: TokenState) -> list[str]:
    out = []
    pe = st.pre_early
    if pe is not None and getattr(pe, "is_pre_early", False):
        out.append("pre_early")
    if st.early and st.early.is_early is True:
        out.append("early_signal")
    if st.group == "opportunity":
        out.append("opportunity_group")
    if st.lifecycle in MOMENTUM_STAGES and st.score and st.score.total >= 50:
        out.append("momentum")
    ew = st.early_watch
    if ew is not None and getattr(ew, "rank", None) is not None and ew.rank >= EARLY_WATCH_MIN_RANK:
        out.append("early_watch")
    return out


def scan(states: list[TokenState]) -> list[tuple[TokenState, list[str]]]:
    """EVERY tracked token is classified (WATCH / PENDING_IDENTITY / REJECT / TRADE) so none is lost for missing data.
    This cannot create a new trade: TRADE needs Early Signal TRUE, which is itself a scan reason, so tokens without a
    reason (sorted after all others, same key as before) can never be TRADE."""
    cands = [(st, scan_reasons(st)) for st in states]
    cands.sort(key=lambda x: (-len(x[1]), -((x[0].score.total if x[0].score else 0))))
    return cands


# ---------------------------------------------------------------- VET
def _market_age(st: TokenState, now: float) -> float | None:
    s = st.stamps.get("market")
    return None if not s else now - s.updated_at


def vet(st: TokenState, cfg: TradingConfig, now: float | None = None) -> Vet:
    now = now or time.time()
    m, h, d, ident, rk = st.market, st.holders, st.dev, st.identity, st.risk
    C = []

    es = st.early
    C.append(Check("early_signal", PASS if es is not None and es.is_early is True else
                   (UNKNOWN if es is None or es.strength is None else FAIL),
                   "TRUE" if es is not None and es.is_early is True else
                   ("UNKNOWN" if es is None or es.strength is None else f"FALSE ({es.strength})"),
                   "Early Signal TRUE (D1–D8, unchanged) is mandatory for a trade"))
    C.append(Check("identity", PASS if ident.status == "VERIFIED" else FAIL, ident.status,
                   "symbol confirmed by a source looked up by this CA, no conflict"))
    ca_ok = bool(MINT_RE.match(st.mint)) and "dexscreener" in ident.claims
    C.append(Check("ca", PASS if ca_ok else (UNKNOWN if MINT_RE.match(st.mint) else FAIL),
                   st.mint[:6] + "…" + st.mint[-4:], "valid base58 CA, DexScreener pair whose base token == this CA"))
    age = _market_age(st, now)
    fresh = age is not None and age <= cfg.max_data_age_s
    dq_ok = st.dq_status != INVALID and fresh
    C.append(Check("data_quality", PASS if dq_ok else FAIL,
                   f"{st.dq_status}, market {age:.0f}s old" if age is not None else f"{st.dq_status}, no market data",
                   f"not INVALID and market data <= {cfg.max_data_age_s:.0f}s old"))
    liq = m.liquidity_usd if m else None
    C.append(Check("liquidity", UNKNOWN if liq is None else (PASS if liq >= cfg.min_liquidity_usd else FAIL),
                   f"${liq:,.0f}" if liq is not None else "", f">= ${cfg.min_liquidity_usd:,.0f}"))
    hstamp = st.stamps.get("holders")
    holders_fresh = hstamp is None or now - hstamp.updated_at <= MAX_HOLDER_AGE_S
    holders_ok = h is not None and h.valid and st.holder_status == "ok" and h.holder_count is not None and holders_fresh
    C.append(Check("holders", UNKNOWN if not holders_ok else (PASS if h.holder_count >= MIN_HOLDERS else FAIL),
                   str(h.holder_count) if holders_ok else ("stale (> 15 min)" if h is not None and not holders_fresh
                                                          else (st.holder_status or "no data")),
                   f">= {MIN_HOLDERS} (Helius, <= 15 min old)"))
    top = h.top10_pct if holders_ok else None
    C.append(Check("top_holders", UNKNOWN if top is None else (PASS if top <= MAX_TOP10 else FAIL),
                   f"top10 {top:.1f}%" if top is not None else "", f"top10 <= {MAX_TOP10:.0f}%"))
    if d and d.balance_verified:
        bad = d.status in ("SOLD ALL", "MAJOR SELL") or (d.current_pct or 0) > MAX_DEV_PCT
        C.append(Check("dev", FAIL if bad else PASS, f"{d.status}, holds {d.current_pct or 0:.1f}%",
                       f"not SOLD ALL / MAJOR SELL, holds <= {MAX_DEV_PCT:.0f}%"))
    else:
        C.append(Check("dev", UNKNOWN, "unverified", "dev balance verified on-chain"))
    if ident.helius_checked:
        auth = [x for x, v in (("mint", ident.mint_authority), ("freeze", ident.freeze_authority)) if v]
        C.append(Check("authorities", FAIL if auth else PASS, ("active: " + ", ".join(auth)) if auth else "revoked",
                       "mint & freeze authority revoked (Helius getAsset)"))
        ext = sorted(set(ident.extensions) & DANGEROUS_EXT)
        C.append(Check("token_2022", FAIL if ext else PASS,
                       ("Token-2022: " + ", ".join(ext)) if ext else ("Token-2022, safe extensions" if ident.token_program == T22 else "SPL Token"),
                       "no permanent delegate / transfer hook / pausable / transfer fee / frozen default"))
    else:
        C.append(Check("authorities", UNKNOWN, "not checked yet", "Helius getAsset needed"))
        C.append(Check("token_2022", UNKNOWN, "not checked yet", "Helius getAsset needed"))
    rug = [f.key for f in rk.factors if f.category == "rug"] if rk else []
    shock = bool(st.liquidity_intel and st.liquidity_intel.state == "SHOCK")
    rug_ok = rk is not None and not rug and not shock and rk.score <= 60
    C.append(Check("rug", PASS if rug_ok else (UNKNOWN if rk is None else FAIL),
                   ", ".join(rug + (["liquidity SHOCK"] if shock else [])) or (f"risk {rk.score}" if rk else ""),
                   "no rug-category flag, no liquidity shock, Risk <= 60"))
    recent_break = any(e.type == "DATA_BREAK" and now - e.ts < 300 for e in st.recent_events)
    C.append(Check("migration", FAIL if recent_break else PASS,
                   "pair changed < 5 min ago" if recent_break else ("bonding curve" if m and m.is_curve else "stable pair"),
                   "no graduation / migration in the last 5 min"))
    bs = (m.buys_5m / m.txns_5m) if m and m.txns_5m else None
    vol = m.vol_5m if m else None
    if vol is None or bs is None:
        C.append(Check("volume_buy_pressure", UNKNOWN, "", f"vol 5m >= ${MIN_VOL_5M:,.0f} and buys >= 50 %"))
    else:
        C.append(Check("volume_buy_pressure", PASS if vol >= MIN_VOL_5M and bs >= MIN_BUY_SHARE else FAIL,
                       f"${vol:,.0f} / {100 * bs:.0f}% buys", f"vol 5m >= ${MIN_VOL_5M:,.0f} and buys >= 50 %"))
    C.append(Check("x_alpha", NA, "NOT AVAILABLE", "no X data source (no scraping) — not counted"))
    return Vet(st.mint, C)


def vet_passed(v: Vet) -> bool:
    return bool(v.checks) and all(c.result in (PASS, NA) for c in v.checks)


# ---------------------------------------------------------------- SCORE
def _sub(st: TokenState, key: str) -> int | None:
    s = st.subscores.get(key)
    return s.score if s and s.score is not None else None


def score(st: TokenState, v: Vet, cfg: TradingConfig) -> Score:
    comp: dict[str, int | None] = {"x_alpha": None, "smart_money": None}
    comp["momentum"] = _sub(st, "momentum")
    onchain = [x for x in (_sub(st, "holder"), _sub(st, "whale"), _sub(st, "onchain")) if x is not None]
    comp["onchain"] = round(sum(onchain) / len(onchain)) if onchain else None
    comp["liquidity"] = _sub(st, "liquidity")
    if st.early and st.early.strength is not None:
        comp["early"] = st.early.strength
    elif st.pre_early is not None and getattr(st.pre_early, "computable", 0) >= 3:
        comp["early"] = round(100 * st.pre_early.fired / st.pre_early.total)
    else:
        comp["early"] = None
    comp["risk"] = (100 - st.risk.score) if st.risk else None
    total_w = sum(WEIGHTS.values())
    avail = {k: v_ for k, v_ in comp.items() if v_ is not None and WEIGHTS.get(k)}
    aw = sum(WEIGHTS[k] for k in avail)
    opp = round(sum(WEIGHTS[k] * avail[k] for k in avail) / aw) if aw else None
    conf = round(100 * aw / total_w * (1.0 if st.dq_status == VALID else 0.8))

    why = [f"{k} {val}" for k, val in sorted(avail.items(), key=lambda x: -WEIGHTS[x[0]] * x[1])[:4]]
    why += [f"scan: {r}" for r in scan_reasons(st)]
    invalidate = [f"price <= stop (-{cfg.stop_loss_pct:.0f}%)", "liquidity drops > 30 % or SHOCK",
                  "Risk > 60 or a rug flag", "buy share < 50 % / volume collapse", "identity conflict or holder anomaly",
                  "market data stale or INVALID"]
    waiting: list[str] = []
    rejected: list[str] = []
    if not vet_passed(v):
        decision, waiting, rejected = _classify_untradable(st, v, opp, comp["momentum"], cfg)
        why = [f"vet {c.key}: {c.result} {c.value}".strip() for c in v.checks if c.result not in (PASS, NA)] + why
    elif opp is not None and opp >= cfg.trade_min_opportunity and conf >= cfg.trade_min_confidence:
        decision = TRADE
    elif opp is not None and opp >= cfg.watch_min_opportunity:
        decision = WATCH
        waiting = ["opportunity"] if opp < cfg.trade_min_opportunity else []
        waiting += ["confidence"] if conf < cfg.trade_min_confidence else []
    else:
        decision, waiting, rejected = _classify_untradable(st, v, opp, comp["momentum"], cfg)
    if decision in (WATCH, PENDING_IDENTITY) and waiting:
        why = ["waiting: " + " + ".join(waiting)] + why
    elif decision == REJECT and rejected:
        why = ["reject: " + ", ".join(rejected)] + why
    return Score(st.mint, comp, opp, conf, decision, why, invalidate, waiting, rejected)


def _classify_untradable(st: TokenState, v: Vet, opp: int | None, mom: int | None, cfg: TradingConfig):
    """WATCH / PENDING_IDENTITY / REJECT for a token that cannot trade (yet). Never changes what TRADE requires."""
    from scoring.groups import _missing_only
    rejected, waiting = [], []
    pending_identity = False
    if st.identity.status == "CONFLICT":
        rejected.append("identity_conflict")
    elif st.identity.status != "VERIFIED":
        pending_identity = True                          # no canonical source yet: data missing, not a conflict
        waiting.append("identity")
    for c in v.checks:
        if c.result in (PASS, NA):
            continue
        if c.result == FAIL:
            if c.key == "identity":
                continue                                 # handled above (CONFLICT rejects, UNVERIFIED waits)
            if c.key == "early_signal" and st.early is not None and st.early.groups_computable < 7:
                waiting.append("early_signal")           # FALSE with groups still missing: more data may change it
                continue
            if c.key == "data_quality":
                if st.dq_status == INVALID and not _missing_only(st) and not _no_activity_only(st):
                    rejected.append("data_invalid")      # wrong data
                else:
                    waiting.append("market_data")        # stale / not yet available
                continue
            if c.key in SOFT_FAIL:
                waiting.append(WAIT_GROUP.get(c.key, "vet"))  # not yet enough volume/holders: may still develop
                continue
            rejected.append(c.key)                       # a real failure
        else:                                            # UNKNOWN = not enough data yet
            waiting.append(WAIT_GROUP.get(c.key, "vet"))
    waiting = list(dict.fromkeys(waiting))
    if pending_identity:
        return PENDING_IDENTITY, waiting, []           # WATCH or REJECT is decided once the identity is known
    if rejected:
        return REJECT, waiting, rejected
    opp_low = opp is not None and opp < cfg.watch_min_opportunity
    mom_low = mom is not None and mom < WATCH_MIN_MOMENTUM
    if opp_low and mom_low:                              # both KNOWN and both weak: a real reason
        return REJECT, waiting, ["low_opportunity"]
    if opp is None or mom is None:
        waiting.append("scores")
    if opp is not None and opp < cfg.trade_min_opportunity:
        waiting.append("opportunity")
    return WATCH, list(dict.fromkeys(waiting)), []


def _no_activity_only(st: TokenState) -> bool:
    """INVALID only because data is missing or 5m/1h volume is 0 (no trades yet): not yet, not wrong."""
    from scoring.groups import MISSING_KEYS, RAW_MISSING_KEYS
    crit = [i for i in (st.quality.issues if st.quality else []) if i.severity == "critical"]
    return bool(crit) and all(
        i.key in MISSING_KEYS or (i.key in RAW_MISSING_KEYS and i.params.get("raw") in (None, "None"))
        or (i.key == "volume_bad" and i.params.get("raw") in ("0", "0.0", 0, 0.0)) for i in crit)


def trade_blockers(st: TokenState, v: Vet, sc: Score, cfg: TradingConfig) -> list[str]:
    """READ-ONLY diagnostics: every gate that currently stops this token from being a Trade Candidate, in gate
    order (identity, early, risk, liquidity, VET, opportunity, confidence). Decides nothing."""
    out = []
    ident = st.identity.status
    if ident != "VERIFIED":
        out.append("identity_conflict" if ident == "CONFLICT" else "identity_pending")
    es = st.early
    if es is None or es.strength is None:
        out.append("early_unknown")
    elif es.is_early is not True:
        out.append("early_false" if es.groups_computable >= 7 else "early_false_partial")
    checks = {c.key: c for c in v.checks}
    rug = checks.get("rug")
    if rug is not None and rug.result != PASS:
        out.append("risk" if rug.result == FAIL else "risk_unknown")
    liq = checks.get("liquidity")
    if liq is not None and liq.result != PASS:
        out.append("liquidity" if liq.result == FAIL else "liquidity_unknown")
    for c in v.checks:
        if c.key in ("identity", "early_signal", "rug", "liquidity") or c.result in (PASS, NA):
            continue
        out.append(("vet:" if c.result == FAIL else "vet_unknown:") + c.key)
    if sc.opportunity is None:
        out.append("opportunity_unknown")
    elif sc.opportunity < cfg.trade_min_opportunity:
        out.append("opportunity")
    if sc.confidence < cfg.trade_min_confidence:
        out.append("confidence")
    return out


# ---------------------------------------------------------------- SIZE
def size(st: TokenState, sc: Score, cfg: TradingConfig, equity: float, cash: float, exposure: float) -> Size:
    reasons = []
    risk_usd = equity * cfg.risk_per_trade_pct / 100
    from trading.exit_options import stop_pct
    stop = stop_pct(cfg)                       # wide_stop_pct (A/B) or stop_loss_pct: risk at the stop stays fixed
    usd = risk_usd / (stop / 100)
    reasons.append(f"risk {cfg.risk_per_trade_pct}% of ${equity:,.0f} / stop {stop:.0f}% = ${usd:,.0f}")
    q = 0.5 + 0.5 * max(0.0, min(1.0, ((sc.opportunity or 0) - cfg.trade_min_opportunity) / max(1, 100 - cfg.trade_min_opportunity)))
    usd *= q * (sc.confidence / 100)
    reasons.append(f"× opportunity factor {q:.2f} × confidence {sc.confidence}%")
    pc5 = st.market.price_change_5m if st.market else None
    if pc5 is not None and abs(pc5) > 50:
        usd *= 0.5
        reasons.append(f"× 0.5 volatility (5m change {pc5:+.0f}%)")
    caps = {"max_position": equity * cfg.max_position_pct / 100,
            "pool_liquidity": (st.market.liquidity_usd or 0) * cfg.max_liquidity_pct / 100 if st.market else 0.0,
            "cash": cash,
            "exposure": max(0.0, equity * cfg.max_total_exposure_pct / 100 - exposure)}
    capped_by = ""
    for k, cap in caps.items():
        if usd > cap:
            usd, capped_by = cap, k
    if capped_by:
        reasons.append(f"capped by {capped_by} (${caps[capped_by]:,.0f})")
    if usd < cfg.min_position_usd:
        reasons.append(f"below minimum ${cfg.min_position_usd:,.0f} -> no trade")
        usd = 0.0
    return Size(round(usd, 2), reasons, capped_by)
