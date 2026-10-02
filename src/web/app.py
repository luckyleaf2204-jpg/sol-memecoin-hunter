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
VERSION = "web-15"
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


def create_app(engine: ScannerEngine | None = None, start_scanner: bool = True,
               access_code: str | None = None, bot: PaperBot | None = None) -> FastAPI:
    set_language("vi")
    guard = Guard(access_code if access_code is not None else env("APP_ACCESS_CODE"))
    state: dict = {"engine": engine, "task": None, "started_at": time.time(), "bot": bot, "bot_task": None,
                   "bot_stop": None}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["engine"] is None:
            state["engine"] = ScannerEngine(Settings.load(), Database(DB_PATH), on_log=lambda m: print(m, flush=True))
        if state["bot"] is None:                         # PAPER trading bot: reads scanner results only
            persist = start_scanner
            cfg_path = DATA_DIR / "trading.json"
            state["bot"] = PaperBot(state["engine"], TradingConfig.load(cfg_path) if persist else TradingConfig(),
                                    state_path=DATA_DIR / "paper_bot.json" if persist else None,
                                    config_path=cfg_path if persist else None)
        if start_scanner:
            from trading.jupiter import JupiterQuotes
            state["bot"].jupiter = JupiterQuotes(state["engine"].http)      # paper BUYs on real Jupiter quotes
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
            ep = state["bot"].begin_sample()
            print(f"[startup] commit {ep['commit']} · params {ep['fingerprint']} · strategy {ep['strategy_version']} "
                  f"· sample epoch since {ep['started_at_utc']}", flush=True)
            state["task"] = asyncio.create_task(state["engine"].run())
            state["bot_stop"] = asyncio.Event()
            state["bot_task"] = asyncio.create_task(state["bot"].run(state["bot_stop"]))
        yield
        if state["bot_task"]:
            state["bot_stop"].set()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state["bot_task"], 10)
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
        out = {"ok": True, "commit": git_commit()}
        if bot is not None:
            out["params"] = bot.cfg.sample_id()
            ep = bot.sample_epoch
            out["sample_epoch"] = {"strategy_version": ep.strategy_version, "fingerprint": ep.fingerprint,
                                   "started_at_utc": ep.as_dict()["started_at_utc"]}
        return out

    # ------------------------------------------------------------------ API
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
