"""pump.fun facts for the page (public frontend API, no key): a coin's creator (dev), its description and links,
and the pump.fun profile name of a wallet. Cached; a failure or 'not found' is remembered for a while so a busy page
never hammers the API. Everything returned is untrusted text: the page escapes it."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

API = "https://frontend-api-v3.pump.fun"
COIN_TTL_S = 600
USER_TTL_S = 86400
MISS_TTL_S = 600
TIMEOUT_S = 6


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    try:
        return json.loads(urllib.request.urlopen(req, timeout=TIMEOUT_S).read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None                                       # not a pump.fun coin / no profile
        raise


def _https(u) -> str | None:
    return u if isinstance(u, str) and u.startswith("https://") else None


def _public_https(u) -> str | None:
    """A metadata link set by the coin's creator: fetched only when it is https to a public host name (no IP literal,
    no localhost / internal names), so the server is never pointed at its own network."""
    import ipaddress
    from urllib.parse import urlsplit
    if not _https(u):
        return None
    host = (urlsplit(u).hostname or "").lower()
    if not host or "." not in host or host == "localhost" or host.endswith((".local", ".internal", ".localhost")):
        return None
    try:
        ipaddress.ip_address(host)
        return None
    except ValueError:
        return u


def _get_small(url: str, limit: int = 65536):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=TIMEOUT_S).read(limit))


class PumpInfo:
    def __init__(self, get=_get, now=time.time, get_meta=_get_small):
        self.get, self.now, self.get_meta = get, now, get_meta
        self.cache: dict[str, tuple[float, dict | None]] = {}
        self.stat = {"ok": 0, "missing": 0, "error": 0, "last_error": None}
        self.lock = threading.Lock()

    def _cached(self, key: str, ttl: float, fetch):
        with self.lock:
            hit = self.cache.get(key)
        if hit and self.now() < hit[0]:
            return hit[1]
        try:
            val = fetch()
            exp = self.now() + (ttl if val is not None else MISS_TTL_S)
            self.stat["ok" if val is not None else "missing"] += 1
        except Exception as e:                                # network / API trouble: try again later
            val, exp = (hit[1] if hit else None), self.now() + MISS_TTL_S
            self.stat["error"] += 1
            self.stat["last_error"] = type(e).__name__
        with self.lock:
            self.cache[key] = (exp, val)
            if len(self.cache) > 5000:
                for k in sorted(self.cache, key=lambda k: self.cache[k][0])[:1000]:
                    del self.cache[k]
        return val

    def coin(self, mint: str) -> dict | None:
        def fetch():
            d = self.get(f"{API}/coins-v2/{mint}")
            if not isinstance(d, dict) or not d.get("mint"):
                return None
            out = {"creator": d.get("creator"), "name": d.get("name"), "symbol": d.get("symbol"),
                   "description": (d.get("description") or "").strip()[:600] or None,
                   "twitter": _https(d.get("twitter")), "telegram": _https(d.get("telegram")),
                   "website": _https(d.get("website")), "complete": d.get("complete"),
                   "created_ts": (d.get("created_timestamp") or 0) / 1000 or None,
                   "mcap_usd": d.get("usd_market_cap"), "ath_mcap_usd": d.get("ath_market_cap")}   # both in USD
            meta = _public_https(d.get("metadata_uri"))
            if meta and not (out["description"] and out["website"] and out["telegram"]):
                try:
                    m = self.get_meta(meta) or {}
                    out["description"] = out["description"] or ((m.get("description") or "").strip()[:600] or None)
                    for k in ("twitter", "telegram", "website"):
                        out[k] = out[k] or _https(m.get(k))
                except Exception:
                    pass
            return out
        return self._cached("c:" + mint, COIN_TTL_S, fetch)

    def user(self, addr: str) -> dict | None:
        def fetch():
            d = self.get(f"{API}/users/{addr}")
            if not isinstance(d, dict) or not d.get("username"):
                return None
            return {"username": d.get("username"), "followers": d.get("followers"), "x": d.get("x_username"),
                    "bio": (d.get("bio") or "")[:200] or None}
        return self._cached("u:" + addr, USER_TTL_S, fetch)
