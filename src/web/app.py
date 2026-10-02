"""Web server for the iPhone PWA: FastAPI + the unchanged ScannerEngine running in the same process.

Security model
  * HELIUS_API_KEY / TELEGRAM_* stay in server environment variables. Nothing in core.models holds a
    secret, and responses are built only from TokenState / health data (tests assert the key never appears).
  * Every /api/* route (except /healthz) requires the header X-Access-Code == APP_ACCESS_CODE.
    FAIL-CLOSED: if APP_ACCESS_CODE is not configured the API answers 503.
    Constant-time comparison; 10 wrong codes per IP per 10 min -> 429.
  * Only research actions exist: read lists/tokens, refresh one token, maintain the watchlist.
    No wallet, no private key, no seed phrase, no transaction, no buy/sell endpoint.
  * On-demand refreshes (which call Helius) are rate-limited per token and globally.
"""
from __future__ import annotations

import asyncio
import contextlib
import hmac
import os
import re
import time
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from analytics.narratives import aggregate_narratives
from api.serialize import card, view
from core.config import DATA_DIR, DB_PATH, Settings, env
from core.snapshot import NOT_DURABLE, SnapshotError, fmt_bytes
from database.db import Database
from i18n import load as load_lang, set_language, t
from scanner.engine import ScannerEngine
from intel.early_watch import select_watch
from scoring.groups import GROUPS, confirm_key
from scoring.ranking import rank_early, rank_opportunities
from trading.bot import PaperBot
from trading.config import ModeNotAllowed, TradingConfig
from trading.serialize import bot_status, module_detail

STATIC = Path(__file__).resolve().parent / "static"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
AUTH_WINDOW_S, AUTH_MAX_FAILS = 600, 10
REFRESH_PER_MINT_S, REFRESH_GLOBAL_PER_MIN = 60, 10
MAX_WATCH = 30
LIST_KINDS = ("top", "new", "early", "whales", "dev", "social")
VERSION = "web-16"
HOME_LIMIT = {"opportunity": 60, "watch": 60, "nodata": 40, "excluded": 40}


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else "?")


class Guard:
    """Access code check + brute-force limiter + refresh rate limiter."""

    def __init__(self, code: str):
        self.code = code
        self.fails: dict[str, list[float]] = {}
        self.refresh_mint: dict[str, float] = {}
        self.refresh_times: list[float] = []

    def check(self, request: Request) -> JSONResponse | None:
        if not self.code:
            return JSONResponse({"error": "access_code_not_configured"}, status_code=503)
        ip, now = client_ip(request), time.time()
        recent = [x for x in self.fails.get(ip, []) if now - x < AUTH_WINDOW_S]
        self.fails[ip] = recent
        if len(recent) >= AUTH_MAX_FAILS:
            return JSONResponse({"error": "too_many_attempts"}, status_code=429)
        given = request.headers.get("x-access-code", "")
        if not hmac.compare_digest(given.encode(), self.code.encode()):
            recent.append(now)
            return JSONResponse({"error": "unauthorized"}, status_code=401)
        return None

    def allow_refresh(self, mint: str) -> bool:
        now = time.time()
        self.refresh_times = [x for x in self.refresh_times if now - x < 60]
        if now - self.refresh_mint.get(mint, 0) < REFRESH_PER_MINT_S or len(self.refresh_times) >= REFRESH_GLOBAL_PER_MIN:
            return False
        self.refresh_mint[mint] = now
        self.refresh_times.append(now)
        return True


def _utc(t: float | None) -> str | None:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t)) if t else None


def create_app(engine: ScannerEngine | None = None, start_scanner: bool = True,
               access_code: str | None = None, bot: PaperBot | None = None) -> FastAPI:
    set_language("vi")
    guard = Guard(access_code if access_code is not None else env("APP_ACCESS_CODE"))
    state: dict = {"engine": engine, "task": None, "started_at": time.time(), "bot": bot, "bot_task": None,
                   "bot_stop": None,
                   "snapshot": {"store": "NOT CONFIGURED", "durable": False, "target": "-",
                                "reason": "SNAPSHOT_DIR / SNAPSHOT_URL not set", "warning": NOT_DURABLE,
                                "last_ts": None, "last": None, "last_error": None, "restore": None},
                   "aux": []}

    def _save_price_history() -> None:
        """G8: the scanner's recent price points -> data/price_history.json (loaded back on a quick restart)."""
        from history import persist
        if state["engine"] is None or state.get("halted"):
            return
        try:
            persist.dump(state["engine"].history, DATA_DIR / persist.FILE)
        except Exception as e:                     # never block a snapshot / shutdown on this
            print(f"[history] save failed: {type(e).__name__}", flush=True)

    async def _snapshot_now(reason: str) -> None:
        from core.snapshot import snapshot
        from core.version import git_commit
        store = state.get("snapshot_store")
        if store is None or state.get("halted") or state.get("role") not in (None, "ACTIVE"):
            return
        lease = state.get("lease")
        if lease is not None and not await asyncio.to_thread(lease.held):
            print(f"[snapshot] {reason} SKIPPED: this instance does not hold the lease ({lease.status})", flush=True)
            return
        try:
            if state["bot"] is not None:
                state["bot"].persist()
            _save_price_history()
            man = await asyncio.to_thread(snapshot, DATA_DIR, store, git_commit())
            state["snapshot"].update(last_ts=man["ts"], last_error=None, last={
                "reason": reason,
                "files": {k: {"size": v["size"], "size_gz": v["size_gz"]} for k, v in man["files"].items()},
                "bytes_raw": man["bytes_raw"], "bytes_gz": man["bytes_gz"], "duration_s": man["duration_s"],
                "target": man["target"], "store": man["store"]})
            print(f"[snapshot] {reason} OK {_utc(man['ts'])}: {len(man['files'])} files, "
                  f"{fmt_bytes(man['bytes_gz'])} gz ({fmt_bytes(man['bytes_raw'])} raw) -> {man['store']} "
                  f"{man['target']} in {man['duration_s']} s", flush=True)
        except Exception as e:                             # never break trading for a backup (type only: no URL)
            state["snapshot"]["last_error"] = {"ts": time.time(), "reason": reason, "error": type(e).__name__}
            print(f"[snapshot] {reason} FAILED: {type(e).__name__}", flush=True)

    async def _snapshot_loop() -> None:
        from core.snapshot import SNAPSHOT_EVERY_S
        while True:
            await asyncio.sleep(SNAPSHOT_EVERY_S)
            await _snapshot_now("hourly")
            if state["bot"] is not None:
                print("[sample] " + state["bot"].sample_summary_line(), flush=True)

    async def _restore(store) -> None:
        from core.snapshot import restore
        try:
            res = await asyncio.to_thread(restore, DATA_DIR, store)
        except Exception as e:                       # SnapshotError text is ours; others: type only (URL)
            why = f"{type(e).__name__}: {e}" if isinstance(e, SnapshotError) else type(e).__name__
            res = {"status": f"RESTORE FAILED: {why}", "restored": [], "at": time.time()}
            state["halted"] = f"restore failed ({why}): the bot is STOPPED instead of trading on an empty book"
            state["snapshot"]["halted"] = state["halted"]
        state["snapshot"]["restore"] = res
        frm = f" from snapshot {_utc(res['snapshot_ts'])}" if res.get("snapshot_ts") else ""
        print(f"[snapshot] restore {res['status']}: {', '.join(res.get('restored', [])) or '-'}{frm}", flush=True)

    def _ensure_engine() -> None:
        if state["engine"] is None:
            state["engine"] = ScannerEngine(Settings.load(), Database(DB_PATH), on_log=lambda m: print(m, flush=True))

    def _start() -> None:
        """Scanner + bot + snapshots + keep-alive: only on the instance that holds the lease (or has no store)."""
        from trading.jupiter import JupiterQuotes
        state["bot"].jupiter = JupiterQuotes(state["engine"].http)      # paper BUYs on real Jupiter quotes
        from trading.quote_budget import QuoteBudget
        state["bot"].quote_budget = QuoteBudget()                      # 50/min, SELL quotes first
        state["bot"].cfg.experimental = os.environ.get("EXPERIMENTAL_MODE", "1") != "0"   # spec Part 3 (PAPER)
        state["bot"].cfg.latency_probe = os.environ.get("LATENCY_PROBE", "1") != "0"     # measure real drift
        state["bot"].cfg.lifecycle = os.environ.get("LIFECYCLE_ENGINE", "1") != "0"      # Lifecycle-Aware Hunter V1
        lm = os.environ.get("LATENCY_SLIPPAGE_MODEL", "AUTO").upper()   # AUTO | CURRENT | CONSERVATIVE | EMPIRICAL
        if lm in ("AUTO", "CURRENT", "CONSERVATIVE", "EMPIRICAL"):
            state["bot"].cfg.latency_slippage_model = state["bot"].exec.latency_model = lm
        if os.environ.get("RESEARCH_LOG", "1") != "0":                # research dataset (read-only log)
            from research.dataset import DatasetRecorder
            state["bot"].recorder = DatasetRecorder(DATA_DIR / "research.db", dex=state["engine"].dex)
            if os.environ.get("MONEYFLOW", "1") != "0":                         # shadow money flow (budgeted)
                from trading.money_flow import MoneyFlowCollector
                state["bot"].money_flow = MoneyFlowCollector(state["engine"].rpc)
            if os.environ.get("RESEARCH_ONCHAIN", "1") != "0":                  # shadow anti-rug data (budgeted)
                from research.onchain import OnchainResearch
                state["bot"].onchain = OnchainResearch(state["engine"].rpc, state["bot"].recorder)
        ign = getattr(state["bot"].cfg, "ignored_file_keys", [])
        if ign:
            print(f"[config] trading.json strategy keys IGNORED (code defaults win): {', '.join(ign)}", flush=True)
        ep = state["bot"].begin_sample()
        print(f"[startup] commit {ep['commit']} · params {ep['fingerprint']} · strategy {ep['strategy_version']} "
              f"· sample epoch since {ep['started_at_utc']}", flush=True)
        from history import persist as hist_persist
        ph = hist_persist.load(state["engine"].history, DATA_DIR / hist_persist.FILE)
        state["snapshot"]["price_history"] = ph
        print(f"[history] price history {ph['status']}: {ph['tokens']} tokens, {ph['points']} points", flush=True)
        state["task"] = asyncio.create_task(state["engine"].run())
        state["bot_stop"] = asyncio.Event()
        state["bot"].snapshot_status = state["snapshot"]
        state["bot_task"] = asyncio.create_task(state["bot"].run(state["bot_stop"]))
        if state.get("snapshot_store") is not None:
            state["aux"].append(asyncio.create_task(_snapshot_loop()))
        else:
            state["aux"].append(asyncio.create_task(_not_durable_loop()))
        if state.get("lease") is not None:
            state["aux"].append(asyncio.create_task(_lease_loop(state["lease"])))
        from web.keepalive import keepalive_url, keepalive_loop
        ka = keepalive_url()
        if ka:
            state["aux"].append(asyncio.create_task(keepalive_loop(ka)))
            print(f"[keepalive] pinging {ka} every 10 min (Render free plan spins down after 15 min idle)",
                  flush=True)

    async def _activate() -> None:
        """Lease held (or no store): restore, build the real bot, start. Halted: nothing runs."""
        store = state.get("snapshot_store")
        if store is not None:
            await _restore(store)
        _ensure_engine()
        halted = state.get("halted")
        if state["bot"] is None or state.get("placeholder_bot"):    # PAPER trading bot: reads scanner results only
            persist = not halted                         # halted: never write an empty book over the real one
            cfg_path = DATA_DIR / "trading.json"
            state["bot"] = PaperBot(state["engine"], TradingConfig.load(cfg_path) if persist else TradingConfig(),
                                    state_path=DATA_DIR / "paper_bot.json" if persist else None,
                                    config_path=cfg_path if persist else None)
            state["placeholder_bot"] = False
        state["bot"].snapshot_status = state["snapshot"]
        if halted:
            print(f"[HALT] {halted}. Fix the snapshot store, then restart. Scanner, bot and snapshots are NOT "
                  "started (a snapshot now would overwrite the good one).", flush=True)
            if state.get("lease") is not None:
                await asyncio.to_thread(state["lease"].release)       # a halted instance never writes anything
            state["role"] = "HALTED"
            return
        state["role"] = "ACTIVE"
        _start()

    async def _standby_loop(lease) -> None:
        from core.lease import LEASE_POLL_S
        while True:
            await asyncio.sleep(LEASE_POLL_S)
            try:
                got = await asyncio.to_thread(lease.acquire)
            except Exception as e:                     # store unreachable: keep waiting
                print(f"[lease] acquire failed: {type(e).__name__}", flush=True)
                continue
            if got:
                print("[lease] acquired after STANDBY: restoring the previous instance's final snapshot", flush=True)
                await _activate()
                return

    async def _lease_loop(lease) -> None:
        from core.lease import LEASE_RENEW_S
        while True:
            await asyncio.sleep(LEASE_RENEW_S)
            try:
                ok = await asyncio.to_thread(lease.renew)
            except Exception as e:                     # store unreachable: the lease simply ages; try again
                print(f"[lease] renew failed: {type(e).__name__}", flush=True)
                continue
            if not ok:
                state["halted"] = f"lease lost ({lease.status}): another instance is active — this one stopped " \
                                  "trading and snapshots"
                state["snapshot"]["halted"] = state["halted"]
                state["role"] = "LEASE LOST"
                if state.get("bot_stop") is not None:
                    state["bot_stop"].set()
                print(f"[lease] LOST: {lease.status}. Bot and snapshots stopped on this instance.", flush=True)
                return

    async def _not_durable_loop() -> None:
        """No snapshot store: say so in the log every hour, with the sample line (it will be lost on restart)."""
        from core.snapshot import SNAPSHOT_EVERY_S
        while True:
            await asyncio.sleep(SNAPSHOT_EVERY_S)
            print(f"[durability] {NOT_DURABLE}: {state['snapshot'].get('reason')}", flush=True)
            if state["bot"] is not None:
                print("[sample] " + state["bot"].sample_summary_line(), flush=True)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if start_scanner and state["bot"] is None:     # restore the sample BEFORE anything opens the files
            from core.snapshot import durability_check, store_from_env
            store = store_from_env()
            state["snapshot_store"] = store
            chk = await asyncio.to_thread(durability_check, store, DATA_DIR)
            state["snapshot"].update(store=chk["store"], durable=chk["durable"], target=chk["target"],
                                     reason=chk["reason"], warning=chk.get("warning"))
            if chk["durable"]:
                print(f"[durability] DURABLE: {chk['store']} {chk['target']} ({chk['reason']})", flush=True)
            else:
                print(f"[durability] {NOT_DURABLE}: {chk['reason']}", flush=True)
            got = True
            if store is not None:
                from core.lease import Lease
                state["lease"] = lease = Lease(store)
                try:
                    got = await asyncio.to_thread(lease.acquire)
                except Exception as e:
                    got = False
                    lease.status = f"STANDBY: store unreachable ({type(e).__name__})"
            if got:
                await _activate()
            else:                                       # another instance is active (zero-downtime deploy overlap)
                state["role"] = "STANDBY"
                _ensure_engine()
                state["bot"] = PaperBot(state["engine"], TradingConfig())      # placeholder: no file, no trading
                state["placeholder_bot"] = True
                state["bot"].snapshot_status = state["snapshot"]
                print(f"[lease] {state['lease'].status}: STANDBY — no restore, no trading, no snapshot until the "
                      "active instance releases the lease or it expires", flush=True)
                state["aux"].append(asyncio.create_task(_standby_loop(state["lease"])))
        else:                                           # tests / tools: a given bot, or no scanner at all
            _ensure_engine()
            if state["bot"] is None:
                state["bot"] = PaperBot(state["engine"], TradingConfig())
            if start_scanner:
                state["role"] = "ACTIVE"
                _start()
        yield
        for t in state["aux"]:
            t.cancel()
        if state["bot_task"]:
            state["bot_stop"].set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state["bot_task"], 10)
            if state.get("snapshot_store") is None:
                _save_price_history()                      # local restart without a store still keeps it
            await _snapshot_now("shutdown")                # SIGTERM before a deploy / restart: last snapshot ...
        if state.get("lease") is not None and state.get("role") == "ACTIVE":
            with contextlib.suppress(Exception):
                await asyncio.to_thread(state["lease"].release)   # ... THEN hand the lease to the new instance
                print("[lease] released after the final snapshot", flush=True)
        eng = state["engine"]
        if state["task"]:
            eng.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state["task"], 15)

    app = FastAPI(title="SOL Memecoin Hunter", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.hunter = state
    app.state.guard = guard
    app.add_middleware(GZipMiddleware, minimum_size=1024)      # lists are polled every few seconds

    @app.middleware("http")
    async def security(request: Request, call_next):
        path = request.url.path
        if path.startswith("/api/"):
            denied = guard.check(request)
            if denied:
                return _headers(denied)
        resp = await call_next(request)
        if path.startswith("/static/") or path.startswith("/i18n/"):
            resp.headers["Cache-Control"] = "no-cache"      # always revalidate (ETag) so iPhones get new deploys
        return _headers(resp)

    def eng() -> ScannerEngine:
        return state["engine"]

    def states():
        e = eng()
        return list(e.published) if e and e.published else []

    # ------------------------------------------------------------------ public (no data)
    @app.get("/healthz")
    async def healthz():
        """Public liveness + which code and parameters are running (no secret, no paper data)."""
        from core.version import git_commit
        bot = state.get("bot")
        out = {"ok": True, "commit": git_commit(), "role": state.get("role") or "ACTIVE"}
        if state.get("lease") is not None:
            out["lease"] = state["lease"].as_dict()
        if state.get("halted"):
            out["ok"] = False                              # a halted server is not healthy
            out["halted"] = state["halted"]
        sn = state["snapshot"]
        out["snapshot"] = {"durable": sn["durable"], "store": sn["store"], "last_utc": _utc(sn["last_ts"]),
                           "last_restore_utc": _utc((sn.get("restore") or {}).get("at"))}
        if not sn["durable"]:
            out["snapshot"]["warning"] = sn.get("warning") or NOT_DURABLE
        if bot is not None:
            from trading.strategy_constants import constants_hash
            out["params"] = bot.cfg.sample_id()
            out["constants"] = constants_hash()
            ep = bot.sample_epoch
            out["sample_epoch"] = {"strategy_version": ep.strategy_version, "fingerprint": ep.fingerprint,
                                   "started_at_utc": ep.as_dict()["started_at_utc"]}
        return out

    # ------------------------------------------------------------------ API
    @app.get("/api/snapshot")
    async def snapshot_status():
        """Durability of the paper sample (access code required): store, target (no token), last snapshot with
        sizes, last error, last restore."""
        return _snapshot_view()

    @app.get("/api/review_bundle")
    async def review_bundle():
        """Part 3.4: sample report + last 50 trades + gate statistics + durability, for tools/export_review_bundle.py
        (access code required; no secret in it)."""
        from trading.review_bundle import build
        b = state["bot"]
        if b is None:
            return JSONResponse({"error": "bot_not_started"}, status_code=503)
        rep = b.sample_report()
        from trading.sample_report import summary_line
        rep["summary_line"] = summary_line(rep)
        return build(b.book.journal, rep, _snapshot_view())

    def _snapshot_view() -> dict:
        sn = state["snapshot"]
        rs = sn.get("restore") or {}
        return {"halted": state.get("halted"), "role": state.get("role") or "ACTIVE",
                "lease": state["lease"].as_dict() if state.get("lease") is not None else None,
                "price_history_at_start": sn.get("price_history"),
                "durable": sn["durable"], "warning": None if sn["durable"] else (sn.get("warning") or NOT_DURABLE),
                "store": sn["store"], "target": sn["target"], "check": sn["reason"],
                "last_snapshot": {**sn["last"], "at_utc": _utc(sn["last_ts"])} if sn.get("last") else None,
                "last_error": sn.get("last_error"),
                "last_restore": {"status": rs.get("status"), "at_utc": _utc(rs.get("at")), "files": rs.get("restored"),
                                 "from_snapshot_utc": _utc(rs.get("snapshot_ts"))} if rs else None}

    @app.get("/api/auth")
    async def auth():
        return {"ok": True}

    @app.get("/api/status")
    async def status():
        e, now = eng(), time.time()
        sts = states()
        interval = e.settings.scan_interval_sec if e else 20
        last = e.last_cycle_at if e else None
        task = state["task"]
        running = bool(e and last and now - last < max(90, 4 * interval) and not (task and task.done()))
        hel = dict(e.helius_state) if e else {}
        dq = {k: sum(1 for s in sts if s.dq_status == k) for k in ("VALID", "PARTIAL", "INVALID")}
        return {
            "scanner": {"running": running, "starting": bool(e and not last), "cycle": e.cycle_no if e else 0,
                        "last_cycle_age_s": round(now - last) if last else None, "tracked": len(sts),
                        "interval_s": interval, "uptime_s": round(now - state["started_at"])},
            "helius": {"state": hel.get("state", "UNCHECKED"), "status": hel.get("status"), "ms": hel.get("ms")},
            "data_quality": dq,
            "early_true": sum(1 for s in sts if s.early and s.early.is_early),
            "sol_usd": e.sol_price if e else None,
            "refresh": _refresh_info(e),
            "feeds": e.feeds() if e else {},
            "server_time": now,
            "version": VERSION,
        }

    @app.get("/api/home")
    async def home():
        """The 4 home groups. Grouping reads existing results only (scoring.groups)."""
        sts, now = states(), time.time()
        buckets: dict[str, list] = {g: [] for g in GROUPS + ("quiet",)}
        for s in sts:
            buckets.setdefault(s.group or "nodata", []).append(s)
        newest = lambda s: -(s.mc_track.first_seen if s.mc_track else s.info.discovered_at)   # noqa: E731
        buckets["opportunity"].sort(key=confirm_key)
        buckets["watch"].sort(key=lambda s: (-(s.early.fired_count if s.early else 0), newest(s)))
        buckets["nodata"].sort(key=newest)
        buckets["excluded"].sort(key=newest)
        pre = sorted((s for s in sts if s.pre_early is not None and s.pre_early.is_pre_early),
                     key=lambda s: (-s.pre_early.fired, s.pre_early.age_min or 9))
        return {"server_time": now,
                "pre_early": [card(s) for s in pre[:30]],
                "counts": {g: len(v) for g, v in buckets.items()} | {"pre_early": len(pre)},
                "groups": {g: [card(s) for s in buckets[g][:HOME_LIMIT[g]]] for g in GROUPS}}

    @app.get("/api/list/{kind}")
    async def list_kind(kind: str, limit: int = 200):
        if kind not in LIST_KINDS:
            return JSONResponse({"error": "unknown_list"}, status_code=404)
        sts, limit = states(), max(1, min(limit, 400))
        if kind == "top":
            rows = rank_opportunities(sts)
        elif kind == "early":
            rows = rank_early(sts)
        elif kind == "new":
            # every discovered token, newest first — also the ones DexScreener has not priced yet (rate-limited,
            # not indexed): they show UNKNOWN market values instead of disappearing (production incident, Render)
            def age_key(s):
                return s.age_minutes if s.age_minutes is not None else (time.time() - s.info.discovered_at) / 60
            rows = sorted(sts, key=age_key)
        elif kind == "whales":
            rows = [s for s in sts if s.holders]
        elif kind == "dev":
            rows = [s for s in sts if s.dev]
        else:
            rows = [s for s in sts if s.market and (s.info.twitter or s.info.telegram or s.info.website)]
        return {"kind": kind, "count": len(rows), "server_time": time.time(), "items": [card(s) for s in rows[:limit]]}

    @app.get("/api/narratives")
    async def narratives():
        rows = aggregate_narratives(states())
        for r in rows:
            r["label"] = t(f"narrative.{r['tag']}")
        return {"items": rows}

    @app.get("/api/events")
    async def events(limit: int = 100):
        e = eng()
        evs = list(reversed(e.events))[: max(1, min(limit, 300))] if e else []
        from alerts.report import event_text
        return {"items": [{"ts": x.ts, "mint": x.mint, "symbol": x.symbol, "type": t(f"event.{x.type}"),
                           "detail": event_text(x)[1], "severity": x.severity} for x in evs]}

    def find(mint: str):
        e = eng()
        return (e.tracked.get(mint) if e else None) or next((s for s in states() if s.mint == mint), None)

    @app.get("/api/token/{mint}")
    async def token(mint: str):
        if not MINT_RE.match(mint):
            return JSONResponse({"error": "bad_mint"}, status_code=400)
        st = find(mint)
        if not st:
            return JSONResponse({"error": "not_tracked", "mint": mint}, status_code=404)
        e = eng()
        evs = e.db.recent_events(50, mint) if e else None
        return view(st, evs or st.recent_events)

    @app.get("/api/token/{mint}/history")
    async def history(mint: str, hours: float = 24):
        if not MINT_RE.match(mint):
            return JSONResponse({"error": "bad_mint"}, status_code=400)
        e = eng()
        rows = e.db.snapshots(mint, since=time.time() - max(0.5, min(hours, 72)) * 3600) if e else []
        pts = [{"ts": r["ts"], "mc": r["mc"] if r["dq_status"] != "INVALID" else None,
                "liq": r["liquidity"] if r["dq_status"] != "INVALID" else None,
                "vol5m": r["vol_5m"] if r["dq_status"] != "INVALID" else None, "holders": r["holders"],
                "opp": r["score"], "risk": r["risk"], "early": r["early_signal"]} for r in rows]
        return {"mint": mint, "points": pts[-500:]}

    @app.post("/api/token/{mint}/refresh")
    async def refresh(mint: str):
        if not MINT_RE.match(mint):
            return JSONResponse({"error": "bad_mint"}, status_code=400)
        if not guard.allow_refresh(mint):
            return JSONResponse({"error": "rate_limited"}, status_code=429)
        st = await eng().analyze_one(mint)
        return view(st)

    @app.post("/api/watch")
    async def watch_sync(request: Request):
        """The phone's localStorage watchlist is the source of truth; the server just tracks those mints."""
        body = await _json(request)
        mints = body.get("mints") if isinstance(body, dict) else None
        if not isinstance(mints, list):
            return JSONResponse({"error": "bad_body"}, status_code=400)
        ok = [m for m in dict.fromkeys(mints) if isinstance(m, str) and MINT_RE.match(m)][:MAX_WATCH]
        bad = [m for m in mints if not (isinstance(m, str) and MINT_RE.match(m))]
        e = eng()
        new = [m for m in ok if m not in e.watch]
        if new:
            asyncio.create_task(_add_watch(e, new))
        return {"accepted": ok, "rejected": bad[:10], "added": new}

    @app.post("/api/watch/remove")
    async def watch_remove(request: Request):
        body = await _json(request)
        mint = body.get("mint") if isinstance(body, dict) else None
        if not (isinstance(mint, str) and MINT_RE.match(mint)):
            return JSONResponse({"error": "bad_mint"}, status_code=400)
        eng().remove_watch(mint)
        return {"removed": mint}

    @app.get("/api/watch")
    async def watch_list(mints: str = ""):
        wanted = [m for m in mints.split(",") if MINT_RE.match(m)][:MAX_WATCH]
        items = []
        for m in wanted:
            st = find(m)
            items.append(card(st) if st else {"mint": m, "pending": True})
        return {"items": items}

    # ------------------------------------------------------------------ 4 early tiers
    @app.get("/api/early/{tier}")
    async def early_tier(tier: str, limit: int = 200):
        sts, now = states(), time.time()
        limit = max(1, min(limit, 400))
        if tier == "pre_early":
            order = {"PRE_EARLY": 0, "NOT_YET": 1, "UNKNOWN": 2, "BLOCKED": 3}
            rows = [s for s in sts if s.pre_early is not None and s.pre_early.status in order]
            rows.sort(key=lambda s: (order[s.pre_early.status], -s.pre_early.fired, s.pre_early.age_min or 9))
            items = [card(s) for s in rows[:limit]]
        elif tier == "watch":
            items = [card(s) for s in select_watch(sts)]
        elif tier == "signal":                   # Early Signal (D1–D8) exactly as computed — not re-ranked or filtered
            items = [card(s) for s in rank_early(sts)[:limit]]
        elif tier == "trade":
            b = state["bot"]
            items = []
            for s, rec, holding in (b.trade_candidates(now) if b else []):
                c = card(s)
                c["trade"] = {"opportunity": rec.get("opportunity"), "confidence": rec.get("confidence"),
                              "components": rec.get("components"), "why": rec.get("why", [])[:6],
                              "invalidate": rec.get("invalidate", [])[:6], "size_usd": rec.get("size_usd"),
                              "holding": holding, "state": rec.get("state")}
                items.append(c)
        else:
            return JSONResponse({"error": "unknown_tier"}, status_code=404)
        return {"tier": tier, "count": len(items), "server_time": now, "items": items}

    # ------------------------------------------------------------------ PAPER trading bot (no real execution)
    @app.get("/api/bot")
    async def bot_api():
        b = state["bot"]
        return bot_status(b, eng()) if b else JSONResponse({"error": "bot_not_started"}, status_code=503)

    @app.get("/api/research/summary")
    async def research_summary():
        r = state["bot"].recorder if state["bot"] else None
        return r.summary() if r else JSONResponse({"error": "research_log_off"}, status_code=404)

    @app.get("/api/research/export")
    async def research_export(table: str, since: float = 0.0, limit: int = 200000):
        """CSV download of one research table (Render's disk is ephemeral: export to keep the dataset)."""
        from research.dataset import EXPORT_TABLES, export_csv
        r = state["bot"].recorder if state["bot"] else None
        if r is None:
            return JSONResponse({"error": "research_log_off"}, status_code=404)
        if table not in EXPORT_TABLES:
            return JSONResponse({"error": "bad_table", "tables": sorted(EXPORT_TABLES)}, status_code=400)
        from fastapi.responses import StreamingResponse
        return StreamingResponse(export_csv(r.path, table, since, max(1, min(limit, 1_000_000))), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="{table}.csv"'})

    @app.get("/api/bot/module/{key}")
    async def bot_module(key: str):
        b = state["bot"]
        d = module_detail(b, key) if b else None
        return d if d else JSONResponse({"error": "unknown_module"}, status_code=404)

    @app.get("/api/bot/decision/{mint}")
    async def bot_decision(mint: str):
        if not MINT_RE.match(mint):
            return JSONResponse({"error": "bad_mint"}, status_code=400)
        b = state["bot"]
        d = b.decisions.get(mint) if b else None
        return d if d else JSONResponse({"error": "no_decision", "mint": mint}, status_code=404)

    @app.post("/api/bot/kill")
    async def bot_kill(request: Request):
        body = await _json(request)
        if not isinstance(body, dict) or not isinstance(body.get("engaged"), bool):
            return JSONResponse({"error": "bad_body"}, status_code=400)
        state["bot"].set_kill(body["engaged"])
        return {"kill_switch": state["bot"].cfg.kill_switch}

    @app.get("/api/bot/pending")
    async def bot_pending():
        b = state["bot"]
        return {"items": list(b.pending.values()) if b else []}

    @app.post("/api/bot/approve/{oid}")
    async def bot_approve(oid: str):
        b = state["bot"]
        if not b or not b.approve(oid):
            return JSONResponse({"error": "not_pending_or_expired"}, status_code=404)
        if b.jupiter is not None:
            await b.execute_intents()
        else:
            b.tick()
        return {"approved": oid}

    @app.post("/api/bot/dismiss/{oid}")
    async def bot_dismiss(oid: str):
        b = state["bot"]
        return {"dismissed": bool(b and b.dismiss(oid))}

    @app.post("/api/bot/mode")
    async def bot_mode(request: Request):
        body = await _json(request)
        mode = body.get("mode") if isinstance(body, dict) else None
        try:
            state["bot"].set_mode(str(mode), str(body.get("confirm", "")) if isinstance(body, dict) else "")
        except ModeNotAllowed as e:
            return JSONResponse({"error": "mode_not_allowed", "detail": str(e)}, status_code=403)
        return {"mode": state["bot"].mode, "execution": state["bot"].cfg.mode}

    # ------------------------------------------------------------------ PWA static files
    @app.get("/i18n/vi.json")
    async def i18n_vi():
        return load_lang("vi")

    for name, mime in (("manifest.webmanifest", "application/manifest+json"), ("sw.js", "text/javascript"),
                       ("apple-touch-icon.png", "image/png"), ("favicon.png", "image/png")):
        app.add_api_route(f"/{name}", _static_file(name, mime), methods=["GET"])

    @app.get("/")
    async def index():
        return FileResponse(STATIC / "index.html", media_type="text/html",
                            headers={"Cache-Control": "no-cache"})

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


def _static_file(name: str, mime: str):
    async def handler():
        extra = {"Cache-Control": "no-cache"} if name in ("sw.js", "manifest.webmanifest") else {}
        return FileResponse(STATIC / name, media_type=mime, headers=extra)
    return handler


def _refresh_info(e: ScannerEngine | None) -> dict:
    """Target vs MEASURED refresh intervals (seconds) — shown on the status page."""
    if not e:
        return {}
    from scanner.scheduler import DEV_S, HOLDER_S, MARKET_S
    tiers = e.clock.stats()
    hot = sum(1 for s in e.published if s.priority_reasons)
    return {"tiers": tiers, "market_s": MARKET_S, "holders_s": HOLDER_S, "dev_s": DEV_S, "hot_tokens": hot,
            "deep_max_per_min": e.settings.deep_max_per_min}


async def _add_watch(e: ScannerEngine, mints: list[str]) -> None:
    for m in mints:
        with contextlib.suppress(Exception):
            await e.add_watch(m)


async def _json(request: Request):
    try:
        return await request.json()
    except Exception:
        return None


def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "no-referrer"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=(), payment=()"
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; connect-src 'self'; "
        "manifest-src 'self'; worker-src 'self'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
    if "cache-control" not in resp.headers and resp.headers.get("content-type", "").startswith("application/json"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


app = create_app()   # uvicorn entry: `uvicorn web.app:app`  (scanner starts in the lifespan)
