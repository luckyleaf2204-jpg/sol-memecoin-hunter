"""PaperBot -> JSON for the PWA dashboard. Paper data only: no key, no wallet, no transaction."""
from __future__ import annotations

import time

from trading.bot import EXIT_WHY, MODULES, PaperBot, _ex_dict
from trading.models import TRADE, WATCH
from trading.config import ALLOWED_MODES


def exit_status(p, st, cfg) -> dict:
    """Read-only view of the Exit Engine for one position, with the SAME thresholds as trading.exits
    (it does not decide anything: exits.exit_signal does). state: ok | near | hit | unknown."""
    cur = p.last_price
    out = {}
    if cur is None:
        unk = {"state": "unknown", "detail": "no validated price"}
        out.update(take_profit=unk, stop_loss=unk)
    else:
        tp = p.tp2_price if p.tp1_done else p.tp1_price
        to_tp = 100 * (tp / cur - 1)
        out["take_profit"] = {"state": "hit" if cur >= tp else ("near" if to_tp <= 5 else "ok"),
                              "detail": f"{'TP2' if p.tp1_done else 'TP1'} {to_tp:+.1f}% away"}
        stop = p.stop_price
        if p.tp1_done:
            stop = max(stop, p.high_price * (1 - p.trailing_pct / 100))
        to_sl = 100 * (cur / stop - 1)
        out["stop_loss"] = {"state": "hit" if cur <= stop else ("near" if to_sl <= 5 else "ok"),
                            "detail": f"{'trailing' if p.tp1_done else 'SL'} {to_sl:.1f}% below"}
    liq = st.market.liquidity_usd if st is not None and st.market else None
    shock = bool(st is not None and st.liquidity_intel and st.liquidity_intel.state == "SHOCK")
    if st is None or liq is None or not p.entry_liq:
        out["liquidity"] = {"state": "hit" if shock else "unknown", "detail": "SHOCK" if shock else "liquidity unknown"}
    else:
        r = liq / p.entry_liq
        out["liquidity"] = {"state": "hit" if shock or r < 0.6 else ("near" if r < 0.75 else "ok"),
                            "detail": f"{100 * r:.0f}% of entry liquidity (exit < 60%)"}
    lc = st.lifecycle if st is not None else None
    out["momentum"] = {"state": "unknown" if lc is None else ("hit" if lc in ("DISTRIBUTION", "DECLINING") else "ok"),
                       "detail": lc or "unknown"}
    rk = st.risk if st is not None else None
    rug = bool(rk and any(f.category == "rug" for f in rk.factors))
    out["risk"] = {"state": "unknown" if rk is None else ("hit" if rk.score > 60 or rug else ("near" if rk.score > 45 else "ok")),
                   "detail": "rug flag" if rug else (f"Risk {rk.score} (exit > 60)" if rk else "unknown")}
    return out


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
            "realized": round(p.realized_usd, 2), "setup": p.setup, "path": p.path_log()}


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
        d["holding_min"] = round((now - p.opened_at) / 60, 1)
        d["exit"] = exit_status(p, st, bot.cfg)
        hit = [k for k, v in d["exit"].items() if v["state"] == "hit"]
        near = [k for k, v in d["exit"].items() if v["state"] == "near"]
        d["exit_state"] = ("EXIT: " + ", ".join(hit)) if hit else (("NEAR: " + ", ".join(near)) if near else "HOLD")
        positions.append(d)
    tracked = list(engine.published) if engine and engine.published else []
    cands = bot.trade_candidates(now)
    scan = {"total": len(tracked),
            "pre_early": sum(1 for x in tracked if x.pre_early is not None and x.pre_early.status == "PRE_EARLY"),
            "early_watch": sum(1 for x in tracked if x.early_watch is not None and x.early_watch.rank is not None),
            "early_signal": sum(1 for x in tracked if x.early is not None and x.early.is_early is True),
            "trade_candidates": len(cands)}
    evaluated = []
    for mint, rec in bot.decisions.items():                # every fresh decision — the UI paginates, never the engine
        st = states.get(mint)
        if st is None or now - rec.get("ts", 0) > 30:
            continue
        checks = rec.get("checks", [])
        failed = [c for c in checks if c["result"] not in ("PASS", "N/A")]
        is_cand = any(c[0].mint == mint for c in cands)
        mom = st.subscores.get("momentum")
        evaluated.append({
            "mint": mint, "symbol": st.info.symbol, "mc": st.market.market_cap if st.market else None,
            "age_min": round(st.age_minutes, 1) if st.age_minutes is not None else None,
            "opportunity": rec.get("opportunity"), "confidence": rec.get("confidence"),
            "momentum": mom.score if mom and mom.score is not None else None,
            "risk": st.risk.score if st.risk else None, "identity": st.identity.status,
            "vet": "PASS" if not failed else f"{len(checks) - len(failed)}/{len(checks)}",
            "vet_failed": [f"{c['key']}: {c['result']}" for c in failed][:4],
            "action": "BUY" if is_cand else ("WATCH" if rec.get("decision") in (WATCH, TRADE) else
                                             "PENDING" if rec.get("decision") == "PENDING_IDENTITY" else "REJECT"),
            "state": rec.get("state"), "why": rec.get("why", [])[:4],
            "waiting": rec.get("waiting", []), "rejected": rec.get("rejected", []),
            "blocked_by": rec.get("blocked_by", []),
            "early": ("TRUE" if st.early and st.early.is_early is True else
                      "UNKNOWN" if st.early is None or st.early.strength is None else f"FALSE {st.early.groups_computable}/7"),
            "liquidity": st.market.liquidity_usd if st.market else None,
            "holders": st.holders.holder_count if st.holders and st.holder_status == "ok" else None,
            "dev": st.dev.status if st.dev and st.dev.balance_verified else None,
            "engine": rec.get("engine", "old"), "old_decision": rec.get("old_decision"),
            "old_candidate": bool(rec.get("old_candidate")), "early_score": rec.get("early_score"),
            "lifecycle": rec.get("lifecycle_name"), "lifecycle_confidence": rec.get("lifecycle_confidence"),
            "migration_progress": rec.get("migration_progress"), "setup_type": rec.get("setup_type"),
            "setup_score": rec.get("setup_score"), "setup_threshold": rec.get("setup_threshold"),
            "setup_confidence": (rec.get("setup") or {}).get("data_confidence"), "post_state": rec.get("post_state"),
            "experimental_decision": rec.get("experimental_decision")})
    evaluated.sort(key=lambda x: (x["action"] != "BUY", -((x["opportunity"] or 0) + (x["confidence"] or 0) - (x["risk"] or 100))))
    if bot.cfg.kill_switch:
        doing = {"kind": "kill"}
    elif bot.book.positions:
        best = max(bot.book.positions.values(), key=lambda p: p.pnl_pct() or -1e9)
        doing = {"kind": "holding", "symbol": best.symbol, "pnl_pct": round(best.pnl_pct(), 1) if best.pnl_pct() is not None else None,
                 "count": len(bot.book.positions)}
    elif cands:
        doing = {"kind": "candidates", "count": len(cands)}
    elif tracked:
        doing = {"kind": "searching", "count": len(tracked), "evaluated": len(evaluated)}
    else:
        doing = {"kind": "starting"}
    c = bot.cfg
    exits = [{"key": "take_profit", "value": f"+{c.tp1_pct:.0f}% (bán {100 * c.tp1_sell_frac:.0f}%) · +{c.tp2_pct:.0f}% (bán hết)"},
             {"key": "trailing", "value": f"-{c.trailing_pct:.0f}% từ đỉnh sau TP1"},
             {"key": "stop_loss", "value": f"-{c.stop_loss_pct:.0f}% (sau TP1: hoà vốn)"},
             {"key": "liquidity", "value": "liquidity < 60% lúc vào / SHOCK"},
             {"key": "momentum", "value": "DISTRIBUTION / DECLINING · volume sụp"},
             {"key": "risk", "value": "Risk > 60 · cờ rug · cá voi xả · identity conflict"},
             {"key": "time", "value": f"giữ tối đa {c.max_hold_min:.0f} phút"}]
    engine_rows = []
    for key in ("take_profit", "stop_loss", "liquidity", "momentum", "risk"):
        states_ = [p["exit"][key]["state"] for p in positions]
        engine_rows.append({"key": key, "rule": next((r["value"] for r in exits if r["key"] == key or
                                                      (key == "stop_loss" and r["key"] == "stop_loss")), ""),
                            "hit": states_.count("hit"), "near": states_.count("near"),
                            "unknown": states_.count("unknown"), "watching": len(states_)})
    return {
        "pipeline": bot.pipeline(now),
        "doing": doing, "scan": scan, "evaluated": evaluated, "exit_rules": exits, "exit_engine": engine_rows,
        "version": "web-9",
        "engine": "lifecycle" if bot.cfg.lifecycle else ("experimental" if bot.cfg.experimental else "old"),
        "lifecycle_summary": bot.lifecycle_summary(now) if hasattr(bot, "lifecycle_summary") else None,
        "fast_lane": bot.fast_lane_stats() if hasattr(bot, "fast_lane_stats") else None,
        "jupiter_quotes": dict(getattr(bot, "quote_stats", {}) or {}),
        "audit": _audit_summary(bot, now),
        "live_available": False,
        "pending": [{"id": o["id"], "symbol": o["symbol"], "mint": o["mint"], "usd": o["usd"],
                     "expires_in": max(0, round(o["expires"] - now)), "why": o["why"][:4]} for o in bot.pending.values()],
        "helius": (engine.feeds().get("helius") if engine is not None and hasattr(engine, "feeds") else None),
        "server_time": now, "mode": bot.mode, "execution": bot.cfg.mode, "allowed_modes": list(ALLOWED_MODES), "enabled": bot.cfg.enabled,
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


def _audit_summary(bot, now) -> dict | None:
    a = getattr(bot, "audit", None)
    if a is None:
        return None
    r = a.report(now, top=50)
    return {"stats": r["stats"], "blocked_by": r["blocked_by"], "near": r["near"], "top": r["top"][:50],
            "events": r["events"][:20]}
