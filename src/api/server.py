"""Local READ-ONLY JSON API (stdlib only, bound to 127.0.0.1). No trading, no keys, no writes.

  GET /api/health                 source health + counts + last cycle time
  GET /api/tokens?status=VALID&limit=100   all tracked tokens (summary rows)
  GET /api/tokens/<mint>          full detail: metrics (value/source/ts/age/confidence), sub-scores,
                                  risk factors, early signal, lifecycle, events
  GET /api/top?limit=20           Top Opportunities (VALID only, ranked)
  GET /api/early?limit=20         Early-signal ranking
  GET /api/events?limit=100       recent events
  GET /api/narratives             narrative tag aggregates over tracked tokens
"""
from __future__ import annotations

import json
import threading
import time
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable
from urllib.parse import parse_qs, urlparse

from analytics.narratives import aggregate_narratives
from api.serialize import detail, summary  # noqa: F401  (re-exported for backwards compatibility)
from core.models import TokenState
from scoring.ranking import rank_early, rank_opportunities


class ApiServer:
    def __init__(self, get_states: Callable[[], list[TokenState]], get_events: Callable[[], list],
                 get_health: Callable[[], dict], port: int = 8765, host: str = "127.0.0.1"):
        self.get_states, self.get_events, self.get_health = get_states, get_events, get_health
        self.host, self.port = host, port
        self.httpd: ThreadingHTTPServer | None = None

    def route(self, path: str, query: dict) -> tuple[int, object]:
        limit = int((query.get("limit") or ["100"])[0])
        states = self.get_states()
        if path == "/api/health":
            return 200, self.get_health()
        if path == "/api/tokens":
            status = (query.get("status") or [None])[0]
            rows = [summary(s) for s in states if not status or s.dq_status == status.upper()]
            return 200, rows[:limit]
        if path.startswith("/api/tokens/"):
            mint = path.rsplit("/", 1)[-1]
            st = next((s for s in states if s.mint == mint), None)
            return (200, detail(st)) if st else (404, {"error": "token not tracked", "mint": mint})
        if path == "/api/top":
            return 200, [summary(s) for s in rank_opportunities(states)[:limit]]
        if path == "/api/early":
            return 200, [summary(s) for s in rank_early(states)[:limit]]
        if path == "/api/events":
            return 200, [asdict(e) for e in list(reversed(self.get_events()))[:limit]]
        if path == "/api/narratives":
            return 200, aggregate_narratives(states)
        return 404, {"error": "unknown endpoint",
                     "endpoints": ["/api/health", "/api/tokens", "/api/tokens/<mint>", "/api/top", "/api/early",
                                   "/api/events", "/api/narratives"]}

    def start(self) -> None:
        api = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802
                u = urlparse(self.path)
                try:
                    code, body = api.route(u.path.rstrip("/") or "/", parse_qs(u.query))
                except Exception as e:  # never crash the server thread
                    code, body = 500, {"error": f"{type(e).__name__}: {e}"}
                data = json.dumps(body, default=str, ensure_ascii=False).encode("utf-8")
                self.send_response(code)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *args):
                pass

        self.httpd = ThreadingHTTPServer((self.host, self.port), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def stop(self) -> None:
        if self.httpd:
            self.httpd.shutdown()
            self.httpd.server_close()
