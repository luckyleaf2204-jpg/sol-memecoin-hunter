"""PaperBot -> JSON for the PWA dashboard. Paper data only: no key, no wallet, no transaction."""
from __future__ import annotations

import time

from trading.bot import MODULES, PaperBot, _ex_dict
from trading.config import ALLOWED_MODES


def _pos(p, now: float) -> dict:
    return {"id": p.id, "mint": p.mint, "symbol": p.symbol, "opened_at": p.opened_at, "entry": p.entry_price,
            "current": p.last_price, "price_age_s": round(now - p.last_price_ts) if p.last_price_ts else None,
            "stale": p.stale, "size_usd": round(p.cost_usd, 2), "value_usd": round(p.value(), 2) if p.value() is not None else None,
            "pnl_usd": round(p.pnl_usd(), 2) if p.pnl_usd() is not None else None,
            "pnl_pct": round(p.pnl_pct(), 2) if p.pnl_pct() is not None else None,
            "stop": p.stop_price, "tp1": p.tp1_price, "tp2": p.tp2_price, "tp1_done": p.tp1_done,
            "trailing_pct": p.trailing_pct, "trailing_armed": p.tp1_done,
            "trail_price": p.high_price * (1 - p.trailing_pct / 100) if p.tp1_done else None,
            "tokens": p.tokens, "fees": round(p.fees_usd, 4), "entry_score": p.entry_score, "why": p.entry_why[:6],
            "status": "STALE" if p.stale else p.status, "exit_reason": p.exit_reason, "closed_at": p.closed_at,
            "realized": round(p.realized_usd, 2)}


def bot_status(bot: PaperBot, engine=None, now: float | None = None) -> dict:
    now = now or time.time()
    s = bot.book.stats(now)
    eh = bot.book.equity_history
    step = max(1, len(eh) // 200)
    risk_state = "KILL" if bot.cfg.kill_switch else bot.modules["risk"].status
    states = {st.mint: st for st in (engine.published if engine and engine.published else [])}
    positions = []
    for p in bot.book.positions.values():
        d = _pos(p, now)
        st = states.get(p.mint)
        d["momentum"] = st.subscores["momentum"].score if st and "momentum" in st.subscores and st.subscores["momentum"].score is not None else None
        d["risk"] = st.risk.score if st and st.risk else None
        d["lifecycle"] = st.lifecycle if st else None
        positions.append(d)
    return {
        "server_time": now, "mode": bot.cfg.mode, "allowed_modes": list(ALLOWED_MODES), "enabled": bot.cfg.enabled,
        "live": bool(bot.last_tick and now - bot.last_tick < 30),
        "last_tick": bot.last_tick, "tick_ms": bot.tick_ms, "ticks": bot.ticks, "ops_per_s": bot.ops_per_s(),
        "kill_switch": bot.cfg.kill_switch, "risk_state": risk_state,
        "stats": s,
        "modules": [{"key": k, "status": bot.modules[k].status, "detail": bot.modules[k].detail,
                     "updated": bot.modules[k].updated} for k in MODULES],
        "positions": positions,
        "closed": [_pos(p, now) for p in bot.book.closed[-20:]][::-1],
        "activity": [{"ts": a.ts, "kind": a.kind, "symbol": a.symbol, "mint": a.mint, "text": a.text, "usd": a.usd,
                      "price": a.price} for a in list(bot.activity)[-80:]][::-1],
        "equity_history": eh[::step][-200:],
        "limits": {k: getattr(bot.cfg, k) for k in (
            "starting_balance", "risk_per_trade_pct", "max_position_pct", "max_open_positions", "max_total_exposure_pct",
            "max_daily_loss_pct", "max_drawdown_pct", "max_slippage_pct", "min_liquidity_usd", "stop_loss_pct",
            "tp1_pct", "tp2_pct", "trailing_pct", "max_hold_min", "trade_min_opportunity", "trade_min_confidence")},
        "sources": {"pumpportal": "live", "pumpfun": "live", "dexscreener": "live", "helius": "live",
                    "x_alpha": "NOT_AVAILABLE", "smart_money": "NOT_AVAILABLE"},
        "execution_model": "PAPER — modelled route / price impact / slippage / fees / failures / latency; no transaction is sent",
    }


def module_detail(bot: PaperBot, key: str) -> dict | None:
    if key not in bot.modules:
        return None
    m = bot.modules[key]
    items = m.items
    if key == "book":
        items = [_pos(p, time.time()) for p in list(bot.book.positions.values()) + bot.book.closed[-30:][::-1]]
    if key == "fills":
        items = [_ex_dict(e) for e in bot.book.executions[-50:]][::-1]
    return {"key": key, "status": m.status, "detail": m.detail, "updated": m.updated, "items": items}
