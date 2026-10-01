"""Scanner orchestrator.

Tiered loop (scanner.scheduler has the cadences and the priority rules):
  discovery  ~5s   PumpPortal WS queue drain; Pump.fun latest every 10s, recently-traded every 20s
  market     ~5s   tick: DexScreener batch for the tokens that are DUE (hot 5s · normal 10s · quiet 30s)
                   -> validated + history -> MC journey -> evaluate (full pipeline) -> events -> groups -> publish
  persist    20s   SQLite snapshots (unchanged cadence) + MC journey + alerts (ranked VALID only)
A separate worker does on-chain validation (holders 30s hot / 60s normal, dev 60s / 120s) for candidates,
highest priority first, capped per minute, and re-evaluates what it touched.
The pipeline (validation, Risk, Opportunity, Early Signal D1-D8) is called exactly as before; only WHEN
data is fetched changed. History keeps ~20s anchor points (history.store) so time-series inputs keep the
same density.
Performance: DexScreener batches 30 mints/request, per-host throttling, retry/backoff/timeouts and a per-
source cooldown in core.http, caches for dev history/funding, dedup by mint, bounded in-memory history,
shallow copies for publishing.
"""
from __future__ import annotations

import asyncio
import copy
import time
from collections import deque
from typing import Callable

from alerts.report import telegram_alert
from alerts.telegram import TelegramAlerter
from core.config import ENV_PATH, ApiKeys, Settings
from core.http import HttpClient
from core.models import Event, SourceStamp, TokenInfo, TokenState
from database.db import Database
from dev.analyzer import DevAnalyzer
from dex.dexscreener import DexScreenerClient
from history.store import HistoryStore, HolderSnap
from holders.analyzer import HolderAnalyzer
from intel.events import EventDetector
from pumpfun.client import PumpFunClient
from pumpfun.stream import PumpPortalStream
from intel.early_watch import compute_early_watch
from intel.pre_early import compute_pre_early
from intel.mc_track import _pump_quote_is_sol, anchor_at_discovery, compute_trend, confirm_quote, update_mc_track
from scanner.pipeline import evaluate, ingest_market
from scanner.scheduler import TierClock, deep_due, dev_due, market_due, priority_score
from scoring.groups import classify_group
from scoring.ranking import rank_opportunities
from solana_data.rpc import SolanaRpc
from i18n import t
from solana_data.rpc import SOURCE_DAS
from validation.identity import apply_identity, claim_from_info, record_claim
from validation.market import CURVE_MAX_AGE_S

WSOL = "So11111111111111111111111111111111111111112"
CURVE_REFRESH_PER_CYCLE = 4       # per 10s (Pump.fun budget: 24/min curves + 9/min discovery < 40/min)
DEEP_MIN_WATCH_RANK = 45
DEEP_MIN_OPPORTUNITY = 55
CURVE_YOUNG_MIN = 3.5             # tokens this young (PRE-EARLY window) are refreshed even if not candidates
MAX_EVENTS = 500
PUMP_LATEST_S, PUMP_ACTIVE_S, CURVE_S = 10.0, 20.0, 10.0
REEVALUATE_S = 20.0               # tokens without fresh data are re-evaluated at the historic cadence
LOG_EVERY_S = 60.0
MC_STASH_MAX = 20_000
NO_DATA_GRACE_S = 3600            # a token whose MC is still UNKNOWN is kept this long (data may arrive late)
KNOWN_LOW_MC_AFTER_S = 900        # known MC below tracking_min_mc after this age = dead coin (real reason)             # MC journeys of pruned tokens kept in memory (anchor survives re-discovery)


class ScannerEngine:
    def __init__(self, settings: Settings, db: Database, keys: ApiKeys | None = None,
                 on_update: Callable[[list[TokenState]], None] | None = None,
                 on_log: Callable[[str], None] | None = None,
                 on_events: Callable[[list[Event]], None] | None = None):
        self.settings = settings
        self.db = db
        self.keys = keys or ApiKeys.from_env()
        self.on_update = on_update or (lambda states: None)
        self.on_log = on_log or print
        self.on_events = on_events or (lambda events: None)
        self.http = HttpClient()
        self.pump = PumpFunClient(self.http)
        self.dex = DexScreenerClient(self.http)
        self.rpc = SolanaRpc(self.http, self.keys.helius, self.keys.solana_rpc_url)
        self.holders = HolderAnalyzer(self.rpc)
        self.dev = DevAnalyzer(self.rpc, self.pump)
        self.alerter = TelegramAlerter(self.http, self.keys.telegram_bot_token, self.keys.telegram_chat_id)
        self.tracked: dict[str, TokenState] = {}
        self.history = HistoryStore()
        self.detector = EventDetector()
        self.events: list[Event] = []
        self.published: list[TokenState] = []
        self.watch: set[str] = set(db.watchlist())
        self.sol_price: float | None = None
        self.cycle_no = 0
        self.last_cycle_at: float | None = None
        self._queue: asyncio.Queue | None = None
        self._stop: asyncio.Event | None = None
        self.stream: PumpPortalStream | None = None
        self.helius_state: dict = {"state": "NO_KEY" if not self.keys.helius else "UNCHECKED"}
        self._helius_checked_at = 0.0
        self.clock = TierClock()
        self._pump_at = {"latest": 0.0, "active": 0.0, "curve": 0.0, "fallback": 0.0}
        self._pump_last: dict[str, bool | None] = {"latest": None, "active": None}   # last call returned data?
        self._started_at = time.time()
        self._feeds_logged = False
        self._deep_times: list[float] = []
        self.deep_extra: set[str] = set()                 # mints the paper bot holds (registered by the bot)
        self.deep_hint: set[str] = set()                  # experimental bot: near an EarlyScore PASS (deep scan only)
        self.deep_skipped: list[tuple[str, str]] = []
        self.pipe = {"discovered": deque(maxlen=5000), "pre_early": deque(maxlen=5000), "early_watch": deque(maxlen=5000),
                     "evicted": 0, "pruned_low_mc": 0, "pruned_no_data": 0, "pruned_age": 0}
        self._seen_pre: set[str] = set()
        self._seen_watch: set[str] = set()
        self._skip_logged_at = 0.0
        self._logged_at = 0.0
        self._round_new = 0
        try:
            self._mc_saved = db.load_mc_tracks(time.time() - max(24.0, settings.max_age_hours) * 3600)
        except Exception:
            self._mc_saved = {}

    def log(self, msg: str) -> None:
        self.on_log(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self._stop = asyncio.Event()
        self._queue = asyncio.Queue(maxsize=5000)
        tasks = [asyncio.create_task(self._deep_worker())]
        if self.settings.use_pumpportal_ws:
            self.stream = PumpPortalStream(self._queue, self.http.health, on_log=self.log)
            tasks.append(asyncio.create_task(self.stream.run(self._stop)))
        for m in self.watch:
            self.tracked.setdefault(m, TokenState(info=TokenInfo(mint=m), watch=True))
        self.log("Scanner started")
        await self.check_helius(startup=True)
        try:
            while not self._stop.is_set():
                await self.tick()
                wait = max(0.2, min(1.0, self.clock.seconds_until_next()))
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=wait)
                except asyncio.TimeoutError:
                    pass
        finally:
            self._stop.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await self.http.aclose()
            self.log("Scanner stopped")

    def stop(self) -> None:
        if self._stop:
            self._stop.set()

    # ------------------------------------------------------------------ Helius diagnostics
    async def check_helius(self, startup: bool = False) -> dict:
        """Log where the key came from (length only) and whether Helius answers. Never logs the key."""
        if startup:
            if self.keys.helius:
                self.log(t("log.helius_key_found", path=self.keys.helius_source or str(ENV_PATH),
                           n=len(self.keys.helius)))
            else:
                self.log(t("log.helius_key_missing", path=str(ENV_PATH)))
        prev = self.helius_state.get("state")
        res = await self.rpc.helius_check()
        self.helius_state, self._helius_checked_at = res, time.time()
        if res["state"] == "CONNECTED":
            if startup or prev != "CONNECTED":
                self.log(t("log.helius_connected", endpoint=res["endpoint"], call=res["call"],
                           status=res["status"], ms=res["ms"]))
        elif res["state"] == "FAILED":
            self.log(t("log.helius_failed", endpoint=res["endpoint"] or "https://mainnet.helius-rpc.com/",
                       call=res["call"], status=res["status"] if res["status"] is not None else "—",
                       error=res["error"] or "—"))
        return res

    # ------------------------------------------------------------------ cycle
    async def tick(self) -> None:
        """One pass of the tiered loop: run every tier that is due. Errors only back off that tier."""
        for name, fn in (("discovery", self._discovery_round), ("market", self._market_round),
                         ("persist", self._persist_round)):
            if self._stop and self._stop.is_set():
                return
            if not self.clock.due(name):
                continue
            self.clock.start(name)
            try:
                ok = await fn() is not False
            except Exception as e:  # never let one bad round kill the scanner
                ok = False
                self.log(f"{name} error: {type(e).__name__}: {e}")
            self.clock.done(name, ok)

    async def cycle(self) -> None:
        """A complete round of every tier at once (CLI / audit tools)."""
        self.cycle_no += 1
        t0 = time.monotonic()
        new = await self.discover(full=True)
        await self.refresh_curves(force=True)
        await self.refresh_market()
        self.prune()
        now = time.time()
        for st in list(self.tracked.values()):
            self._evaluate(st, now)
        self.persist()
        await self.send_alerts()
        self.last_cycle_at = now
        self.log(f"cycle {self.cycle_no} ({time.monotonic()-t0:.1f}s): +{new} new, tracking {len(self.tracked)}, "
                 f"SOL ${self.sol_price or 0:.2f}")
        self.publish()

    async def _discovery_round(self) -> bool:
        self._round_new += await self.discover()
        return True

    async def _market_round(self) -> bool:
        now = time.time()
        due = market_due(list(self.tracked.values()), now)
        ok = True
        fresh: set[str] = set()
        if due:
            res = await self.refresh_market([s.mint for s in due])
            ok = res is not None
            fresh = set(res or ())
        await self.refresh_curves()
        self.prune()
        now = time.time()
        for st in list(self.tracked.values()):
            if st.mint in fresh or now - st.refreshed.get("eval", 0.0) >= REEVALUATE_S - 1:
                self._evaluate(st, now)
        self.cycle_no += 1
        self.last_cycle_at = now
        self.publish()
        return ok

    async def _persist_round(self) -> bool:
        self.persist()
        await self.send_alerts()
        now = time.time()
        if now - self._logged_at >= LOG_EVERY_S:
            self._logged_at = now
            stats = self.clock.stats()
            hot = sum(1 for s in self.tracked.values() if s.priority_reasons)
            self.log(f"round {self.cycle_no}: +{self._round_new} new, tracking {len(self.tracked)} ({hot} hot), "
                     f"market tick {stats['market']['measured_s']}s, SOL ${self.sol_price or 0:.2f}")
            self._round_new = 0
            self.log(self.feeds_line())                   # every 60 s, next to the round line
        elif not self._feeds_logged and now - self._started_at >= 30:
            self._feeds_logged = True                     # early snapshot so a broken feed shows up fast
            self.log(self.feeds_line())
        return True

    def feeds(self) -> dict:
        """Live state of every data feed — for logs and /api/status. No keys (endpoints are query-stripped)."""
        now = time.time()
        h = self.http.health

        def src(name: str, host: str = "") -> dict:
            s = h.get(name)
            return {"ok": s.ok if s.requests else None, "requests": s.requests, "errors": s.errors,
                    "last_status": s.last_status, "last_error": s.last_error or None,
                    "last_ok_age_s": round(now - s.last_ok) if s.last_ok else None,
                    "cooldown_s": round(h.cooling(name), 1), "skipped": s.skipped,
                    "rate_per_min": self.http.rate_per_min(host) if host else None}
        st = self.stream
        return {
            "pumpportal": {"enabled": bool(self.settings.use_pumpportal_ws), "connected": bool(st and st.connected),
                           "connects": st.connects if st else 0, "events_total": st.events_total if st else 0,
                           "events_last_min": st.events_last_min() if st else 0,
                           "last_event_age_s": round(now - st.last_event_at) if st and st.last_event_at else None,
                           "last_error": (st.last_error or None) if st else None},
            "pumpfun": src("pumpfun", "frontend-api-v3.pump.fun") | {
                "latest_ok": self._pump_last["latest"], "active_ok": self._pump_last["active"]},
            "dexscreener": src("dexscreener", "api.dexscreener.com"),
            "helius": {"state": self.helius_state.get("state"), "credits": self.rpc.credits.state(),
                       "deep_pool": len(self._deep_pool()), "deep_scale": self.deep_scale(now),
                       "deep_skipped": [sym for _, sym in self.deep_skipped[:20]]},
            "tracked": len(self.tracked),
            "with_market": sum(1 for s in self.tracked.values() if s.market),
        }

    def feeds_line(self) -> str:
        f = self.feeds()
        pp, pf, dx = f["pumpportal"], f["pumpfun"], f["dexscreener"]

        def http_part(x):
            return (f"HTTP {x['last_status'] if x['last_status'] is not None else '—'}"
                    + (f", errors {x['errors']}/{x['requests']}" if x["errors"] else f", {x['requests']} req")
                    + (f", COOLDOWN {x['cooldown_s']}s" if x["cooldown_s"] else "")
                    + (f", last error: {x['last_error'][:120]}" if x["last_error"] and not x["ok"] else ""))
        return (f"FEEDS: PumpPortal WS {'CONNECTED' if pp['connected'] else 'DISCONNECTED'} "
                f"({pp['events_last_min']} events/min, {pp['events_total']} total"
                + (f", last error: {pp['last_error'][:120]}" if pp["last_error"] and not pp["connected"] else "") + ")"
                f" | Pump.fun {http_part(pf)} | DexScreener {http_part(dx)}"
                f" | tracked {f['tracked']}, with market data {f['with_market']}")

    def publish(self) -> None:
        """Shallow copies are enough: evaluate() replaces result objects instead of mutating them."""
        out = []
        for st in self.tracked.values():
            c = copy.copy(st)
            c.info = copy.copy(st.info)
            out.append(c)
        self.published = out
        self.on_update(out)

    def _evaluate(self, st: TokenState, now: float | None = None) -> None:
        now = now or time.time()
        if st.holders is None and st.holder_status not in ("failed", "invalid"):
            st.holder_status = "pending" if self.rpc.has_das else "no_key"
        h = self.history.get(st.mint)
        apply_identity(st)                                   # canonical symbol/name of THIS CA (+ conflicts)
        evaluate(st, self.settings, h, now, self.sol_price)
        st.refreshed["eval"] = now
        st.trend = compute_trend(st, h, now)                 # display / priority only
        st.group, st.group_reasons = classify_group(st, self.settings)
        st.pre_early = compute_pre_early(st, h, now)         # separate layers; never feed Early Signal
        st.early_watch = compute_early_watch(st, now)
        events = self.detector.detect(st, h, now)
        if events:
            st.recent_events = (st.recent_events + events)[-20:]
            self.events = (self.events + events)[-MAX_EVENTS:]
            self.db.insert_events(events)
            self.on_events(events)

    def _add(self, info: TokenInfo) -> bool:
        st = self.tracked.get(info.mint)
        if st:
            self._merge_info(st, info)
            return False
        age_h = (time.time() - info.created_at) / 3600 if info.created_at else 0
        if info.mint not in self.watch and age_h > self.settings.max_age_hours:
            return False
        if info.mint not in self.watch and len(self.tracked) >= self.settings.max_tracked and not self._evict_one():
            return False                                  # full of starred / held tokens only
        st = self.tracked[info.mint] = TokenState(info=info, watch=info.mint in self.watch)
        self.pipe["discovered"].append(time.time())
        claim_from_info(st, info.sources, info.symbol, info.name)   # a feed only CLAIMS a symbol/name
        apply_identity(st)
        self._restore_mc(st)
        anchor_at_discovery(st, self.sol_price)          # initial MC from the discovery source, if reported
        return True

    def _merge_info(self, st: TokenState, info: TokenInfo) -> None:
        """Merge an observation of this CA, recording its identity claim and the curve quote (Pump.fun)."""
        claim_from_info(st, info.sources, info.symbol, info.name)
        st.info.merge(info)
        apply_identity(st)
        if info.quote_mint:
            confirm_quote(st, _pump_quote_is_sol(info))

    def _save_mc(self) -> None:
        """Write changed MC journeys now (a new anchor is persisted in the same round it was set)."""
        dirty = [st for st in self.tracked.values() if st.mc_track and st.mc_track.dirty]
        if not dirty:
            return
        try:
            self.db.save_mc_tracks([(st.mint, st.mc_track) for st in dirty])
        except Exception as e:           # DB trouble must not stop scanning; retried next round
            self.log(f"mc_track save failed: {type(e).__name__}: {e}")
            return
        for st in dirty:
            st.mc_track.dirty = False

    def _evict_one(self) -> bool:
        """Make room for a NEW token instead of silently dropping it (pipeline retention). Order: a token excluded for
        a REAL reason (wrong data / rug / Risk > 60 / identity conflict / holder anomaly / top10), then a known dead
        coin (MC below tracking_min_mc), then the oldest token still without market data, then the oldest other one.
        Never a starred token or one the paper bot holds."""
        cands = [st for st in self.tracked.values() if not st.watch and st.mint not in self.deep_extra]
        if not cands:
            return False

        def rank(st):
            mc = st.market.market_cap if st.market else None
            age = st.info.discovered_at
            if st.group == "excluded":
                return (0, age)
            if mc is not None and mc < self.settings.tracking_min_mc:
                return (1, age)
            if st.market is None:
                return (2, age)
            return (3, age)
        victim = min(cands, key=rank)
        self._drop(victim.mint)
        self.pipe["evicted"] += 1
        return True

    def _drop(self, mint: str) -> None:
        st = self.tracked.get(mint)
        if st is None:
            return
        if st.mc_track and st.mc_track.initial_mc is not None:
            self._mc_saved[mint] = st.mc_track          # re-discovery restores the same anchor
            while len(self._mc_saved) > MC_STASH_MAX:
                self._mc_saved.pop(next(iter(self._mc_saved)))
        del self.tracked[mint]
        self.history.drop(mint)
        self.detector.forget(mint)

    def pipeline_counts(self, now: float | None = None) -> dict:
        """Tokens entering each stage per minute (first time), plus retention counters."""
        now = now or time.time()
        for st in self.tracked.values():
            pe, ew = st.pre_early, st.early_watch
            if pe is not None and pe.status != "NOT_ELIGIBLE" and st.mint not in self._seen_pre:
                self._seen_pre.add(st.mint)
                self.pipe["pre_early"].append(now)
            if ew is not None and ew.eligible and st.mint not in self._seen_watch:
                self._seen_watch.add(st.mint)
                self.pipe["early_watch"].append(now)
        per_min = {k: sum(1 for t in self.pipe[k] if now - t <= 60) for k in ("discovered", "pre_early", "early_watch")}
        return {"discovery_per_min": per_min["discovered"], "pre_early_per_min": per_min["pre_early"],
                "early_watch_per_min": per_min["early_watch"], "tracked": len(self.tracked),
                "max_tracked": self.settings.max_tracked, "evicted": self.pipe["evicted"],
                "pruned_low_mc": self.pipe["pruned_low_mc"], "pruned_no_data": self.pipe["pruned_no_data"],
                "pruned_age": self.pipe["pruned_age"]}

    def _restore_mc(self, st: TokenState) -> None:
        """A restart must not reset the MC at discovery: reload the saved journey (SQLite)."""
        saved = self._mc_saved.pop(st.mint, None)
        if saved and st.mc_track is None:
            st.mc_track = saved
            st.info.discovered_at = saved.first_seen

    async def discover(self, full: bool = False) -> int:
        added = 0
        if self._queue:
            while not self._queue.empty():
                added += self._add(self._queue.get_nowait())
        now = time.monotonic()
        want_latest = full or now - self._pump_at["latest"] >= PUMP_LATEST_S - 0.5
        want_active = full or now - self._pump_at["active"] >= PUMP_ACTIVE_S - 0.5
        if not (want_latest or want_active):
            self._save_mc()
            return added

        async def skip():
            return []
        latest, active = await asyncio.gather(self.pump.latest(50) if want_latest else skip(),
                                              self.pump.recently_traded(50) if want_active else skip())
        if want_latest:
            self._pump_at["latest"] = now
            self._pump_last["latest"] = latest is not None
        if want_active:
            self._pump_at["active"] = now
            self._pump_last["active"] = active is not None
        for batch in (latest, active):
            for info in batch or []:
                added += self._add(info)
        self._save_mc()
        pump_down = not any(self._pump_last.values())          # both Pump.fun lists failed on their last call
        if pump_down and not (self.stream and self.stream.connected) and (full or now - self._pump_at["fallback"] >= 20):
            self._pump_at["fallback"] = now
            mints = await self.dex.latest_profiles() or []
            for m in mints:
                added += self._add(TokenInfo(mint=m, sources={"dexscreener"}))
            if mints:
                self.log("Pump.fun unavailable — using DexScreener discovery fallback")
        return added

    async def refresh_curves(self, force: bool = False) -> None:
        if not force and time.monotonic() - self._pump_at["curve"] < CURVE_S - 0.5:
            return
        self._pump_at["curve"] = time.monotonic()
        now = time.time()
        pool = {st.mint: st for st in self._candidates()}
        for st in self.tracked.values():                 # PRE-EARLY window: young curve tokens too
            if st.age_minutes is not None and st.age_minutes <= CURVE_YOUNG_MIN and st.market and st.market.is_curve:
                pool.setdefault(st.mint, st)
        stale = [st for st in pool.values()
                 if not st.info.complete and (st.info.pump_updated_at is None
                                              or now - st.info.pump_updated_at > CURVE_MAX_AGE_S / 2)]
        stale.sort(key=lambda st: (-priority_score(st), st.info.pump_updated_at or 0))
        for st in stale[:CURVE_REFRESH_PER_CYCLE]:
            fresh = await self.pump.coin(st.mint)
            if fresh:
                self._merge_info(st, fresh)

    async def refresh_market(self, mints: list[str] | None = None) -> dict | None:
        """DexScreener batch for `mints` (default: every tracked token). Returns the result or None."""
        mints = list(self.tracked) if mints is None else mints
        started = time.time()                # cadence is measured from the request start, not its end
        res = await self.dex.tokens([WSOL] + [m for m in mints if m != WSOL])
        now = time.time()
        for m in mints:                      # also when DexScreener has no pair yet: do not hammer it
            st = self.tracked.get(m)
            if st:
                st.refreshed["market"] = started
        if res is None:
            self.log("DexScreener unavailable - market data ages and turns INVALID if this persists")
            return None
        wsol = res.get(WSOL, (None, None))[0]
        if wsol and wsol.price_usd and wsol.price_usd > 0:
            self.sol_price = wsol.price_usd
        for mint, (market, socials) in res.items():
            st = self.tracked.get(mint)
            if st:
                ingest_market(st, market, socials, self.sol_price, self.history.get(mint), now)
                record_claim(st.identity, "dexscreener", market.base_symbol, market.base_name)
                apply_identity(st)
                update_mc_track(st, now)
        self._save_mc()
        return res

    def prune(self) -> None:
        now = time.time()
        max_age = self.settings.max_age_hours * 3600
        for mint, st in list(self.tracked.items()):
            if st.watch:
                continue
            if mint in self.deep_extra:
                continue                                  # the paper bot holds it
            created = st.info.created_at or (st.market.pair_created_at if st.market else None) or st.info.discovered_at
            age = now - created
            mc = st.market.market_cap if st.market else None
            reason = None
            if age > max_age:
                reason = "pruned_age"
            elif not st.snapshotted and mc is not None and age > KNOWN_LOW_MC_AFTER_S and mc < self.settings.tracking_min_mc:
                reason = "pruned_low_mc"                  # known MC: a dead coin (real reason)
            elif not st.snapshotted and mc is None and age > NO_DATA_GRACE_S:
                reason = "pruned_no_data"                 # UNKNOWN MC is kept 1 h, not treated as 0
            if reason:
                self.pipe[reason] += 1
                if st.mc_track and st.mc_track.initial_mc is not None:
                    self._mc_saved[mint] = st.mc_track      # re-discovery restores the same anchor
                    while len(self._mc_saved) > MC_STASH_MAX:
                        self._mc_saved.pop(next(iter(self._mc_saved)))
                del self.tracked[mint]
                self.history.drop(mint)
                self.detector.forget(mint)

    def _candidates(self) -> list[TokenState]:
        s = self.settings
        out = []
        for st in self.tracked.values():
            m = st.market
            if st.watch or (m and ((m.market_cap or 0) >= s.min_mc * 0.5 or (m.vol_5m or 0) >= s.min_volume_5m * 0.5)):
                out.append(st)
        out.sort(key=lambda st: (not st.watch, -((st.market.vol_5m or 0) if st.market else 0)))
        return out

    def deep_reasons(self, st: TokenState) -> list[str]:
        """PROGRESSIVE deep scan — Helius is spent only on tokens that earned it with CHEAP data first:
        starred · held by the paper bot · Early Signal TRUE · ⚡ PRE-EARLY (or NOT_YET with >= 2 signals) ·
        👀 Early Watch rank >= DEEP_MIN_WATCH_RANK · Opportunity >= DEEP_MIN_OPPORTUNITY.
        Discovery, market data and MC alone never trigger Helius."""
        out = []
        if st.watch:
            out.append("starred")
        if st.mint in self.deep_extra:
            out.append("position")
        if st.mint in self.deep_hint:
            out.append("early_score")
        if st.early is not None and st.early.is_early is True:
            out.append("early_signal")
        pe = st.pre_early
        if pe is not None and (pe.status == "PRE_EARLY" or (pe.status == "NOT_YET" and pe.fired >= 2)):
            out.append("pre_early")
        ew = st.early_watch
        if ew is not None and ew.rank is not None and ew.rank >= DEEP_MIN_WATCH_RANK:
            out.append("early_watch")
        if st.score is not None and st.score.total >= DEEP_MIN_OPPORTUNITY:
            out.append("opportunity")
        return out

    def _deep_pool(self) -> list[TokenState]:
        return [st for st in self.tracked.values() if self.deep_reasons(st)]

    def deep_scale(self, now: float) -> float:
        """1 normally; ×2 / ×4 when today's spend gets close to the paced Helius budget."""
        c = self.rpc.credits
        if not self.rpc.has_das or not c.daily_budget:
            return 1.0
        allowed = c.daily_budget * min(1.0, (now % 86400) / 86400 + 0.05)
        ratio = c.used / allowed if allowed else 1.0
        return 4.0 if ratio > 0.95 else 2.0 if ratio > 0.8 else 1.0

    def _deep_batch(self, now: float) -> list[TokenState]:
        """Next holder/dev batch: due tokens by priority, capped per round and per minute (Helius budget)."""
        self._deep_times = [x for x in self._deep_times if now - x < 60]
        budget = max(0, self.settings.deep_max_per_min - len(self._deep_times))
        scale = self.deep_scale(now)
        if self.rpc.has_das and (self.rpc.credits.remaining() < 60 or not self.rpc.credits.paced_ok(now)):
            skipped = deep_due(self._deep_pool(), now, 50, scale)
            self.deep_skipped = [(s.mint, s.info.symbol or s.mint[:6]) for s in skipped]
            if skipped and now - self._skip_logged_at >= 60:
                self._skip_logged_at = now
                c = self.rpc.credits.state()
                self.log(f"HELIUS budget: deep scan skipped for {len(skipped)} token(s) "
                         f"({', '.join(sym for _, sym in self.deep_skipped[:8])}) — credits {c['used']}/{c['daily_budget']} "
                         f"today, holders stay UNKNOWN (VET will not pass)")
            return []                                     # Helius credits: daily budget, spent evenly over the day
        self.deep_skipped = []
        todo = deep_due(self._deep_pool(), now, min(self.settings.deep_per_cycle, budget), scale)
        self._deep_times += [now] * len(todo)
        return todo

    async def _deep_worker(self) -> None:
        """ON-CHAIN VALIDATION runs in its own loop so slow RPC never delays market scans."""
        sem = asyncio.Semaphore(3)

        async def one(st: TokenState):
            async with sem:
                try:
                    await asyncio.wait_for(self._deep(st), timeout=90)
                except Exception as e:
                    self.log(f"deep analysis {st.info.symbol or st.mint[:6]} failed: {type(e).__name__} {e}")
                st.last_deep = time.time()

        while not self._stop.is_set():
            now = time.time()
            if self.helius_state.get("state") == "FAILED" and now - self._helius_checked_at > 60:
                await self.check_helius()
            todo = self._deep_batch(now)
            if todo:
                await asyncio.gather(*(one(st) for st in todo))
                if self.rpc.has_das:
                    ok = [s for s in todo if s.holder_status == "ok"]
                    bad = [s for s in todo if s.holder_status == "failed"]
                    inv = [s for s in todo if s.holder_status == "invalid"]
                    das = self.http.health.get(SOURCE_DAS)
                    self.log(t("log.helius_round", ok=len(ok), failed=len(bad), total=len(todo),
                               status=das.last_status if das.last_status is not None else "—",
                               ms=das.last_ms if das.last_ms is not None else "—")
                             + (t("log.holders_invalid_count", n=len(inv)) if inv else ""))
                    for s in bad:
                        self.log(t("log.helius_token_failed", symbol=s.info.symbol or s.mint[:6], error=s.holder_error))
                    for s in inv:
                        self.log(t("log.holders_invalid", symbol=s.info.symbol or s.mint[:6], error=s.holder_error))
                for st in todo:
                    if st.mint in self.tracked:
                        self._evaluate(st)
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.clock.tiers["holders"].interval)
            except asyncio.TimeoutError:
                pass

    async def _deep(self, st: TokenState, force_dev: bool = False) -> None:
        st.refreshed["holders"] = time.time()          # cadence measured from the start of the scan
        info = st.info
        h = self.history.get(st.mint)
        if not info.creator or info.total_supply is None or (info.real_sol_reserves is None and not info.complete):
            fresh = await self.pump.coin(info.mint)
            if fresh:
                self._merge_info(st, fresh)
        if self.rpc.has_das and not st.identity.helius_checked:
            asset = await self.rpc.das_get_asset(info.mint)
            if asset is not None:
                st.identity.helius_checked = True
                st.identity.token_program, st.identity.extensions = asset["token_program"], asset["extensions"]
                st.identity.mint_authority = asset.get("mint_authority", "")
                st.identity.freeze_authority = asset.get("freeze_authority", "")
                record_claim(st.identity, "helius", asset["symbol"], asset["name"])
        if apply_identity(st).status == "CONFLICT":
            st.holder_status = st.holder_status or "pending"
            return                                        # identity first: never analyse a mislabelled CA
        if info.total_supply is None:
            sup = await self.rpc.token_supply(info.mint)
            if sup:
                info.total_supply, info.decimals = sup
        exclude = {info.bonding_curve: "bonding curve", info.pool: "AMM pool"}
        if st.market and st.market.pair_address:
            exclude[st.market.pair_address] = "AMM pool"
        das = self.http.health.get(SOURCE_DAS)
        errors_before = das.errors
        hs = await self.holders.analyze(info.mint, info.total_supply, info.decimals, exclude, info.creator)
        # D6: holder count collapsing > 50 % within ~5 min is treated as a data error, not a signal
        last = h.holders[-1] if h.holders else None
        if hs and hs.valid and last and last.count and hs.holder_count is not None \
                and hs.fetched_at - last.ts <= 360 and hs.holder_count < 0.5 * last.count:
            hs.valid = False
            hs.invalid_reason = (f"holder count {last.count} -> {hs.holder_count} "
                                 f"({100 * (hs.holder_count / last.count - 1):.0f}%) in "
                                 f"{(hs.fetched_at - last.ts) / 60:.1f} min")
        if hs and not hs.valid:
            st.holders, st.holder_status, st.holder_error = None, "invalid", hs.invalid_reason
            st.stamps.pop("holders", None)
            hs = None
            invalid_holders = True
        else:
            invalid_holders = False
        if invalid_holders:
            pass
        elif not self.rpc.has_das:
            st.holder_status = "ok" if hs else "no_key"
        elif hs and hs.source == "helius_das":
            st.holder_status, st.holder_error = "ok", ""
        else:
            detail = (f"{das.last_call or 'getTokenAccounts'} {das.last_endpoint} "
                      f"HTTP {das.last_status if das.last_status is not None else '—'}: {das.last_error or '—'}")
            if hs:   # DAS failed but the RPC top-20 fallback worked
                st.holder_status, st.holder_error = "ok", f"DAS failed, RPC top-20 fallback used ({detail})"
            else:
                st.holder_status, st.holder_error = "failed", detail if das.errors > errors_before or das.last_error else "no response"
        if hs:
            st.holders = hs
            st.stamps["holders"] = SourceStamp(hs.source, hs.fetched_at)
            h.add_holders(HolderSnap(hs.fetched_at, hs.holder_count, hs.owner_amounts, hs.complete_list))
        if force_dev or not st.dev or dev_due(st, time.time()):
            st.refreshed["dev"] = time.time()
            d = await self.dev.analyze(info, st.holders)
            if d:
                st.dev = d
                if d.balance_verified:
                    st.stamps["dev"] = SourceStamp("Solana RPC (creator balance)", d.fetched_at)
                    h.add_dev_balance(d.fetched_at, d.current_tokens)
        st.last_deep = time.time()

    def persist(self) -> None:
        rows = []
        for st in self.tracked.values():
            if not st.market:
                continue
            if st.watch or st.snapshotted or (st.market.market_cap or 0) >= self.settings.snapshot_min_mc:
                if not st.snapshotted:
                    self.db.upsert_token(st)
                    st.snapshotted = True
                rows.append(st)
        self.db.insert_snapshots(rows)
        self._save_mc()

    async def send_alerts(self) -> None:
        """Alerts come ONLY from the ranked list (VALID data quality + Opportunity Score)."""
        s = self.settings
        if not s.alerts_enabled:
            return
        for st in rank_opportunities(list(self.tracked.values())):
            if st.score.total < s.alert_min_score:
                break
            if not st.dev or st.risk.score > s.alert_max_risk:
                continue
            if s.alert_require_filters and st.filter_fails:
                continue
            last = self.db.last_alert_ts(st.mint)
            if last and time.time() - last < s.alert_cooldown_min * 60:
                continue
            msg = telegram_alert(st, s.language)
            sent = await self.alerter.send(msg)
            self.db.insert_alert(st, msg, sent)
            self.log(f"ALERT ${st.info.symbol} opportunity {st.score.total} risk {st.risk.score} "
                     f"DQ {st.quality.score}" + (" (sent to Telegram)" if sent else ""))

    # ------------------------------------------------------------------ on-demand
    async def analyze_one(self, mint: str) -> TokenState:
        """Full, forced analysis of a single token (CLI --check, watchlist add, detail refresh)."""
        st = self.tracked.get(mint)
        if st is None:
            st = TokenState(info=TokenInfo(mint=mint), watch=mint in self.watch)
            self._restore_mc(st)
        info = await self.pump.coin(mint)
        if info:
            self._merge_info(st, info)
        await self._deep(st, force_dev=True)
        res = await self.dex.tokens([WSOL, mint]) or {}
        wsol = res.get(WSOL, (None, None))[0]
        if wsol and wsol.price_usd and wsol.price_usd > 0:
            self.sol_price = wsol.price_usd
        if mint in res:
            ingest_market(st, res[mint][0], res[mint][1], self.sol_price, self.history.get(mint))
            record_claim(st.identity, "dexscreener", res[mint][0].base_symbol, res[mint][0].base_name)
            apply_identity(st)
            update_mc_track(st)
            st.refreshed["market"] = time.time()
        self._evaluate(st)
        return st

    async def add_watch(self, mint: str) -> TokenState:
        self.db.add_watch(mint)
        self.watch.add(mint)
        st = await self.analyze_one(mint)
        st.watch = True
        self.tracked[mint] = st
        self.persist()
        return st

    def remove_watch(self, mint: str) -> None:
        self.db.remove_watch(mint)
        self.watch.discard(mint)
        if mint in self.tracked:
            self.tracked[mint].watch = False
