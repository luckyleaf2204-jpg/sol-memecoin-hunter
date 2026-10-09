"""Live recorder for the smart-money research (docs/smart_money_plan.md): pump.fun bonding-curve trades decoded from
the program's own event logs on the public Solana RPC. Read-only: it only listens; nothing is signed or sent.

Stored in SQLite (compact: mints / wallets interned to integer ids):
  trades(ts, slot, mint_id, wallet_id, is_buy, sol_lamports, token_raw, recv_ms)   one row per TradeEvent >= MIN_SOL
      ts = on-chain block time (s, 1 s resolution); recv_ms = when THIS recorder received it (ms; NULL for rows
      recorded before 2026-10-08 — never back-filled or guessed)
  completes(mint_id, ts, recv_ms)          curve completed (migration to PumpSwap), on-chain time + receipt time
  gaps(start, end, reason)                 RECORDER_GAP: time without a live stream (process down, sleep, reconnect)
  recorder_events(ts, kind, detail)        start / connect / disconnect / stop / finished
  rpc_checks(...)                          RPC_COMPLETENESS samples (see completeness_check)
  recovered_events(...)                    events found only by the completeness check — kept APART, never used by
                                           the analysis (the experiment's data stays the live stream)
Instrumentation only: none of it changes which trades are stored or how they are analysed.
"""
from __future__ import annotations

import asyncio
import base64
import collections
import hashlib
import json
import os
import sqlite3
import struct
import time
import urllib.request
from pathlib import Path

PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WS_URL = "wss://api.mainnet-beta.solana.com"
HTTP_URL = "https://api.mainnet-beta.solana.com"
TRADE_DISC = hashlib.sha256(b"event:TradeEvent").digest()[:8]
COMPLETE_DISC = hashlib.sha256(b"event:CompleteEvent").digest()[:8]
MIN_SOL_LAMPORTS = 1_000_000          # 0.001 SOL: dust is not stored
FLUSH_S = 2.0
SILENCE_GAP_S = 30.0                  # no message for this long while connected (sleep / stall) -> RECORDER_GAP
STARTUP_GAP_MIN_S = 5.0
CHECK_EVERY_S = 300.0                 # completeness check cadence
CHECK_AGE_S = 60.0                    # check a window this old (delivery has had time to arrive)
SIG_KEEP_S = 900.0
MAX_RECOVER = 20
MIN_CHECKS = 12                       # successful samples before RPC_COMPLETENESS is reported
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

SCHEMA = """
CREATE TABLE IF NOT EXISTS names (id INTEGER PRIMARY KEY, kind TEXT NOT NULL, key TEXT NOT NULL, UNIQUE(kind, key));
CREATE TABLE IF NOT EXISTS trades (ts INTEGER NOT NULL, slot INTEGER, mint_id INTEGER NOT NULL, wallet_id INTEGER NOT NULL,
                                   is_buy INTEGER NOT NULL, sol_lamports INTEGER NOT NULL, token_raw INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS completes (mint_id INTEGER PRIMARY KEY, ts INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS gaps (start REAL NOT NULL, end REAL NOT NULL);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS recorder_events (ts REAL NOT NULL, kind TEXT NOT NULL, detail TEXT);
CREATE TABLE IF NOT EXISTS rpc_checks (ts REAL NOT NULL, anchor TEXT, slot_lo INTEGER, slot_hi INTEGER,
                                       expected INTEGER, received INTEGER, missing INTEGER, recovered_tx INTEGER,
                                       recovered_events INTEGER, error TEXT);
CREATE TABLE IF NOT EXISTS recovered_events (ts INTEGER, slot INTEGER, signature TEXT, kind TEXT, mint TEXT,
                                             wallet TEXT, is_buy INTEGER, sol_lamports INTEGER, token_raw INTEGER,
                                             found_at REAL);
CREATE INDEX IF NOT EXISTS ix_trades_mint_ts ON trades(mint_id, ts);
CREATE INDEX IF NOT EXISTS ix_trades_wallet ON trades(wallet_id);
"""
ADDED_COLUMNS = (("trades", "recv_ms", "INTEGER"), ("completes", "recv_ms", "INTEGER"), ("gaps", "reason", "TEXT"))


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


def pid_alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":
        import ctypes
        k = ctypes.windll.kernel32
        h = k.OpenProcess(0x1000, False, int(pid))            # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        ok = k.GetExitCodeProcess(h, ctypes.byref(code))
        k.CloseHandle(h)
        return bool(ok) and code.value == 259                  # STILL_ACTIVE
    try:
        os.kill(int(pid), 0)
        return True
    except OSError:
        return False


class Store:
    """The recorder's SQLite store. Its connection is OWNED by the thread that created it (the event-loop thread);
    sqlite3 refuses use from any other thread, so worker threads never receive a Store (see completeness_loop)."""
    def __init__(self, path: Path):
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)
        for table, col, typ in ADDED_COLUMNS:                  # additive migration; existing rows keep NULL
            if col not in {r[1] for r in self.db.execute(f"PRAGMA table_info({table})")}:
                self.db.execute(f"ALTER TABLE {table} ADD COLUMN {col} {typ}")
        self.db.commit()
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

    def add(self, ev: dict, slot: int | None, recv_ms: int | None = None) -> None:
        if ev["kind"] == "trade":
            if ev["sol"] < MIN_SOL_LAMPORTS or ev["token"] <= 0:
                return
            self.buf.append((ev["ts"], slot, self._id("mint", ev["mint"]), self._id("wallet", ev["wallet"]),
                             int(ev["is_buy"]), ev["sol"], ev["token"], recv_ms))
        elif ev["kind"] == "complete":
            self.done.append((self._id("mint", ev["mint"]), ev["ts"], recv_ms))

    def gap(self, start: float, end: float, reason: str | None = None) -> None:
        """RECORDER_GAP: never merged, edited or deleted."""
        self.db.execute("INSERT INTO gaps (start, end, reason) VALUES (?, ?, ?)", (start, end, reason))
        self.db.commit()

    def event(self, kind: str, detail: str = "") -> None:
        self.db.execute("INSERT INTO recorder_events VALUES (?, ?, ?)", (time.time(), kind, detail))
        self.db.commit()

    def meta(self, k: str, default=None):
        r = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return r[0] if r else default

    def set_meta(self, k: str, v) -> None:
        self.db.execute("INSERT INTO meta VALUES (?, ?) ON CONFLICT(k) DO UPDATE SET v=excluded.v", (k, str(v)))

    def flush(self, now: float | None = None) -> int:
        n = len(self.buf)
        if self.buf:
            self.db.executemany("INSERT INTO trades (ts, slot, mint_id, wallet_id, is_buy, sol_lamports, token_raw, "
                                "recv_ms) VALUES (?,?,?,?,?,?,?,?)", self.buf)
        if self.done:
            self.db.executemany("INSERT OR IGNORE INTO completes (mint_id, ts, recv_ms) VALUES (?, ?, ?)", self.done)
        self.set_meta("heartbeat", time.time() if now is None else now)
        self.db.commit()
        self.n_trades += n
        self.buf, self.done = [], []
        return n


def startup_gap(store: Store, now: float | None = None, reason: str = "process down") -> tuple | None:
    """At start: the time since the last sign of life (heartbeat; before the heartbeat existed, the last stored
    block time) is a RECORDER_GAP. Returns (start, end) or None."""
    now = time.time() if now is None else now
    hb = store.meta("heartbeat")
    if hb is not None:
        last, basis = float(hb), "since last heartbeat"
    else:
        r = store.db.execute("SELECT MAX(ts) FROM trades").fetchone()[0]
        if r is None:
            return None
        last, basis = float(r), "since last stored trade (no heartbeat yet)"
    if now - last <= STARTUP_GAP_MIN_S:
        return None
    if store.db.execute("SELECT 1 FROM gaps WHERE start<=? AND end>=?", (last + 1, now - 1)).fetchone():
        return None                                            # already logged (e.g. by hand before this code)
    store.gap(last, now, f"{reason} ({basis})")
    store.set_meta("heartbeat", now)                           # alive again: a quick second restart logs only its own gap
    store.db.commit()
    return last, now


class SigWindow:
    """Signatures received from the stream in the last SIG_KEEP_S (all, failed included) for completeness checks."""
    def __init__(self, keep_s: float = SIG_KEEP_S):
        self.keep_s, self.q, self.slot_of = keep_s, collections.deque(), {}

    def add(self, sig: str, slot: int | None, now: float) -> None:
        if sig and sig not in self.slot_of:
            self.q.append((now, sig))
            self.slot_of[sig] = slot
        while self.q and now - self.q[0][0] > self.keep_s:
            self.slot_of.pop(self.q.popleft()[1], None)

    def anchor(self, age_s: float, now: float) -> str | None:
        """The received signature closest to `age_s` ago."""
        best = None
        for t, sig in self.q:
            if now - t >= age_s:
                best = sig
            else:
                break
        return best


def rpc_call(method: str, params: list, url: str = HTTP_URL, timeout: float = 30.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                       "params": params}).encode(),
                                 headers={"Content-Type": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def completeness_probe(anchor: str | None, received, now: float, call=rpc_call,
                       max_recover: int = MAX_RECOVER) -> tuple[dict, list[tuple]]:
    """The NETWORK half of one RPC_COMPLETENESS sample; safe in a worker thread because it touches neither the Store
    (its SQLite connection belongs to the recorder's event-loop thread) nor the live SigWindow (`received` is a
    snapshot taken on that thread). Ask the RPC for the program's signatures just BEFORE `anchor` (a signature we
    received ~CHECK_AGE_S ago: a contiguous slot range); drop its lowest and highest slot (possibly partial);
    expected = the signatures left, received = those the stream delivered. Missing ones are fetched (up to
    max_recover) and decoded. Returns (rpc_checks row, recovered_events rows); errors go into the row, never raised."""
    row = {"ts": now, "anchor": anchor, "slot_lo": None, "slot_hi": None, "expected": 0, "received": 0, "missing": 0,
           "recovered_tx": 0, "recovered_events": 0, "error": None}
    recovered: list[tuple] = []
    try:
        if anchor is None:
            raise RuntimeError("no anchor signature yet")
        res = call("getSignaturesForAddress", [PUMP, {"before": anchor, "limit": 1000, "commitment": "confirmed"}])
        items = res.get("result") or []
        if not items:
            raise RuntimeError(f"empty getSignaturesForAddress: {res.get('error')}")
        slots = [x["slot"] for x in items]
        lo, hi = min(slots), max(slots)
        expected = [x["signature"] for x in items if lo < x["slot"] < hi]
        got = [s for s in expected if s in received]
        miss = [s for s in expected if s not in received]
        row.update(slot_lo=lo, slot_hi=hi, expected=len(expected), received=len(got), missing=len(miss))
        for sig in miss[:max_recover]:
            tx = call("getTransaction", [sig, {"maxSupportedTransactionVersion": 1, "commitment": "confirmed",
                                               "encoding": "json"}]).get("result")
            if not tx:
                continue
            row["recovered_tx"] += 1
            for line in ((tx.get("meta") or {}).get("logMessages") or []):
                ev = decode(line)
                if ev:
                    row["recovered_events"] += 1
                    recovered.append((ev["ts"], tx.get("slot"), sig, ev["kind"], ev["mint"], ev.get("wallet"),
                                      int(ev.get("is_buy", 0)), ev.get("sol"), ev.get("token"), now))
    except Exception as e:                                     # a failed check is recorded, never fatal
        row["error"] = f"{type(e).__name__}: {str(e)[:160]}"
    return row, recovered


def save_check(store: Store, row: dict, recovered: list[tuple] = ()) -> None:
    """The DATABASE half of a sample: runs on the Store's own (event-loop) thread."""
    if recovered:
        store.db.executemany("INSERT INTO recovered_events VALUES (?,?,?,?,?,?,?,?,?,?)", recovered)
    store.db.execute("INSERT INTO rpc_checks VALUES (?,?,?,?,?,?,?,?,?,?)", tuple(row.values()))
    store.db.commit()


def completeness_check(store: Store, sigs: SigWindow, now: float | None = None, call=rpc_call,
                       max_recover: int = MAX_RECOVER) -> dict:
    """One RPC_COMPLETENESS sample done synchronously on the CALLER's thread (which must own the Store). Never
    changes trades / selection: recovered events go to recovered_events only."""
    now = time.time() if now is None else now
    row, recovered = completeness_probe(sigs.anchor(CHECK_AGE_S, now), set(sigs.slot_of), now, call, max_recover)
    save_check(store, row, recovered)
    return row


def completeness_summary(db: sqlite3.Connection, min_checks: int = MIN_CHECKS) -> dict:
    """RPC_COMPLETENESS over every successful sample; UNKNOWN (never assumed 100 %) until at least `min_checks`
    successful samples exist (fixed before any sample: one hour of samples). The running ratio is reported apart as
    `provisional`."""
    exp, rec, n, err = db.execute("SELECT COALESCE(SUM(expected),0), COALESCE(SUM(received),0), "
                                  "SUM(error IS NULL AND expected>0), SUM(error IS NOT NULL) FROM rpc_checks").fetchone()
    rtx, rev = db.execute("SELECT COALESCE(SUM(recovered_tx),0), COALESCE(SUM(recovered_events),0) "
                          "FROM rpc_checks").fetchone()
    return {"checks_ok": n or 0, "checks_failed": err or 0, "expected": exp, "received": rec, "missing": exp - rec,
            "recovered_tx": rtx, "recovered_events": rev,
            "provisional": round(rec / exp, 4) if exp else None,
            "rpc_completeness": round(rec / exp, 4) if exp and (n or 0) >= min_checks else "UNKNOWN"}


async def record(store: Store, url: str = WS_URL, stop: asyncio.Event | None = None, log=print,
                 connect=None, sigs: SigWindow | None = None, clock=time.time) -> None:
    """Listen until stop: reconnect with backoff; every disconnect / silence is a RECORDER_GAP from the last message."""
    import websockets
    connect = connect or (lambda: websockets.connect(url, max_size=2 ** 24, ping_interval=20, ping_timeout=30))
    stop = stop or asyncio.Event()
    sigs = sigs if sigs is not None else SigWindow()
    last_msg, down_since, backoff, last_stat = clock(), None, 1.0, clock()
    while not stop.is_set():
        try:
            async with connect() as ws:
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "logsSubscribe",
                                          "params": [{"mentions": [PUMP]}, {"commitment": "confirmed"}]}))
                await ws.recv()                                  # subscription id
                now = clock()
                if down_since is not None and now - down_since > 1:
                    store.gap(down_since, now, "stream reconnect")
                down_since, backoff, last_msg = None, 1.0, now
                store.event("connect")
                log("[sm] stream connected")
                last_flush = now
                while not stop.is_set():
                    raw = await asyncio.wait_for(ws.recv(), 60)
                    now = clock()
                    if now - last_msg > SILENCE_GAP_S:           # e.g. the machine slept with the socket open
                        store.gap(last_msg, now, "stream silent (sleep / stall)")
                    last_msg = now
                    v = json.loads(raw).get("params", {}).get("result", {})
                    val, slot = v.get("value") or {}, (v.get("context") or {}).get("slot")
                    sigs.add(val.get("signature"), slot, now)
                    if val.get("err") is None:
                        recv_ms = int(now * 1000)
                        for line in val.get("logs") or []:
                            ev = decode(line)
                            if ev:
                                store.add(ev, slot, recv_ms)
                    if now - last_flush >= FLUSH_S:
                        store.flush(now)
                        last_flush = now
                    if now - last_stat >= 60:
                        last_stat = now
                        log(f"[sm] {time.strftime('%Y-%m-%d %H:%M:%S')} trades stored {store.n_trades:,}")
        except asyncio.CancelledError:
            raise
        except Exception as e:                                   # network / RPC: reconnect, keep the gap
            store.flush()
            if down_since is None:
                down_since = last_msg
                store.event("disconnect", f"{type(e).__name__}: {str(e)[:120]}")
            log(f"[sm] stream error {type(e).__name__}: {str(e)[:120]} -> reconnect in {backoff:.0f}s")
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)
    store.flush()


async def completeness_loop(store: Store, sigs: SigWindow, stop: asyncio.Event, every_s: float = CHECK_EVERY_S,
                            log=print, call=rpc_call) -> None:
    """Sample RPC completeness every `every_s`. Connection ownership: the anchor and the received-signature snapshot
    are taken here, on the event-loop thread; only completeness_probe (HTTP, no Store) runs in a worker thread; the
    rows are written back here. A failing sample is logged (recorder_events 'sampler_error' + an rpc_checks error
    row) and never ends the recorder."""
    while not stop.is_set():
        try:
            await asyncio.wait_for(stop.wait(), every_s)
        except asyncio.TimeoutError:
            pass
        if stop.is_set():
            break
        try:
            now = time.time()
            anchor, received = sigs.anchor(CHECK_AGE_S, now), frozenset(sigs.slot_of)
            row, recovered = await asyncio.to_thread(completeness_probe, anchor, received, now, call)
            save_check(store, row, recovered)
            log(f"[sm] completeness sample: expected {row['expected']} received {row['received']} "
                f"missing {row['missing']} recovered_tx {row['recovered_tx']} error {row['error']}")
        except asyncio.CancelledError:
            raise
        except Exception as e:                                 # a sampler bug must not stop the recording
            msg = f"{type(e).__name__}: {str(e)[:160]}"
            log(f"[sm] completeness sampler error (recording continues): {msg}")
            try:
                store.event("sampler_error", msg)
                save_check(store, {"ts": time.time(), "anchor": None, "slot_lo": None, "slot_hi": None,
                                   "expected": 0, "received": 0, "missing": 0, "recovered_tx": 0,
                                   "recovered_events": 0, "error": f"sampler: {msg}"})
            except Exception as e2:
                log(f"[sm] could not log the sampler error: {type(e2).__name__}: {e2}")


def estimate_storage(size_bytes: int, recorded_s: float, remaining_s: float, free_bytes: int) -> dict:
    """Linear projection from the observed bytes per recorded second."""
    rate = size_bytes / recorded_s if recorded_s > 0 else 0.0
    projected = size_bytes + rate * max(0.0, remaining_s)
    return {"mb_per_day": round(rate * 86400 / 1e6, 1), "projected_gb": round(projected / 1e9, 2),
            "free_gb_after": round((free_bytes - (projected - size_bytes)) / 1e9, 2),
            "margin_ratio": round(free_bytes / max(1.0, projected - size_bytes), 2)}
