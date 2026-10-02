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
from trading import experimental as X
from trading import lifecycle as LC
from trading import lifecycle_decision as LD
from trading.book import PaperBook
from trading.config import ModeNotAllowed, PAPER, TradingConfig
from trading.execution import PaperExecutor
from trading.exit_options import apply_exit_options, on_filled_sell, stop_pct
from trading.exit_policy import is_protective
from trading.exits import exit_signal
from trading.models import BLOCKED, ERROR, PENDING_IDENTITY, READY, RUN, TRADE, WATCH, Activity, ModuleState
from trading.risk import RiskEngine

MODULES = ("scan", "vet", "size", "risk", "fills", "book")
EXIT_WHY = {"stop_loss": "price hit the stop loss", "break_even_stop": "fell back to entry after TP1",
            "take_profit_1": "TP1 reached — partial profit", "take_profit_2": "TP2 reached",
            "trailing_stop": "trailing stop from the high", "liquidity_collapse": "liquidity collapsed / SHOCK",
            "whale_dump": "whales distributing", "risk_spike": "Risk > 60 or a rug flag",
            "identity_conflict": "token identity conflict", "holder_anomaly": "holder data anomaly",
            "momentum_deterioration": "momentum reversed (DISTRIBUTION / DECLINING)",
            "volume_collapse": "volume collapsed below entry", "max_hold_time": "max holding time",
            "runner_trailing_stop": "TP2 runner left on its trailing stop", "time_stop": "no new high (time stop)"}
TICK_S = 5.0
LAT_P90_MIN, LAT_P75_MIN = 50, 100        # AUTO latency model: samples needed for EMPIRICAL P90 / P75
FAST_SCORE_MARGIN, FAST_CONF_MARGIN = 0.05, 0.10   # fast getAsset lane: "genuinely near a NEW BUY"
QUOTE_RETRY_WINDOW_S = 60.0     # transient Jupiter failures (429 / timeout / 5xx) are retried this long
GATE_EVAL_EVERY_S = 600.0       # blocked vs entered vs baseline (research.db) refreshed every 10 min
SELL_QUOTE_ATTEMPTS, SELL_QUOTE_BUDGET_S = 2, 4.0   # one SELL quote call is short: the bot-level retry does the rest
NO_ROUTE_BLOCK_S = 120.0        # after a confirmed "no route": no re-quote of that CA for this long
MAX_ENTRIES_PER_TICK = 2
STALE_TIMEOUT_S = 600.0         # no validated price for this long -> forced exit "stale_timeout" (P&L counted)
SELL_FILL_ERROR_MAX = 3          # a HARD exit whose fill raised this many times -> emergency haircut
STALE_MAX_STEP_S = 30.0         # one tick adds at most this much stale time (a pause / restart never counts)
FAILED_BUY_COOLDOWN_S = 300.0   # after a FAILED BUY fill, no new BUY attempt on that mint for this long
TRUTH_QUOTES_PER_MIN = 24       # V1.3 truth SELL quotes: own budget, below Jupiter's 50/min (BUY quotes first)


class PaperBot:
    def __init__(self, engine, cfg: TradingConfig | None = None, state_path: Path | None = None,
                 config_path: Path | None = None):
        self.engine = engine
        self.cfg = cfg or TradingConfig()
        self.state_path = state_path
        self.config_path = config_path
        self.book = PaperBook.load(state_path, self.cfg.starting_balance) if state_path else PaperBook(self.cfg.starting_balance)
        self.exec = PaperExecutor(self.cfg.seed, self.cfg.max_slippage_pct, self.cfg.latency_slippage_model)
        self.exec.empirical = self._empirical_slip
        self._apply_cost_model()
        self.book.cost_levels = tuple(self.cfg.cost_stress_pct)
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
        self.quote_budget = None                 # trading.quote_budget.QuoteBudget (server); None = unlimited
        self.entry_block: str | None = None      # set by the server when the sample is not durable: no NEW entries
        self.stale_s: dict[str, float] = {}       # mint -> seconds WITHOUT a price while feed + Jupiter were healthy
        self._last_positions_ts: float | None = None
        self.entry_block_seen: set[str] = set()   # tokens that would have been bought meanwhile (counted)
        self.gate_eval_cache: dict | None = None  # blocked vs entered vs matched baseline (research.gate_eval)
        self._last_gate_eval = 0.0
        self._gate_eval_task: asyncio.Task | None = None
        self._exec_task: asyncio.Task | None = None   # sells + buys run beside the tick, never block it
        self._selling = False
        self.pending: dict[str, dict] = {}     # CONFIRM mode: BUYs waiting for the owner's approval (id -> order)
        self.mode = PAPER                      # operating mode: PAPER | CONFIRM (AUTO needs a live executor)
        self._next_order = 1
        self._discovered: set[str] = set()
        self._pipe_logged = 0.0
        self.last_buy_attempt = self.book.last_buy_attempt    # persisted with the book (survives a restart)
        self.quote_block: dict[str, float] = {}  # mint -> until: Jupiter confirmed NO route (no re-quote spam)
        self.quote_stats: dict[str, int] = {}    # Jupiter BUY-quote outcomes by status
        self.sell_quote_stats: dict[str, int] = {}  # Jupiter SELL-quote outcomes by status
        self.exit_fallback = "haircut"         # no Jupiter client: "haircut" (live paper) | "model" (backtest only)
        self.snapshot_status: dict | None = None   # set by the web app: durable store of the sample (core.snapshot)
        from trading.gaps import GapTracker
        self.gap_tracker = GapTracker(self.book)
        from trading.sample_epoch import SampleEpoch
        self.sample_epoch = SampleEpoch(Path(state_path).with_name("sample_epoch.json") if state_path else None)
        from trading.audit import RunAudit
        self.audit = RunAudit(Path(state_path).with_name("bot_audit.json") if state_path else None)
        self.recorder = None                   # research.dataset.DatasetRecorder (read-only research log)
        self.onchain = None                    # research.onchain.OnchainResearch (shadow anti-rug data, budgeted)
        self.lifecycle_tracker = LC.LifecycleTracker()   # NEW -> PRE -> POST transitions per CA
        self.money_flow = None                 # trading.money_flow.MoneyFlowCollector (shadow research, budgeted)
        self._last_mf = 0.0
        self.pre_buf: dict[str, deque] = {}    # recent shadow snapshots per watched CA (T-30s / -10s / -5s at BUY)
        self.provenance: dict[str, dict] = {}  # V1.2: price provenance per paper trade (entry obs, marks, exits)
        self.quote_obs_log: list[dict] = []     # every BUY quote: market print vs Jupiter implied price
        self.market_trail: dict[str, deque] = {}   # market prints right after a quote (validation of discrepancies)
        self.cs_shadow: dict[str, object] = {}  # V1.3 TRUTH trackers (trading.truth_price.TruthTracker), per trade
        self.truth_quotes: deque = deque(maxlen=200)   # timestamps of truth SELL quotes (own budget)
        self.truth_skipped_budget = 0
        from trading.truth_price import TruthLedger
        self.truth_ledger = TruthLedger(Path(state_path).with_name("truth_ledger.json") if state_path else None)
        self._last_cs = 0.0
        self._mf_sticky: dict[str, float] = {}
        self._last_onchain = 0.0
        self._gate_waiting: set[str] = set()   # experimental: ever WATCH because authority / Token-2022 unchecked
        self.fast_promoted: set[str] = set()   # ...and later a NEW candidate after the fast lane checked them
        self.fill_log: list[dict] = []          # every paper BUY fill attempt: impact / latency slip / total / max
        self.latency_samples: deque = deque(maxlen=1000)   # (|chg5m| bucket, adverse drift fraction) from re-quotes
        self.latency_log: deque = deque(maxlen=1000)       # per re-quote: quote/re-quote ts, real latency, route, liq
        self.forensics: dict[str, dict] = {}    # mint -> Risk snapshots ENTRY, +5s, +10s, +15s, +30s, +60s
        self.buffer_blocks: list[dict] = []     # NEW BUYs refused by the entry risk buffer (55 < Risk <= 60)
        import random as _random
        self._probe_rng = _random.Random(11)    # probe waits never consume the executor's random stream
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

    @staticmethod
    def _post_entry(st: TokenState | None, p) -> bool:
        """True when the market print was FETCHED at or after the position opened. The entry is a Jupiter fill;
        a DexScreener print fetched before it (typically 5-25 s old at the decision) cannot trigger the stop / TP or
        set MFE / MAE of that position (V1.2 forensic: BLOCUS / >_ / bwam / Papu exits fired on the pre-entry print)."""
        stamp = st.stamps.get("market") if st is not None else None
        return stamp is not None and stamp.updated_at >= p.opened_at

    def _apply_cost_model(self) -> None:
        """Copy the cost-model settings (priority fee, dump-tail slippage) from the config to the executor."""
        c = self.cfg
        self.exec.priority_fee_sol = c.priority_fee_sol
        self.exec.dump_tail_threshold_pct = c.dump_tail_threshold_pct
        self.exec.dump_tail_scale = c.dump_tail_scale
        self.exec.dump_tail_cap = c.dump_tail_cap_pct / 100

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
        self._forensic_tick(states, now)
        self._provenance_tick(states, now)
        if self.recorder is not None:
            self._rec("observe", list(states.values()), self.decisions, now,
                      {m for m, r in self.decisions.items() if r.get("ts") == now and m in states
                       and is_trade_candidate(states[m], r, allow_unchecked_risk=True)},
                      set(self.book.positions), {m for m, t in self.book.last_exit.items() if now - t < 30})
        if hasattr(self.engine, "deep_extra"):
            self.engine.deep_extra = set(self.book.positions)
        if self.cfg.experimental and hasattr(self.engine, "deep_hint"):
            self.engine.deep_hint = self._deep_hint(now)
        eq = self.book.mark(now)
        self._set("book", RUN if self.book.positions else READY,
                  f"equity ${eq:,.2f} · {len(self.book.positions)} open · net {self.book.stats(now)['net_pnl']:+,.2f}", now=now)
        self.ticks += 1
        self.last_tick = now
        self.gap_tracker.beat(now, self._feeds_ok()[0])
        self._log_pipeline(now)
        self.tick_ms = round((time.monotonic() - t0) * 1000, 1)
        self.ops.append((now, len(states) + len(self.book.positions)))
        self.persist()

    def _jupiter_ok(self) -> bool:
        http = getattr(self.jupiter, "http", None)
        if http is None:
            return True
        from trading.jupiter import SOURCE
        return http.health.cooling(SOURCE) <= 0

    def _stale_clock_runs(self, states: dict) -> bool:
        """Stale time only counts while the market feed has published and is healthy and Jupiter is not in its
        breaker: a feed / Jupiter outage or a restart is not 'this token lost its price'."""
        return bool(states) and self._feeds_ok()[0] and self._jupiter_ok()

    def _positions(self, states: dict, now: float) -> None:
        step = 0.0 if self._last_positions_ts is None else min(max(now - self._last_positions_ts, 0.0), STALE_MAX_STEP_S)
        self._last_positions_ts = now
        healthy = self._stale_clock_runs(states)
        try:
            for p in list(self.book.positions.values()):
                st = states.get(p.mint)
                price = self._price(st, now)
                if price is not None and not self._post_entry(st, p):
                    price = None            # V1.2 fix: a print fetched BEFORE the fill says nothing about this position
                p.stale = price is None
                if price is not None:
                    p.last_price, p.last_price_ts = price, now
                    if price > p.high_price:
                        p.high_ts = now                  # step 3 time stop: last new high
                    p.high_price = max(p.high_price, price)
                    self._path_marks(p, price, now)
                sig = apply_exit_options(p, exit_signal(p, st, price, self.cfg, now, p.entry_liq, p.entry_vol),
                                         price, self.cfg, now)       # A/B variants (off by default)
                if price is not None:
                    self.stale_s.pop(p.mint, None)
                elif healthy:
                    self.stale_s[p.mint] = self.stale_s.get(p.mint, 0.0) + step
                if price is None and self.stale_s.get(p.mint, 0.0) >= STALE_TIMEOUT_S and p.last_price:
                    sig = (1.0, "stale_timeout")         # B6: never keep an unpriceable position out of the P&L
                    if st is None:                       # token gone from the feed: nothing to quote with
                        self.sell_intents.pop(p.mint, None)
                        self._close_without_state(p, "stale_timeout", now)
                        continue
                if not sig:
                    continue
                frac, reason = sig
                mark = price if price is not None else p.last_price    # = the fill until a post-entry mark exists
                if mark is None or st is None:
                    self.log("INFO", f"exit signal {reason} but no validated price — waiting", st, now=now)
                    continue
                tokens = p.tokens * frac
                hard = is_protective(reason)            # HARD + break-even / trailing stops
                if self.jupiter is not None:
                    it = {"frac": frac, "reason": reason, "mark": mark, "ts": now, "hard": hard}
                    cur = self.sell_intents.get(p.mint)
                    if cur is not None:
                        cur["mark"] = mark                  # keep its retry state, refresh the reference
                    if hard and (cur is None or not cur.get("hard")):
                        if cur is not None and "first" in cur:
                            it["first"] = cur["first"]      # the quote problem started then: no fresh 60 s
                        self.sell_intents[p.mint] = it      # protective exit replaces a pending TP / momentum intent
                    elif cur is None:
                        self.sell_intents[p.mint] = it
                    continue                            # filled on a real Jupiter SELL quote in execute_sells()
                if self.exit_fallback == "model":       # backtest: the same liquidity model as its entries
                    ex = self.exec.sell(st, tokens, mark, self._sol(), reason, now, force=hard)
                else:                                   # live paper without a Jupiter client: never near the mark
                    ex = self.exec.sell_haircut(st, tokens, mark, self._sol(), reason,
                                                self.cfg.hard_exit_no_quote_haircut_pct / 100, now)
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
        if self.cfg.lifecycle and self.cfg.entry_location_gate:
            # step 2: PULLBACK / SECOND_WAVE (last known location) take the limited entry slots first
            pref = set(self.cfg.entry_prefer_locations)
            def prio(c):
                r = self.decisions.get(c[0].mint) or {}
                ok = r.get("entry_location") in pref and r.get("entry_pullback_stable") is not False
                return 0 if ok else 1                   # a PULLBACK that is still falling gets no priority
            cands = sorted(cands, key=prio)
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
                decision = sc.decision
                rec["old_decision"], rec["blocked_by_old"] = sc.decision, rec["blocked_by"]
                rec["old_candidate"] = X.old_candidate(st, rec)
                es0 = st.early
                rec["old_early"] = ("UNKNOWN" if es0 is None or es0.strength is None else
                                    "TRUE" if es0.is_early is True else f"FALSE {es0.groups_computable}/7")
                rec["old_opportunity"], rec["old_confidence"] = sc.opportunity, sc.confidence
                rs = st.risk.score if st.risk else None
                rec["risk"] = rs
                rec["old_entry_risk_pass"] = rs is not None and rs <= 60          # OLD_ENTRY_RISK (hard limit)
                rec["new_entry_risk_pass"] = rs is not None and rs <= self.cfg.entry_max_risk   # NEW_ENTRY_RISK_BUFFER
                if self.cfg.experimental:                   # NEW engine decides; OLD kept for A/B
                    xd = X.evaluate(st, v, sc, self.cfg, now)
                    decision = xd.decision
                    rec.update({"engine": "experimental", "decision": decision, "blocked_by": xd.blocked_by,
                                "waiting": xd.waiting, "rejected": xd.rejected,
                                "why": xd.why + ["OLD: " + w for w in sc.why[:3]],
                                "early_score": xd.es.as_dict(), "new_opportunity": xd.opportunity,
                                "new_confidence": xd.confidence,
                                "new_early": "PASS" if xd.es.passed else ("UNKNOWN" if xd.es.score is None else "LOW")})
                    lq = xd.es.liquidity or {}
                    rec.update({"old_liquidity": lq.get("old"), "new_liquidity": lq.get("decision"),
                                "liquidity_model": lq.get("model"), "liquidity_equivalent_usd": lq.get("equivalent_usd"),
                                "liquidity_confidence": lq.get("confidence"), "curve_real_sol": lq.get("real_sol"),
                                "curve_virtual_sol": lq.get("virtual_sol")})
                else:
                    rec["early_score"] = X.early_score(st, now, self.cfg).as_dict()   # logged only (A/B)
                if self.cfg.lifecycle:                      # LIFECYCLE engine decides; OLD + experimental logged
                    if not self.cfg.experimental:
                        xd = X.evaluate(st, v, sc, self.cfg, now)
                    decision = self._lifecycle_decide(st, v, sc, rec, xd, now)
                rec["risk_allowed"], rec["risk_reasons"], rec["state"] = None, [], decision
                self.decisions[st.mint] = rec
                vet_items.append(rec)
                if decision != TRADE:
                    if rec["old_candidate"] and self.recorder is not None:
                        self._rec("candidate", st, rec, now, None, False, 0.0)       # OLD-only candidate, for A/B
                    if prev != decision:
                        self.log("WATCH" if decision in (WATCH, PENDING_IDENTITY) else "REJECT",
                                 f"{decision} opp {sc.opportunity} conf {sc.confidence} · {'; '.join(rec['why'][:2])}",
                                 st, now=now)
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
                fast = getattr(self.engine, "fast", None)
                if rec.get("engine") == "experimental" and st.mint in self._gate_waiting and fast is not None                         and st.mint in fast.checked:
                    self.fast_promoted.add(st.mint)
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
                if self.cfg.lifecycle and (rec.get("engine") or "old") != self.cfg.entry_engine:
                    rec["state"] = "SHADOW_ONLY"            # step 4: one frozen entry engine; the others are logged
                    continue
                last_fail = self.last_buy_attempt.get(st.mint)
                if last_fail is not None and now - last_fail < FAILED_BUY_COOLDOWN_S:
                    rec["state"] = "BUY_COOLDOWN"            # a failed fill pays fees: do not hammer the same mint
                    rec["blocked_by"] = (rec.get("blocked_by") or []) + [
                        f"buy_cooldown: last BUY fill FAILED {now - last_fail:.0f}s ago < {FAILED_BUY_COOLDOWN_S:.0f}s"]
                    continue
                if self.entry_block:                        # no durable store: observe / count, never open
                    rec["state"] = "NOT_DURABLE"
                    rec["blocked_by"] = (rec.get("blocked_by") or []) + [f"durability: {self.entry_block}"]
                    if st.mint not in self.entry_block_seen:
                        self.entry_block_seen.add(st.mint)
                        self.log("BLOCK", f"BUY not opened — {self.entry_block}", st, now=now)
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
                                             "why": sc.why, "setup": "+".join(sorted(reasons)),
                                             "risk_at_candidate": st.risk.score if st.risk else None,
                                             "lifecycle": rec.get("lifecycle_name"), "setup_type": rec.get("setup_type"),
                                             "setup_score": rec.get("setup_score"),
                                             "decision_market": self._mobs(st, now)}
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
                    if ex.status == "FAILED":
                        self.last_buy_attempt[st.mint] = now
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

    def _simulated_fill(self, st, rec: dict, it: dict, usd: float, sol: float, qr, now: float) -> None:
        """EXPERIMENTAL PAPER (spec 3.6): the candidate stays; no Jupiter route -> fill on the liquidity model so the
        signal's quality is still measured. Same Risk Engine re-check (model price impact). Tagged 'noquote' so
        P&L of executable and simulated-only trades are always reported apart."""
        est = self.exec.estimate(st, usd)
        feeds_ok, why = self._feeds_ok()
        last = self.book.last_exit.get(st.mint)
        rd = self.risk.check_entry(mint=st.mint, usd=usd, equity=self.book.equity(), peak=self.book.peak,
                                   day_start=self.book.day_start, open_positions=len(self.book.positions),
                                   holding=False, in_cooldown=bool(last and now - last < self.cfg.cooldown_min * 60),
                                   exposure=self.book.exposure(), est_impact=est["impact"], feeds_ok=feeds_ok,
                                   feeds_reason=why)
        if not rd.allowed:
            rec["state"] = "QUOTE_FAILED"
            self.log("BLOCK", f"BUY → QUOTE FAILED ({qr.label()}) → simulated fill refused by risk: "
                              + "; ".join(rd.reasons), st, now=now)
            self.audit.execution("skip", st, now, "; ".join(rd.reasons), reason="risk_at_simulated_fill")
            self._rec("quote", st, rec, now, qr.status, qr.label() + " · simulated fill refused by risk", None, False,
                      int(self.cfg.max_slippage_pct * 100))
            return
        ex = self.exec.buy(st, usd, sol, now)
        ex.route = f"{ex.route} · SIMULATED (no Jupiter quote: {qr.status})"
        self._rec("quote", st, rec, now, qr.status, qr.label() + " · simulated fill" +
                  ("" if ex.status == "FILLED" else f" failed: {ex.reason}"), None, ex.status == "FILLED",
                  int(self.cfg.max_slippage_pct * 100))
        if ex.status == "FILLED":
            setup = (it.get("setup") or "") + "+noquote"
            self.book.open(ex, self.cfg, now, it.get("opportunity"), it.get("why"), st.market.liquidity_usd,
                           st.market.vol_5m, setup=setup)
            self._tag_position(st.mint, it)
            rec["state"] = "BOUGHT_SIMULATED"
            self.log("BUY", f"BUY → QUOTE FAILED ({qr.status}) → SIMULATED FILL ${usd:,.2f} @ ${ex.fill_price:.8g} · "
                            f"model impact {ex.price_impact_pct:.2f}% · {ex.route}", st, usd=usd, price=ex.fill_price, now=now)
            self.audit.execution("buy", st, now, f"SIMULATED (no quote {qr.status}) ${usd:,.2f}", reason="simulated_noquote")
        else:
            self.book.record(ex)
            if ex.status == "FAILED":
                self.last_buy_attempt[st.mint] = now
            self.log("FAILED", f"BUY → QUOTE FAILED ({qr.status}) → simulated fill failed: {ex.reason}", st, now=now)
            self.audit.execution("skip", st, now, ex.reason, reason="simulated_fill_failed")

    # ---------------------------------------------------------------- execution calibration
    @staticmethod
    def _chg_bucket(st) -> str:
        pc = abs(st.market.price_change_5m) if st.market and st.market.price_change_5m is not None else None
        return "unknown" if pc is None else "<10%" if pc < 10 else "10-40%" if pc < 40 else ">=40%"

    async def _latency_probe(self, st, mint: str, lamports: int, q: dict):
        """Real latency drift: wait a simulated latency (0.4-1.5 s), re-quote the same size, compare outAmount.
        Positive = adverse (fewer tokens). Quotes only. Returns bps or None."""
        if not self.cfg.latency_probe or not hasattr(self.jupiter, "quote_result") or self.sell_intents:
            return None                                 # research probe never competes with a pending SELL
        if not self._quota("probe"):
            return None
        q_ts = time.time()
        await asyncio.sleep(self._probe_rng.uniform(0.4, 1.5))
        from trading.jupiter import WSOL, route_label
        r = await self._jq(WSOL, mint, lamports, "probe", attempts=1, budget_s=3.0)
        if not r.ok:
            return None
        try:
            drift = 1 - int(r.quote["outAmount"]) / int(q["outAmount"])
        except (KeyError, TypeError, ValueError, ZeroDivisionError):
            return None
        self.latency_samples.append((self._chg_bucket(st), drift))
        rq_ts = time.time()
        m = st.market
        try:
            imp = float(q.get("priceImpactPct"))
        except (TypeError, ValueError):
            imp = None
        created = st.info.created_at or (m.pair_created_at if m else None)
        self.latency_log.append({"mint": mint, "quote_ts": q_ts, "requote_ts": rq_ts, "latency_s": round(rq_ts - q_ts, 3),
                                 "drift_bps": round(10_000 * drift, 1), "route": route_label(q),
                                 "liquidity_usd": m.liquidity_usd if m else None, "bucket": self._chg_bucket(st),
                                 "is_curve": bool(m and m.is_curve), "age_s": round(q_ts - created) if created else None,
                                 "impact_pct": None if imp is None else round(100 * imp, 3),
                                 "quote_size_usd": round(lamports / 1e9 * (self._sol() or 0), 2),
                                 "hour_utc": time.gmtime(q_ts).tm_hour})
        self._rec("latency", self.latency_log[-1])
        self._last_probe = self.latency_log[-1]
        return round(10_000 * drift)

    @staticmethod
    def _pctl(xs, p: float) -> float | None:
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * (len(xs) - 1)))] if xs else None

    def latency_tier(self) -> tuple[str, float | None, str]:
        """AUTO tier from the measured adverse drift (re-quotes):
        < 50 samples -> CURRENT · >= 50 -> EMPIRICAL_P90 if the P90 is stable · >= 100 -> EMPIRICAL_P75 if the P75 is
        stable. Stable = the percentile of the two most recent halves differs by <= max(10 bps, 35 %), and the
        distribution is not erratic (P90 <= 5 x P75 + 20 bps). Otherwise stay on the safer tier.
        Returns (tier, percentile, why)."""
        xs = [max(0.0, d) for _, d in self.latency_samples]
        n = len(xs)

        def stable(p: float, window: int) -> bool:
            w = xs[-window:]
            a, b = self._pctl(w[: window // 2], p), self._pctl(w[window // 2:], p)
            return a is not None and b is not None and abs(a - b) <= max(0.0010, 0.35 * max(a, b))
        if n < LAT_P90_MIN:
            return "CURRENT", None, f"{n} < {LAT_P90_MIN} samples"
        p75, p90 = self._pctl(xs, 0.75), self._pctl(xs, 0.90)
        if p90 > 5 * p75 + 0.0020:
            return "CURRENT", None, f"erratic distribution (P90 {p90 * 1e4:.0f} bps vs P75 {p75 * 1e4:.0f} bps)"
        if n >= LAT_P75_MIN and stable(0.75, LAT_P75_MIN):
            return "EMPIRICAL_P75", 0.75, f"{n} samples, P75 stable"
        if stable(0.90, LAT_P90_MIN):
            return "EMPIRICAL_P90", 0.90, f"{n} samples, P90 stable"
        return "CURRENT", None, f"{n} samples, P90 not stable yet"

    def _empirical_slip(self, st) -> float | None:
        """EMPIRICAL (explicit): P75 once cfg.empirical_min_samples exist. AUTO: the tier from latency_tier().
        Percentile over the token's |change 5m| bucket when it has >= 15 samples, else over all samples."""
        if self.cfg.latency_slippage_model == "AUTO":
            tier, p, why = self.latency_tier()
            self.exec.empirical_label = tier if p is not None else why
            if p is None:
                return None
        else:
            if len(self.latency_samples) < self.cfg.empirical_min_samples:
                self.exec.empirical_label = "not enough samples"
                return None
            p = 0.75
            self.exec.empirical_label = "EMPIRICAL"
        b = self._chg_bucket(st)
        xs = [max(0.0, d) for k, d in self.latency_samples if k == b]
        if len(xs) < 15:
            xs = [max(0.0, d) for _, d in self.latency_samples]
        return self._pctl(xs, p)

    def latency_stats(self) -> dict:
        xs = sorted(d for _, d in self.latency_samples)
        pct = lambda p: round(10_000 * xs[min(len(xs) - 1, int(p * (len(xs) - 1)))], 1) if xs else None  # noqa: E731
        tier, _, why = self.latency_tier()
        lat = [x["latency_s"] for x in self.latency_log]
        return {"samples": len(xs), "min_samples_for_empirical": self.cfg.empirical_min_samples,
                "empirical_active": len(xs) >= self.cfg.empirical_min_samples,
                "auto_tier": tier, "auto_tier_why": why,
                "real_latency_s_p50": round(self._pctl(lat, 0.5), 3) if lat else None,
                "drift_bps_p50": pct(0.5), "drift_bps_p75": pct(0.75), "drift_bps_p90": pct(0.9),
                "adverse_share": round(sum(1 for x in xs if x > 0) / len(xs), 2) if xs else None,
                "model": self.cfg.latency_slippage_model}

    # ---------------------------------------------------------------- risk-spike forensics
    FORENSIC_OFFSETS = (5, 10, 15, 30, 60, 120, 300, 600, 1800)

    @staticmethod
    def _risk_snap(st, label: str, t_off: float, entry: dict | None = None, buy_ts: float | None = None) -> dict:
        rk, m = st.risk, st.market
        hs, ds = st.stamps.get("holders"), st.stamps.get("dev")
        snap = {"at": label, "t": round(t_off, 1), "risk": rk.score if rk else None,
                "factors": sorted(f"{f.category}:{f.key}:{f.points}" for f in (rk.factors if rk else [])),
                "holders_stamp": hs.updated_at if hs else None, "dev_stamp": ds.updated_at if ds else None,
                "buy_ts": buy_ts,
                "price": m.price_usd if m else None, "market_age_s": round(time.time() - m.updated_at, 1) if m else None,
                "liq": m.liquidity_usd if m else None, "liq_state": st.liquidity_intel.state if st.liquidity_intel else None,
                "holders_fetched": st.holders.fetched_at if st.holders else None, "holder_status": st.holder_status,
                "dev_status": st.dev.status if st.dev else None, "dq": st.dq_status}
        if entry is not None:
            snap.update(attribute_risk(entry, snap, buy_ts))
        return snap

    def _forensic_open(self, st, now: float, ctx: dict) -> None:
        entry = self._risk_snap(st, "ENTRY", 0, buy_ts=now)
        entry.update({"risk_new": 0, "risk_data_refresh": 0, "risk_data_stale": 0, "changed": []})
        pre = list(self.pre_buf.get(st.mint) or [])
        before = {}
        for lbl, off in (("T-30s", 30), ("T-10s", 10), ("T-5s", 5)):
            cand = [x for x in pre if x["t"] <= now - off + 2.5]
            before[lbl] = cand[-1] if cand else None
        rec = self.decisions.get(st.mint) or {}
        self.forensics[st.mint] = {"mint": st.mint, "symbol": st.info.symbol, "entry_ts": now, "ctx": ctx,
                                   "snaps": [entry], "done": set(), "exit": None, "pre_entry": before,
                                   "lifecycle": rec.get("lifecycle_name"), "setup_type": rec.get("setup_type"),
                                   "setup_score": rec.get("setup_score"),
                                   "shadow_at_entry": {k: rec.get(k) for k in (
                                       "money_flow_score", "independent_buyer_score", "cluster_risk",
                                       "exit_liquidity_risk", "entry_location", "entry_extension",
                                       "edge_shadow_score", "money_flow")}}

    def _forensic_tick(self, states: dict, now: float) -> None:
        for mint, fx in list(self.forensics.items()):
            age = now - fx["entry_ts"]
            st = states.get(mint)
            if st is None or age > max(self.FORENSIC_OFFSETS) + 10:
                continue
            for off in self.FORENSIC_OFFSETS:
                if off not in fx["done"] and age >= off:
                    fx["done"].add(off)
                    snap = self._risk_snap(st, f"+{off}s", age, fx["snaps"][0], fx["entry_ts"])
                    r = self.decisions.get(mint) or {}
                    snap.update({k: r.get(k) for k in ("money_flow_score", "exit_liquidity_risk", "entry_location",
                                                       "cluster_risk")})
                    fx["snaps"].append(snap)
                    if off >= 60 and self.recorder is not None:      # upsert progressively (+60s .. +30m)
                        self._rec("forensic", fx)
                    break
        if len(self.forensics) > 200:
            for m in sorted(self.forensics, key=lambda k: self.forensics[k]["entry_ts"])[:-200]:
                self.forensics.pop(m, None)

    def _tag_position(self, mint: str, it: dict) -> None:
        p = self.book.positions.get(mint)
        if p is not None:
            p.entry_engine = (self.decisions.get(mint) or {}).get("engine") or "old"
            p.sample_id = self.cfg.sample_id()
            p.sample_epoch = self.sample_epoch.id if self.sample_epoch.started_at else ""
            p.entry_lifecycle, p.entry_setup, p.entry_setup_score = it.get("lifecycle"), it.get("setup_type"), it.get("setup_score")
            d = self.decisions.get(mint) or {}
            p.entry_location, p.entry_extension_5m = d.get("entry_location"), d.get("entry_extension")

    def _history(self, mint: str):
        store = getattr(self.engine, "history", None)
        return getattr(store, "_h", {}).get(mint) if store is not None else None

    def _lifecycle_decide(self, st, v, sc, rec: dict, xd, now: float) -> str:
        li = LC.classify(st, self.cfg, now)
        tr, migrated = self.lifecycle_tracker.update(st, li, now)
        if migrated:
            self.log("INFO", f"LIFECYCLE → POST_MIGRATION: NEW / PRE-MIGRATION setups closed, second-wave state "
                             f"started on pair {li.pair_address[:8]}", st, now=now)
        oc = self.onchain.done.get(st.mint) if self.onchain is not None else None
        h = self._history(st.mint)
        ld = LD.evaluate(st, v, sc, self.cfg, now, tr, h, oc, li)
        su = ld.setup
        sd = su.as_dict() if su is not None else None
        rec.update({
            "engine": "lifecycle", "decision": ld.decision, "blocked_by": ld.blocked_by, "waiting": ld.waiting,
            "rejected": ld.rejected, "why": ld.why + ["OLD: " + w for w in sc.why[:2]],
            "experimental_decision": xd.decision if xd is not None else None,
            "blocked_by_experimental": xd.blocked_by if xd is not None else None,
            "would_have_bought_old": bool(rec.get("old_candidate")),
            "would_have_bought_experimental": xd is not None and xd.decision == TRADE,
            "would_have_bought_lifecycle": ld.decision == TRADE,
            "lifecycle": li.as_dict(), "lifecycle_name": li.lifecycle, "lifecycle_confidence": li.confidence,
            "migration_progress": li.migration_progress,
            "transitions": {k: getattr(tr, k) for k in ("discovery_ts", "new_start_ts", "premigration_start_ts",
                                                        "migration_ts", "postmigration_start_ts")},
            "setup": sd, "setup_type": su.setup_type if su else None,
            "setup_score": None if su is None or su.score is None else round(su.score, 1),
            "setup_threshold": ld.threshold, "setup_decision": ld.decision, "setup_timestamp": now,
            "new_score": sd["score"] if su and su.setup_type == "NEW" else None,
            "premigration_score": sd["score"] if su and su.setup_type == "PRE_MIGRATION" else None,
            "second_wave_score": sd["score"] if su and su.setup_type == "SECOND_WAVE" else None,
            "post_state": (su.extra.get("post_state") or {}).get("state") if su and su.setup_type == "SECOND_WAVE" else None,
            "early_score": ld.es.as_dict() if ld.es is not None else rec.get("early_score"),
            "risk_at_lifecycle": st.risk.score if st.risk else None,
            "pre_shadow_decision": "WOULD_BUY" if ld.pre_shadow_would_buy else (
                ld.decision if su is not None and su.setup_type == "PRE_MIGRATION" else None)})
        self._shadow(st, rec, su, ld, h, now)
        decision = self._entry_location_gate(st, rec, ld.decision, now)
        lq = (ld.es.liquidity if ld.es is not None else None) or {}
        rec.update({"old_liquidity": lq.get("old"), "new_liquidity": lq.get("decision"),
                    "liquidity_model": lq.get("model"), "liquidity_equivalent_usd": lq.get("equivalent_usd"),
                    "liquidity_confidence": lq.get("confidence")})
        return decision

    def _entry_location_gate(self, st, rec: dict, decision: str, now: float) -> str:
        """Step 2: a lifecycle TRADE is not bought while the price is extended. Blocked when the 5-min extension is
        above entry_max_extension_5m_pct or the location is EXTENDED / MID_MOVE / UNKNOWN, there is < 300 s of
        history, or a PULLBACK / SECOND_WAVE made a new low within the last 180 s. Blocked -> WATCH with the reason in blocked_by; re-evaluated every tick."""
        c = self.cfg
        if decision != TRADE or not c.entry_location_gate:
            return decision
        from trading.entry_location import MIN_HISTORY_S, PULLBACK_STABLE_S, SECOND_WAVE_STABLE_S
        loc, ext = rec.get("entry_location"), rec.get("entry_extension")
        det = rec.get("entry_location_detail") or {}
        hist, low_age = det.get("history_s"), det.get("last_low_age_s")
        why = []
        if loc in (None, "UNKNOWN"):
            why.append("entry_location: UNKNOWN")
        if hist is None or hist < MIN_HISTORY_S:
            why.append(f"entry_location: history {hist or 0:.0f}s < {MIN_HISTORY_S:.0f}s")
        if loc == "PULLBACK":                       # a pullback always has a low after the high: unknown = falling
            stable = low_age is not None and low_age >= PULLBACK_STABLE_S
        elif loc == "SECOND_WAVE":                  # G7: no new low within 180 s (None = no low below the high)
            stable = low_age is None or low_age >= SECOND_WAVE_STABLE_S
        else:
            stable = True
        rec["entry_pullback_stable"] = stable if loc in ("PULLBACK", "SECOND_WAVE") else None
        if not stable:
            why.append(f"entry_location: {loc} still falling (new low {low_age if low_age is not None else '?'}s "
                       f"ago < {SECOND_WAVE_STABLE_S if loc == 'SECOND_WAVE' else PULLBACK_STABLE_S:.0f}s)")
        if ext is not None and ext > c.entry_max_extension_5m_pct:
            why.append(f"entry_location: extension_5m {ext:.0f}% > {c.entry_max_extension_5m_pct:.0f}%")
        if loc in c.entry_block_locations:
            why.append(f"entry_location: {loc}")
        from trading.gate_stats import record
        record(self.book.gate_seen, st.mint, why, now)  # G8: block rate by reason (unique tokens)
        if not why:
            return decision
        rec.update({"decision": WATCH, "entry_gate_blocked": True,
                    "blocked_by": (rec.get("blocked_by") or []) + why, "setup_decision": WATCH})
        if (self.decisions.get(st.mint) or {}).get("entry_gate_blocked") is not True:
            self.log("BLOCK", "no chasing — " + "; ".join(why), st, now=now)
            self._rec("gate_event", st, "blocked", rec, now)            # step C: follow it like an entry
        return WATCH

    @staticmethod
    def _mobs(st, now: float) -> dict:
        from trading.price_provenance import market_obs
        return market_obs(st, now).as_dict()

    def _provenance_open(self, st, it: dict, qobs: dict, m_at_quote: dict, ex, fill: dict, now: float) -> None:
        from trading.price_provenance import pct
        p = self.book.positions.get(st.mint)
        if p is None:
            return
        if self.recorder is not None:
            self._rec("gate_event", st, "entered", self.decisions.get(st.mint) or {}, now)
        self.provenance[st.mint] = {
            "mint": st.mint, "symbol": st.info.symbol, "lifecycle": it.get("lifecycle"),
            "entry_ts": now, "decision_ts": it.get("ts"),
            "decision_market": it.get("decision_market"), "quote_market": m_at_quote, "entry_quote": qobs,
            "entry_fill": {"price": ex.fill_price, "source": "jupiter_buy_quote x (1 + simulated latency slip)",
                           "latency_ms": ex.latency_ms, "latency_slip_pct": ex.slippage_pct,
                           "route_impact_pct": ex.price_impact_pct},
            "entry_price_book": p.entry_price, "stop_price_book": p.stop_price,
            "stop_reference_source": "dexscreener mark fetched after the fill (bot._price + _post_entry)",
            "discrepancy_quote_vs_market_pct": pct(qobs.get("price"), m_at_quote.get("price")),
            "fill_vs_market_pct": pct(ex.fill_price, m_at_quote.get("price")),
            "fill_vs_quote_pct": pct(ex.fill_price, qobs.get("price")),
            "decision_to_quote_ms": round(1000 * ((qobs.get("ts") or now) - (it.get("ts") or now)))
            if qobs.get("ts") else None, "quote_to_fill_ms": ex.latency_ms,
            "marks": [], "jupiter_marks": [], "exit": None, "tokens": p.initial_tokens,
            "pair_at_entry": m_at_quote.get("pair"), "route_pool": qobs.get("pair")}
        from trading.truth_price import TruthTracker
        c = self.cfg
        m = st.market
        cs = TruthTracker(f"{p.id}:{st.mint}", st.mint, st.info.symbol, it.get("lifecycle"), ex.fill_price, now,
                          p.initial_tokens, stop_pct(c), c.tp1_pct, c.tp1_sell_frac, c.tp2_pct, c.trailing_pct,
                          c.max_hold_min * 60, pair=(m.pair_address or "") if m else "", dex=(m.dex_id or "") if m else "",
                          entry_quote_price=qobs.get("price"), execution_impact_pct=ex.price_impact_pct,
                          latency_model_pct=ex.slippage_pct)
        rec = self.decisions.get(st.mint) or {}
        created = st.info.created_at or (m.pair_created_at if m else None)
        probe = getattr(self, "_last_probe", None)
        drift_ok = probe is not None and probe.get("mint") == st.mint and now - probe.get("quote_ts", 0) <= 15
        cs.ctx = {"setup": "/".join(x for x in (it.get("lifecycle"), rec.get("setup_type")) if x) or None,
                  "setup_score": rec.get("setup_score"), "age_s": round(now - created) if created else None,
                  "liquidity_usd": m.liquidity_usd if m else None, "opportunity": rec.get("opportunity"),
                  "risk": st.risk.score if st.risk else None, "cost_usd": p.cost_usd,
                  # measured entry latency: Jupiter re-quote of the same BUY after the landing window (bps -> %)
                  "entry_latency_measured_pct": round(probe["drift_bps"] / 100, 4) if drift_ok else None}
        self.cs_shadow[st.mint] = cs
        if len(self.cs_shadow) > 50:
            for k in [k for k, t in self.cs_shadow.items() if not t.due(time.time()) and t.finalized][:10]:
                self.cs_shadow.pop(k, None)

    def _provenance_tick(self, states: dict, now: float) -> None:
        for mint, pv in list(self.provenance.items()):
            if pv.get("exit") is None and mint in states and mint in self.book.positions:
                o = self._mobs(states[mint], now)
                p = self.book.positions[mint]
                o["post_entry"] = self._post_entry(states[mint], p)      # pre-entry prints are not position marks
                pv["marks"].append(o)
                del pv["marks"][:-400]
        for mint, trail in list(self.market_trail.items()):
            st = states.get(mint)
            if st is None or now - trail[0]["ts"] > 90:
                if now - trail[0]["ts"] > 90:
                    self.market_trail.pop(mint, None)
                continue
            o = self._mobs(st, now)
            if o.get("price") != trail[-1].get("price"):
                trail.append(o)

    async def common_source_round(self, now: float | None = None) -> int:
        """V1.3 TRUTH PRICE round (shadow): Jupiter SELL quote for each tracked trade's OWN position size, 5 s for the
        first minute then 15 s, up to 30 min. BUY quotes have priority (skipped while BUY intents are pending) and
        the round has its own budget (TRUTH_QUOTES_PER_MIN). Nothing here is read by entries, exits or the book."""
        from trading.jupiter import WSOL
        from trading.price_provenance import sell_quote_obs
        from trading.truth_price import compare_sources, curve_spot, snapshot_from_quote
        now = now or time.time()
        if self.jupiter is None or not hasattr(self.jupiter, "quote_result"):
            return 0
        states = {x.mint: x for x in (self.engine.published or [])}
        if self.sell_intents:                     # SELL quotes first: truth quotes wait (own budget, lower priority)
            return 0
        n = await self._truth_exit_quotes(states, now)
        self._truth_finalize(now)
        if self.intents:
            return n
        for mint, tr in list(self.cs_shadow.items()):
            if not tr.due(now):
                continue
            while self.truth_quotes and now - self.truth_quotes[0] > 60:
                self.truth_quotes.popleft()
            if len(self.truth_quotes) >= TRUTH_QUOTES_PER_MIN or not self._quota("truth"):
                self.truth_skipped_budget += 1
                break
            st = states.get(mint)
            dec = (st.info.decimals if st else None) or 6
            pos = self.book.positions.get(mint)
            # the EXACT size held: production's tokens while it holds, else the truth shadow's own remaining size
            size = pos.tokens if pos is not None and pos.tokens > 0 else tr.size_now()
            if size <= 0:
                tr.last_quote = now
                continue
            t_req = time.time()
            r = await self._jq(mint, WSOL, int(size * 10 ** dec), "truth")
            t_resp = time.time()
            self.truth_quotes.append(now)
            tr.last_quote = now
            n += 1
            snap = snapshot_from_quote(r, mint=mint, tokens=size, sol_price=self._sol(), t_req=t_req, t_resp=t_resp)
            snap.timestamp = max(snap.timestamp, now)        # bot clock (tests drive `now`)
            self.truth_ledger.count_quote(snap)
            rec = self.decisions.get(mint) or {}
            snap.lifecycle = rec.get("lifecycle_name") or tr.lifecycle
            if st is not None and st.market is not None:
                m = st.market
                snap.pair_address, snap.pair_identity = m.pair_address or "", f"{m.dex_id or '?'}:{m.pair_address or ''}"
                conflict = (m.is_curve and st.info.complete is True) or (not m.is_curve and st.info.complete is False)
                snap.migration_state = "MIGRATING" if conflict else ("CURVE" if m.is_curve else "AMM")
                if m.price_usd:
                    snap.ds_price = m.price_usd
                    snap.ds_age_s = round(now - m.updated_at, 1) if m.updated_at else None
            if st is not None:
                snap.curve_price, snap.curve_status, _ = curve_spot(st.info, self._sol(), now)
            if pos is not None and pos.entry_price and pos.last_price:
                snap.old_pnl_pct = round(100 * (pos.last_price / pos.entry_price - 1), 3)
            elif tr.old_exit:
                snap.old_pnl_pct = tr.old_exit.get("pnl_pct")
            compare_sources(snap)
            before = len(tr.snapshots), tr.rejected
            event = tr.add(snap)
            if tr.rejected == before[1]:
                self._rec("truth_snapshot", tr.snapshots[-1])
            if event is not None:
                self._rec("truth_epoch", event)
            pv = self.provenance.get(mint)
            if pv is not None:
                if r.ok:
                    pv["jupiter_marks"].append(sell_quote_obs(r.quote, size, self._sol(), mint, t_req, t_resp).as_dict())
                    del pv["jupiter_marks"][:-200]
                pv["common_source"] = tr.summary(now)
                self._rec("price_provenance", mint, pv)
            self._truth_trade_rec(tr, now)
        return n

    async def _sell_quote_snap(self, mint: str, tokens: float, dec: int, now: float):
        from trading.jupiter import WSOL
        from trading.truth_price import snapshot_from_quote
        t_req = time.time()
        r = await self._jq(mint, WSOL, int(tokens * 10 ** dec), "truth")
        t_resp = time.time()
        self.truth_quotes.append(now)
        snap = snapshot_from_quote(r, mint=mint, tokens=tokens, sol_price=self._sol(), t_req=t_req, t_resp=t_resp)
        snap.timestamp = max(snap.timestamp, now)
        self.truth_ledger.count_quote(snap)
        return snap

    async def _truth_exit_quotes(self, states: dict, now: float) -> int:
        """OFFICIAL TRUTH P&L inputs: every production SELL gets a Jupiter SELL quote for EXACTLY the tokens sold as
        soon as possible (priority over BUY intents and the periodic budget), then a same-size re-quote after
        LATENCY_REQUOTE_S = measured exit latency. Quotes only; nothing is sent."""
        from trading.truth_price import LATENCY_REQUOTE_S, apply_exit_quote, apply_requote
        n = 0
        for mint, tr in list(self.cs_shadow.items()):
            st = states.get(mint)
            dec = (st.info.decimals if st else None) or 6
            for ev in tr.exit_events:
                if (ev["status"] == "PENDING" or ev["requote_status"] == "PENDING") and not self._quota("truth"):
                    return n                            # bot budget used up: stays PENDING for the next round
                if ev["status"] == "PENDING":
                    snap = await self._sell_quote_snap(mint, ev["tokens"], dec, now)
                    apply_exit_quote(ev, snap, self._sol())
                    n += 1
                elif ev["requote_status"] == "PENDING" and now - (ev["quote_ts"] or now) >= LATENCY_REQUOTE_S:
                    apply_requote(ev, await self._sell_quote_snap(mint, ev["tokens"], dec, now))
                    n += 1
        return n

    def _truth_finalize(self, now: float) -> None:
        """Closed production trade + every exit quoted (and re-quoted) -> one TRUTH P&L record in the ledger."""
        from trading.truth_price import trade_record
        for mint, tr in list(self.cs_shadow.items()):
            if tr.finalized or tr.production_open or not tr.exit_events:
                continue
            if any(e["status"] == "PENDING" or e["requote_status"] == "PENDING" for e in tr.exit_events):
                continue
            o = tr.old_exit or {}
            cost = tr.ctx.get("cost_usd")
            legacy = {"pnl_pct": o.get("pnl_pct"), "source": o.get("fill_source"),
                      "pnl_usd": None if o.get("pnl_pct") is None or not cost else round(cost * o["pnl_pct"] / 100, 4)}
            rec = trade_record(tr, legacy, self.exec.network_fee(self._sol()))
            tr.finalized = True
            self.truth_ledger.add(rec)
            self._rec("truth_ledger_trade", rec)
            self.log("INFO", f"TRUTH P&L {rec['symbol']}: {rec['truth_pnl_pct']}% (legacy {rec['legacy_pnl_pct']}%) · "
                             f"{'common-source' if rec['valid'] else 'INVALID: ' + '; '.join(rec['invalid_reasons'])}",
                     None, now=now)

    def _truth_trade_rec(self, tr, now: float) -> None:
        """trade_price_truth (+ truth_exit_shadow once production closed). DS-only P&L = the production rules on
        post-entry DexScreener marks from the DexScreener entry price (no Jupiter at all)."""
        from trading.price_provenance import CommonSourceExit
        pv = self.provenance.get(tr.mint) or {}
        sm = tr.summary(now)
        c = self.cfg
        pnl_ds = None
        dm = (pv.get("quote_market") or {}).get("price")
        if dm:
            mc = CommonSourceExit(dm, tr.entry_ts, stop_pct(c), c.tp1_pct, c.tp1_sell_frac, c.tp2_pct,
                                  c.trailing_pct, c.max_hold_min * 60)
            for mk in pv.get("marks") or []:
                if mk.get("price") and mk.get("post_entry"):
                    mc.on_price(mk["price"], mk["ts"])
            ex = pv.get("exit") or {}
            if mc.closed_at is None and (ex.get("exit_market") or {}).get("price"):
                mc.force_close(ex["exit_market"]["price"], ex["ts"], "mirror:" + (ex.get("reason") or "open"))
            pnl_ds = mc.summary()["pnl_pct"]
        path = (pv.get("exit") or {}).get("path") or {}
        imp, lat = tr.execution_impact_pct, tr.latency_model_pct
        row = {"trade_id": tr.trade_id, "token_ca": tr.mint, "symbol": tr.symbol, "entry_ts": tr.entry_ts,
               "lifecycle": tr.lifecycle, "pair_address": tr.pair, "entry_fill_price": tr.entry_fill,
               "jupiter_buy_quote_price": tr.entry_quote_price, "execution_impact_pct": imp,
               "latency_model_pct": lat,
               "total_simulated_entry_cost_pct": None if imp is None or lat is None
               else round(100 * ((1 + imp / 100) * (1 + lat / 100) - 1), 4),
               "pnl_ds": pnl_ds, "pnl_truth": sm["pnl_pct"], "pnl_common_source": sm["pnl_common_source"],
               "old_pnl": (tr.old_exit or {}).get("pnl_pct"), "mfe_truth": sm["mfe_pct"], "mae_truth": sm["mae_pct"],
               "time_to_mfe_s": sm["time_to_mfe_s"], "time_to_mae_s": sm["time_to_mae_s"],
               "mfe_ds": path.get("mfe_pct"), "mae_ds": path.get("mae_pct"),
               "truth_coverage_pct": sm["coverage_valid_pct"], "label": sm["label"], "data": sm}
        tr.trade_row = row
        self._rec("truth_trade", row)
        if tr.old_exit:
            self._rec("truth_exit", {"trade_id": tr.trade_id, "token_ca": tr.mint, "timestamp": tr.old_exit["ts"],
                                     "exit_reason_old": sm["exit_reason_old"], "exit_reason_truth": sm["exit_reason_truth"],
                                     "exit_reason_old_raw": tr.old_exit["reason"], "exit_reason_truth_raw": sm["exit_reason"],
                                     "old_price": tr.old_exit["price"], "truth_price": sm["truth_price"],
                                     "old_pnl": tr.old_exit["pnl_pct"], "truth_pnl": sm["pnl_pct"],
                                     "pnl_common_source": sm["pnl_common_source"],
                                     "old_fast_sl_class": sm["old_fast_sl_class"]})

    def truth_panel(self, now: float | None = None) -> dict:
        """Dashboard PRICE TRUTH: no executable quote (or older than 30 s) -> TRUTH UNKNOWN, never a substitute."""
        from trading.truth_price import is_fresh
        now = now or time.time()
        opened = []
        for p in self.book.positions.values():
            tr = self.cs_shadow.get(p.mint)
            lv = tr.summary(now)["last_valid"] if tr is not None else None
            fresh = lv is not None and is_fresh(lv, now)
            opened.append({"symbol": p.symbol, "mint": p.mint, "entry": p.entry_price, "ds_mark": p.last_price,
                           "ds_mark_post_entry": p.last_price_ts is not None and p.last_price_ts > p.opened_at,
                           "jup_sell": lv["price_usd"] if fresh else None,
                           "old_pnl_pct": round(p.pnl_pct(), 2) if p.pnl_pct() is not None else None,
                           "truth_pnl_pct": lv["truth_pnl_pct"] if fresh else None,
                           "truth": "VALID" if fresh else "TRUTH UNKNOWN",
                           "truth_age_s": round(now - lv["timestamp"], 1) if lv else None})
        recent = []
        for tr in self.cs_shadow.values():
            for x in tr.snapshots[-3:]:
                recent.append({"symbol": tr.symbol, "ts": x["timestamp"], "ds": x["ds_price"], "jup_sell": x["price_usd"],
                               "entry": tr.entry_fill, "discrepancy_pct": x["ds_vs_truth_pct"], "ds_age_s": x["ds_age_s"],
                               "status": x["source_status"], "class": x["ds_class"], "confidence": x["confidence"]})
        recent.sort(key=lambda r: -r["ts"])
        from trading.truth_price import ledger_stats
        led = self.truth_ledger
        st_ = ledger_stats(led.trades)
        book = self.book.stats(now)
        return {"status": led.acceptance(), "truth": st_,
                "legacy": {"label": "LEGACY / REFERENCE (DexScreener marks + liquidity model) — not truth",
                           "net_pnl": book.get("net_pnl"), "closed": book.get("closed"), "win_rate": book.get("win_rate"),
                           "same_trades_mean_pct": st_["legacy_pnl_pct_same_trades"]["mean"],
                           "sign_differs_from_truth": st_["sign_differs_from_legacy"]},
                "last_trades": [{k: t.get(k) for k in ("symbol", "valid", "truth_pnl_pct", "legacy_pnl_pct", "exit_reason",
                                                       "holding_s", "mfe_truth", "mae_truth")} for t in led.trades[-8:]][::-1],
                "open": opened, "recent": recent[:15], "label": "TRUTH_PNL (executable SELL quotes)",
                "quotes_last_min": sum(1 for t in self.truth_quotes if now - t <= 60),
                "skipped_budget": self.truth_skipped_budget}

    def _shadow(self, st, rec: dict, su, ld, h, now: float) -> None:
        """V1.1 SHADOW research on the decision record: money flow, independence, cluster risk, exit liquidity,
        entry location, NEW_EDGE score and the A/B shadow decisions (B = +money flow, C = +money flow +exit liquidity).
        Logged only: none of it changes `decision`. Computed for tokens that matter (TRADE, near threshold, held)."""
        from trading.entry_location import entry_location
        from trading.exit_liquidity import exit_liquidity
        from trading.money_flow import money_flow_features, score_money_flow
        thr = rec.get("setup_threshold")
        near = su is not None and su.score is not None and thr is not None and su.score >= thr - 20
        if not (near or ld.decision == TRADE or st.mint in self.book.positions or ld.pre_shadow_would_buy):
            return
        mf_rec = self.money_flow.recs.get(st.mint) if self.money_flow is not None else None
        funders = self.money_flow.funders if self.money_flow is not None else {}
        f = money_flow_features(mf_rec, now, st.info.creator or None, funders,
                                st.dev.funding_wallet if st.dev else None)
        s = score_money_flow(f)
        el = exit_liquidity(mf_rec, st, now, f)
        pair = st.market.pair_address if st.market else ""
        pts = [(p.ts, p.price, p.vol_5m, p.liq) for p in (h.points if h is not None else []) if p.pair == pair]
        loc = entry_location(pts, now, rec.get("post_state"), f.get("buyer_acceleration"))
        mfs, elr = s["money_flow_score"], el["exit_liquidity_risk"]
        edge = None
        if su is not None and su.score is not None and mfs is not None and elr is not None:
            edge = round(0.40 * su.score + 0.35 * mfs + 0.25 * (100 - elr)
                         - (15 if loc["entry_location"] == "EXTENDED" else 0), 1)
        trade = ld.decision == TRADE
        rec.update({"money_flow": f, "money_flow_score": mfs, "independent_buyer_score": s["independent_buyer_score"],
                    "cluster_risk": s["cluster_risk"], "independence": s["independence"],
                    "money_flow_components": s["components"], "exit_liquidity_risk": elr,
                    "exit_liquidity_components": el["components"], "entry_location": loc["entry_location"],
                    "entry_location_detail": loc, "entry_extension": loc["extension_5m_pct"],
                    "edge_shadow_score": edge,
                    "shadow_B_would_buy": bool(trade and mfs is not None and mfs >= 50),
                    "shadow_C_would_buy": bool(trade and mfs is not None and mfs >= 50 and elr is not None and elr < 50),
                    "new_edge_shadow_would_buy": bool(trade and su is not None and su.setup_type == "NEW"
                                                      and edge is not None and edge >= 60)})
        buf = self.pre_buf.setdefault(st.mint, deque(maxlen=24))
        buf.append({"t": now, "price": st.market.price_usd if st.market else None, "money_flow_score": mfs,
                    "exit_liquidity_risk": elr, "entry_location": loc["entry_location"],
                    "extension_5m_pct": loc["extension_5m_pct"], "buy_share": (f.get("buyer_count") or 0),
                    "vol_5m": st.market.vol_5m if st.market else None,
                    "liq": st.market.liquidity_usd if st.market else None, "setup_score": rec.get("setup_score"),
                    "tx_rate": f.get("tx_rate_now"), "repeat_buyer_ratio": f.get("repeat_buyer_ratio")})
        if len(self.pre_buf) > 400:
            for k in list(self.pre_buf)[:100]:
                self.pre_buf.pop(k, None)

    def mf_targets(self, now: float, cap: int = 3) -> list:
        """Sticky (>= 120 s) so a token's sample can reach the 5-trades-per-minute floor instead of being spread thin."""
        keep = [m for m, until in self._mf_sticky.items() if until > now and now - (self.decisions.get(m) or {}).get("ts", 0) <= 30]
        self._mf_sticky = {m: self._mf_sticky[m] for m in keep}
        rows = []
        for mint, rec in self.decisions.items():
            if now - rec.get("ts", 0) > 30:
                continue
            pri = 0 if mint in self.book.positions else 1 if rec.get("decision") == TRADE else \
                2 if rec.get("pre_shadow_decision") == "WOULD_BUY" else None
            su, thr = rec.get("setup") or {}, rec.get("setup_threshold")
            if pri is None and su.get("score") is not None and thr is not None and su["score"] >= thr - 10:
                pri = 3
            if pri is not None:
                rows.append((pri, -(su.get("score") or 0), mint))
        out = keep[:cap]
        for _, _, m in sorted(rows):
            if len(out) >= cap:
                break
            if m not in out:
                out.append(m)
                self._mf_sticky[m] = now + 120
        return out

    async def _money_flow_round(self, now: float) -> None:
        states = {s.mint: s for s in (self.engine.published or [])}
        for mint in self.mf_targets(now):
            st = states.get(mint)
            if st is None:
                continue
            await self.money_flow.refresh(mint, st.info.creator or None, now)
            if mint in self.book.positions or (self.decisions.get(mint) or {}).get("decision") == TRADE:
                rec = self.money_flow.recs.get(mint) or {}
                recent = [x.get("who") for x in rec.get("txs", []) if x.get("side") == "buy"
                          and x.get("t") is not None and now - x["t"] <= 60]           # buyers of the scored window
                await self.money_flow.lookup_funding(list(dict.fromkeys(recent))[:6], now)

    def lifecycle_summary(self, now: float | None = None) -> dict:
        """Per-lifecycle counts for the dashboard: tokens, candidates, open / closed positions, P&L, and what the bot
        is doing right now."""
        now = now or time.time()
        out = {k: {"tokens": 0, "candidates": 0, "buys": 0, "open": 0, "pnl": 0.0} for k in
               (LC.NEW, LC.PRE_MIGRATION, LC.POST_MIGRATION, LC.UNKNOWN)}
        unknown_reasons: dict = {}
        for mint, rec in self.decisions.items():
            if now - rec.get("ts", 0) > 30 or rec.get("engine") != "lifecycle":
                continue
            lc = rec.get("lifecycle_name") or LC.UNKNOWN
            out.setdefault(lc, {"tokens": 0, "candidates": 0, "buys": 0, "open": 0, "pnl": 0.0})
            out[lc]["tokens"] += 1
            out[lc]["candidates"] += rec.get("decision") == TRADE
            if lc == LC.UNKNOWN:
                r = ((rec.get("lifecycle") or {}).get("reasons") or ["?"])[0]
                unknown_reasons[r] = unknown_reasons.get(r, 0) + 1
        for p in list(self.book.positions.values()) + list(self.book.closed):
            lc = getattr(p, "entry_lifecycle", None) or LC.UNKNOWN
            o = out.setdefault(lc, {"tokens": 0, "candidates": 0, "buys": 0, "open": 0, "pnl": 0.0})
            o["buys"] += 1
            if p.status == "OPEN":
                o["open"] += 1
                o["pnl"] += p.pnl_usd() or 0.0
            else:
                o["pnl"] += p.realized_usd - p.cost_usd
        for o in out.values():
            o["pnl"] = round(o["pnl"], 2)
        doing = []
        held = [p for p in self.book.positions.values()]
        for p in held[:3]:
            doing.append(f"Đang giữ token {getattr(p, 'entry_lifecycle', None) or '?'} — "
                         f"{(p.pnl_pct() or 0):+.1f}%")
        names = {LC.NEW: "Đang săn NEW", LC.PRE_MIGRATION: "Đang shadow PRE-MIGRATION (không BUY)",
                 LC.POST_MIGRATION: "Đang chờ SECOND-WAVE"}
        for k, label in names.items():
            if out[k]["tokens"]:
                doing.append(f"{label} — {out[k]['tokens']} token")
        mf = [r for r in self.decisions.values() if now - r.get("ts", 0) <= 30 and r.get("lifecycle_name") == LC.NEW
              and (r.get("independent_buyer_score") or 0) >= 50]
        if mf:
            doing.append(f"Đang săn NEW có dòng tiền độc lập — {len(mf)} token [SHADOW]")
        if not any(out[k]["candidates"] for k in names) and not held:
            doing.append("Không BUY — chưa có setup đạt chuẩn")
        return {"by_lifecycle": out, "unknown_reasons": dict(sorted(unknown_reasons.items(), key=lambda x: -x[1])[:8]),
                "doing": doing}

    def _deep_hint(self, now: float) -> set[str]:
        """EXPERIMENTAL: tokens GENUINELY near a NEW-engine BUY get Helius priority (fast getAsset lane + holder deep
        scan) so their hard gates can be evaluated: EarlyScore >= threshold - 0.05 AND Confidence >= threshold - 0.10,
        no hard failure (not REJECT), identity not CONFLICT. Top 15 by score. The fast lane itself rate-limits."""
        rows = []
        for mint, rec in self.decisions.items():
            if rec.get("engine") == "lifecycle" and now - rec.get("ts", 0) <= 30 and rec.get("decision") != "REJECT":
                su = rec.get("setup") or {}
                thr = rec.get("setup_threshold")
                if su.get("score") is not None and thr is not None and su["score"] >= thr - 10 \
                        and (rec.get("lifecycle") or {}).get("active"):
                    rows.append((su["score"] / 100, mint))
                continue
            es = rec.get("early_score") or {}
            if now - rec.get("ts", 0) > 30 or rec.get("engine") != "experimental" or es.get("score") is None:
                continue
            if rec.get("decision") == "REJECT" or any(b.startswith("hard:") or b == "identity_conflict"
                                                      for b in rec.get("blocked_by") or []):
                continue
            if es["score"] >= es["theta"] - FAST_SCORE_MARGIN and es["confidence"] >= es["gamma"] - FAST_CONF_MARGIN:
                rows.append((es["score"], mint))
            if any(b in ("gate_unknown:authorities", "gate_unknown:token_2022") for b in rec.get("blocked_by") or []):
                self._gate_waiting.add(mint)
        return {m for _, m in sorted(rows, reverse=True)[:15]}

    def fast_lane_stats(self) -> dict:
        fast = getattr(self.engine, "fast", None)
        out = dict(fast.stats()) if fast is not None else {}
        out["promoted_watch_to_candidate"] = len(self.fast_promoted)
        out["promoted"] = sorted(self.fast_promoted)[:20]
        return out

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

    @staticmethod
    def _path_marks(p, price: float, now: float) -> None:
        """MFE / MAE / time-to-TP / time-to-SL diagnostics (spec 3.8). Logging only: exits are not affected."""
        if p.initial_stop is None:
            p.initial_stop = p.stop_price
        p.low_price = price if p.low_price is None else min(p.low_price, price)
        if p.tp1_hit_ts is None and price >= p.tp1_price:
            p.tp1_hit_ts = now
        if p.tp2_hit_ts is None and price >= p.tp2_price:
            p.tp2_hit_ts = now
        if p.sl_hit_ts is None and price <= p.initial_stop:
            p.sl_hit_ts = now

    def _sell_fill_error(self, mint: str, it: dict, p, st, err: Exception, now: float) -> None:
        """The quote came back but filling it raised: back off like a transient quote error; a protective (HARD) exit
        that fails SELL_FILL_ERROR_MAX times is filled at the haircut instead of retrying forever."""
        if self.sell_intents.get(mint) is not it or self.book.positions.get(mint) is not p:
            return
        it["fill_errors"] = it.get("fill_errors", 0) + 1
        it["next"] = now + min(20.0, 2.5 * 2 ** (it["fill_errors"] - 1))
        if not it.get("hard") or it["fill_errors"] < SELL_FILL_ERROR_MAX:
            return
        try:
            ex = self.exec.sell_haircut(st, p.tokens * it["frac"], p.last_price or it["mark"], self._sol(), it["reason"],
                                        self.cfg.hard_exit_no_quote_haircut_pct / 100, now)
            ex.reason = (ex.reason + " · " if ex.reason else "") +                 f"emergency after {it['fill_errors']} fill errors ({type(err).__name__})"
            self.sell_intents.pop(mint, None)
            self._after_sell(p, st, ex, it["reason"], it["frac"], now)
        except Exception as e2:                          # even the haircut failed: keep the intent, try next round
            print(f"[sell] {mint[:8]} emergency haircut failed: {type(e2).__name__}: {e2}", flush=True)

    def _close_without_state(self, p, reason: str, now: float, frac: float = 1.0) -> None:
        """Protective / stale exit of a token the feed no longer publishes: no quote is possible, so the haircut at the
        last validated mark (counted in the P&L like every haircut exit)."""
        from types import SimpleNamespace
        st0 = SimpleNamespace(mint=p.mint, info=SimpleNamespace(symbol=p.symbol, decimals=None), market=None)
        ex = self.exec.sell_haircut(st0, p.tokens * frac, p.last_price or p.entry_price, self._sol(), reason,
                                    self.cfg.hard_exit_no_quote_haircut_pct / 100, now)
        ex.reason = (ex.reason + " · " if ex.reason else "") + "token no longer in the feed"
        self.log("RISK", f"{p.symbol}: {reason} without market state → haircut at the last mark", None, now=now)
        self._after_sell(p, None, ex, reason, frac, now)

    def _after_sell(self, p, st, ex, reason: str, frac: float, now: float) -> None:
        self.book.reduce(p, ex, now, reason)
        self.book.heartbeat = max(self.book.heartbeat or 0.0, now)   # alive at the sell: gaps start after it
        tr = self.cs_shadow.get(p.mint)
        if tr is not None and ex.status == "FILLED" and ex.tokens:
            from trading.truth_price import new_exit_event
            tr.exit_events.append(new_exit_event(
                now, reason, ex.tokens, ex.fill_price,
                "jupiter_sell_quote" if (getattr(ex, "model", "") or "").startswith("PAPER on a real Jupiter")
                else "liquidity_model_at_dexscreener_mark"))
        fx = self.forensics.get(p.mint)
        if fx is not None and st is not None and fx["exit"] is None:
            fx["exit"] = {"reason": reason, **self._risk_snap(st, "EXIT", now - fx["entry_ts"], fx["snaps"][0],
                                                               fx["entry_ts"])}
            held = now - fx["entry_ts"]
            fx["fast_sl_flag"] = reason == "stop_loss" and held <= 15
            fx["fast_sl_reason"] = f"stop_loss after {held:.0f}s" if fx["fast_sl_flag"] else None
            if self.recorder is not None:
                self._rec("forensic", fx)
        pv = self.provenance.get(p.mint)
        # V1.2: recorded on the FINAL close (also after a partial TP1), not only on the first sell
        if pv is not None and pv.get("exit") is None and p.status == "CLOSED" and st is not None:
            pv["exit"] = {"ts": now, "reason": reason, "exit_fill": ex.fill_price, "exit_route": ex.route,
                          "exit_market": self._mobs(st, now),
                          "exit_fill_source": "jupiter_sell_quote" if (getattr(ex, "model", "") or "").startswith(
                              "PAPER on a real Jupiter") else "liquidity_model_at_dexscreener_mark",
                          "realized_pnl_pct": round(100 * (p.realized_usd - p.cost_usd) / p.cost_usd, 3) if p.cost_usd else None,
                          "path": p.path_log()}
            cs = self.cs_shadow.get(p.mint)
            if cs is not None:                         # non-price exits are mirrored on the next executable quote
                cs.on_production_exit(reason, ex.fill_price, pv["exit"]["realized_pnl_pct"], now,
                                      pv["exit"]["exit_fill_source"], pv["exit"]["exit_market"])
                self._truth_trade_rec(cs, now)
            self._rec("price_provenance", p.mint, pv)
        if ex.status == "FILLED" and p.status == "CLOSED" and self.book.journal:
            self._rec("trade_journal", self.book.journal[-1])
        if ex.status == "FILLED":
            on_filled_sell(p, reason, self.cfg)
            if reason == "take_profit_1":
                p.tp1_done = True
                p.stop_price = max(p.stop_price, p.entry_price)      # break-even
            closed = p.status == "CLOSED"
            pl = p.path_log()
            net = (f" · NET P&L {p.realized_usd - p.cost_usd:+,.2f}$ · MFE {pl['mfe_pct']}% MAE {pl['mae_pct']}% · "
                   f"t(TP1) {pl['time_to_tp1_s']}s t(SL) {pl['time_to_sl_s']}s t(exit) {pl['time_to_exit_s']}s") if closed else ""
            self.log("SELL", f"{reason} · {100 * frac:.0f}% @ ${ex.fill_price:.8g} · {ex.route} · WHY: "
                             f"{EXIT_WHY.get(reason, reason)}{net}", st, usd=ex.usd_in, price=ex.fill_price, now=now)
        else:
            self.log("FAILED", f"SELL {reason}: {ex.reason}", st, now=now)

    async def execute_sells(self, now: float | None = None) -> None:
        """Every exit is filled on a fresh Jupiter SELL quote. The quote result keeps its classification:
          OK                      -> fill (non-HARD: waits while the route impact is above max_slippage_pct)
          NO_ROUTE / INVALID      -> no executable route: HAIRCUT fill (hard_exit_no_quote_haircut_pct), any exit
          RATE_LIMITED / TIMEOUT / API_ERROR / COOLDOWN (breaker open) / no SOL price -> TRANSIENT: the intent is
                                     KEPT and retried with backoff for QUOTE_RETRY_WINDOW_S (like a BUY). When the
                                     window is over a HARD exit takes the haircut (emergency); a non-HARD exit is
                                     dropped and comes back with the next exit signal (never sold blind).
        The intent is removed only once there is an outcome. SELL quotes have priority over truth / probe quotes."""
        if self._selling:                               # one SELL round at a time: never two fills per position
            return
        self._selling = True
        try:
            await self._execute_sells(now)
        finally:
            self._selling = False

    async def _execute_sells(self, now: float | None) -> None:
        from trading.jupiter import INVALID, NO_ROUTE, price_impact
        now = now or time.time()
        states = {s.mint: s for s in (self.engine.published or [])}
        due = []
        for mint, it in list(self.sell_intents.items()):
            expired = "first" in it and now - it["first"] >= QUOTE_RETRY_WINDOW_S
            if now < it.get("next", 0.0) and not expired:
                continue                                # backing off after a transient quote error
            p, st = self.book.positions.get(mint), states.get(mint)
            if p is None:
                self.sell_intents.pop(mint, None)
                continue
            if st is None:                              # the token left the feed
                self.sell_intents.pop(mint, None)
                if it.get("hard"):                      # a protective exit is never dropped: haircut at the mark
                    self._close_without_state(p, it["reason"], now, it.get("frac", 1.0))
                continue
            due.append((mint, it, p, st, p.tokens))
        sol = self._sol()

        async def quote(entry):
            m, it, p, st, tk = entry
            try:
                return entry, await self._sell_quote(m, int(tk * it["frac"] * 10 ** (st.info.decimals or 6)), sol)
            except Exception as e:                     # one broken quote never breaks the round
                from trading.jupiter import API_ERROR, QuoteResult
                print(f"[sell] quote error {m[:8]}: {type(e).__name__}: {e}", flush=True)
                return entry, QuoteResult(API_ERROR, detail=f"client error {type(e).__name__}")
        for fut in asyncio.as_completed([quote(e) for e in due]):     # concurrently, filled as each quote lands:
            (mint, it, p, st, tokens_then), qr = await fut             # one slow quote never delays the others
            try:
                if self.book.positions.get(mint) is not p or p.tokens != tokens_then or self.sell_intents.get(mint) is not it:
                    continue                                # position / intent changed while quoting: next round
                tokens, reason = p.tokens * it["frac"], it["reason"]
                if p.last_price:
                    it["mark"] = p.last_price               # latest validated mark (refreshed every tick)
                self.sell_quote_stats[qr.status] = self.sell_quote_stats.get(qr.status, 0) + 1
                haircut = self.cfg.hard_exit_no_quote_haircut_pct / 100
                if qr.ok:
                    q = qr.quote
                    imp = price_impact(q)
                    if not it.get("hard") and imp is not None and 100 * imp > self.cfg.max_slippage_pct \
                            and it["mark"] <= p.stop_price:
                        partial = it["frac"] < 1.0
                        it["hard"], it["frac"] = True, 1.0  # bad route, but the price is through the stop: protect
                        reason = it["reason"] = f"{reason}->stop_loss"
                        self.log("RISK", f"SELL escalated to HARD: mark <= stop although impact {100 * imp:.2f}% > "
                                         f"{self.cfg.max_slippage_pct}%", st, now=now)
                        if partial:
                            it["next"] = 0.0                # the quote was for a part: re-quote the whole next round
                            continue
                    if not it.get("hard") and imp is not None and 100 * imp > self.cfg.max_slippage_pct:
                        self.sell_intents.pop(mint, None)   # re-signalled next tick
                        self.log("INFO", f"SELL {reason} waits: Jupiter impact {100 * imp:.2f}% > "
                                         f"{self.cfg.max_slippage_pct}%", st, now=now)
                        continue
                    ex = self.exec.sell_from_quote(st, tokens, q, sol, reason, now)
                    if it.get("hard"):
                        ex.ref_price = it["mark"]           # validated mark (post-entry print or the fill), not a raw print
                elif qr.status in (NO_ROUTE, INVALID):      # really no executable route
                    ex = self.exec.sell_haircut(st, tokens, it["mark"], sol, reason, haircut, now)
                else:                                       # transient: keep the intent, retry inside the window
                    first = it.setdefault("first", now)
                    if now - first < QUOTE_RETRY_WINDOW_S:
                        it["retries"] = it.get("retries", 0) + 1
                        wait = min(20.0, 2.5 * 2 ** (it["retries"] - 1))
                        it["next"] = now + wait
                        self.log("INFO", f"SELL {reason} → QUOTE {qr.label()} → RETRY in {wait:.0f}s "
                                         f"(attempt {it['retries'] + 1})", st, now=now)
                        continue
                    if not it.get("hard") and it["mark"] <= p.stop_price:
                        it["hard"], it["frac"] = True, 1.0  # the price fell through the stop while we waited: protect
                        reason = it["reason"] = f"{reason}->stop_loss"
                        tokens = p.tokens                   # a stop sells everything (TP1 would have sold a part)
                        self.log("RISK", f"SELL escalated to HARD: mark {it['mark']:.10g} <= stop {p.stop_price:.10g} "
                                         f"after {QUOTE_RETRY_WINDOW_S:.0f}s without a quote", st, now=now)
                    if not it.get("hard"):
                        self.sell_intents.pop(mint, None)
                        self.log("INFO", f"SELL {reason} postponed: no quote for {QUOTE_RETRY_WINDOW_S:.0f}s "
                                         f"({qr.label()}); re-signalled next tick, never sold blind", st, now=now)
                        continue
                    ex = self.exec.sell_haircut(st, tokens, it["mark"], sol, reason, haircut, now)
                    ex.reason = (ex.reason + " · " if ex.reason else "") + f"emergency after {QUOTE_RETRY_WINDOW_S:.0f}s of {qr.status}"
                self.sell_intents.pop(mint, None)
                self._after_sell(p, st, ex, reason, it["frac"], now)
            except Exception as e:                     # logged; the other sells of this round still run
                print(f"[sell] {mint[:8]} round error: {type(e).__name__}: {e}", flush=True)
                self.log("INFO", f"SELL round error on {mint[:8]}: {type(e).__name__}: {e}")
                self._sell_fill_error(mint, it, p, st, e, now)

    # ---------------------------------------------------------------- CONFIRM mode
    def approve(self, order_id: str, now: float | None = None) -> bool:
        """Owner approved a pending BUY: it goes through the SAME re-checks and execution as an automatic one."""
        now = now or time.time()
        o = self.pending.pop(order_id, None)
        if o is None or now > o["expires"]:
            return False
        if self.entry_block:                            # not durable right now: no new position, even approved
            self.log("BLOCK", f"BUY approval refused — {self.entry_block}", None, now=now)
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

    def _quota(self, kind: str) -> bool:
        """Bot-wide Jupiter budget (trading.quote_budget): SELL first, then buy / truth / probe. Only a gate: the
        Jupiter client counts its real HTTP attempts (a client without a budget hook is counted here, once)."""
        if self.quote_budget is None:
            return True
        if not self.quote_budget.allow(kind, sells_pending=bool(self.sell_intents)):
            return False
        if getattr(self.jupiter, "budget", None) is not self.quote_budget:
            self.quote_budget.record(kind)
        return True

    async def _jq(self, input_mint: str, output_mint: str, amount_raw: int, kind: str, **kw):
        """quote_result with the extras the client understands (kind / priority / attempts / budget_s)."""
        import inspect
        fn = self.jupiter.quote_result
        try:
            ps = inspect.signature(fn).parameters
            anykw = any(p.kind == p.VAR_KEYWORD for p in ps.values())
        except (TypeError, ValueError):
            ps, anykw = {}, False
        extra = {"kind": kind, "priority": kind == "sell", **kw}
        extra = {k: v for k, v in extra.items() if anykw or k in ps}
        return await fn(input_mint, output_mint, amount_raw, int(self.cfg.max_slippage_pct * 100), **extra)

    async def _sell_quote(self, mint: str, amount_raw: int, sol: float | None = None):
        """Classified Jupiter SELL quote (token -> SOL) as a trading.jupiter.QuoteResult. A quote-only client is
        wrapped like _buy_quote (None -> API_ERROR, transient). No SOL price -> API_ERROR (cannot value the fill)."""
        from trading.jupiter import API_ERROR, BUDGET, OK, WSOL, QuoteResult
        if not sol:
            return QuoteResult(API_ERROR, detail="no SOL price", attempts=0)
        if not self._quota("sell"):
            return QuoteResult(BUDGET, detail="bot quote budget (50/min) used up", attempts=0)
        slip = int(self.cfg.max_slippage_pct * 100)
        if hasattr(self.jupiter, "quote_result"):
            return await self._jq(mint, WSOL, amount_raw, "sell", attempts=SELL_QUOTE_ATTEMPTS,
                                  budget_s=SELL_QUOTE_BUDGET_S)
        q = await self.jupiter.quote(mint, WSOL, amount_raw, slip)
        return QuoteResult(OK, quote=q, http=200, attempts=1) if q else QuoteResult(API_ERROR, detail="no quote", attempts=1)

    async def _buy_quote(self, mint: str, lamports: int):
        """Classified Jupiter quote (trading.jupiter.QuoteResult); a quote-only client is wrapped."""
        from trading.jupiter import API_ERROR, BUDGET, OK, WSOL, QuoteResult
        if not self._quota("buy"):
            return QuoteResult(BUDGET, detail="bot quote budget: SELL quotes first", attempts=0)
        slip = int(self.cfg.max_slippage_pct * 100)
        if hasattr(self.jupiter, "quote_result"):
            return await self._jq(WSOL, mint, lamports, "buy")
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
            try:
                if it.get("next", 0) > now:
                    continue                                   # quote retry scheduled later
                self.intents.pop(mint, None)
                if self.entry_block:                           # became non-durable after the intent was made
                    self.entry_block_seen.add(mint)
                    self.log("BLOCK", f"BUY cancelled at execution — {self.entry_block}", None, now=now)
                    continue
                st = states.get(mint)
                rec = self.decisions.get(mint, {})
                sol = self._sol()
                if st is None or not is_trade_candidate(st, rec) or not sol or mint in self.book.positions:
                    self.log("BLOCK", "BUY cancelled at execution: no longer a valid Trade Candidate", st, now=now)
                    self.audit.execution("skip", st, now, reason="no_longer_candidate")
                    continue
                usd = it["usd"]
                t_req = time.time()
                m_at_quote = self._mobs(st, t_req)
                qr = await self._buy_quote(mint, int(usd / sol * 1e9))
                t_resp = time.time()
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
                    if self.cfg.experimental and self.cfg.paper_fill_without_quote:
                        self._simulated_fill(st, rec, it, usd, sol, qr, now)
                        continue
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
                from trading.price_provenance import buy_quote_obs, pct
                qobs = buy_quote_obs(q, usd, st.info.decimals or 6, mint, t_req, t_resp).as_dict()
                self.quote_obs_log.append({"mint": mint, "symbol": st.info.symbol, "ts": t_resp, "usd": usd,
                                           "decimal_source": "pump.fun/rpc" if st.info.total_supply else "default 6",
                                           "decision_market": it.get("decision_market"), "market": m_at_quote, "quote": qobs,
                                           "decision_to_quote_ms": round(1000 * (t_req - it["ts"])),
                                           "quote_vs_market_pct": pct(qobs["price"], m_at_quote.get("price")),
                                           "lifecycle": it.get("lifecycle"), "liq": st.market.liquidity_usd if st.market else None,
                                           "impact_pct": None if imp is None else round(100 * imp, 3)})
                del self.quote_obs_log[:-500]
                self.market_trail[mint] = deque([m_at_quote], maxlen=40)
                self.quote_obs_log[-1]["trail"] = self.market_trail[mint]      # later prints validate the discrepancy
                risk_at_quote = st.risk.score if st.risk else None
                drift = await self._latency_probe(st, mint, int(usd / sol * 1e9), q)
                risk_at_entry = st.risk.score if st.risk else None
                risk_ctx = {"risk_at_candidate": it.get("risk_at_candidate"), "risk_at_quote": risk_at_quote,
                            "risk_at_entry": risk_at_entry, "entry_risk_buffer": self.cfg.entry_max_risk}
                if self.cfg.experimental and (risk_at_entry is None or risk_at_entry > self.cfg.entry_max_risk):
                    rec["state"] = "RISK_BUFFER"
                    self.buffer_blocks.append({"ts": now, "mint": mint, "symbol": st.info.symbol, "stage": "entry", **risk_ctx})
                    del self.buffer_blocks[:-300]
                    self.log("BLOCK", f"BUY → QUOTE → MATCH → ENTRY RISK BUFFER: Risk {risk_at_entry} > "
                                      f"{self.cfg.entry_max_risk} (hard limit 60) → WAIT · candidate kept", st, now=now)
                    self.audit.execution("skip", st, now, f"risk {risk_at_entry}", reason="entry_risk_buffer")
                    self._rec("fill", st, now, {"status": "BLOCKED", "fail_reason": "entry_blocked_by_risk_buffer",
                                                "entry_blocked_by_risk_buffer": 1, "candidate_status": "risk_buffer",
                                                **risk_ctx})
                    continue
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
                try:
                    out_tokens = int(q["outAmount"]) / 10 ** (st.info.decimals or 6)
                except (KeyError, TypeError, ValueError):
                    out_tokens = 0.0
                quote_price = usd / out_tokens if out_tokens else None
                fill = {"jupiter_impact_pct": round(ex.price_impact_pct, 3), "latency_slippage_pct": round(ex.slippage_pct, 3),
                        "total_slippage_pct": round(ex.price_impact_pct + ex.slippage_pct, 3),
                        "max_slippage_pct": self.cfg.max_slippage_pct, "status": ex.status,
                        "fail_reason": "" if ex.status == "FILLED" else ex.reason,
                        "quote_ts": now, "execution_ts": now + ex.latency_ms / 1000, "quote_price": quote_price,
                        "simulated_execution_price": ex.fill_price if ex.fill_price else
                        (quote_price * (1 + ex.slippage_pct / 100) if quote_price else None),
                        "jupiter_impact_bps": round(100 * ex.price_impact_pct), "latency_slippage_bps": round(100 * ex.slippage_pct),
                        "total_slippage_bps": round(100 * (ex.price_impact_pct + ex.slippage_pct)),
                        "max_slippage_bps": round(100 * self.cfg.max_slippage_pct), "fill_result": ex.status,
                        "latency_model": self.exec.last_model_used, "requote_drift_bps": drift,
                        "ref_price": ex.ref_price, "fill_price": ex.fill_price,
                        "fill_vs_ref_pct": round(100 * (ex.fill_price / ex.ref_price - 1), 3)
                        if ex.fill_price and ex.ref_price else None,
                        "requote_ts": (getattr(self, "_last_probe", None) or {}).get("requote_ts") if drift is not None else None,
                        "latency_actual_s": (getattr(self, "_last_probe", None) or {}).get("latency_s") if drift is not None else None,
                        "route": ex.route, "liquidity_usd": st.market.liquidity_usd if st.market else None,
                        "lifecycle": it.get("lifecycle"), "setup_type": it.get("setup_type"), "setup_score": it.get("setup_score"),
                        "position_size_usd": usd,
                        "candidate_status": "bought" if ex.status == "FILLED" else
                        ("slippage_blocked" if "slippage" in (ex.reason or "") else "fill_failed"), **risk_ctx}
                self.fill_log.append({"ts": now, "mint": mint, "symbol": st.info.symbol, **fill})
                del self.fill_log[:-300]
                self.audit.execution("fill_ok" if ex.status == "FILLED" else "fill_fail", st, now,
                                     f"impact {fill['jupiter_impact_pct']}% + latency {fill['latency_slippage_pct']}% = "
                                     f"{fill['total_slippage_pct']}% (max {fill['max_slippage_pct']}%)", reason=fill["fail_reason"])
                self._rec("fill", st, now, fill)
                if ex.status == "FILLED":
                    self.book.open(ex, self.cfg, now, it.get("opportunity"), it.get("why"), st.market.liquidity_usd,
                                   st.market.vol_5m, setup=it.get("setup", ""))
                    self.log("BUY", f"${usd:,.2f} @ ${ex.fill_price:.8g} · impact {ex.price_impact_pct:.2f}% · "
                                    f"slip {ex.slippage_pct:.2f}% · {ex.route} · WHY: {'; '.join((it.get('why') or [])[:3])}",
                             st, usd=usd, price=ex.fill_price, now=now)
                    self.audit.execution("buy", st, now, f"${usd:,.2f} @ {ex.fill_price:.8g} · {ex.route}")
                    self._forensic_open(st, now, risk_ctx)
                    self._tag_position(mint, it)
                    self._provenance_open(st, it, qobs, m_at_quote, ex, fill, now)
                else:
                    self.book.record(ex)
                    if ex.status == "FAILED":
                        self.last_buy_attempt[mint] = now           # cooldown: FAILED_BUY_COOLDOWN_S
                    self.log("FAILED", f"BUY → QUOTE → MATCH → PAPER FILL FAILED: {ex.reason} · Jupiter impact "
                                       f"{fill['jupiter_impact_pct']:.2f}% + simulated latency slippage "
                                       f"{fill['latency_slippage_pct']:.2f}% = {fill['total_slippage_pct']:.2f}% "
                                       f"(max {fill['max_slippage_pct']}%) · candidate kept", st, now=now)
                    self.audit.execution("skip", st, now, ex.reason, reason="paper_fill_failed")
            except Exception as e:                     # logged; the other intents of this round still run
                print(f"[buy] {mint[:8]} round error: {type(e).__name__}: {e}", flush=True)
                self.log("INFO", f"BUY round error on {mint[:8]}: {type(e).__name__}: {e}")
        self.persist()

    def begin_sample(self, now: float | None = None) -> dict:
        """Start / resume the sample epoch for the running code + parameters (call once the config is final)."""
        from core.version import git_commit
        self.gap_tracker.on_start(now or time.time())          # restart / sleep since the last heartbeat
        new = self.sample_epoch.start(self.cfg.sample_id(), git_commit(), now)
        d = self.sample_epoch.as_dict()
        self.log("INFO", f"SAMPLE {'NEW EPOCH' if new else 'epoch resumed'}: commit {d['commit'][:7]} · params "
                         f"{d['fingerprint']} · strategy {d['strategy_version']} · counting trades opened since "
                         f"{d['started_at_utc']}")
        return d

    def sample_report(self, now: float | None = None) -> dict:
        from trading.sample_report import report
        now = now or time.time()
        r = report(self.book.journal, self.sample_epoch, self.cfg.cost_stress_pct, self.cfg.starting_balance,
                   gaps=self.gap_tracker.all_gaps(now), now=now)
        from trading.gate_stats import block_rates
        r["gate_block_rates"] = block_rates(self.book.gate_seen, self.sample_epoch.started_at)
        if self.quote_budget is not None:
            r["quote_budget"] = self.quote_budget.as_dict()
        r["gate_forward_returns"] = self.gate_eval_cache or {
            "status": "not computed yet (every GATE_EVAL_EVERY_S from research.db, RESEARCH_LOG on)"}
        r["costs"]["failed_attempts_all_time"] = {"count": self.book.failed, "fees_usd": round(self.book.failed_fees, 4),
                                                  "fee_per_tx_usd_now": round(self.exec.network_fee(self._sol()), 4)}
        from core.snapshot import NOT_DURABLE
        snap = self.snapshot_status or {}
        durable = bool(snap.get("durable"))
        r["durability"] = {"durable": durable, "store": snap.get("store", "NOT CONFIGURED"),
                           "reason": snap.get("reason", "SNAPSHOT_DIR / SNAPSHOT_URL not set"),
                           "last_snapshot_ts": snap.get("last_ts")}
        if not durable:
            r["warnings"].insert(0, f"{NOT_DURABLE} ({r['durability']['reason']})")
        if self.entry_block:
            r["entries_blocked"] = {"reason": self.entry_block, "tokens_not_bought": len(self.entry_block_seen)}
            r["warnings"].insert(0, f"NO NEW ENTRIES: {self.entry_block} — {len(self.entry_block_seen)} candidate "
                                    "tokens observed but not bought")
        if snap.get("halted"):
            r["warnings"].insert(0, f"BOT STOPPED: {snap['halted']}")
        return r

    def sample_summary_line(self, now: float | None = None) -> str:
        from trading.sample_report import summary_line
        return summary_line(self.sample_report(now))

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
        if self.sample_epoch.started_at is None:
            self.begin_sample()
        while not self._stop.is_set():
            try:
                self.tick()
                self._expire_pending(time.time())
                if (self.sell_intents or self.intents) and (self._exec_task is None or self._exec_task.done()):
                    self._exec_task = asyncio.create_task(self._execution_round())   # never blocks the next tick
                if self.recorder is not None and time.time() - self._last_gate_eval >= GATE_EVAL_EVERY_S                         and (self._gate_eval_task is None or self._gate_eval_task.done()):
                    self._last_gate_eval = time.time()            # its own task + thread: never awaited here
                    self._gate_eval_task = asyncio.create_task(self._gate_eval_safe())
                if self.recorder is not None and time.time() - self._last_followup >= 30:
                    self._last_followup = time.time()
                    await self.recorder.run_followups()
                if self.cs_shadow and time.time() - self._last_cs >= 1.0:
                    self._last_cs = time.time()
                    try:
                        await self.common_source_round(time.time())
                    except Exception as e:                # research never breaks trading
                        self.log("INFO", f"common-source shadow error: {type(e).__name__}: {e}")
                if self.money_flow is not None and time.time() - self._last_mf >= 10:
                    self._last_mf = time.time()
                    try:
                        await self._money_flow_round(time.time())
                    except Exception as e:                # research never breaks trading
                        self.log("INFO", f"money flow research error: {type(e).__name__}: {e}")
                if self.onchain is not None and time.time() - self._last_onchain >= 10:
                    self._last_onchain = time.time()
                    try:
                        await self.onchain.round({s.mint: s for s in (self.engine.published or [])}, self.decisions,
                                                 set(self.book.positions))
                    except Exception as e:                # research never breaks trading
                        self.log("INFO", f"onchain research error: {type(e).__name__}: {e}")
            except Exception as e:                       # the bot never takes the scanner down
                self.log("INFO", f"bot tick error: {type(e).__name__}: {e}")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=TICK_S)
            except asyncio.TimeoutError:
                pass
        if self._exec_task is not None and not self._exec_task.done():
            try:                                         # let a running SELL / BUY round finish (bounded)
                await asyncio.wait_for(self._exec_task, 10)
            except Exception:
                pass

    async def _gate_eval_safe(self) -> None:
        try:
            await self.refresh_gate_eval()
        except Exception as e:                           # research never breaks trading
            print(f"[gate_eval] error: {type(e).__name__}: {e}", flush=True)
            self.log("INFO", f"gate evaluation error: {type(e).__name__}: {e}")

    async def refresh_gate_eval(self) -> dict:
        """Forward returns of gate-BLOCKED vs ENTERED tokens vs an age / liquidity matched baseline, for this sample
        epoch (research.db, in a thread: it never blocks the tick)."""
        from research.gate_eval import evaluate
        res = await asyncio.to_thread(evaluate, str(self.recorder.path), 7, self.sample_epoch.started_at)
        res["computed_at"] = time.time()
        self.gate_eval_cache = res
        return res

    async def _execution_round(self) -> None:
        """SELLs first, then BUYs. Runs as its own task so a slow / retrying quote never delays the tick (stop
        losses of other positions are still detected every TICK_S); only one round runs at a time."""
        try:
            if self.sell_intents:
                await self.execute_sells()
            if self.intents:
                await self.execute_intents()
        except Exception as e:                           # never kill the loop
            print(f"[exec] execution round error: {type(e).__name__}: {e}", flush=True)
            self.log("INFO", f"execution round error: {type(e).__name__}: {e}")

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


def attribute_risk(entry: dict, snap: dict, buy_ts: float | None) -> dict:
    """Split the change of Risk components since ENTRY into
    RISK_NEW          a component that changed although its data did not merely arrive (market, liquidity, rug, ...)
    RISK_DATA_REFRESH a holders / dev component that appeared or grew because Helius data for it was fetched AFTER the
                      BUY (risk that existed but was not visible at entry — e.g. top10 / single whale / dev share)
    RISK_DATA_STALE   'data' components (missing / stale inputs)
    Reading only: the Risk Engine and the exits are not affected. Also gives spike_type when Risk > 60:
    RISK_NEW if the new-risk share of the increase alone would have crossed 60, else DATA_REFRESH / DATA_STALE."""
    def parse(fs):
        out = {}
        for f in fs:
            cat_key, _, pts = f.rpartition(":")
            try:
                out[cat_key] = float(pts)
            except ValueError:
                out[cat_key] = 0.0
        return out
    e, c = parse(entry.get("factors") or []), parse(snap.get("factors") or [])
    buckets = {"risk_new": 0.0, "risk_data_refresh": 0.0, "risk_data_stale": 0.0}
    changed = []
    for k in sorted(set(e) | set(c)):
        d = c.get(k, 0.0) - e.get(k, 0.0)
        if not d:
            continue
        cat = k.split(":")[0]
        if cat in ("holders", "dev"):
            stamp = snap.get("holders_stamp" if cat == "holders" else "dev_stamp")
            before = entry.get("holders_stamp" if cat == "holders" else "dev_stamp")
            # first data AFTER the BUY (nothing to compare at entry) -> risk that already existed, now visible.
            # data present at entry and worse at a later fetch (e.g. the dev actually sold) -> a real change.
            first_seen = stamp is not None and buy_ts is not None and stamp > buy_ts and before is None
            b = "risk_data_refresh" if first_seen else "risk_new"
        elif cat == "data":
            b = "risk_data_stale"
        else:
            b = "risk_new"
        buckets[b] += d
        changed.append([k, d, b.upper()])
    out = {k: round(v, 1) for k, v in buckets.items()} | {"changed": changed}
    r0, r1 = entry.get("risk"), snap.get("risk")
    if r1 is not None and r1 > 60 and r0 is not None:
        inc = r1 - r0
        pos = {k: max(0.0, v) for k, v in buckets.items()}
        tot = sum(pos.values())
        new_part = inc * (pos["risk_new"] / tot) if tot else inc
        if r0 + new_part > 60:
            out["spike_type"] = "RISK_NEW"
        else:
            out["spike_type"] = "DATA_REFRESH" if pos["risk_data_refresh"] >= pos["risk_data_stale"] else "DATA_STALE"
    return out


def is_trade_candidate(st: TokenState, rec: dict, allow_unchecked_risk: bool = False) -> bool:
    """🟢 gate: Early Signal TRUE + identity VERIFIED + VET PASS + Decision TRADE (+ Risk, checked by the caller).
    EXPERIMENTAL engine: identity VERIFIED + Decision TRADE (which already requires every hard gate checked and passed,
    EarlyScore PASS for the age, Opportunity >= 65, Confidence >= 60) (+ Risk Engine, checked by the caller)."""
    if rec.get("engine") in ("experimental", "lifecycle"):
        if st.identity.status != "VERIFIED" or rec.get("decision") != TRADE:
            return False
        return allow_unchecked_risk or bool(rec.get("risk_allowed"))
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
