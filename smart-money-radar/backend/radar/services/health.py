"""Writes per-source request statistics (radar.net.http.STATS) to api_sync_status."""
from __future__ import annotations

import asyncio
import datetime as dt
import logging

from ..context import Ctx
from ..net.http import STATS

log = logging.getLogger(__name__)
UTC = dt.timezone.utc


def _t(x):
    return dt.datetime.fromtimestamp(x, UTC) if x else None


async def flush_stats(ctx: Ctx) -> None:
    rows = []
    for source, st in list(STATS.items()):
        recent_err = st.last_error_at and (not st.last_ok_at or st.last_error_at > st.last_ok_at)
        status = "ok" if not recent_err else ("rate_limited" if (st.last_error or "").find("429") >= 0 else "error")
        rows.append((source, ctx.worker, status, _t(st.last_ok_at), _t(st.last_error_at), st.last_error, st.ok,
                     st.failed, st.rate_limited, {"retries": st.retries, "latency_ms": st.latency_ms_ewma}))
    if rows:
        await ctx.pool.executemany("""INSERT INTO api_sync_status(source, job, status, last_success_at, last_error_at,
                last_error, requests_ok, requests_failed, rate_limited, details, updated_at)
            VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,now()) ON CONFLICT (source, job) DO UPDATE SET
                status = EXCLUDED.status, last_success_at = EXCLUDED.last_success_at,
                last_error_at = EXCLUDED.last_error_at, last_error = EXCLUDED.last_error,
                requests_ok = EXCLUDED.requests_ok, requests_failed = EXCLUDED.requests_failed,
                rate_limited = EXCLUDED.rate_limited, details = EXCLUDED.details, updated_at = now()""", rows)


async def loop(ctx: Ctx, every: float = 30.0) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            await flush_stats(ctx)
        except Exception as e:
            log.warning("stats flush failed: %s", e)
