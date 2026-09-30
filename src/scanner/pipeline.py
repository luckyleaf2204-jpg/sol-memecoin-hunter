"""The evaluation pipeline — fixed order (spec §18), no shortcuts:

  DISCOVERY               engine.discover()
  DATA VALIDATION         ingest_market(): raw DexScreener pair -> validate_market() -> history point
                          evaluate(): assess_quality()
  ON-CHAIN VALIDATION     engine._deep(): holders / dev, each result stamped + verified flags
  MARKET STRUCTURE        intel.market + intel.liquidity
  HOLDER ANALYSIS         intel.holders
  DEV ANALYSIS            dev metrics (verified data only)
  SMART MONEY             NOT AVAILABLE
  WHALE                   intel.whales
  SOCIAL                  links only (activity NOT AVAILABLE)
  NARRATIVE               keyword tags (score NOT AVAILABLE)
  SECURITY                NOT AVAILABLE
  RISK ENGINE             risk.engine
  OPPORTUNITY ENGINE      scoring.subscores + scoring.opportunity  (None if INVALID)
  EARLY SIGNAL ENGINE     intel.early_signal (time-series deltas only) -> lifecycle
  RANKING                 scoring.ranking (VALID only)
"""
from __future__ import annotations

import statistics
import time

from core.config import Settings
from core.models import MarketData, SourceStamp, TokenState
from history.store import TokenHistory
from intel.early_signal import compute_early_signal
from intel.holders import analyze_holders
from intel.lifecycle import classify_lifecycle
from intel.liquidity import analyze_liquidity
from intel.market import analyze_market
from intel.metrics import MetricBuilder
from intel.whales import analyze_whales
from risk.engine import assess_risk
from scoring.filters import check_filters
from scoring.opportunity import compute_opportunity
from scoring.subscores import compute_subscores, early
from social.narrative import classify
from validation.market import validate_market
from validation.quality import assess_quality


def ingest_market(st: TokenState, market: MarketData, socials: dict[str, str], sol_price: float | None,
                  h: TokenHistory | None = None, now: float | None = None) -> None:
    """DATA VALIDATION: validate a fresh DexScreener observation, then record it in the history."""
    now = now or time.time()
    market.updated_at = now
    for k, v in socials.items():
        if not getattr(st.info, k):
            setattr(st.info, k, v)
    if market.dex_id and not market.is_curve and st.info.complete is None:
        st.info.complete = True
    st.market_issues = validate_market(market, st.info, sol_price, now)
    st.market = market
    st.stamps["market"] = SourceStamp("DexScreener /tokens/v1", now)
    if market.liquidity_source == "pumpfun_curve" and st.info.pump_updated_at:
        st.stamps["curve"] = SourceStamp("Pump.fun frontend-api-v3 (curve reserve)", st.info.pump_updated_at)
    if h is not None:
        h.add_market(market, now)   # validated values only (None where rejected)


def _dev_metrics(st: TokenState, M: MetricBuilder) -> None:
    d = st.dev
    ts = d.fetched_at if d else None
    ok = bool(d and d.balance_verified)
    src = d.balance_source if ok else "Solana RPC"
    M.add("dev_wallet", st.info.creator or None, "text", "dev", "Pump.fun / PumpPortal",
          st.info.pump_updated_at or st.info.discovered_at)
    M.add("dev_holding", d.current_pct if ok else None, "pct", "dev", src, ts, note="dev_unverified")
    M.add("dev_tokens", d.current_tokens if ok else None, "int", "dev", src, ts, note="dev_unverified")
    M.add("dev_status", d.status if ok else None, "text", "dev", src, ts, note="dev_unverified")
    M.add("dev_initial_buy", st.info.dev_initial_buy, "int", "dev", "PumpPortal", st.info.discovered_at,
          note="initial_buy_unknown")
    M.add("dev_sold", d.sold_pct if ok else None, "pct", "dev", src, ts, derived=True, note="initial_buy_unknown")
    M.add("dev_sol", d.sol_balance if d else None, "sol", "dev", "Solana RPC", ts, note="dev_unverified")
    hv = bool(d and d.history_verified)
    M.add("dev_prev_tokens", d.prev_tokens_count if hv else None, "int", "dev", "Pump.fun", ts, note="history_unavailable")
    M.add("dev_prev_graduated", d.prev_graduated if hv else None, "int", "dev", "Pump.fun", ts, note="history_unavailable")
    M.add("dev_prev_dead", d.prev_dead if hv else None, "int", "dev", "Pump.fun", ts, note="history_unavailable")
    no_hist = "history_unavailable" if not hv else "no_prev_tokens"
    M.add("dev_best_ath", d.prev_best_ath if hv else None, "usd", "dev", "Pump.fun", ts, note=no_hist)
    gap = None
    if hv and d.prev_tokens:
        times = sorted(t["created_at"] for t in d.prev_tokens if t.get("created_at"))
        if len(times) >= 2:
            gap = statistics.median(b - a for a, b in zip(times, times[1:])) / 3600
    M.add("dev_launch_gap", gap, "hours", "dev", "Pump.fun", ts, derived=True, note=no_hist)
    M.add("dev_funding", d.funding_wallet if d else None, "text", "dev", "Solana RPC", ts, note="funding_unknown")
    M.add("dev_funding_sol", d.funding_sol if d else None, "sol", "dev", "Solana RPC", ts, note="funding_unknown")
    for k in ("dev_transfers", "dev_related_wallets", "dev_cluster"):
        M.unknown(k, "text", "dev", "not_implemented")


def _not_available_sections(M: MetricBuilder) -> None:
    for k in ("sm_wallets", "sm_entries", "sm_exits", "sm_pnl"):
        M.unknown(k, "text", "smart_money", "not_implemented")
    for k in ("cluster_risk", "common_funding", "synced_buys", "bundle", "snipers"):
        M.unknown(k, "text", "cluster", "not_implemented")
    for k in ("x_followers", "x_mentions", "x_engagement", "tg_members", "tg_velocity", "discord_members"):
        M.unknown(k, "text", "social", "not_implemented")
    for k in ("mint_authority", "freeze_authority", "metadata_mutable", "transfer_fee"):
        M.unknown(k, "text", "security", "not_implemented")


def evaluate(st: TokenState, s: Settings, h: TokenHistory | None = None, now: float | None = None,
             sol_price: float | None = None) -> None:
    now = now or time.time()
    h = h if h is not None else TokenHistory()
    M = MetricBuilder(now, s.scan_interval_sec)

    # DATA VALIDATION -> quality (ingest already validated the market fields)
    st.quality = assess_quality(st, s.scan_interval_sec, now)
    # MARKET STRUCTURE
    mi = analyze_market(st, h, M)
    st.liquidity_intel = analyze_liquidity(st, h, M, sol_price)
    # HOLDER / DEV / SMART MONEY / WHALE
    st.holder_intel = analyze_holders(st, h, M)
    _dev_metrics(st, M)
    st.whale_intel = analyze_whales(st, h, M)
    # SOCIAL (links only)
    i = st.info
    if i.twitter or i.telegram or i.website:
        src = "Pump.fun" if i.pump_updated_at else "DexScreener"
        st.stamps["social_links"] = SourceStamp(f"{src} metadata (links only)",
                                                i.pump_updated_at or st.stamps.get("market", SourceStamp("", now)).updated_at)
    ts_links = st.stamps["social_links"].updated_at if "social_links" in st.stamps else None
    for k in ("twitter", "telegram", "website"):
        M.add(f"link_{k}", getattr(i, k) or None, "text", "social", "metadata", ts_links, note="not_listed")
    # NARRATIVE (tags are data; narrative score NOT AVAILABLE)
    st.narratives = classify(i.name, i.symbol)
    M.add("narrative_tags", ", ".join(st.narratives), "text", "narrative", "keyword rules (name/symbol)", now, derived=True)
    _not_available_sections(M)
    # RISK ENGINE
    st.risk = assess_risk(st, s, market_intel=mi)
    # OPPORTUNITY ENGINE
    st.subscores = compute_subscores(st, s, mi)
    st.score = compute_opportunity(st, st.subscores)
    # EARLY SIGNAL ENGINE (time-series based) -> lifecycle
    st.early = compute_early_signal(st, h, now, M, st.holder_intel, st.whale_intel, st.risk)
    st.subscores["early_signal"] = early(st)
    st.lifecycle = classify_lifecycle(st, st.early, st.whale_intel, mi.drawdown_pct, mi.breakout)
    st.filter_fails = check_filters(st, s)
    # overview metrics
    M.add("opportunity", st.score.total if st.score else None, "int", "overview", "opportunity engine", now,
          derived=True, note="invalid_not_scored")
    M.add("risk", st.risk.score, "int", "overview", "risk engine", now, derived=True)
    M.add("data_quality", st.quality.score, "int", "overview", "validation", now, derived=True)
    M.add("lifecycle", st.lifecycle if st.lifecycle != "UNKNOWN" else None, "text", "overview", "lifecycle rules",
          now, derived=True, note="needs_market")
    M.add("age", st.age_minutes, "min", "overview", "Pump.fun / DexScreener",
          st.stamps["market"].updated_at if "market" in st.stamps else None)
    st.metrics = M.items
