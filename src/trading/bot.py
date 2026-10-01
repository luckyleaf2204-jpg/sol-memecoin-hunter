"""PaperBot — orchestrates SCAN -> VET -> SCORE -> SIZE -> RISK -> EXECUTE -> POSITION -> EXIT -> P&L.

Runs as its own asyncio task next to the scanner and only reads `engine.published`. A crash in the bot never
touches the scanner; a crash in a stage marks that module ERROR and blocks new entries for the tick.
Module keys: scan, vet, size, risk, fills, book (shown as 01 SCAN … 06 BOOK).
"""
from __future__ import annotations

import asyncio
import time
from collections import deque
from pathlib import Path

from core.models import TokenState
from trading import decision as D
from trading.book import PaperBook
from trading.config import ModeNotAllowed, PAPER, TradingConfig
from trading.execution import PaperExecutor
from trading.exits import HARD, exit_signal
from trading.models import BLOCKED, ERROR, READY, RUN, TRADE, WATCH, Activity, ModuleState
from trading.risk import RiskEngine

MODULES = ("scan", "vet", "size", "risk", "fills", "book")
EXIT_WHY = {"stop_loss": "price hit the stop loss", "break_even_stop": "fell back to entry after TP1",
            "take_profit_1": "TP1 reached — partial profit", "take_profit_2": "TP2 reached",
            "trailing_stop": "trailing stop from the high", "liquidity_collapse": "liquidity collapsed / SHOCK",
            "whale_dump": "whales distributing", "risk_spike": "Risk > 60 or a rug flag",
            "identity_conflict": "token identity conflict", "holder_anomaly": "holder data anomaly",
            "momentum_deterioration": "momentum reversed (DISTRIBUTION / DECLINING)",
            "volume_collapse": "volume collapsed below entry", "max_hold_time": "max holding time"}
TICK_S = 5.0
QUOTE_RETRY_WINDOW_S = 60.0     # transient Jupiter failures (429 / timeout / 5xx) are retried this long
NO_ROUTE_BLOCK_S = 120.0        # after a confirmed "no route": no re-quote of that CA for this long
MAX_ENTRIES_PER_TICK = 2


class PaperBot:
    def __init__(self, engine, cfg: TradingConfig | None = None, state_path: Path | None = None,
                 config_path: Path | None = None):
        self.engine = engine
        self.cfg = cfg or TradingConfig()
        self.state_path = state_path
        self.config_path = config_path
        self.book = PaperBook.load(state_path, self.cfg.starting_balance) if state_path else PaperBook(self.cfg.starting_balance)
        self.exec = PaperExecutor(self.cfg.seed, self.cfg.max_slippage_pct)
        self.risk = RiskEngine(self.cfg)
        self.modules = {k: ModuleState(k) for k in MODULES}
        self.activity: deque[Activity] = deque(maxlen=400)
        self.decisions: dict[str, dict] = {}
        self.ticks = 0
        self.last_tick: float | None = None
        self.tick_ms: float | None = None
        self.ops: deque[tuple[float, int]] = deque(maxlen=120)        # (ts, evaluations) for ops/s
        self._stop: asyncio.Event | None = None
        self.jupiter = None                    # trading.jupiter.JupiterQuotes -> BUYs are filled on real quotes
        self.intents: dict[str, dict] = {}     # mint -> pending BUY intent (one per CA: duplicate-order guard)
        self.sell_intents: dict[str, dict] = {}  # mint -> SELL waiting for its Jupiter quote (non-protective exits)
        self.pending: dict[str, dict] = {}     # CONFIRM mode: BUYs waiting for the owner's approval (id -> order)
        self.mode = PAPER                      # operating mode: PAPER | CONFIRM (AUTO needs a live executor)
        self._next_order = 1
        self._discovered: set[str] = set()
        self._pipe_logged = 0.0
        self.last_buy_attempt: dict[str, float] = {}
        self.quote_block: dict[str, float] = {}  # mint -> until: Jupiter confirmed NO route (no re-quote spam)
        self.quote_stats: dict[str, int] = {}    # Jupiter BUY-quote outcomes by status
        from trading.audit import RunAudit
        self.audit = RunAudit(Path(state_path).with_name("bot_audit.json") if state_path else None)
        self.recorder = None                   # research.dataset.DatasetRecorder (read-only research log)
        self._last_followup = 0.0

    # ---------------------------------------------------------------- helpers
    def log(self, kind: str, text: str, st: TokenState | None = None, usd=None, price=None, now=None) -> None:
        self.activity.append(Activity(now or time.time(), kind, st.mint if st else "", st.info.symbol if st else "",
                                      text, usd, price))

    def _set(self, key: str, status: str, detail: str = "", items=None, now=None) -> None:
        m = self.modules[key]
        m.status, m.detail, m.updated = status, detail, now or time.time()
        if items is not None:
            m.items = items

    def _feeds_ok(self) -> tuple[bool, str]:
        try:
            f = self.engine.feeds()
        except Exception as e:
            return False, f"feeds unknown ({type(e).__name__})"
        dx = f.get("dexscreener", {})
        if dx.get("cooldown_s"):
            return False, f"DexScreener cooldown {dx['cooldown_s']}s"
        if dx.get("ok") is False:
            return False, f"DexScreener failing ({dx.get('last_error') or dx.get('last_status')})"
        return True, ""

    def _price(self, st: TokenState | None, now: float) -> float | None:
        """Validated, fresh price only — never a guess."""
        if st is None or st.market is None or st.market.price_usd is None or st.market.price_usd <= 0:
            return None
        if any(i.severity == "critical" for i in st.market_issues) or st.identity.status == "CONFLICT":
            return None
        stamp = st.stamps.get("market")
        if not stamp or now - stamp.updated_at > self.cfg.max_data_age_s * 4:
            return None
        return st.market.price_usd

    def _sol(self):
        return getattr(self.engine, "sol_price", None)

    # ---------------------------------------------------------------- tick
    def tick(self, now: float | None = None) -> None:
        now = now or time.time()
        t0 = time.monotonic()
        states = {s.mint: s for s in (self.engine.published or [])}
        self._positions(states, now)
        if self.cfg.enabled:
            self._entries(states, now)
        self._observe(states, now)
        if self.recorder is not None:
            self._rec("observe", list(states.values()), self.decisions, now,
                      {m for m, r in self.decisions.items() if r.get("ts") == now and m in states
                       and is_trade_candidate(states[m], r, allow_unchecked_risk=True)},
                      set(self.book.positions), {m for m, t in self.book.last_exit.items() if now - t < 30})
        if hasattr(self.engine, "deep_extra"):
            self.engine.deep_extra = set(self.book.positions)
        eq = self.book.mark(now)
        self._set("book", RUN if self.book.positions else READY,
                  f"equity ${eq:,.2f} · {len(self.book.positions)} open · net {self.book.stats(now)['net_pnl']:+,.2f}", now=now)
        self.ticks += 1
        self.last_tick = now
        self._log_pipeline(now)
        self.tick_ms = round((time.monotonic() - t0) * 1000, 1)
        self.ops.append((now, len(states) + len(self.book.positions)))
        self.persist()

    def _positions(self, states: dict, now: float) -> None:
        try:
            for p in list(self.book.positions.values()):
                st = states.get(p.mint)
                price = self._price(st, now)
                p.stale = price is None
                if price is not None:
                    p.last_price, p.last_price_ts = price, now
                    p.high_price = max(p.high_price, price)
                sig = exit_signal(p, st, price, self.cfg, now, p.entry_liq, p.entry_vol)
                if not sig:
                    continue
                frac, reason = sig
                mark = price if price is not None else p.last_price
                if mark is None or st is None:
                    self.log("INFO", f"exit signal {reason} but no validated price — waiting", st, now=now)
                    continue
                tokens = p.tokens * frac
                if self.jupiter is not None and reason not in HARD:
                    self.sell_intents.setdefault(p.mint, {"frac": frac, "reason": reason, "mark": mark, "ts": now})
                    continue                            # filled on a real Jupiter quote in execute_intents()
                ex = self.exec.sell(st, tokens, mark, self._sol(), reason, now, force=reason in HARD)
                self._after_sell(p, st, ex, reason, frac, now)
            self._set("fills", RUN if self.book.executions and now - self.book.executions[-1].ts < 60 else READY,
                      f"{len(self.book.executions)} executions · {self.book.failed} failed",
                      items=[_ex_dict(e) for e in self.book.executions[-30:]][::-1], now=now)
        except Exception as e:
            self._set("fills", ERROR, f"{type(e).__name__}: {e}", now=now)

    def _entries(self, states: dict, now: float) -> None:
        # SCAN
        try:
            cands = D.scan(list(states.values()))
            for st, reasons in cands:
                if reasons and st.mint not in self._discovered:
                    self._discovered.add(st.mint)
                    self.log("DISCOVER", "scan: " + ", ".join(reasons), st, now=now)
            scanned = sum(1 for _, r in cands if r)
            self._set("scan", RUN if scanned else READY, f"{scanned} with scan signals · {len(cands)} classified · "
                      "X Alpha / smart money: NOT AVAILABLE",
                      items=[{"mint": s.mint, "symbol": s.info.symbol, "why": r} for s, r in cands[:30] if r], now=now)
        except Exception as e:
            self._set("scan", ERROR, f"{type(e).__name__}: {e}", now=now)
            return
        feeds_ok, feeds_reason = self._feeds_ok()
        vet_items, size_items, risk_items, entries = [], [], [], 0
        try:
            for st, reasons in cands:
                v = D.vet(st, self.cfg, now)
                sc = D.score(st, v, self.cfg)
                rec = {"mint": st.mint, "symbol": st.info.symbol, "scan": reasons, "decision": sc.decision,
                       "opportunity": sc.opportunity, "confidence": sc.confidence, "components": sc.components,
                       "why": sc.why, "invalidate": sc.invalidate, "waiting": sc.waiting, "rejected": sc.rejected,
                       "checks": [{"key": c.key, "result": c.result, "value": c.value, "rule": c.rule} for c in v.checks],
                       "ts": now}
                prev = self.decisions.get(st.mint, {}).get("state")
                rec["vet_passed"] = D.vet_passed(v)
                rec["blocked_by"] = D.trade_blockers(st, v, sc, self.cfg)
                rec["risk_allowed"], rec["risk_reasons"], rec["state"] = None, [], sc.decision
                self.decisions[st.mint] = rec
                vet_items.append(rec)
                if sc.decision != TRADE:
                    if prev != sc.decision:
                        self.log("WATCH" if sc.decision == WATCH else "REJECT",
                                 f"{sc.decision} opp {sc.opportunity} conf {sc.confidence} · {'; '.join(sc.why[:2])}", st, now=now)
                    continue
                eq = self.book.equity()
                sz = D.size(st, sc, self.cfg, eq, self.book.cash, self.book.exposure())
                size_items.append({"mint": st.mint, "symbol": st.info.symbol, "usd": sz.usd, "reasons": sz.reasons})
                est = self.exec.estimate(st, sz.usd)
                last = self.book.last_exit.get(st.mint)
                rd = self.risk.check_entry(mint=st.mint, usd=sz.usd, equity=eq, peak=self.book.peak,
                                           day_start=self.book.day_start, open_positions=len(self.book.positions),
                                           holding=st.mint in self.book.positions,
                                           in_cooldown=bool(last and now - last < self.cfg.cooldown_min * 60),
                                           exposure=self.book.exposure(), est_impact=est["impact"],
                                           feeds_ok=feeds_ok, feeds_reason=feeds_reason)
                risk_items.append({"mint": st.mint, "symbol": st.info.symbol, "allowed": rd.allowed, "reasons": rd.reasons})
                rec["risk_allowed"], rec["risk_reasons"], rec["size_usd"] = rd.allowed, rd.reasons, sz.usd
                if self.recorder is not None and is_trade_candidate(st, rec, allow_unchecked_risk=True):
                    self._rec("candidate", st, rec, now, sz.usd, rd.allowed,
                              100 * ((est.get("impact") or 0) + (est.get("fee_rate") or 0)))
                if not rd.allowed:
                    rec["blocked_by"] = rec.get("blocked_by", []) + ["risk_engine: " + r for r in rd.reasons]
                if not rd.allowed:
                    rec["state"] = "BLOCKED"
                    if prev != "BLOCKED":
                        self.log("BLOCK", "risk: " + "; ".join(rd.reasons), st, now=now)
                    continue
                if entries >= MAX_ENTRIES_PER_TICK:
                    rec["state"] = "QUEUED"                 # allowed, waits for the next tick's entry slot
                    continue
                if not is_trade_candidate(st, rec):         # defence in depth: never buy outside 🟢
                    rec["state"] = "BLOCKED"
                    continue
                if st.mint in self.intents or st.mint in self.book.positions or \
                        any(o["mint"] == st.mint for o in self.pending.values()):
                    rec["state"] = "DUPLICATE"              # in flight / awaiting approval / held: never twice,
                    continue                                # never averaging down
                if self.mode == "CONFIRM":
                    oid = f"o{self._next_order}"
                    self._next_order += 1
                    self.pending[oid] = {"id": oid, "mint": st.mint, "symbol": st.info.symbol, "usd": sz.usd,
                                         "ts": now, "expires": now + 120, "opportunity": sc.opportunity,
                                         "why": sc.why, "setup": "+".join(sorted(reasons))}
                    rec["state"] = "AWAITING_CONFIRM"
                    self.log("WATCH", f"BUY proposed ${sz.usd:,.2f} — waiting for confirmation · WHY: "
                                      f"{'; '.join(sc.why[:3])}", st, now=now)
                    continue
                if self.jupiter is not None:                # async path: fill on a real Jupiter quote (atick)
                    if self.quote_block.get(st.mint, 0) > now:
                        rec["state"] = "NO_ROUTE"           # Jupiter confirmed no route moments ago: no re-quote spam
                        rec["blocked_by"] = rec.get("blocked_by", []) + ["jupiter: no route"]
                        continue
                    self.intents[st.mint] = {"mint": st.mint, "usd": sz.usd, "ts": now, "opportunity": sc.opportunity,
                                             "why": sc.why, "setup": "+".join(sorted(reasons))}
                    rec["state"] = "QUOTING"
                    self.audit.execution("candidate", st, now, f"${sz.usd:,.2f}")
                    self.log("INFO", f"BUY CANDIDATE ${sz.usd:,.2f} → requesting Jupiter quote", st, now=now)
                    entries += 1
                    continue
                ex = self.exec.buy(st, sz.usd, self._sol(), now)
                if ex.status == "FILLED":
                    self.book.open(ex, self.cfg, now, sc.opportunity, sc.why, st.market.liquidity_usd, st.market.vol_5m,
                                   setup="+".join(sorted(reasons)))
                    self.log("BUY", f"${sz.usd:,.2f} @ ${ex.fill_price:.8g} · impact {ex.price_impact_pct:.2f}% · "
                                    f"slip {ex.slippage_pct:.2f}% · {ex.route}", st, usd=sz.usd, price=ex.fill_price, now=now)
                    entries += 1
                else:
                    self.book.record(ex)
                    self.log("FAILED", f"BUY: {ex.reason}", st, now=now)
            self._set("vet", RUN if vet_items else READY,
                      f"{sum(1 for x in vet_items if all(c['result'] in ('PASS', 'N/A') for c in x['checks']))}/{len(vet_items)} passed",
                      items=vet_items[:30], now=now)
            self._set("size", RUN if size_items else READY, f"{len(size_items)} sized", items=size_items[:30], now=now)
        except Exception as e:
            self._set("vet", ERROR, f"{type(e).__name__}: {e}", now=now)
            return
        blocked = self.cfg.kill_switch or not feeds_ok
        self._set("risk", BLOCKED if blocked else (RUN if risk_items else READY),
                  ("KILL SWITCH" if self.cfg.kill_switch else ("feed: " + feeds_reason if not feeds_ok else "limits OK"))
                  + (f" · last error: {self.risk.last_error}" if self.risk.last_error else ""),
                  items=risk_items[:30], now=now)

    # ---------------------------------------------------------------- control
    def set_kill(self, engaged: bool) -> None:
        self.cfg.kill_switch = engaged
        self.log("KILL", "kill switch ENGAGED — no new positions" if engaged else "kill switch released")
        if self.config_path:
            self.cfg.save(self.config_path)

    def _observe(self, states: dict, now: float) -> None:
        """Feed the read-only run audit (stages of every published token + this tick's decisions)."""
        try:
            for st in states.values():
                self.audit.observe_stage(st, now)
            for mint, rec in self.decisions.items():
                st = states.get(mint)
                if st is not None and rec.get("ts") == now:
                    self.audit.observe_decision(st, rec, now, is_trade_candidate(st, rec, allow_unchecked_risk=True))
            self.audit.save(now)
        except Exception as e:                           # diagnostics never break trading
            self.log("INFO", f"audit error: {type(e).__name__}: {e}", now=now)

    def _rec(self, fn: str, *args) -> None:
        """Research-log call that can never break trading."""
        try:
            getattr(self.recorder, fn)(*args)
        except Exception as e:
            self.log("INFO", f"research log error ({fn}): {type(e).__name__}: {e}")

    def persist(self) -> None:
        if self.state_path:
            try:
                self.book.save(self.state_path)
            except OSError:
                pass

    def _after_sell(self, p, st, ex, reason: str, frac: float, now: float) -> None:
        self.book.reduce(p, ex, now, reason)
        if ex.status == "FILLED":
            if reason == "take_profit_1":
                p.tp1_done = True
                p.stop_price = max(p.stop_price, p.entry_price)      # break-even
            closed = p.status == "CLOSED"
            net = f" · NET P&L {p.realized_usd - p.cost_usd:+,.2f}$" if closed else ""
            self.log("SELL", f"{reason} · {100 * frac:.0f}% @ ${ex.fill_price:.8g} · {ex.route} · WHY: "
                             f"{EXIT_WHY.get(reason, reason)}{net}", st, usd=ex.usd_in, price=ex.fill_price, now=now)
        else:
            self.log("FAILED", f"SELL {reason}: {ex.reason}", st, now=now)

    async def execute_sells(self, now: float | None = None) -> None:
        """Non-protective exits (TP, trailing, momentum, volume, time): fresh Jupiter quote right before the fill;
        no quote -> liquidity-model fill (an exit is never skipped); impact above the limit -> retry next tick."""
        from trading.jupiter import WSOL, price_impact
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        for mint, it in list(self.sell_intents.items()):
            self.sell_intents.pop(mint, None)
            p, st = self.book.positions.get(mint), states.get(mint)
            if p is None or st is None:
                continue
            tokens, reason, sol = p.tokens * it["frac"], it["reason"], self._sol()
            q = await self.jupiter.quote(mint, WSOL, int(tokens * 10 ** (st.info.decimals or 6)),
                                         int(self.cfg.max_slippage_pct * 100)) if sol else None
            if q is not None:
                imp = price_impact(q)
                if imp is not None and 100 * imp > self.cfg.max_slippage_pct:
                    self.log("INFO", f"SELL {reason} waits: Jupiter impact {100 * imp:.2f}% > {self.cfg.max_slippage_pct}%",
                             st, now=now)
                    continue
                ex = self.exec.sell_from_quote(st, tokens, q, sol, reason, now)
            else:
                ex = self.exec.sell(st, tokens, it["mark"], sol, reason, now)
                ex.reason = (ex.reason + " · " if ex.reason else "") + "Jupiter quote unavailable — model fill"
            self._after_sell(p, st, ex, reason, it["frac"], now)

    # ---------------------------------------------------------------- CONFIRM mode
    def approve(self, order_id: str, now: float | None = None) -> bool:
        """Owner approved a pending BUY: it goes through the SAME re-checks and execution as an automatic one."""
        now = now or time.time()
        o = self.pending.pop(order_id, None)
        if o is None or now > o["expires"]:
            return False
        self.intents.setdefault(o["mint"], o)
        self.log("INFO", f"BUY approved by owner (order {order_id})", None, now=now)
        return True

    def dismiss(self, order_id: str) -> bool:
        return self.pending.pop(order_id, None) is not None

    def _expire_pending(self, now: float) -> None:
        for oid, o in list(self.pending.items()):
            if now > o["expires"]:
                self.pending.pop(oid, None)

    async def _buy_quote(self, mint: str, lamports: int):
        """Classified Jupiter quote (trading.jupiter.QuoteResult); a quote-only client is wrapped."""
        from trading.jupiter import API_ERROR, OK, WSOL, QuoteResult
        slip = int(self.cfg.max_slippage_pct * 100)
        if hasattr(self.jupiter, "quote_result"):
            return await self.jupiter.quote_result(WSOL, mint, lamports, slip)
        q = await self.jupiter.quote(WSOL, mint, lamports, slip)
        return QuoteResult(OK, quote=q, http=200, attempts=1) if q else QuoteResult(API_ERROR, detail="no quote", attempts=1)

    async def execute_intents(self, now: float | None = None) -> None:
        """BUY intents: Candidate -> fresh Jupiter quote -> MATCH -> RE-CHECK everything right before the fill
        (still a 🟢 Trade Candidate, Risk, real price impact) -> BUY. A transient quote failure (429 / timeout / 5xx /
        cooldown) is retried with backoff for QUOTE_RETRY_WINDOW_S; a confirmed "no route" is a final SKIP.
        Nothing is skipped silently: every outcome is logged and counted."""
        from trading.jupiter import NO_ROUTE, price_impact
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        for mint, it in list(self.intents.items()):
            if it.get("next", 0) > now:
                continue                                   # quote retry scheduled later
            self.intents.pop(mint, None)
            st = states.get(mint)
            rec = self.decisions.get(mint, {})
            sol = self._sol()
            if st is None or not is_trade_candidate(st, rec) or not sol or mint in self.book.positions:
                self.log("BLOCK", "BUY cancelled at execution: no longer a valid Trade Candidate", st, now=now)
                self.audit.execution("skip", st, now, reason="no_longer_candidate")
                continue
            usd = it["usd"]
            qr = await self._buy_quote(mint, int(usd / sol * 1e9))
            self.quote_stats[qr.status] = self.quote_stats.get(qr.status, 0) + 1
            if not qr.ok:
                first = it.setdefault("first", it["ts"])
                if qr.transient and now - first < QUOTE_RETRY_WINDOW_S:
                    it["retries"] = it.get("retries", 0) + 1
                    wait = min(20.0, 2.5 * 2 ** (it["retries"] - 1))
                    it["next"] = now + wait
                    self.intents[mint] = it
                    rec["state"] = "QUOTE_RETRY"
                    self._rec("quote", st, rec, now, qr.status, qr.label() + " (retrying)", None, False,
                              int(self.cfg.max_slippage_pct * 100))
                    self.log("INFO", f"BUY → QUOTE FAILED ({qr.label()}) → RETRY in {wait:.0f}s "
                                     f"(attempt {it['retries'] + 1})", st, now=now)
                    self.audit.execution("retry", st, now, qr.label())
                    continue
                if qr.status == NO_ROUTE:
                    self.quote_block[mint] = now + NO_ROUTE_BLOCK_S
                rec["state"] = "QUOTE_FAILED"
                self._rec("quote", st, rec, now, qr.status, qr.label(), None, False, int(self.cfg.max_slippage_pct * 100))
                self.log("FAILED", f"BUY → QUOTE FAILED ({qr.label()}) → SKIP · BUY SKIPPED — JUPITER", st, now=now)
                self.audit.execution("skip", st, now, qr.label(), reason="jupiter:" + qr.status)
                continue
            q = qr.quote
            imp = price_impact(q)
            from trading.jupiter import route_label
            self.log("INFO", f"BUY → QUOTE → MATCH · {route_label(q)} · impact "
                             f"{100 * imp:.2f}%" if imp is not None else f"BUY → QUOTE → MATCH · {route_label(q)}",
                     st, now=now)
            self.audit.execution("quote_ok", st, now, route_label(q))
            feeds_ok, why = self._feeds_ok()
            last = self.book.last_exit.get(mint)
            rd = self.risk.check_entry(mint=mint, usd=usd, equity=self.book.equity(), peak=self.book.peak,
                                       day_start=self.book.day_start, open_positions=len(self.book.positions),
                                       holding=False, in_cooldown=bool(last and now - last < self.cfg.cooldown_min * 60),
                                       exposure=self.book.exposure(), est_impact=imp, feeds_ok=feeds_ok, feeds_reason=why)
            if not rd.allowed:
                self.log("BLOCK", "risk at execution: " + "; ".join(rd.reasons) + " → SKIP", st, now=now)
                self.audit.execution("skip", st, now, "; ".join(rd.reasons), reason="risk_at_execution")
                self._rec("quote", st, rec, now, "OK", "risk at execution: " + "; ".join(rd.reasons), q, False,
                          int(self.cfg.max_slippage_pct * 100))
                continue
            ex = self.exec.buy_from_quote(st, usd, q, sol, now)
            self._rec("quote", st, rec, now, "OK", "filled" if ex.status == "FILLED" else f"paper fill failed: {ex.reason}",
                      q, ex.status == "FILLED", int(self.cfg.max_slippage_pct * 100))
            if ex.status == "FILLED":
                self.book.open(ex, self.cfg, now, it.get("opportunity"), it.get("why"), st.market.liquidity_usd,
                               st.market.vol_5m, setup=it.get("setup", ""))
                self.log("BUY", f"${usd:,.2f} @ ${ex.fill_price:.8g} · impact {ex.price_impact_pct:.2f}% · "
                                f"slip {ex.slippage_pct:.2f}% · {ex.route} · WHY: {'; '.join((it.get('why') or [])[:3])}",
                         st, usd=usd, price=ex.fill_price, now=now)
                self.audit.execution("buy", st, now, f"${usd:,.2f} @ {ex.fill_price:.8g} · {ex.route}")
            else:
                self.book.record(ex)
                self.log("FAILED", f"BUY: {ex.reason}", st, now=now)
                self.audit.execution("skip", st, now, ex.reason, reason="paper_fill_failed")
        self.persist()

    def set_mode(self, mode: str, confirm: str = "") -> None:
        """PAPER = automatic paper fills · CONFIRM = the same decisions wait for the owner's approval, then fill
        through the installed executor (paper in this build) · AUTO = automatic LIVE execution: refused, because
        no live executor (transaction signing) is installed. Every restart comes back in PAPER; AUTO is OFF."""
        from trading.execution import live_available
        if mode in (PAPER, "CONFIRM"):
            self.mode = mode
            if mode == PAPER:
                self.pending.clear()
            return
        if mode == "AUTO" and live_available():
            raise ModeNotAllowed("AUTO requires the live executor's own activation flow")
        raise ModeNotAllowed(f"{mode} needs live execution (wallet signing), which is not installed in this build "
                             "— PAPER / CONFIRM (paper fills) only")

    async def run(self, stop: asyncio.Event | None = None) -> None:
        self._stop = stop or asyncio.Event()
        while not self._stop.is_set():
            try:
                self.tick()
                self._expire_pending(time.time())
                if self.sell_intents:
                    await self.execute_sells()
                if self.intents:
                    await self.execute_intents()
                if self.recorder is not None and time.time() - self._last_followup >= 30:
                    self._last_followup = time.time()
                    await self.recorder.run_followups()
            except Exception as e:                       # the bot never takes the scanner down
                self.log("INFO", f"bot tick error: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
            except asyncio.TimeoutError:
                pass

    def pipeline(self, now: float | None = None) -> dict:
        """Decision-state counts over every fresh decision + real REJECT reasons by category."""
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        cands = {st.mint for st, _, _ in self.trade_candidates(now)}
        counts = {"WATCH": 0, "PENDING_IDENTITY": 0, "TRADE_CANDIDATE": 0, "REJECT": 0, "TRADE_BLOCKED": 0}
        reasons: dict[str, int] = {}
        for mint, rec in self.decisions.items():
            st = states.get(mint)
            if st is None or now - rec.get("ts", 0) > 30:
                continue
            d = rec.get("decision")
            if mint in cands:
                counts["TRADE_CANDIDATE"] += 1
            elif d == TRADE:
                counts["TRADE_BLOCKED"] += 1             # TRADE decision held back by Risk / Early / identity gate
            elif d in counts:
                counts[d] += 1
            if d == "REJECT":
                for r in rec.get("rejected") or ["other"]:
                    for cat in REJECT_CATEGORY(r, st):
                        reasons[cat] = reasons.get(cat, 0) + 1
        out = dict(counts)
        out["reject_reasons"] = dict(sorted(reasons.items(), key=lambda x: -x[1]))
        out["summary"] = self.block_summary(now)
        if hasattr(self.engine, "pipeline_counts"):
            out |= self.engine.pipeline_counts(now)
        return out

    def block_summary(self, now: float | None = None) -> dict:
        """Totals in the owner's format: REJECT by gate, UNKNOWN/PENDING, Trade Candidate, and the gate that blocks
        the most tokens from TRADE (first blocker of each non-candidate)."""
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        cands = {st.mint for st, _, _ in self.trade_candidates(now)}
        rej = {"identity": 0, "risk": 0, "liquidity": 0, "early": 0, "vet": 0, "opportunity": 0, "confidence": 0}
        pending, first, any_block = 0, {}, {}
        gate = {"identity_conflict": "identity", "rug": "risk", "liquidity": "liquidity", "early_signal": "early",
                "low_opportunity": "opportunity"}
        for mint, rec in self.decisions.items():
            st = states.get(mint)
            if st is None or now - rec.get("ts", 0) > 30 or mint in cands:
                continue
            d = rec.get("decision")
            if d == "REJECT":
                for r in rec.get("rejected") or []:
                    rej[gate.get(r, "vet")] += 1
            elif d in ("WATCH", "PENDING_IDENTITY"):
                pending += 1
            blocks = rec.get("blocked_by") or []
            if blocks:
                b0 = blocks[0].split(":")[0]
                first[b0] = first.get(b0, 0) + 1
            for b in {x.split(":")[0] for x in blocks}:
                any_block[b] = any_block.get(b, 0) + 1
        top = max(first.items(), key=lambda x: x[1]) if first else None
        return {"reject_by": rej, "unknown_pending": pending, "trade_candidates": len(cands),
                "first_blocker": dict(sorted(first.items(), key=lambda x: -x[1])),
                "blocked_by_any": dict(sorted(any_block.items(), key=lambda x: -x[1])),
                "most_blocking": top[0] if top else None}

    def _log_pipeline(self, now: float) -> None:
        if now - self._pipe_logged < 60 or not hasattr(self.engine, "log"):
            return
        self._pipe_logged = now
        p = self.pipeline(now)
        top = ", ".join(f"{k} {v}" for k, v in list(p["reject_reasons"].items())[:6]) or "—"
        self.engine.log(f"PIPELINE: discovery {p.get('discovery_per_min', '—')}/min · pre-early {p.get('pre_early_per_min', '—')}/min"
                        f" · early-watch {p.get('early_watch_per_min', '—')}/min · WATCH {p['WATCH']} · PENDING-ID "
                        f"{p['PENDING_IDENTITY']} · TRADE CANDIDATE {p['TRADE_CANDIDATE']} · REJECT {p['REJECT']} ({top})"
                        f" · evicted {p.get('evicted', 0)} · pruned no-data {p.get('pruned_no_data', 0)}")

    def trade_candidates(self, now: float | None = None) -> list[tuple]:
        """🟢 TRADE CANDIDATE: Early Signal TRUE + identity VERIFIED + VET passed + Decision TRADE + Risk allowed
        (fresh decision of a token still published). An open position on the CA is shown as a candidate being held."""
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        out = []
        for mint, rec in self.decisions.items():
            st = states.get(mint)
            if st is None or now - rec.get("ts", 0) > 30:
                continue
            if not is_trade_candidate(st, rec, allow_unchecked_risk=True):
                continue
            holding = mint in self.book.positions
            reasons = rec.get("risk_reasons") or []
            if not (rec.get("risk_allowed") or (holding and reasons and all(r == "already holding this CA" for r in reasons))):
                continue
            out.append((st, rec, holding))
        out.sort(key=lambda x: -(x[1].get("opportunity") or 0))
        return out

    def ops_per_s(self) -> float:
        if len(self.ops) < 2:
            return 0.0
        span = self.ops[-1][0] - self.ops[0][0]
        return round(sum(n for _, n in self.ops) / span, 1) if span > 0 else 0.0


def REJECT_CATEGORY(reason: str, st: TokenState) -> list[str]:
    """Real REJECT reason -> report category."""
    if reason == "rug":
        return ["risk"] if st.risk is not None and st.risk.score > 60 else ["rug_shock"]
    if reason == "low_opportunity":
        return ["weak_opportunity", "weak_momentum"]
    return [{"identity_conflict": "identity_conflict", "liquidity": "liquidity", "dev": "dev",
             "authorities": "authority", "token_2022": "token_2022", "data_invalid": "data_invalid",
             "early_signal": "early_signal_false"}.get(reason, "other:" + reason)]


def is_trade_candidate(st: TokenState, rec: dict, allow_unchecked_risk: bool = False) -> bool:
    """🟢 gate: Early Signal TRUE + identity VERIFIED + VET PASS + Decision TRADE (+ Risk, checked by the caller)."""
    if st.early is None or st.early.is_early is not True:
        return False
    if st.identity.status != "VERIFIED":
        return False
    if rec.get("decision") != TRADE or not rec.get("vet_passed"):
        return False
    return allow_unchecked_risk or bool(rec.get("risk_allowed"))


def _ex_dict(e) -> dict:
    return {"ts": e.ts, "symbol": e.symbol, "mint": e.mint, "side": e.side, "status": e.status, "route": e.route,
            "usd": round(e.usd_in, 2), "fill": e.fill_price, "ref": e.ref_price, "impact": round(e.price_impact_pct, 3),
            "slip": round(e.slippage_pct, 3), "fee": round(e.fee_usd + e.network_fee_usd, 4), "latency": e.latency_ms,
            "reason": e.reason}
