"""Shared runtime context for every worker: config, Postgres pool, Redis, RPC, HTTP, settings,
plus Redis queue helpers (streams with consumer groups + dedupe keys) and event publishing."""
from __future__ import annotations

import asyncio
import datetime as dt
import json
import logging
import socket
from dataclasses import dataclass, field
from typing import Any

import asyncpg
import redis.asyncio as aioredis

from .config import Config, get_config
from .db.migrate import migrate
from .db.pool import create_pool
from .logging_setup import setup_logging
from .net.http import HttpClient
from .settings import SettingsStore
from .solana.rpc import SolanaRpc

log = logging.getLogger(__name__)

EVENTS_CHANNEL = "radar:events"
Q_VERIFY = "q:verify"
Q_WALLET_SYNC = "q:wallet_sync"
Q_PRICE_BACKFILL = "q:price_backfill"
S_SMART_TRADES = "ev:smart_trades"
K_TRACKED = "set:tracked_wallets"
K_SMART = "set:smart_wallets"
K_DIRTY_WALLETS = "set:dirty_wallets"
K_SOL_USD = "price:sol_usd"


@dataclass
class Ctx:
    cfg: Config
    pool: asyncpg.Pool
    redis: Any
    rpc: SolanaRpc
    http: HttpClient
    settings: SettingsStore
    worker: str
    started_at: dt.datetime = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc))

    async def close(self) -> None:
        await self.rpc.close()
        await self.http.close()
        await self.pool.close()
        await self.redis.aclose()

    # ---- queues -----------------------------------------------------------------------
    async def enqueue(self, stream: str, payload: dict, dedupe_key: str | None = None, dedupe_ttl: int = 600) -> bool:
        if dedupe_key:
            ok = await self.redis.set(f"dd:{stream}:{dedupe_key}", "1", nx=True, ex=dedupe_ttl)
            if not ok:
                return False
        await self.redis.xadd(stream, {"p": json.dumps(payload)}, maxlen=200_000, approximate=True)
        return True

    async def consume(self, stream: str, group: str, count: int = 20, block_ms: int = 5000):
        consumer = f"{socket.gethostname()}-{self.worker}"
        try:
            await self.redis.xgroup_create(stream, group, id="0", mkstream=True)
        except Exception as e:  # BUSYGROUP
            if "BUSYGROUP" not in str(e):
                raise
        # first re-claim messages that were delivered but never acked (crashed worker)
        try:
            claimed = await self.redis.xautoclaim(stream, group, consumer, min_idle_time=120_000, start_id="0-0",
                                                  count=count)
            msgs = claimed[1] if claimed else []
            if msgs:
                return [(mid, json.loads(f["p"])) for mid, f in msgs if f and "p" in f]
        except Exception:
            pass
        res = await self.redis.xreadgroup(group, consumer, {stream: ">"}, count=count, block=block_ms)
        out = []
        for _s, msgs in res or []:
            for mid, f in msgs:
                try:
                    out.append((mid, json.loads(f["p"])))
                except (KeyError, ValueError):
                    await self.redis.xack(stream, group, mid)
        return out

    async def ack(self, stream: str, group: str, ids: list) -> None:
        if ids:
            await self.redis.xack(stream, group, *ids)

    async def publish(self, kind: str, data: dict) -> None:
        try:
            await self.redis.publish(EVENTS_CHANNEL, json.dumps({"type": kind, "data": data}, default=str))
        except Exception as e:  # pragma: no cover
            log.warning("publish failed: %s", e)

    async def sol_usd(self) -> float | None:
        try:
            v = await self.redis.get(K_SOL_USD)
            return float(v) if v else None
        except Exception:
            return None


async def create_ctx(worker: str, run_migrations: bool = False, pool_size: int = 8) -> Ctx:
    cfg = get_config()
    setup_logging(cfg.log_level, cfg.log_json, cfg.secret_values())
    if cfg.trading_enabled:  # pragma: no cover - hard guarantee
        raise SystemExit("trading is not supported")
    pool = None
    for attempt in range(30):
        try:
            pool = await create_pool(cfg.database_url, max_size=pool_size)
            break
        except (OSError, asyncpg.PostgresError) as e:
            log.warning("database not ready (%s), retrying", type(e).__name__)
            await asyncio.sleep(2)
    if pool is None:
        raise SystemExit("cannot connect to PostgreSQL")
    if run_migrations:
        async with pool.acquire() as conn:
            applied = await migrate(conn)
            if applied:
                log.info("migrations applied: %s", applied)
    r = aioredis.from_url(cfg.redis_url, decode_responses=True)
    for attempt in range(30):
        try:
            await r.ping()
            break
        except Exception:
            log.warning("redis not ready, retrying")
            await asyncio.sleep(2)
    rpc = SolanaRpc(cfg.rpc_endpoints, redis=r)
    rpc.tag = worker
    http = HttpClient(redis=r)
    return Ctx(cfg=cfg, pool=pool, redis=r, rpc=rpc, http=http, settings=SettingsStore(pool), worker=worker)
