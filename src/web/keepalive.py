"""Keep-alive for the Render FREE plan: a free web service spins down after 15 minutes without inbound HTTP traffic;
the scanner and the paper bot then stop and the sample gets a GAP. The app pings its own PUBLIC /healthz (through
Render's edge, so it counts as inbound traffic) every 10 minutes.

On by default when Render provides RENDER_EXTERNAL_URL; KEEPALIVE=0 turns it off (e.g. on a paid plan, which never
spins down — then it is not needed). Only /healthz is called: no secret, no paper data.
"""
from __future__ import annotations

import asyncio
import os

KEEPALIVE_EVERY_S = 600.0


def keepalive_url() -> str | None:
    if os.environ.get("KEEPALIVE", "1") == "0":
        return None
    base = os.environ.get("RENDER_EXTERNAL_URL", "").strip().rstrip("/")
    return f"{base}/healthz" if base.startswith("https://") else None


async def ping(url: str, client=None) -> bool:
    import httpx
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=20.0) as c:
                r = await c.get(url)
        else:
            r = await client.get(url)
        return r.status_code == 200
    except Exception:
        return False


async def keepalive_loop(url: str, every: float = KEEPALIVE_EVERY_S, client=None) -> None:
    while True:
        await asyncio.sleep(every)
        await ping(url, client)
