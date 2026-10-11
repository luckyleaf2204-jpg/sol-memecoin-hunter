"""Price candles of a coin for the early-signal board (GeckoTerminal, no key): the bonding-curve pool (pump.fun) and the
main PumpSwap pool after the migration are merged into one series of USD prices per token. Cached briefly so the page
can refresh every 30 s without hammering the API (GeckoTerminal allows ~30 calls / minute)."""
from __future__ import annotations

import json
import threading
import time
import urllib.request

GT = "https://api.geckoterminal.com/api/v2/networks/solana"
POOLS_TTL_S = 600
CANDLES_TTL_S = 60


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


class Charts:
    def __init__(self, get=_get, now=time.time, sleep=time.sleep):
        self.get, self.now, self.sleep = get, now, sleep
        self.pools: dict[str, tuple[float, list]] = {}
        self.cache: dict[str, tuple[float, dict]] = {}
        self.lock = threading.Lock()

    def _pools(self, mint: str) -> list[dict]:
        hit = self.pools.get(mint)
        if hit and self.now() - hit[0] < POOLS_TTL_S:
            return hit[1]
        data = self.get(f"{GT}/tokens/{mint}/pools?page=1").get("data") or []
        out = []
        for p in data:
            a, r = p.get("attributes") or {}, p.get("relationships") or {}
            dex = ((r.get("dex") or {}).get("data") or {}).get("id")
            base = ((r.get("base_token") or {}).get("data") or {}).get("id", "")
            if base == f"solana_{mint}" and dex in ("pump-fun", "pumpswap", "raydium", "meteora", "raydium-clmm"):
                out.append({"address": a.get("address"), "dex": dex, "reserve": float(a.get("reserve_in_usd") or 0)})
        curve = [p for p in out if p["dex"] == "pump-fun"][:1]
        amm = sorted((p for p in out if p["dex"] != "pump-fun"), key=lambda p: -p["reserve"])[:1]
        sel = curve + amm
        self.pools[mint] = (self.now(), sel)
        return sel

    def candles(self, mint: str, created_ts: float | None = None) -> dict:
        """{"candles": [[ts, open, high, low, close, volume_usd]], "pools": [...], "agg": minutes} (USD per token)."""
        with self.lock:
            hit = self.cache.get(mint)
            if hit and self.now() - hit[0] < CANDLES_TTL_S:
                return hit[1]
        age_min = (self.now() - created_ts) / 60 if created_ts else 24 * 60
        agg = 1 if age_min <= 600 else 5 if age_min <= 3000 else 15     # <= 1 000 candles cover the coin's life
        merged: dict[int, list] = {}
        pools, err = [], None
        try:
            pools = self._pools(mint)
        except Exception as e:
            err = f"pools: {type(e).__name__}"
        for p in pools:
            for k in range(2):                                # one retry: GeckoTerminal answers 429 in bursts
                try:
                    d = self.get(f"{GT}/pools/{p['address']}/ohlcv/minute?aggregate={agg}&limit=1000&currency=usd")
                    break
                except Exception as e:
                    d, err = None, f"candles: {type(e).__name__}"
                    if k == 0:
                        self.sleep(2.5)
            for c in (((d or {}).get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []:
                ts = int(c[0])
                if ts not in merged or (c[5] or 0) > (merged[ts][5] or 0):
                    merged[ts] = [ts, *[float(x) for x in c[1:6]]]
        out = {"candles": [merged[k] for k in sorted(merged)], "pools": pools, "agg": agg, "error": err}
        prev = self.cache.get(mint, (0, {}))[1]
        if not out["candles"] and prev.get("candles"):        # keep the last good series on an API hiccup
            out = {**prev, "error": err}
        with self.lock:                                       # an error is retried sooner than a good answer
            self.cache[mint] = (self.now() - (CANDLES_TTL_S - 10 if err else 0), out)
        return out
