"""Scanner orchestrator.

Cycle:  discover -> refresh curve data -> DexScreener batch (validated + history) -> prune
        -> evaluate (full pipeline) -> events -> persist -> alerts (ranked VALID only) -> publish.
A separate worker does on-chain validation (holders + dev) and re-evaluates what it touched.
Performance: DexScreener batches 30 mints/request, per-host throttling, retry/backoff/timeouts in
core.http, caches for dev history/funding, dedup by mint, bounded in-memory history, shallow copies
for publishing.
"""
from __future__ import annotations

import asyncio
import copy
import time
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
from scanner.pipeline import evaluate, ingest_market
from scoring.ranking import rank_opportunities
from solana_data.rpc import SolanaRpc
from i18n import t
from solana_data.rpc import SOURCE_DAS
from validation.market import CURVE_MAX_AGE_S

WSOL = "So11111111111111111111111111111111111111112"
HOLDER_TTL = 90
DEV_TTL = 300
CURVE_REFRESH_PER_CYCLE = 8
MAX_EVENTS = 500


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

    def log(self, msg: str) -> None:
        self.on_log(f"[{time.strftime('%H:%M:%S')}] {msg}")

    # ------------------------------------------------------------------ lifecycle
    async def run(self) -> None:
        self._stop = asyncio.Event()
        self._queue = asyncio.Queue(maxsize=5000)
        tasks = [asyncio.create_task(self._deep_worker())]
        if self.settings.use_pumpportal_ws:
            self.stream = PumpPortalStream(self._queue, self.http.health)
            tasks.append(asyncio.create_task(self.stream.run(self._stop)))
        for m in self.watch:
            self.tracked.setdefault(m, TokenState(info=TokenInfo(mint=m), watch=True))
        self.log("Scanner started")
        await self.check_helius(startup=True)
        try:
            while not self._stop.is_set():
                t0 = time.monotonic()
                try:
                    await self.cycle()
                except Exception as e:  # never let one bad cycle kill the scanner
                    self.log(f"cycle error: {type(e).__name__}: {e}")
                wait = max(1.0, self.settings.scan_interval_sec - (time.monotonic() - t0))
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
    async def cycle(self) -> None:
        self.cycle_no += 1
        t0 = time.monotonic()
        new = await self.discover()
        await self.refresh_curves()
        await self.refresh_market()
        self.prune()
        now = time.time()
        for st in self.tracked.values():
            self._evaluate(st, now)
        self.persist()
        await self.send_alerts()
        self.last_cycle_at = now
        self.log(f"cycle {self.cycle_no} ({time.monotonic()-t0:.1f}s): +{new} new, tracking {len(self.tracked)}, "
                 f"SOL ${self.sol_price or 0:.2f}")
        self.publish()

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
        evaluate(st, self.settings, h, now, self.sol_price)
        events = self.detector.detect(st, h, now)
        if events:
            st.recent_events = (st.recent_events + events)[-20:]
            self.events = (self.events + events)[-MAX_EVENTS:]
            self.db.insert_events(events)
            self.on_events(events)

    def _add(self, info: TokenInfo) -> bool:
        st = self.tracked.get(info.mint)
        if st:
            st.info.merge(info)
            return False
        age_h = (time.time() - info.created_at) / 3600 if info.created_at else 0
        if info.mint not in self.watch and (age_h > self.settings.max_age_hours
                                            or len(self.tracked) >= self.settings.max_tracked):
            return False
        self.tracked[info.mint] = TokenState(info=info, watch=info.mint in self.watch)
        return True

    async def discover(self) -> int:
        added = 0
        if self._queue:
            while not self._queue.empty():
                added += self._add(self._queue.get_nowait())
        latest, active = await asyncio.gather(self.pump.latest(50), self.pump.recently_traded(50))
        for batch in (latest, active):
            for info in batch or []:
                added += self._add(info)
        if latest is None and active is None and not (self.stream and self.stream.connected):
            mints = await self.dex.latest_profiles() or []
            for m in mints:
                added += self._add(TokenInfo(mint=m, sources={"dexscreener"}))
            if mints:
                self.log("Pump.fun unavailable — using DexScreener discovery fallback")
        return added

    async def refresh_curves(self) -> None:
        now = time.time()
        stale = [st for st in self._candidates()
                 if not st.info.complete and (st.info.pump_updated_at is None
                                              or now - st.info.pump_updated_at > CURVE_MAX_AGE_S / 2)]
        for st in stale[:CURVE_REFRESH_PER_CYCLE]:
            fresh = await self.pump.coin(st.mint)
            if fresh:
                st.info.merge(fresh)

    async def refresh_market(self) -> None:
        res = await self.dex.tokens([WSOL] + list(self.tracked))
        if res is None:
            self.log("DexScreener unavailable this cycle - market data ages and turns INVALID if this persists")
            return
        wsol = res.get(WSOL, (None, None))[0]
        if wsol and wsol.price_usd and wsol.price_usd > 0:
            self.sol_price = wsol.price_usd
        now = time.time()
        for mint, (market, socials) in res.items():
            st = self.tracked.get(mint)
            if st:
                ingest_market(st, market, socials, self.sol_price, self.history.get(mint), now)

    def prune(self) -> None:
        now = time.time()
        max_age = self.settings.max_age_hours * 3600
        for mint, st in list(self.tracked.items()):
            if st.watch:
                continue
            created = st.info.created_at or (st.market.pair_created_at if st.market else None) or st.info.discovered_at
            age = now - created
            mc = st.market.market_cap if st.market else None
            if age > max_age or (not st.snapshotted and age > 900 and (mc or 0) < self.settings.tracking_min_mc):
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
            todo = [st for st in self._candidates() if now - st.last_deep > HOLDER_TTL][: self.settings.deep_per_cycle]
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
                await asyncio.wait_for(self._stop.wait(), timeout=3)
            except asyncio.TimeoutError:
                pass

    async def _deep(self, st: TokenState, force_dev: bool = False) -> None:
        info = st.info
        h = self.history.get(st.mint)
        if not info.creator or info.total_supply is None or (info.real_sol_reserves is None and not info.complete):
            fresh = await self.pump.coin(info.mint)
            if fresh:
                info.merge(fresh)
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
        if force_dev or not st.dev or time.time() - st.dev.fetched_at > DEV_TTL:
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
        st = self.tracked.get(mint) or TokenState(info=TokenInfo(mint=mint), watch=mint in self.watch)
        info = await self.pump.coin(mint)
        if info:
            st.info.merge(info)
        await self._deep(st, force_dev=True)
        res = await self.dex.tokens([WSOL, mint]) or {}
        wsol = res.get(WSOL, (None, None))[0]
        if wsol and wsol.price_usd and wsol.price_usd > 0:
            self.sol_price = wsol.price_usd
        if mint in res:
            ingest_market(st, res[mint][0], res[mint][1], self.sol_price, self.history.get(mint))
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
