"""PumpPortal real-time data websocket (third-party, free data API).

wss://pumpportal.fun/api/data  ->  {"method": "subscribeNewToken"}
Verified 2026-09-29: emits one message per new Pump.fun token including the
creator wallet (traderPublicKey) and the creator's initial buy (initialBuy / solAmount).
PumpPortal asks clients to keep a single connection, which is what this does.
"""
from __future__ import annotations

import asyncio
import json
import time

from core.http import Health
from core.models import TokenInfo

WS_URL = "wss://pumpportal.fun/api/data"
SOURCE = "pumpportal"


def parse_event(d: dict) -> TokenInfo | None:
    if d.get("txType") != "create" or not d.get("mint"):
        return None
    return TokenInfo(
        mint=d["mint"],
        name=d.get("name") or "",
        symbol=d.get("symbol") or "",
        creator=d.get("traderPublicKey") or "",
        created_at=time.time(),
        bonding_curve=d.get("bondingCurveKey") or "",
        complete=False,
        dev_initial_buy=d.get("initialBuy"),
        dev_initial_sol=d.get("solAmount"),
        discovery_mc_sol=d.get("marketCapSol") if isinstance(d.get("marketCapSol"), (int, float)) else None,
        sources={SOURCE},
    )


class PumpPortalStream:
    def __init__(self, queue: asyncio.Queue, health: Health, on_log=None):
        self.queue = queue
        self.health = health
        self.connected = False
        self.on_log = on_log or (lambda m: None)
        self.events_total = 0
        self.event_times: list[float] = []     # receive times of the last minute (diagnostics)
        self.last_event_at: float | None = None
        self.connects = 0
        self.last_error = ""

    def events_last_min(self) -> int:
        now = time.time()
        self.event_times = [t for t in self.event_times if now - t < 60]
        return len(self.event_times)

    async def run(self, stop: asyncio.Event) -> None:
        import websockets  # imported lazily so the module can be tested without it

        backoff = 2
        while not stop.is_set():
            try:
                async with websockets.connect(WS_URL, ping_interval=20, open_timeout=15) as ws:
                    await ws.send(json.dumps({"method": "subscribeNewToken"}))
                    self.connected, backoff = True, 2
                    self.connects += 1
                    self.health.ok(SOURCE)
                    self.on_log(f"PumpPortal WS connected ({WS_URL}, subscribeNewToken, connection #{self.connects})")
                    while not stop.is_set():
                        try:
                            raw = await asyncio.wait_for(ws.recv(), timeout=60)
                        except asyncio.TimeoutError:
                            continue
                        try:
                            info = parse_event(json.loads(raw))
                        except ValueError:
                            continue
                        if info:
                            self.health.ok(SOURCE)
                            now = time.time()
                            self.events_total += 1
                            self.last_event_at = now
                            self.event_times.append(now)
                            if len(self.event_times) > 2000:
                                del self.event_times[:1000]
                            try:
                                self.queue.put_nowait(info)
                            except asyncio.QueueFull:
                                pass
            except Exception as e:  # network errors must never kill the scanner
                self.last_error = f"{type(e).__name__}: {e}"[:300]
                self.health.fail(SOURCE, self.last_error)
                self.on_log(f"PumpPortal WS error: {self.last_error} — reconnect in {backoff}s")
            if self.connected:
                self.on_log(f"PumpPortal WS disconnected — reconnect in {backoff}s")
            self.connected = False
            if not stop.is_set():
                try:
                    await asyncio.wait_for(stop.wait(), timeout=backoff)
                except asyncio.TimeoutError:
                    pass
                backoff = min(60, backoff * 2)
