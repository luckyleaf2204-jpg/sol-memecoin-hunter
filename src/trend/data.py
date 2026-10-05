"""GeckoTerminal public API (read-only, no key): token -> pool selection and hourly OHLCV, cached as CSV.
The public API serves the last 180 days and allows only a few requests per minute: every call is spaced and
retried with backoff on 429."""
from __future__ import annotations

import csv
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trend.universe import CANDIDATES, MIN_POOL_AGE_DAYS, MIN_RESERVE_USD, QUOTES

BASE = "https://api.geckoterminal.com/api/v2"
SPACING_S = 6.5                     # ~9 requests / minute
MAX_DAYS = 180


class Api:
    def __init__(self, spacing_s: float = SPACING_S, opener=None, sleep=time.sleep, log=print):
        self.spacing_s, self.sleep, self.log = spacing_s, sleep, log
        self.opener = opener or (lambda req: urllib.request.urlopen(req, timeout=30).read())
        self._last = 0.0
        self.calls = 0

    def get(self, path: str) -> dict:
        for attempt in range(6):
            wait = self._last + self.spacing_s - time.monotonic()
            if wait > 0:
                self.sleep(wait)
            self._last = time.monotonic()
            self.calls += 1
            try:
                raw = self.opener(urllib.request.Request(BASE + path, headers={"Accept": "application/json"}))
                return json.loads(raw)
            except urllib.error.HTTPError as e:
                if e.code == 429 or e.code >= 500:
                    self.sleep(min(90.0, 10.0 * 2 ** attempt))
                    continue
                raise
            except (urllib.error.URLError, TimeoutError):
                self.sleep(5.0 * (attempt + 1))
        raise RuntimeError(f"GeckoTerminal: giving up on {path}")


def select_pool(api: Api, symbol: str, mint: str, now: datetime) -> dict:
    """Largest pool of the token quoted in SOL / USDC / USDT, reserve >= MIN_RESERVE_USD, age >= MIN_POOL_AGE_DAYS.
    Returns {"symbol", "mint", "pool", ...} or {"dropped": reason}."""
    tok = api.get(f"/networks/solana/tokens/{mint}")
    api_symbol = ((tok.get("data") or {}).get("attributes") or {}).get("symbol") or ""
    if api_symbol.upper() != symbol.upper():
        return {"symbol": symbol, "mint": mint, "dropped": f"symbol mismatch (API says {api_symbol!r})"}
    pools = api.get(f"/networks/solana/tokens/{mint}/pools?page=1").get("data") or []
    cut = now - timedelta(days=MIN_POOL_AGE_DAYS)
    best = None
    for p in pools:
        a, r = p["attributes"], p["relationships"]
        base = r["base_token"]["data"]["id"].split("_", 1)[-1]
        quote = r["quote_token"]["data"]["id"].split("_", 1)[-1]
        if base != mint or quote not in QUOTES:
            continue
        reserve = float(a.get("reserve_in_usd") or 0)
        created = datetime.fromisoformat((a.get("pool_created_at") or "2100-01-01T00:00:00Z").replace("Z", "+00:00"))
        if reserve < MIN_RESERVE_USD or created > cut:
            continue
        if best is None or reserve > best["reserve_usd"]:
            best = {"symbol": symbol, "mint": mint, "pool": a["address"], "quote": QUOTES[quote],
                    "dex": r["dex"]["data"]["id"], "reserve_usd": reserve, "pool_created_at": a["pool_created_at"]}
    return best or {"symbol": symbol, "mint": mint,
                    "dropped": f"no SOL/USDC/USDT pool with reserve >= ${MIN_RESERVE_USD:,.0f} "
                               f"older than {MIN_POOL_AGE_DAYS} days"}


def fetch_ohlcv(api: Api, pool: str, now: datetime, days: int = MAX_DAYS) -> list[tuple]:
    """Hourly bars (ts, open, high, low, close, volume_usd), ascending, unique, last `days` days."""
    start = int((now - timedelta(days=days)).timestamp()) + 3600
    before, bars = int(now.timestamp()), {}
    while True:
        d = api.get(f"/networks/solana/pools/{pool}/ohlcv/hour?aggregate=1&limit=1000&currency=usd"
                    f"&before_timestamp={before}")
        rows = ((d.get("data") or {}).get("attributes") or {}).get("ohlcv_list") or []
        for ts, o, h, lo, c, v in rows:
            if ts >= start:
                bars[int(ts)] = (int(ts), float(o), float(h), float(lo), float(c), float(v))
        if not rows or min(r[0] for r in rows) <= start:
            break
        before = int(min(r[0] for r in rows))
    return [bars[k] for k in sorted(bars)]


def save_csv(path: Path, bars: list[tuple]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(("ts", "open", "high", "low", "close", "volume_usd"))
        w.writerows(bars)


def load_csv(path: Path) -> list[tuple]:
    with open(path, encoding="utf-8") as f:
        r = csv.reader(f)
        next(r)
        return [(int(a), float(b), float(c), float(d), float(e), float(g)) for a, b, c, d, e, g in r]


def build_dataset(out_dir: Path, now: datetime | None = None, api: Api | None = None, log=print) -> dict:
    """Select pools, download bars (cached: an existing CSV is reused), write universe.json."""
    now = now or datetime.now(timezone.utc)
    api = api or Api(log=log)
    out_dir.mkdir(parents=True, exist_ok=True)
    uni_path = out_dir / "universe.json"
    universe = json.loads(uni_path.read_text(encoding="utf-8")) if uni_path.exists() else None
    if universe is None:
        universe = {"built_at": now.isoformat(), "tokens": []}
        for sym, mint in CANDIDATES:
            sel = select_pool(api, sym, mint, now)
            what = sel.get("dropped") or f"{sel['pool']} {sel['dex']} reserve ${sel['reserve_usd']:,.0f}"
            log(f"[universe] {sym}: {what}")
            universe["tokens"].append(sel)
        uni_path.write_text(json.dumps(universe, indent=1), encoding="utf-8")
    for t in universe["tokens"]:
        if t.get("dropped"):
            continue
        p = out_dir / f"{t['symbol']}.csv"
        if p.exists():
            continue
        bars = fetch_ohlcv(api, t["pool"], datetime.fromisoformat(universe["built_at"]))
        save_csv(p, bars)
        log(f"[bars] {t['symbol']}: {len(bars)} hourly bars")
    return universe
