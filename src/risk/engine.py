"""RISK ENGINE — additive, explained, separate from Opportunity.

RISK = min(100, Σ factor points). Levels: 0-30 LOW · 31-60 MEDIUM · 61-80 HIGH · 81-100 EXTREME.
Each factor has a category; category risk = min(100, Σ points in category × 2)
(MANIPULATION RISK and RUG RISK are shown separately). UNKNOWN inputs add explicit "unknown"
factors — missing data is never treated as safe.

Category   Factor (points)
data       invalid_market (20)
liquidity  liquidity_unknown (15) · low_liquidity (15 / 7) · mc_liq_high (15 / 7) · bonding_curve (5)
           liquidity_falling (8)
           curve_drain (10: bonding-curve reserve fell ≥ 30 % in ≤ 5 min — sell-off, not LP removal)
rug        liquidity_shock (25, AMM only) · dev_dump (10) · dev_sold_recent (10)
holders    holders_unknown (10) · top10_high (25 / 15 / 7) · single_whale (10) · few_holders (8)
           whale_distribution (10) · high_churn (10)
dev        dev_unknown (8) · dev_concentration (15 / 8) · serial_launcher (15) · dev_history_bad (8) · dev_snipe (10)
age        age_unknown (5) · new_token (5)
manipulation  volume_anomaly (10) · wash_pattern (8) · volume_collapse (10) · sudden_spike (8) · sell_pressure (8)
           suspicious_holders (15) · volume_without_holders (10) · pump_without_liquidity (8)
social     no_socials (5)
Not measurable yet (listed as missing): wallet_cluster, bundle_sniper, social_spam, contract_security.
"""
from __future__ import annotations

from core.config import Settings
from core.models import INVALID, RiskFactor, RiskResult, TokenState
from i18n import t

DEX, RPC, PUMP = "DexScreener", "Solana RPC", "Pump.fun"
CATEGORIES = ("data", "liquidity", "rug", "holders", "dev", "age", "manipulation", "social")
ALWAYS_MISSING = ["wallet_cluster", "bundle_sniper", "social_spam", "contract_security"]


def risk_level(score: int) -> str:
    if score <= 30:
        return "LOW"
    if score <= 60:
        return "MEDIUM"
    if score <= 80:
        return "HIGH"
    return "EXTREME"


def assess_risk(st: TokenState, s: Settings | None = None, market_intel=None) -> RiskResult:
    s = s or Settings()
    F: list[RiskFactor] = []
    missing = list(ALWAYS_MISSING)
    m, h, d, info = st.market, st.holders, st.dev, st.info
    li, hi, wi = st.liquidity_intel, st.holder_intel, st.whale_intel

    def add(key, pts, cat, source, **params):
        F.append(RiskFactor(key, pts, cat, params, source))

    # data
    if st.quality and st.quality.status == INVALID:
        crit = [i.key for i in st.quality.issues if i.severity == "critical"]
        add("invalid_market", 20, "data", "validation", issues=len(crit))

    # liquidity
    if m and m.liquidity_usd is not None:
        src = DEX if m.liquidity_source == "dexscreener_amm" else f"{PUMP} curve"
        if m.liquidity_usd < s.min_liquidity * 0.5:
            add("low_liquidity", 15, "liquidity", src, value=round(m.liquidity_usd), min=round(s.min_liquidity))
        elif m.liquidity_usd < s.min_liquidity:
            add("low_liquidity", 7, "liquidity", src, value=round(m.liquidity_usd), min=round(s.min_liquidity))
        r = m.mc_liq_ratio
        if r is not None and m.liquidity_source == "dexscreener_amm":
            if r > 20:
                add("mc_liq_high", 15, "liquidity", DEX, ratio=round(r, 1))
            elif r > 10:
                add("mc_liq_high", 7, "liquidity", DEX, ratio=round(r, 1))
        if m.liquidity_source == "pumpfun_curve":
            add("bonding_curve", 5, "liquidity", PUMP, value=round(m.liquidity_usd))
    else:
        add("liquidity_unknown", 15, "liquidity", "validation")
        missing.append("liquidity")
    curve = bool(m and m.liquidity_source == "pumpfun_curve")
    if li and li.state == "SHOCK" and curve:
        add("curve_drain", 10, "liquidity", "snapshots", drop=round(li.max_drop_5m_pct or 0, 1))
    elif li and li.state == "SHOCK":
        add("liquidity_shock", 25, "rug", "snapshots", drop=round(li.max_drop_5m_pct or 0, 1))
    elif li and li.state == "FALLING":
        add("liquidity_falling", 8, "liquidity", "snapshots", change=round(li.change_15m_pct or 0, 1))

    # holders
    if h and h.top10_pct is not None:
        src = h.source
        t10 = h.top10_pct
        if t10 > 50:
            add("top10_high", 25, "holders", src, value=round(t10, 1))
        elif t10 > 35:
            add("top10_high", 15, "holders", src, value=round(t10, 1))
        elif t10 > 25:
            add("top10_high", 7, "holders", src, value=round(t10, 1))
        if h.max_single_pct and h.max_single_pct > 10:
            add("single_whale", 10, "holders", src, value=round(h.max_single_pct, 1))
        if h.holder_count is not None and h.holder_count < 50 and m and (m.market_cap or 0) > 50_000:
            add("few_holders", 8, "holders", src, count=h.holder_count, mc=round(m.market_cap))
    else:
        from intel.holders import holder_reason
        add("holders_unknown", 10, "holders", "Helius DAS", reason=t(f"note.{holder_reason(st)}"))
        missing.append("holders")
    if wi and wi.state == "DISTRIBUTION":
        add("whale_distribution", 10, "holders", "holder snapshots", delta=wi.delta_pct)
    if hi and hi.organic == "SUSPICIOUS":
        add("suspicious_holders", 15, "manipulation", h.source if h else "holders", flags=",".join(hi.flags))
    if hi and hi.churn_pct is not None and hi.churn_pct >= 30:
        add("high_churn", 10, "holders", h.source if h else "holders", value=round(hi.churn_pct, 1))

    # dev
    if d and d.balance_verified:
        if d.current_pct is not None and d.current_pct > 10:
            add("dev_concentration", 15, "dev", d.balance_source, value=round(d.current_pct, 2))
        elif d.current_pct is not None and d.current_pct > 5:
            add("dev_concentration", 8, "dev", d.balance_source, value=round(d.current_pct, 2))
        if d.status in ("SOLD ALL", "MAJOR SELL"):
            add("dev_dump", 10, "rug", d.balance_source, status=d.status)
    else:
        add("dev_unknown", 8, "dev", RPC)
        missing.append("dev")
    if any(e.type == "DEV_SELL" for e in st.recent_events):
        add("dev_sold_recent", 10, "rug", RPC)
    if d and d.history_verified and d.prev_tokens_count:
        if d.prev_tokens_count >= 10 and d.prev_graduated == 0:
            add("serial_launcher", 15, "dev", PUMP, count=d.prev_tokens_count)
        elif d.prev_tokens_count >= 3 and (d.prev_dead or 0) / d.prev_tokens_count > 0.8:
            add("dev_history_bad", 8, "dev", PUMP, dead=d.prev_dead, count=d.prev_tokens_count)
    if info.dev_initial_buy and info.total_supply:
        pct = 100 * info.dev_initial_buy / info.total_supply
        if pct > 10:
            add("dev_snipe", 10, "dev", "PumpPortal", value=round(pct, 1))

    # age
    age = st.age_minutes
    if age is None:
        add("age_unknown", 5, "age", "—")
    elif age < 10:
        add("new_token", 5, "age", PUMP, minutes=round(age))

    # manipulation (validated fields only)
    if m and m.market_cap:
        if m.vol_5m is not None and m.vol_5m / m.market_cap > 3:
            add("volume_anomaly", 10, "manipulation", DEX, ratio=round(m.vol_5m / m.market_cap, 1))
        bs, tx = m.buy_sell_ratio_5m, m.txns_5m
        if (tx or 0) >= 40 and bs is not None and 0.9 <= bs <= 1.1 and m.price_change_5m is not None \
                and abs(m.price_change_5m) < 2 and m.vol_5m and m.vol_5m / m.market_cap > 0.5:
            add("wash_pattern", 8, "manipulation", DEX)
        if m.vol_1h and m.vol_1h > 20_000 and m.vol_accel is not None and m.vol_accel < 0.2:
            add("volume_collapse", 10, "manipulation", DEX, pace=round(m.vol_accel * 100))
        if m.price_change_5m is not None and m.price_change_5m > 100:
            add("sudden_spike", 8, "manipulation", DEX, value=round(m.price_change_5m))
        if bs is not None and (tx or 0) >= 20 and bs < 0.6:
            add("sell_pressure", 8, "manipulation", DEX, value=round(bs, 2))
    mi = market_intel
    if mi and mi.vol_trend_5m is not None and mi.vol_trend_5m >= 3 and hi and hi.growth_5m_pct is not None \
            and hi.growth_5m_pct < 1:
        add("volume_without_holders", 10, "manipulation", "snapshots",
            vol=round(mi.vol_trend_5m, 1), holders=round(hi.growth_5m_pct, 1))
    if mi and mi.price_change_15m is not None and mi.price_change_15m >= 50 and li and li.change_15m_pct is not None \
            and li.change_15m_pct <= 0 and m and m.liquidity_source == "dexscreener_amm":
        add("pump_without_liquidity", 8, "manipulation", "snapshots",
            price=round(mi.price_change_15m), liq=round(li.change_15m_pct, 1))

    if not (info.twitter or info.telegram or info.website):
        add("no_socials", 5, "social", f"{PUMP} / {DEX}")

    score = min(100, sum(f.points for f in F))
    cats = {c: min(100, 2 * sum(f.points for f in F if f.category == c)) for c in CATEGORIES}
    return RiskResult(score=score, level=risk_level(score), factors=F, missing=missing, categories=cats)
