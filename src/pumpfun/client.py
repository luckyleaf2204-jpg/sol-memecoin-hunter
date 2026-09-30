"""Pump.fun adapter.

Pump.fun has NO official public API. This uses the same frontend API the pump.fun
website calls (host frontend-api-v3.pump.fun). Verified working 2026-09-29:
  GET /coins?offset&limit&sort=created_timestamp|last_trade_timestamp&order=DESC&includeNsfw=false
  GET /coins?creator=<wallet>&...          (creator's tokens)
  GET /coins-v2/<mint>                     (single coin; the old /coins/<mint> returns 404)
It is unofficial, rate-limited (HTTP 429) and may change — everything that calls it
tolerates a None result, and discovery falls back to PumpPortal / DexScreener.
"""
from __future__ import annotations

import time
from urllib.parse import urlparse

from core.http import HttpClient
from core.models import TokenInfo

BASE_URL = "https://frontend-api-v3.pump.fun"
SOURCE = "pumpfun"
SOL_QUOTES = {"11111111111111111111111111111111", "So11111111111111111111111111111111111111112"}
INITIAL_REAL_TOKEN_RESERVES = 793_100_000  # UI units; curve completes when these reach 0
HEADERS = {"Origin": "https://pump.fun", "Referer": "https://pump.fun/", "Accept": "application/json"}


def _f(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def parse_coin(d: dict) -> TokenInfo | None:
    mint = d.get("mint")
    if not mint:
        return None
    dec = int(d.get("base_decimals") or 6)
    qdec = int(d.get("quote_decimals") or 9)
    quote_is_sol = (d.get("quote_mint") or "11111111111111111111111111111111") in SOL_QUOTES
    total = _f(d.get("total_supply"))
    rtr = _f(d.get("real_token_reserves"))
    rtr_ui = rtr / 10 ** dec if rtr is not None else None
    complete = bool(d.get("complete"))

    real_sol = virt_sol = None
    if quote_is_sol:
        vq = _f(d.get("virtual_quote_reserves"))
        if vq is None:
            vq = _f(d.get("virtual_sol_reserves"))
        virt_sol = vq / 10 ** qdec if vq is not None else None
        rq = _f(d.get("real_quote_reserves"))
        if rq is None:
            rq = _f(d.get("real_sol_reserves"))
        real_sol = rq / 10 ** qdec if rq is not None else None

    usd_mc = _f(d.get("usd_market_cap")) or _f(d.get("market_cap_usd"))
    mc_quote = _f(d.get("market_cap"))
    sol_price = usd_mc / mc_quote if quote_is_sol and usd_mc and mc_quote else None

    if complete:
        progress = 100.0
    elif rtr_ui is not None:
        progress = max(0.0, min(100.0, 100 * (1 - rtr_ui / INITIAL_REAL_TOKEN_RESERVES)))
    else:
        progress = None

    ts = _f(d.get("created_timestamp"))
    return TokenInfo(
        mint=mint,
        name=d.get("name") or "",
        symbol=d.get("symbol") or "",
        creator=d.get("creator") or "",
        created_at=ts / 1000 if ts else None,
        image=d.get("image_uri") or "",
        twitter=d.get("twitter") or "",
        telegram=d.get("telegram") or "",
        website=d.get("website") or "",
        bonding_curve=d.get("bonding_curve") or "",
        pool=d.get("pump_swap_pool") or (d.get("pool_address") if complete else "") or "",
        complete=complete,
        decimals=dec,
        total_supply=total / 10 ** dec if total else None,
        real_sol_reserves=real_sol,
        virtual_sol_reserves=virt_sol,
        real_token_reserves=rtr_ui,
        curve_progress=progress,
        pump_usd_mc=usd_mc,
        ath_usd_mc=_f(d.get("ath_market_cap")),
        sol_price=sol_price,
        pump_updated_at=time.time(),
        quote_mint=d.get("quote_mint") or "11111111111111111111111111111111",
        sources={SOURCE},
    )


class PumpFunClient:
    def __init__(self, http: HttpClient, base_url: str = BASE_URL):
        self.http = http
        self.base = base_url.rstrip("/")
        http.set_rate(urlparse(self.base).netloc, 40)  # stays under the observed 429 threshold

    async def _get(self, path: str, params: dict | None = None):
        return await self.http.get_json(f"{self.base}{path}", source=SOURCE, params=params,
                                        headers=HEADERS, retries=1)

    async def _list(self, **params) -> list[TokenInfo] | None:
        base = {"offset": 0, "limit": 50, "order": "DESC", "includeNsfw": "false"}
        data = await self._get("/coins", {**base, **params})
        if not isinstance(data, list):
            return None
        return [t for t in (parse_coin(d) for d in data if isinstance(d, dict)) if t]

    async def latest(self, limit: int = 50) -> list[TokenInfo] | None:
        return await self._list(sort="created_timestamp", limit=limit)

    async def recently_traded(self, limit: int = 50) -> list[TokenInfo] | None:
        return await self._list(sort="last_trade_timestamp", limit=limit)

    async def creator_coins(self, creator: str, limit: int = 50) -> list[TokenInfo] | None:
        return await self._list(creator=creator, sort="created_timestamp", limit=limit)

    async def coin(self, mint: str) -> TokenInfo | None:
        data = await self._get(f"/coins-v2/{mint}")
        return parse_coin(data) if isinstance(data, dict) else None
