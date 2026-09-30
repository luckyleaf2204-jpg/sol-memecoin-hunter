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
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from analytics.narratives import aggregate_narratives
from api.serialize import card, view
from core.config import DB_PATH, Settings, env
from database.db import Database
from i18n import load as load_lang, set_language, t
from scanner.engine import ScannerEngine
from scoring.ranking import rank_early, rank_opportunities

STATIC = Path(__file__).resolve().parent / "static"
MINT_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
AUTH_WINDOW_S, AUTH_MAX_FAILS = 600, 10
REFRESH_PER_MINT_S, REFRESH_GLOBAL_PER_MIN = 60, 10
MAX_WATCH = 30
LIST_KINDS = ("top", "new", "early", "whales", "dev", "social")
VERSION = "web-1"


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
               access_code: str | None = None) -> FastAPI:
    set_language("vi")
    guard = Guard(access_code if access_code is not None else env("APP_ACCESS_CODE"))
    state: dict = {"engine": engine, "task": None, "started_at": time.time()}

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["engine"] is None:
            state["engine"] = ScannerEngine(Settings.load(), Database(DB_PATH), on_log=lambda m: print(m, flush=True))
        if start_scanner:
            state["task"] = asyncio.create_task(state["engine"].run())
        yield
        eng = state["engine"]
        if state["task"]:
            eng.stop()
            with contextlib.suppress(Exception):
                await asyncio.wait_for(state["task"], 15)

    app = FastAPI(title="SOL Memecoin Hunter", docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.hunter = state
    app.state.guard = guard

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
        return {"ok": True}

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
            "version": VERSION,
        }

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
            rows = sorted((s for s in sts if s.market), key=lambda s: s.age_minutes if s.age_minutes is not None else 1e9)
        elif kind == "whales":
            rows = [s for s in sts if s.holders]
        elif kind == "dev":
            rows = [s for s in sts if s.dev]
        else:
            rows = [s for s in sts if s.market and (s.info.twitter or s.info.telegram or s.info.website)]
        return {"kind": kind, "count": len(rows), "items": [card(s) for s in rows[:limit]]}

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
