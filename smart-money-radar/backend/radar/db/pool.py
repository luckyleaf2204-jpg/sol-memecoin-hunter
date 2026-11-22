"""asyncpg connection pool + JSON codecs."""
from __future__ import annotations

import json
import logging

import asyncpg

log = logging.getLogger(__name__)


async def _init_conn(conn: asyncpg.Connection) -> None:
    for t in ("json", "jsonb"):
        await conn.set_type_codec(t, encoder=lambda v: json.dumps(v, default=str),
                                  decoder=json.loads, schema="pg_catalog")


async def create_pool(dsn: str, min_size: int = 1, max_size: int = 10) -> asyncpg.Pool:
    return await asyncpg.create_pool(dsn, min_size=min_size, max_size=max_size, init=_init_conn,
                                     command_timeout=120)
