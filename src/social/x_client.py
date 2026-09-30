"""X (Twitter) module — PHASE 2 (interface only).

Legal route: the official X API v2 recent search (GET https://api.x.com/2/tweets/search/recent)
with a Bearer token (X_BEARER_TOKEN in .env). This requires a paid X API tier; scraping
x.com is against X's Terms and is deliberately NOT implemented.
Planned metrics: mentions 5m/15m/1h for "$TICKER" / contract / name, unique authors,
engagement rate = (likes+reposts+replies)/followers, first poster, known KOL list.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class XActivity:
    available: bool = False
    mentions_5m: int | None = None
    mentions_1h: int | None = None
    note: str = "Phase 2 — needs X API bearer token (paid tier)"


class XClient:
    def __init__(self, bearer_token: str = ""):
        self.bearer_token = bearer_token

    async def activity(self, symbol: str, mint: str, name: str) -> XActivity:
        return XActivity()
