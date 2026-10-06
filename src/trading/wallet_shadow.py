"""Read-only, paper-only follower for one Solana wallet. Never creates or sends transactions."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import time
from pathlib import Path

from trading.jupiter import WSOL, JupiterQuotes

TARGET = "BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"
LAMPORTS = 1_000_000_000


class WalletShadow:
    def __init__(self, rpc, http, path: Path):
        self.rpc, self.quotes, self.path = rpc, JupiterQuotes(http), Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
          CREATE TABLE IF NOT EXISTS shadow_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS shadow_positions (mint TEXT PRIMARY KEY, symbol TEXT, token_raw INTEGER NOT NULL,
            decimals INTEGER NOT NULL, cost_lamports INTEGER NOT NULL DEFAULT 0, source_raw INTEGER NOT NULL DEFAULT 0);
          CREATE TABLE IF NOT EXISTS shadow_events (signature TEXT PRIMARY KEY, ts REAL, side TEXT, mint TEXT,
            source_sol REAL, paper_sol REAL, token_raw INTEGER, pnl_sol REAL, status TEXT, quote_ms REAL);
        """)
        self.db.commit()
        self.started = time.time()
        self.last_error = ""
        self.last_poll = 0.0
        self.running = False

    def _meta(self, key):
        row = self.db.execute("SELECT value FROM shadow_meta WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

    def _set_meta(self, key, value):
        self.db.execute("INSERT INTO shadow_meta VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
        self.db.commit()

    def status(self):
        events = [dict(x) for x in self.db.execute("SELECT * FROM shadow_events ORDER BY ts DESC LIMIT 100")]
        positions = [dict(x) for x in self.db.execute("SELECT * FROM shadow_positions WHERE token_raw > 0 ORDER BY mint")]
        realized = sum((x["pnl_sol"] or 0) for x in self.db.execute("SELECT pnl_sol FROM shadow_events WHERE side='SELL' AND pnl_sol IS NOT NULL"))
        return {"target": TARGET, "mode": "PAPER ONLY", "running": self.running, "started_at": self.started,
                "last_poll": self.last_poll, "last_error": self.last_error, "realized_sol": realized,
                "events": events, "positions": positions, "coverage": self._meta("coverage") or "BOOTSTRAPPING"}

    async def run(self, stop: asyncio.Event):
        self.running = True
        try:
            while not stop.is_set():
                try:
                    await self.poll_once()
                    self.last_error = ""
                except Exception as exc:
                    self.last_error = type(exc).__name__
                try:
                    await asyncio.wait_for(stop.wait(), timeout=2.0)
                except asyncio.TimeoutError:
                    pass
        finally:
            self.running = False

    async def poll_once(self):
        sigs = await self.rpc.signatures(TARGET, limit=100)
        self.last_poll = time.time()
        if not sigs:
            return
        known = self._meta("last_signature")
        # First start is a baseline. Do not pretend we were running before now.
        if known is None:
            self._set_meta("last_signature", sigs[0]["signature"])
            self._set_meta("coverage", "Watching new signatures from startup; earlier activity excluded")
            return
        fresh = []
        for row in sigs:
            if row.get("signature") == known:
                break
            if row.get("err") is None:
                fresh.append(row["signature"])
        for signature in reversed(fresh):
            tx = await self.rpc.transaction(signature)
            if not tx:
                break  # Keep the cursor behind this signature and retry; never silently skip an RPC gap.
            await self._process(signature, tx)
            self._set_meta("last_signature", signature)
        if len(sigs) == 100 and not any(x.get("signature") == known for x in sigs):
            self._set_meta("coverage", "GAP: source activity exceeded polling window; results may omit trades")

    async def _process(self, signature, tx):
        meta = tx.get("meta") or {}
        if meta.get("err") is not None:
            return
        msg = (tx.get("transaction") or {}).get("message") or {}
        keys = msg.get("accountKeys") or []
        wallet_idx = next((i for i, k in enumerate(keys) if (k.get("pubkey") if isinstance(k, dict) else k) == TARGET), None)
        if wallet_idx is None:
            return
        pre, post = meta.get("preBalances") or [], meta.get("postBalances") or []
        if wallet_idx >= len(pre) or wallet_idx >= len(post):
            return
        sol_delta = (pre[wallet_idx] - post[wallet_idx] - int(meta.get("fee") or 0)) / LAMPORTS
        before, after = {}, {}
        for side, dest in (("preTokenBalances", before), ("postTokenBalances", after)):
            for row in meta.get(side) or []:
                if row.get("owner") == TARGET:
                    amt = row.get("uiTokenAmount") or {}
                    dest[row["mint"]] = (int(amt.get("amount") or 0), int(amt.get("decimals") or 0))
        deltas = [(mint, after.get(mint, (0, 0))[0] - before.get(mint, (0, 0))[0],
                   after.get(mint, before.get(mint, (0, 0)))[1]) for mint in set(before) | set(after)]
        deltas = [(m, d, dec) for m, d, dec in deltas if d and m != WSOL]
        if len(deltas) != 1 or abs(sol_delta) < 0.00005:
            return
        mint, token_delta, decimals = deltas[0]
        buy = token_delta > 0 and sol_delta > 0
        sell = token_delta < 0 and sol_delta < 0
        if not (buy or sell):
            return
        if self.db.execute("SELECT 1 FROM shadow_events WHERE signature=?", (signature,)).fetchone():
            return
        ts = float(tx.get("blockTime") or time.time())
        start = time.perf_counter()
        if buy:
            spend = int(sol_delta * LAMPORTS)
            qr = await self.quotes.quote_result(WSOL, mint, spend, 300, attempts=1, budget_s=2.5)
            if not qr.ok:
                self._event(signature, ts, "BUY", mint, sol_delta, None, token_delta, None, qr.status, (time.perf_counter()-start)*1000)
                return
            bought = int(qr.quote["outAmount"])
            self.db.execute("""INSERT INTO shadow_positions(mint,symbol,token_raw,decimals,cost_lamports,source_raw)
                VALUES(?,? ,?,?,?,?) ON CONFLICT(mint) DO UPDATE SET token_raw=token_raw+excluded.token_raw,
                cost_lamports=cost_lamports+excluded.cost_lamports,source_raw=source_raw+excluded.source_raw""",
                (mint, "", bought, decimals, spend, token_delta))
            self._event(signature, ts, "BUY", mint, sol_delta, sol_delta, bought, None, "PAPER_QUOTED", (time.perf_counter()-start)*1000)
        else:
            pos = self.db.execute("SELECT * FROM shadow_positions WHERE mint=?", (mint,)).fetchone()
            if not pos or pos["source_raw"] <= 0 or pos["token_raw"] <= 0:
                self._event(signature, ts, "SELL", mint, -sol_delta, None, -token_delta, None, "NO_MATCHING_PAPER_POSITION", (time.perf_counter()-start)*1000)
                return
            fraction = min(1.0, abs(token_delta) / pos["source_raw"])
            raw_out = max(1, min(pos["token_raw"], int(pos["token_raw"] * fraction)))
            qr = await self.quotes.quote_result(mint, WSOL, raw_out, 300, attempts=1, budget_s=2.5)
            if not qr.ok:
                self._event(signature, ts, "SELL", mint, -sol_delta, None, raw_out, None, qr.status, (time.perf_counter()-start)*1000)
                return
            proceeds = int(qr.quote["outAmount"])
            cost = int(pos["cost_lamports"] * raw_out / pos["token_raw"])
            self.db.execute("UPDATE shadow_positions SET token_raw=token_raw-?, cost_lamports=cost_lamports-?, source_raw=MAX(0,source_raw-?) WHERE mint=?",
                            (raw_out, cost, abs(token_delta), mint))
            self._event(signature, ts, "SELL", mint, -sol_delta, proceeds/LAMPORTS, raw_out,
                        (proceeds-cost)/LAMPORTS, "PAPER_QUOTED", (time.perf_counter()-start)*1000)
        self.db.commit()

    def _event(self, signature, ts, side, mint, source_sol, paper_sol, token_raw, pnl, status, ms):
        self.db.execute("INSERT OR IGNORE INTO shadow_events VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (signature, ts, side, mint, source_sol, paper_sol, token_raw, pnl, status, ms))
        self.db.commit()

    def close(self):
        self.db.close()
