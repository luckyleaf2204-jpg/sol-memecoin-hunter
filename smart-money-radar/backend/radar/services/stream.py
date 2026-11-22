"""Real-time ingestion: Solana `logsSubscribe` on the pump.fun and PumpSwap programs.

Every successful transaction touching either program arrives with its logs; the events are
decoded locally (radar.decoder.logs). For each trade we:
  * update 1-minute price bars built from on-chain executions (token_prices, source
    'onchain_trades') — the price history used for ROI horizons and 2x..10x checks,
  * update the token's last price / market cap / liquidity / curve progress,
  * store the trade when the wallet is tracked, the trade is >= stream_store_min_sol, or it
    happened in the token's creation slot (bundle analysis),
  * for tracked / smart wallets: queue full verification (getTransaction) and push the trade
    to the signal engine.
Failed transactions (err != null) are ignored here: they move no funds.
Gaps (disconnects) are recorded; the periodic wallet sync re-reads tracked wallets so no
tracked trade is lost.
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import json
import logging
import time
from collections import defaultdict
from typing import Any

import websockets

from ..context import K_DIRTY_WALLETS, Q_VERIFY, S_SMART_TRADES, Ctx
from ..db import repo
from ..decoder.logs import records_from_logs
from ..decoder.models import TradeRecord
from ..solana.base58 import b58encode
from ..solana.constants import PUMP_AMM_PROGRAM, PUMP_INITIAL_REAL_TOKEN, PUMP_PROGRAM
from ..net.http import stats_for

log = logging.getLogger(__name__)
UTC = dt.timezone.utc
# accounts that appear as trade "users" but are protocol agents, never wallets
PROTOCOL_TRADERS = {"BwWK17cbHxwWBKZkUYvzxLcNQ1YVyaFezduWbtm2de6s"}  # pump.fun Mayhem-mode agent (no fees, not a signer)


class BarAggregator:
    def __init__(self, min_trade_sol: float = 0.3):
        self.min_trade = min_trade_sol
        self.bars: dict[tuple[str, int], dict[str, Any]] = {}

    def add(self, t: TradeRecord) -> None:
        price = t.effective_price_sol
        if not price or price <= 0 or t.token_amount <= 0:
            return
        minute = (t.block_time // 60) * 60
        k = (t.mint, minute)
        b = self.bars.get(k)
        if b is None:
            b = self.bars[k] = {"mint": t.mint, "bucket_start": dt.datetime.fromtimestamp(minute, UTC),
                                "source": "onchain_trades", "open": price, "high": price, "low": price,
                                "close": price, "high_confirmed": None, "volume_sol": 0.0, "buy_sol": 0.0,
                                "sell_sol": 0.0, "trades": 0, "liquidity_sol": t.liquidity_sol,
                                "mc_sol": t.market_cap_sol, "fees_sol": 0.0}
        b["high"] = max(b["high"], price)
        b["low"] = min(b["low"], price)
        b["close"] = price
        if t.sol_amount >= self.min_trade:
            b["high_confirmed"] = price if b["high_confirmed"] is None else max(b["high_confirmed"], price)
        b["volume_sol"] += t.sol_amount
        b["buy_sol" if t.tx_type == "buy" else "sell_sol"] += t.sol_amount
        b["fees_sol"] += t.fee_trading_sol
        b["trades"] += 1
        if t.liquidity_sol is not None:
            b["liquidity_sol"] = t.liquidity_sol
        if t.market_cap_sol is not None:
            b["mc_sol"] = t.market_cap_sol

    def drain(self) -> list[dict]:
        out = list(self.bars.values())
        self.bars.clear()
        return out


class FlowAggregator:
    """Per (mint, wallet) totals of EVERY trade (also those below the storage threshold)."""

    def __init__(self):
        self.rows: dict[tuple[str, str], list] = {}

    def add(self, t: TradeRecord) -> None:
        k = (t.mint, t.wallet)
        r = self.rows.get(k)
        ts = dt.datetime.fromtimestamp(t.block_time, UTC)
        if r is None:
            r = self.rows[k] = [t.mint, t.wallet, 0, 0, 0.0, 0.0, 0.0, 0, 0, ts, ts, t.slot]
        if t.tx_type == "buy":
            r[2] += t.token_amount_raw
            r[4] += t.sol_amount
            r[7] += 1
        else:
            r[3] += t.token_amount_raw
            r[5] += t.sol_amount
            r[8] += 1
        r[6] += t.fee_trading_sol
        r[10] = max(r[10], ts)

    def drain(self) -> list[tuple]:
        out = [tuple(r) for r in self.rows.values()]
        self.rows.clear()
        return out


def decode_pool_account(data_b64: str) -> tuple[str, str] | None:
    """PumpSwap Pool layout: disc(8) bump(1) index(2) creator(32) base_mint(32) quote_mint(32) ..."""
    d = base64.b64decode(data_b64)
    if len(d) < 107:
        return None
    return b58encode(d[43:75]), b58encode(d[75:107])


STREAM_IDLE_SECONDS = 45


class StreamIdle(Exception):
    """Websocket open but no message within STREAM_IDLE_SECONDS."""


class StreamService:
    def __init__(self, ctx: Ctx):
        self.ctx = ctx
        self.bars = BarAggregator()
        self.flows = FlowAggregator()
        self.reset_completeness = False
        self.market: dict[str, tuple] = {}
        self.pending_trades: list[TradeRecord] = []
        self.pending_creates: list[tuple] = []
        self.pending_completes: list = []
        self.pending_migrations: list[tuple] = []
        self.tracked: set[str] = set()
        self.smart: set[str] = set()
        self.verify_set: set[str] = set()
        self.excluded: set[str] = set()
        self.pools: dict[str, tuple[str, str]] = {}
        self.unknown_pools: set[str] = set()
        self.creation_slot: dict[str, int] = {}
        self.stats = defaultdict(int)
        self.watched: set[str] = set()
        self.pending_verify: list[tuple[str, bool]] = []
        self._pool_cache = None
        self.last_msg_at = 0.0
        self.connected_endpoint: str | None = None
        self.gaps: list[dict] = []
        self.store_min = 1.0
        self.discovery_min = 2.0

    # ---- helpers ----------------------------------------------------------------------
    async def refresh_sets(self) -> None:
        rows = await self.ctx.pool.fetch("SELECT address, status FROM wallets WHERE status IN "
                                         "('candidate','tracked','smart') AND NOT is_excluded")
        self.tracked = {r["address"] for r in rows}
        self.smart = {r["address"] for r in rows if r["status"] == "smart"}
        # candidates are verified by their own backfill (same transactions): verifying them here
        # too would spend the RPC budget twice
        self.verify_set = {r["address"] for r in rows if r["status"] in ("tracked", "smart")}
        self.watched = {r["address"] for r in await self.ctx.pool.fetch(
            "SELECT address FROM watched_wallets WHERE confirmed")}
        self.tracked |= self.watched
        self.verify_set |= self.watched
        s = await self.ctx.settings.get()
        self.excluded = set(s.get("excluded_wallets") or [])
        self.store_min = float(s["stream_store_min_sol"])
        self.discovery_min = float(s["discovery_min_buy_sol"])
        self.bars.min_trade = float(s["peak_min_trade_sol"])
        if not self.pools:
            for r in await self.ctx.pool.fetch("SELECT pool, base_mint, quote_mint FROM amm_pools"):
                self.pools[r["pool"]] = (r["base_mint"], r["quote_mint"])

    def resolve_pool(self, pool: str) -> tuple[str, str] | None:
        m = self.pools.get(pool)
        if m is None:
            self.unknown_pools.add(pool)
        return m

    async def resolve_unknown_pools(self) -> None:
        todo = list(self.unknown_pools)[:100]
        self.unknown_pools.difference_update(todo)
        if not todo:
            return
        try:
            accts = await self.ctx.rpc.get_multiple_accounts(todo)
        except Exception as e:
            log.warning("pool resolution failed: %s", e)
            self.unknown_pools.update(todo)
            return
        async with self.ctx.pool.acquire() as conn:
            for pool, a in zip(todo, accts):
                if not a or a.get("owner") != PUMP_AMM_PROGRAM:
                    continue
                mints = decode_pool_account(a["data"][0])
                if mints:
                    self.pools[pool] = mints
                    await repo.upsert_pool(conn, pool, mints[0], mints[1], "pool_account")

    # ---- message handling -------------------------------------------------------------
    def handle(self, value: dict, slot: int) -> None:
        self.stats["notifications"] += 1
        if value.get("err") is not None:
            self.stats["failed_tx"] += 1
            return
        sig = value.get("signature")
        logs = value.get("logs") or []
        out = records_from_logs(sig, slot, logs, self.resolve_pool)
        users = out.get("nonsol_users", [])
        if any(u in self.tracked for u in users):
            # a tracked / watched wallet traded a non-SOL quoted curve (e.g. USDC via a router):
            # the logs carry no SOL amount, so the full transaction is needed. Watched (KOL)
            # wallets skip the verification queue so their alert is not delayed.
            self.pending_verify.append((sig, any(u in self.watched for u in users)))
        if out["truncated"]:
            self.stats["truncated_logs"] += 1
        for c in out["creates"]:
            self.creation_slot[c.mint] = slot
            self.pending_creates.append((c, sig, slot))
        self.pending_completes.extend(out["completes"])
        for m in out["migrations"]:
            self.pools[m.pool] = (m.mint, "So11111111111111111111111111111111111111112")
            self.pending_migrations.append((m, sig))
        for t in out["trades"]:
            self.stats["trades"] += 1
            self.bars.add(t)
            if t.wallet not in PROTOCOL_TRADERS:
                self.flows.add(t)
            prog = None
            if t.venue == "pump_curve":
                rtok = t.extra.get("real_token_reserves")
                if rtok is not None:
                    prog = max(0.0, min(1.0, 1 - rtok / PUMP_INITIAL_REAL_TOKEN))
            self.market[t.mint] = (t.mint, dt.datetime.fromtimestamp(t.block_time, UTC), t.effective_price_sol,
                                   t.market_cap_sol, t.liquidity_sol, prog)
            if t.wallet in PROTOCOL_TRADERS or t.wallet in self.excluded:
                continue
            tracked = t.wallet in self.tracked
            if t.venue == "pump_curve":
                keep = t.sol_amount >= self.store_min or self.creation_slot.get(t.mint) == slot
            else:
                # PumpSwap is dominated by arbitrage / wash bots: keep only sizeable buys of
                # untracked wallets (enough for discovery); price bars still use every trade
                keep = t.tx_type == "buy" and t.sol_amount >= self.discovery_min
            if tracked or keep:
                self.pending_trades.append(t)
                if tracked:
                    self.stats["tracked_trades"] += 1

    async def flush(self) -> None:
        trades, self.pending_trades = self.pending_trades, []
        for sig, fast in self.pending_verify:
            if fast:
                asyncio.create_task(self._fast_verify(sig))
            else:
                await self.ctx.enqueue(Q_VERIFY, {"signature": sig, "reason": "nonsol_tracked"}, dedupe_key=sig)
        self.pending_verify = []
        creates, self.pending_creates = self.pending_creates, []
        completes, self.pending_completes = self.pending_completes, []
        migrations, self.pending_migrations = self.pending_migrations, []
        bars = self.bars.drain()
        flows = self.flows.drain()
        market = list(self.market.values())
        self.market.clear()
        sol_usd = await self.ctx.sol_usd()
        async with self.ctx.pool.acquire() as conn:
            if self.reset_completeness:
                # trades were missed during a disconnect: no earlier token is complete any more
                await conn.execute("UPDATE tokens SET stream_from_creation = false WHERE stream_from_creation")
                self.reset_completeness = False
            for c, sig, slot in creates:
                await repo.upsert_created_tokens(conn, [c], sig, slot)
                await conn.execute("UPDATE tokens SET stream_from_creation = true WHERE mint = $1", c.mint)
            await repo.upsert_flows(conn, flows)
            if completes:
                await repo.mark_completes(conn, completes)
            for m, sig in migrations:
                await repo.mark_migrations(conn, [m], sig)
            await repo.upsert_price_bars(conn, bars)
            await repo.update_token_market(conn, market)
            await repo.insert_stream_trades(conn, trades, sol_usd)
        for t in trades:
            if t.wallet in self.tracked:
                if t.wallet in self.verify_set:
                    await self.ctx.enqueue(Q_VERIFY, {"signature": t.signature, "reason": "tracked_trade"},
                                           dedupe_key=t.signature)
                await self.ctx.redis.sadd(K_DIRTY_WALLETS, t.wallet)
                await self.ctx.redis.xadd(S_SMART_TRADES, {"p": json.dumps({
                    "signature": t.signature, "event_key": t.event_key, "wallet": t.wallet, "mint": t.mint,
                    "tx_type": t.tx_type, "sol": t.sol_amount, "ts": t.block_time, "verification": t.verification_status,
                    "smart": t.wallet in self.smart, "watched": t.wallet in self.watched})},
                    maxlen=100_000, approximate=True)
        if len(self.creation_slot) > 50_000:
            self.creation_slot = dict(list(self.creation_slot.items())[-20_000:])

    # ---- loops ------------------------------------------------------------------------
    async def _listen(self, name: str, url: str) -> None:
        async with websockets.connect(url, max_size=2 ** 23, open_timeout=20, ping_interval=20, ping_timeout=30,
                                      max_queue=8192) as ws:
            for i, prog in enumerate((PUMP_PROGRAM, PUMP_AMM_PROGRAM)):
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": i + 1, "method": "logsSubscribe",
                                          "params": [{"mentions": [prog]}, {"commitment": "confirmed"}]}))
            self.connected_endpoint = name
            self.reset_completeness = True
            log.info("stream connected to %s", name)
            connected_at = time.time()
            while True:
                # watchdog: pump.fun logs arrive several times a second; a socket that is open but
                # silent (seen on publicnode: 14 min without a message, no close frame) is dropped
                # and the next endpoint is tried
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=STREAM_IDLE_SECONDS)
                except asyncio.TimeoutError:
                    self.stats["idle_reconnects"] += 1
                    log.warning("stream %s silent for %ss after %ss connected; reconnecting",
                                name, STREAM_IDLE_SECONDS, round(time.time() - connected_at))
                    raise StreamIdle(name) from None
                msg = json.loads(raw)
                params = msg.get("params")
                if not params:
                    continue
                res = params.get("result") or {}
                self.last_msg_at = time.time()
                try:
                    self.handle(res.get("value") or {}, int((res.get("context") or {}).get("slot") or 0))
                except Exception:  # never let one bad message kill the stream
                    self.stats["decode_errors"] += 1
                    log.exception("message handling failed")

    async def run_ws(self) -> None:
        backoff = 1.0
        while True:
            for name, url in self.ctx.rpc.ws_urls():
                t0 = time.time()
                try:
                    await self._listen(name, url)
                except Exception as e:
                    stats_for(f"ws_{name}").record_error(f"{type(e).__name__}: {e}")
                    log.warning("stream %s disconnected: %s", name, type(e).__name__)
                self.connected_endpoint = None
                self.gaps.append({"endpoint": name, "at": time.time(), "uptime_s": round(time.time() - t0)})
                self.gaps = self.gaps[-50:]
                if time.time() - t0 > 60:
                    backoff = 1.0
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    async def _fast_verify(self, sig: str) -> None:
        from .txproc import PoolCache
        from .verifier import verify_one
        if self._pool_cache is None:
            self._pool_cache = PoolCache()
            await self._pool_cache.load(self.ctx.pool)
        for attempt in range(6):          # the node may not serve a just-confirmed tx yet
            try:
                res = await verify_one(self.ctx, sig, self._pool_cache, self.tracked, self.store_min)
            except Exception as e:
                log.warning("fast verify %s: %s", sig, e)
                res = "error"
            if res not in ("not_found", "error"):
                return
            await asyncio.sleep(0.5 * (attempt + 1))
        await self.ctx.enqueue(Q_VERIFY, {"signature": sig, "reason": "nonsol_tracked"}, dedupe_key=sig)

    async def run_flusher(self) -> None:
        n = 0
        while True:
            await asyncio.sleep(2)
            try:
                if n % 15 == 0:
                    await self.refresh_sets()
                if self.unknown_pools:
                    await self.resolve_unknown_pools()
                await self.flush()
                if n % 5 == 0:
                    await self.report()
            except Exception:
                log.exception("flush failed")
            n += 1

    async def report(self) -> None:
        details = dict(self.stats)
        details.update({"endpoint": self.connected_endpoint, "last_message_age_s":
                        round(time.time() - self.last_msg_at, 1) if self.last_msg_at else None,
                        "tracked_wallets": len(self.tracked), "smart_wallets": len(self.smart),
                        "recent_gaps": self.gaps[-5:]})
        ok = self.connected_endpoint is not None and (time.time() - self.last_msg_at) < 60
        await self.ctx.pool.execute("""INSERT INTO api_sync_status(source, job, status, last_success_at, details, updated_at)
            VALUES ('solana_ws', 'logs_stream', $1, CASE WHEN $2 THEN now() END, $3, now())
            ON CONFLICT (source, job) DO UPDATE SET status = EXCLUDED.status,
                last_success_at = COALESCE(EXCLUDED.last_success_at, api_sync_status.last_success_at),
                details = EXCLUDED.details, updated_at = now()""", "ok" if ok else "degraded", ok, details)
        await repo.heartbeat(self.ctx.pool, self.ctx.worker, self.ctx.started_at, details)

    async def run(self) -> None:
        await self.refresh_sets()
        await asyncio.gather(self.run_ws(), self.run_flusher())


async def main(ctx: Ctx) -> None:
    await StreamService(ctx).run()
