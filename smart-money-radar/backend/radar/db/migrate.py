"""Versioned SQL migrations.

Files `migrations/NNN_name.sql` are applied in order inside a transaction, guarded by a
Postgres advisory lock so several containers can start at once safely.
Applied versions are recorded in `schema_migrations`.
"""
from __future__ import annotations

import logging
from pathlib import Path

import asyncpg

log = logging.getLogger(__name__)

MIGRATIONS_DIR = Path(__file__).resolve().parents[2] / "migrations"
_LOCK_ID = 727_271_001


async def migrate(conn: asyncpg.Connection, directory: Path = MIGRATIONS_DIR) -> list[str]:
    await conn.execute("SELECT pg_advisory_lock($1)", _LOCK_ID)
    try:
        await conn.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
            version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())""")
        done = {r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")}
        applied = []
        for f in sorted(directory.glob("*.sql")):
            version = f.stem
            if version in done:
                continue
            log.info("applying migration %s", version)
            async with conn.transaction():
                await conn.execute(f.read_text(encoding="utf-8"))
                await conn.execute("INSERT INTO schema_migrations(version) VALUES ($1)", version)
            applied.append(version)
        return applied
    finally:
        await conn.execute("SELECT pg_advisory_unlock($1)", _LOCK_ID)
