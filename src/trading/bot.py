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
from trading.config import TradingConfig
from trading.execution import PaperExecutor
from trading.exits import HARD, exit_signal
from trading.models import BLOCKED, ERROR, READY, RUN, TRADE, WATCH, Activity, ModuleState
from trading.risk import RiskEngine

MODULES = ("scan", "vet", "size", "risk", "fills", "book")
TICK_S = 5.0
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
        eq = self.book.mark(now)
        self._set("book", RUN if self.book.positions else READY,
                  f"equity ${eq:,.2f} · {len(self.book.positions)} open · net {self.book.stats(now)['net_pnl']:+,.2f}", now=now)
        self.ticks += 1
        self.last_tick = now
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
                ex = self.exec.sell(st, tokens, mark, self._sol(), reason, now, force=reason in HARD)
                self.book.reduce(p, ex, now, reason)
                if ex.status == "FILLED":
                    if reason == "take_profit_1":
                        p.tp1_done = True
                        p.stop_price = max(p.stop_price, p.entry_price)      # break-even
                    self.log("SELL", f"{reason} · {100 * frac:.0f}% @ ${ex.fill_price:.8g} · {ex.route}", st,
                             usd=ex.usd_in, price=ex.fill_price, now=now)
                else:
                    self.log("FAILED", f"SELL {reason}: {ex.reason}", st, now=now)
            self._set("fills", RUN if self.book.executions and now - self.book.executions[-1].ts < 60 else READY,
                      f"{len(self.book.executions)} executions · {self.book.failed} failed",
                      items=[_ex_dict(e) for e in self.book.executions[-30:]][::-1], now=now)
        except Exception as e:
            self._set("fills", ERROR, f"{type(e).__name__}: {e}", now=now)

    def _entries(self, states: dict, now: float) -> None:
        # SCAN
        try:
            cands = D.scan(list(states.values()))
            self._set("scan", RUN if cands else READY, f"{len(cands)} candidates from {len(states)} tokens · "
                      "X Alpha / smart money: NOT AVAILABLE",
                      items=[{"mint": s.mint, "symbol": s.info.symbol, "why": r} for s, r in cands[:30]], now=now)
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
                       "why": sc.why, "invalidate": sc.invalidate,
                       "checks": [{"key": c.key, "result": c.result, "value": c.value, "rule": c.rule} for c in v.checks],
                       "ts": now}
                prev = self.decisions.get(st.mint, {}).get("decision")
                self.decisions[st.mint] = rec
                vet_items.append(rec)
                if sc.decision != TRADE:
                    if prev != sc.decision:
                        self.log("WATCH" if sc.decision == WATCH else "REJECT",
                                 f"{sc.decision} opp {sc.opportunity} conf {sc.confidence} · {'; '.join(sc.why[:2])}", st, now=now)
                    continue
                if entries >= MAX_ENTRIES_PER_TICK:
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
                if not rd.allowed:
                    if prev != "BLOCKED":
                        self.log("BLOCK", "risk: " + "; ".join(rd.reasons), st, now=now)
                    rec["decision_note"] = "BLOCKED by risk"
                    self.decisions[st.mint]["decision"] = "BLOCKED"
                    continue
                ex = self.exec.buy(st, sz.usd, self._sol(), now)
                if ex.status == "FILLED":
                    self.book.open(ex, self.cfg, now, sc.opportunity, sc.why, st.market.liquidity_usd, st.market.vol_5m)
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

    def persist(self) -> None:
        if self.state_path:
            try:
                self.book.save(self.state_path)
            except OSError:
                pass

    async def run(self, stop: asyncio.Event | None = None) -> None:
        self._stop = stop or asyncio.Event()
        while not self._stop.is_set():
            try:
                self.tick()
            except Exception as e:                       # the bot never takes the scanner down
                self.log("INFO", f"bot tick error: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
            except asyncio.TimeoutError:
                pass

    def ops_per_s(self) -> float:
        if len(self.ops) < 2:
            return 0.0
        span = self.ops[-1][0] - self.ops[0][0]
        return round(sum(n for _, n in self.ops) / span, 1) if span > 0 else 0.0


def _ex_dict(e) -> dict:
    return {"ts": e.ts, "symbol": e.symbol, "mint": e.mint, "side": e.side, "status": e.status, "route": e.route,
            "usd": round(e.usd_in, 2), "fill": e.fill_price, "ref": e.ref_price, "impact": round(e.price_impact_pct, 3),
            "slip": round(e.slippage_pct, 3), "fee": round(e.fee_usd + e.network_fee_usd, 4), "latency": e.latency_ms,
            "reason": e.reason}
