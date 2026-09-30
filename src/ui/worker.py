"""Runs the asyncio ScannerEngine in a QThread, bridges results to Qt signals, hosts the local API."""
from __future__ import annotations

import asyncio
import time

from PySide6.QtCore import QThread, Signal

from api.server import ApiServer
from core.config import Settings
from database.db import Database
from scanner.engine import ScannerEngine


class EngineWorker(QThread):
    updated = Signal(object)    # list[TokenState]
    events = Signal(object)     # list[Event]
    log = Signal(str)
    analyzed = Signal(object)   # TokenState from an on-demand analysis
    failed = Signal(str)

    def __init__(self, settings: Settings, db: Database):
        super().__init__()
        self.settings = settings
        self.db = db
        self.loop: asyncio.AbstractEventLoop | None = None
        self.engine: ScannerEngine | None = None
        self.api: ApiServer | None = None
        self.last_states: list = []

    def run(self) -> None:
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.engine = ScannerEngine(self.settings, self.db, on_update=self._updated, on_log=self.log.emit,
                                    on_events=self.events.emit)
        if self.settings.api_enabled:
            try:
                self.api = ApiServer(lambda: self.engine.published, lambda: self.engine.events, self._health,
                                     port=self.settings.api_port)
                self.api.start()
                self.log.emit(f"Local API: http://127.0.0.1:{self.settings.api_port}/api/health")
            except OSError as e:
                self.log.emit(f"Local API not started: {e}")
        try:
            self.loop.run_until_complete(self.engine.run())
        finally:
            if self.api:
                self.api.stop()
            self.loop.close()

    def _updated(self, states) -> None:
        self.last_states = states
        self.updated.emit(states)

    def _health(self) -> dict:
        e = self.engine
        return {"sources": {k: {"ok": v.ok, "requests": v.requests, "errors": v.errors, "last_ok": v.last_ok,
                                "last_error": v.last_error} for k, v in e.http.health.sources.items()},
                "helius": e.helius_state,
                "tracked": len(e.tracked), "cycle": e.cycle_no, "last_cycle_at": e.last_cycle_at,
                "now": time.time(), "sol_usd": e.sol_price}

    def stop(self) -> None:
        if self.loop and self.engine and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.engine.stop)

    def _submit(self, coro_fn) -> None:
        if not (self.loop and self.engine) or self.loop.is_closed():
            self.failed.emit("Scanner not running yet")
            return
        fut = asyncio.run_coroutine_threadsafe(coro_fn(self.engine), self.loop)

        def done(f):
            try:
                self.analyzed.emit(f.result())
            except Exception as e:
                self.failed.emit(f"{type(e).__name__}: {e}")

        fut.add_done_callback(done)

    def analyze(self, mint: str) -> None:
        self._submit(lambda e: e.analyze_one(mint))

    def add_watch(self, mint: str) -> None:
        self._submit(lambda e: e.add_watch(mint))

    def remove_watch(self, mint: str) -> None:
        if self.loop and self.engine and not self.loop.is_closed():
            self.loop.call_soon_threadsafe(self.engine.remove_watch, mint)

    def health(self) -> dict:
        return dict(self.engine.http.health.sources) if self.engine else {}

    def helius_state(self) -> str:
        return self.engine.helius_state.get("state", "—") if self.engine else "—"
