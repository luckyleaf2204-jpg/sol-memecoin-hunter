"""Live recorder for the smart-money research (docs/smart_money_plan.md): pump.fun bonding-curve trades decoded from
the program's own event logs on the public Solana RPC. Read-only: it only listens; nothing is signed or sent.

Stored in SQLite (compact: mints / wallets interned to integer ids):
  trades(ts, slot, mint_id, wallet_id, is_buy, sol_lamports, token_raw)   one row per TradeEvent >= MIN_SOL
  completes(mint_id, ts)                                                   curve completed (migration)
  gaps(start, end)                                                         time without a live stream
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import sqlite3
import struct
import time
from pathlib import Path

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WS_URL = "wss://api.mainnet-beta.solana.com"
TRADE_DISC = hashlib.sha256(b"event:TradeEvent").digest()[:8]
COMPLETE_DISC = hashlib.sha256(b"event:CompleteEvent").digest()[:8]
MIN_SOL_LAMPORTS = 1_000_000          # 0.001 SOL: dust is not stored
FLUSH_S = 2.0
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

SCHEMA = """
CREATE TABLE IF NOT EXISTS names (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, UNIQUE(kind, key));
CREATE TABLE IF NOT EXISTS trades (ts INTEGER NOT NULL, slot INTEGER, mint_id INTEGER NOT NULL, wallet_id INTEGER NOT NULL,
                                   is_buy INTEGER NOT NULL, sol_lamports INTEGER NOT NULL, token_raw INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS completes (mint_id INTEGER PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS gaps (start REAL NOT NULL, end REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE INDEX IF NOT EXISTS ix_trades_mint_ts ON trades(mint_id, ts);
CREATE INDEX IF NOT EXISTS ix_trades_wallet ON trades(wallet_id);
"""


def b58(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def decode(line: str) -> dict | None:
    """One 'Program data: <base64>' log line -> a trade / complete event, or None."""
    if not line.startswith("Program data: "):
        return None
    try:
        d = base64.b64decode(line[14:])
    except ValueError:
        return None
    if d[:8] == TRADE_DISC and len(d) >= 97:
        sol, tok = struct.unpack_from("<QQ", d, 40)
        ts = struct.unpack_from("<q", d, 89)[0]
        if not plausible_ts(ts):
            return None
        return {"kind": "trade", "mint": b58(d[8:40]), "sol": sol, "token": tok, "is_buy": bool(d[56]),
                "wallet": b58(d[57:89]), "ts": ts}
    if d[:8] == COMPLETE_DISC and len(d) >= 8 + 32 * 3 + 8:
        # CompleteEvent: user, mint, bonding_curve, timestamp
        ts = struct.unpack_from("<q", d, 104)[0]
        return {"kind": "complete", "mint": b58(d[40:72]), "ts": ts} if plausible_ts(ts) else None
    return None


def plausible_ts(ts: int) -> bool:
    """A block time that can be real: after 2024-01-01 and not more than a day ahead of this clock. A garbled event
    (seen live: ts = -2.9e17) would otherwise become MIN(ts) and end the recording window at once."""
    return 1_704_067_200 <= ts <= time.time() + 86400


class Store:
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path))
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        self.ids: dict[tuple, int] = {(k, key): i for i, k, key in self.db.execute("SELECT id, kind, key FROM names")}
        self.buf: list[tuple] = []
        self.done: list[tuple] = []
        self.n_trades = self.db.execute("SELECT COUNT(*) FROM trades").fetchone()[0]

    def _id(self, kind: str, key: str) -> int:
        i = self.ids.get((kind, key))
        if i is None:
            cur = self.db.execute("INSERT INTO names (kind, key) VALUES (?, ?)", (kind, key))
            i = self.ids[(kind, key)] = cur.lastrowid
        return i

    def add(self, ev: dict, slot: int | None) -> None:
        if ev["kind"] == "trade":
            if ev["sol"] < MIN_SOL_LAMPORTS or ev["token"] <= 0:
                return
            self.buf.append((ev["ts"], slot, self._id("mint", ev["mint"]), self._id("wallet", ev["wallet"]),
                             int(ev["is_buy"]), ev["sol"], ev["token"]))
        elif ev["kind"] == "complete":
            self.done.append((self._id("mint", ev["mint"]), ev["ts"]))

    def gap(self, start: float, end: float) -> None:
        self.db.execute("INSERT INTO gaps VALUES (?, ?)", (start, end))
        self.db.commit()

    def flush(self) -> int:
        n = len(self.buf)
        if self.buf:
            self.db.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", self.buf)
        if self.done:
            self.db.executemany("INSERT OR IGNORE INTO completes VALUES (?, ?)", self.done)
        self.db.commit()
        self.n_trades += n
        self.buf, self.done = [], []
        return n


async def record(store: Store, url: str = WS_URL, stop: asyncio.Event | None = None, log=print,
                 connect=None) -> None:
    """Listen forever (until stop): reconnect with backoff, record every connection gap."""
    import websockets
    connect = connect or (lambda: websockets.connect(url, max_size=2 ** 24, ping_interval=20, ping_timeout=30))
    stop = stop or asyncio.Event()
    down_since, backoff, last_stat = time.time(), 1.0, time.time()
    while not stop.is_set():
        try:
            async with connect() as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                          "params": [{"mentions": [PUMP]}, {"commitment": "confirmed"}]}))
                await ws.recv()                                  # subscription id
                if down_since is not None and time.time() - down_since > 1:
                    store.gap(down_since, time.time())
                down_since, backoff = None, 1.0
                log("[sm] stream connected")
                last_flush = time.time()
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), 60)
                    v = json.loads(raw).get("params", {}).get("result", {})
                    val, slot = v.get("value") or {}, (v.get("context") or {}).get("slot")
                    if val.get("err") is None:
                        for line in val.get("logs") or []:
                            ev = decode(line)
                            if ev:
                                store.add(ev, slot)
                    if time.time() - last_flush >= FLUSH_S:
                        store.flush()
                        last_flush = time.time()
                    if time.time() - last_stat >= 60:
                        last_stat = time.time()
                        log(f"[sm] {time.strftime('%Y-%m-%d %H:%M:%S')} trades stored {store.n_trades:,}")
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # network / RPC: reconnect, keep the gap
            store.flush()
            if down_since is None:
                down_since = time.time()
            log(f"[sm] stream error {type(e).__name__}: {str(e)[:120]} -> reconnect in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
    store.flush()
