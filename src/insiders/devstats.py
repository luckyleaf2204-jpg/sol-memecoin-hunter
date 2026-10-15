"""A coin creator's track record for the page (read-only): SOL balance, how many coins it created (pump.fun creates
found through Helius' parsed transactions), how many of its recent coins died (rug) and how they did one hour after
launch (GeckoTerminal candles). Worked out in a background thread and cached: a dev's numbers for DEV_TTL_S, a coin's
for good once its first hour is over. Needs the server's Helius key; without it the page shows nothing."""
from __future__ import annotations

import json
import statistics
import threading
import time
import urllib.error
import urllib.request

PUMP_PROGRAM = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
GT = "https://api.geckoterminal.com/api/v2/networks/solana"
DEV_TTL_S = 6 * 3600
SAMPLE = 10                      # recent coins looked at for the rug / first-hour numbers (~25 s each: GeckoTerminal)
MAX_DEVS, MAX_COINS = 400, 3000                                  # kept in the saved state
DEAD_MC_USD = 5_000              # below the launch market cap of a pump.fun coin
DEAD_OF_ATH = 0.10               # or down to 10 % of its peak
GT_GAP_S = 2.2                   # GeckoTerminal allows ~30 calls a minute


def _get(url: str, timeout: int = 30):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def created_coins(page: list, dev: str) -> list[tuple[str, float]]:
    """(mint, created_ts) of the pump.fun creates paid by `dev` in Helius' parsed transactions, newest first."""
    out, seen = [], set()
    for x in page or []:
        if x.get("type") != "CREATE" or x.get("feePayer") != dev or x.get("transactionError"):
            continue
        mint = None
        for ins in x.get("instructions") or []:
            if ins.get("programId") == PUMP_PROGRAM and ins.get("accounts"):
                mint = ins["accounts"][0]                   # the create's first account is the new mint
                break
        if not mint:
            mint = next((t.get("mint") for t in x.get("tokenTransfers") or [] if t.get("mint")), None)
        if mint and mint not in seen:
            seen.add(mint)
            out.append((mint, float(x.get("timestamp") or 0)))
    return out


def first_hour(candles: list, created: float) -> dict | None:
    """Launch price (first minute candle) and price one hour later from GeckoTerminal ohlcv rows
    [ts, open, high, low, close, volume]; None without a candle near the launch."""
    rows = sorted((c for c in candles or [] if c and c[0] >= created - 120), key=lambda c: c[0])
    if not rows or rows[0][0] > created + 900:
        return None
    start = float(rows[0][1])
    upto = [c for c in rows if c[0] <= created + 3600]
    h1 = float(upto[-1][4]) if upto else None
    peak = max(float(c[2]) for c in upto) if upto else None
    if not start or not h1:
        return None
    return {"start": start, "h1": h1, "mult_1h": round(h1 / start, 3), "peak_mult_1h": round(peak / start, 3) if peak else None}


class DevStats:
    def __init__(self, helius_key: str | None, rpc, pump, get=_get, now=time.time, sleep=time.sleep, state=None):
        self.key, self.rpc, self.pump, self.get, self.now, self.sleep = helius_key, rpc, pump, get, now, sleep
        box = state.setdefault("devstats", {"devs": {}, "coins": {}}) if state is not None else {"devs": {}, "coins": {}}
        self.devs: dict[str, dict] = box["devs"]                 # in the watcher's saved state: survives a restart
        self.coins: dict[str, dict] = box["coins"]
        self.todo: list[str] = []
        self.lock = threading.Lock()
        self.stat = {"done": 0, "errors": 0, "last_error": None}

    def stats(self, dev: str) -> dict:
        """Cached numbers of a dev ({'pending': True} while being worked out); asks for a refresh when stale."""
        with self.lock:
            d = self.devs.get(dev)
            if (d is None or self.now() - d["ts"] > DEV_TTL_S) and dev not in self.todo:
                self.todo.append(dev)
        return d["stats"] if d else {"pending": True}

    def _gt(self, url: str):
        for k in range(3):
            try:
                out = self.get(url)
                self.sleep(GT_GAP_S)
                return out
            except urllib.error.HTTPError as e:
                if e.code == 404:
                    return None
                self.sleep(10 * (k + 1) if e.code == 429 else 3)
            except Exception:
                self.sleep(3)
        return None

    def coin(self, mint: str, created: float) -> dict:
        hit = self.coins.get(mint)
        if hit:
            return hit
        out = {"mint": mint, "created_ts": created}
        info = self.pump.coin(mint) if self.pump else None
        if info:
            out.update(mc_now=info.get("mcap_usd"), ath=info.get("ath_mcap_usd"), complete=info.get("complete"),
                       symbol=info.get("symbol"))
        mc, ath = out.get("mc_now"), out.get("ath")
        if mc is not None:
            out["dead"] = mc < DEAD_MC_USD or bool(ath and mc <= DEAD_OF_ATH * ath)
        pools = (self._gt(f"{GT}/tokens/{mint}/pools?page=1") or {}).get("data") or []
        pool = next((p for p in pools if ((p.get("relationships") or {}).get("dex") or {}).get("data", {}).get("id") == "pump-fun"),
                    pools[-1] if pools else None)
        if pool:
            addr = (pool.get("attributes") or {}).get("address")
            o = self._gt(f"{GT}/pools/{addr}/ohlcv/minute?aggregate=1&before_timestamp={int(created) + 3660}&limit=80&currency=usd")
            fh = first_hour(((o or {}).get("data") or {}).get("attributes", {}).get("ohlcv_list"), created)
            if fh:
                out.update(fh)
        if self.now() > created + 3700 and "mc_now" in out:
            self.coins[mint] = out                             # the first hour is history now: keep it
        return out

    def compute(self, dev: str) -> dict:
        bal = None
        try:
            bal = ((self.rpc("getBalance", [dev]) or {}).get("value") or 0) / 1e9
        except Exception:
            pass
        page = self.get(f"https://api.helius.xyz/v0/addresses/{dev}/transactions?api-key={self.key}&type=CREATE&limit=100")
        made = created_coins(page, dev)
        sample = [self.coin(m, ts) for m, ts in made[:SAMPLE]]
        judged = [c for c in sample if "dead" in c]
        hour = [c for c in sample if c.get("mult_1h")]
        mults = sorted(c["mult_1h"] for c in hour)
        return {"balance_sol": round(bal, 3) if bal is not None else None, "created": len(made),
                "created_more": len(page or []) >= 100, "last_created": made[0][1] if made else None,
                "sample": len(sample), "judged": len(judged), "dead": sum(c["dead"] for c in judged),
                "graduated": sum(1 for c in judged if c.get("complete")),
                "hour_n": len(hour), "up2x_1h": sum(m >= 2 for m in mults), "up_1h": sum(m > 1 for m in mults),
                "median_1h": round(statistics.median(mults), 2) if mults else None,
                "best_ath": max((c.get("ath") or 0 for c in sample), default=None) or None,
                "coins": [{k: c.get(k) for k in ("mint", "symbol", "created_ts", "mc_now", "ath", "dead", "mult_1h", "complete")}
                          for c in sample[:8]]}

    def run_forever(self, stop: threading.Event) -> None:
        while not stop.is_set():
            with self.lock:
                dev = self.todo.pop() if self.todo else None      # the newest ask first: the coin just opened
            if not dev or not self.key:
                stop.wait(3)
                continue
            try:
                s = self.compute(dev)
                with self.lock:
                    self.devs[dev] = {"ts": self.now(), "stats": s}
                    for box, cap in ((self.devs, MAX_DEVS), (self.coins, MAX_COINS)):
                        if len(box) > cap:
                            for k in sorted(box, key=lambda k: box[k].get("ts") or box[k].get("created_ts") or 0)[:len(box) - cap]:
                                del box[k]
                self.stat["done"] += 1
            except Exception as e:                              # one dev never stops the queue
                self.stat["errors"] += 1
                self.stat["last_error"] = type(e).__name__
                with self.lock:
                    self.devs[dev] = {"ts": self.now() - DEV_TTL_S + 600, "stats": {"error": True}}   # retry in 10 min
