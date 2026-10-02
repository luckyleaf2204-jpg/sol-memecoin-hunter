"""Backtest the paper bot on stored SQLite snapshots, through the SAME PaperBot.tick (scan, vet, score, size,
risk, modelled execution, exits, P&L).

Snapshots keep validated market fields, holders/top10, sub-scores, Risk, Early Signal, lifecycle and data quality
as they were at that moment (no look-ahead). They do NOT keep live-only checks, so the result lists these
ASSUMPTIONS explicitly: identity VERIFIED, mint/freeze authority revoked, no dangerous Token-2022 extension,
dev balance verified (HOLD). Feeds are treated as healthy. Pre-Early is not replayable (needs the raw series).
"""
from __future__ import annotations

import json
from collections import defaultdict

from core.models import (DataQuality, EarlySignal, HolderStats, LiquidityIntel, MarketData, OpportunityResult,
                         RiskResult, SourceStamp, SubScore, TokenInfo, TokenState, WhaleIntel, DevReport)
from trading.bot import PaperBot
from trading.config import TradingConfig

ASSUMPTIONS = ["identity VERIFIED (not stored in snapshots)", "mint & freeze authority revoked (not stored)",
               "no dangerous Token-2022 extension (not stored)", "dev balance verified, status HOLD (not stored)",
               "data feeds healthy", "PRE-EARLY not replayable"]


class _Engine:
    def __init__(self):
        self.published: list[TokenState] = []
        self.sol_price = None

    def feeds(self) -> dict:
        return {"dexscreener": {"ok": True, "cooldown_s": 0}}


def state_from_row(r: dict) -> TokenState:
    ts = r["ts"]
    st = TokenState(info=TokenInfo(mint=r["mint"], symbol=r.get("symbol") or r["mint"][:6], created_at=r.get("created_at")))
    st.market = MarketData(price_usd=r.get("price"), market_cap=r.get("mc"), liquidity_usd=r.get("liquidity"),
                           vol_5m=r.get("vol_5m"), vol_1h=r.get("vol_1h"), buys_5m=r.get("buys_5m"),
                           sells_5m=r.get("sells_5m"), price_change_5m=r.get("pc_5m"),
                           liquidity_source=r.get("liquidity_source") or "", dex_id="pumpswap", pair_address="BT",
                           updated_at=ts)
    st.stamps["market"] = SourceStamp("snapshot", ts)
    st.quality = DataQuality(r.get("dq") or 0, r.get("dq_status") or "INVALID")
    if r.get("score") is not None:
        st.score = OpportunityResult(r["score"], 0, [], [])
    st.risk = RiskResult(r["risk"], "") if r.get("risk") is not None else None
    if r.get("early_signal") is not None or r.get("is_early") is not None:
        st.early = EarlySignal(r.get("early_signal"), bool(r.get("is_early")) if r.get("is_early") is not None else None, None)
    st.lifecycle = r.get("lifecycle") or "UNKNOWN"
    if r.get("holders") is not None:
        st.holders = HolderStats(holder_count=r["holders"], top10_pct=r.get("top10_pct"), source="snapshot")
        st.holder_status = "ok"
    try:
        subs = json.loads(r.get("subscores") or "{}")
    except ValueError:
        subs = {}
    st.subscores = {k: SubScore(k, v) for k, v in subs.items()}
    if r.get("liq_state"):
        st.liquidity_intel = LiquidityIntel(state=r["liq_state"])
    if r.get("whale_state"):
        st.whale_intel = WhaleIntel(state=r["whale_state"])
    # live-only checks: see ASSUMPTIONS
    st.identity.status, st.identity.helius_checked = "VERIFIED", True
    st.identity.claims["dexscreener"] = (st.info.symbol, "")
    st.dev = DevReport(creator="", balance_verified=True, current_pct=0.0, status="HOLD")
    return st


class _History:
    """Price history built from the replayed rows as time advances (no look-ahead): the lifecycle engine and the
    entry-location gate read it like the scanner's history store."""
    def __init__(self):
        self._h: dict = {}

    def add(self, r: dict) -> None:
        from history.store import Point, TokenHistory
        h = self._h.setdefault(r["mint"], TokenHistory())
        h.points.append(Point(r["ts"], r.get("price"), r.get("mc"), r.get("liquidity"), r.get("liquidity_source") or "",
                              r.get("vol_5m"), r.get("vol_1h"), r.get("buys_5m"), r.get("sells_5m"), None, None, "BT"))

    def get(self, mint):
        return self._h.get(mint)


def backtest(rows: list[dict], cfg: TradingConfig | None = None, allow_legacy: bool = False) -> dict:
    """Default and required: the PRODUCTION config (gated lifecycle engine). Another config is refused unless
    allow_legacy=True (unit tests of the old engine) — so a backtest never measures a strategy the bot does not run."""
    from trading.config import production_config
    cfg = cfg or production_config(latency_probe=False)
    if not allow_legacy and not (cfg.lifecycle and cfg.entry_location_gate):
        raise ValueError("backtest must use the gated lifecycle config (trading.config.production_config)")
    eng = _Engine()
    eng.history = _History()
    bot = PaperBot(eng, cfg)
    by_ts: dict[float, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("dq_status") is None:          # legacy V1 rows: excluded (as in the scanner backtest)
            continue
        by_ts[r["ts"]].append(r)
    latest: dict[str, TokenState] = {}
    for ts in sorted(by_ts):
        for r in by_ts[ts]:
            latest[r["mint"]] = state_from_row(r)
            eng.history.add(r)                    # only rows at or before this frame
        eng.published = list(latest.values())
        bot.tick(now=ts)
    s = bot.book.stats(max(by_ts) if by_ts else None)
    return {"stats": s, "assumptions": ASSUMPTIONS, "frames": len(by_ts), "config": {
                "sample_id": cfg.sample_id(), "lifecycle": cfg.lifecycle, "entry_location_gate": cfg.entry_location_gate,
                "legacy": not (cfg.lifecycle and cfg.entry_location_gate)},
            "trades": [{"symbol": p.symbol, "mint": p.mint, "opened": p.opened_at, "closed": p.closed_at,
                        "net": round(p.realized_usd - p.cost_usd, 2), "exit": p.exit_reason} for p in bot.book.closed],
            "open": [{"symbol": p.symbol, "mint": p.mint, "pnl": p.pnl_usd()} for p in bot.book.positions.values()],
            "executions": len(bot.book.executions)}


def backtest_db(db, since: float = 0, cfg: TradingConfig | None = None, allow_legacy: bool = False) -> dict:
    rows = [dict(r) for r in db._query("SELECT s.*, t.symbol, t.created_at FROM snapshots s "
                                        "LEFT JOIN tokens t ON t.mint = s.mint WHERE s.ts >= ? ORDER BY s.ts", (since,))]
    return backtest(rows, cfg, allow_legacy)
