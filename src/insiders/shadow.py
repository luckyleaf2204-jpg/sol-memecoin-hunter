"""Shadow measurement of the special-wallet signals, live (research only: nothing is signed or sent).

Every signal is recorded the moment it happens, on ANY coin (not only the coins the channel later calls, which is
what biased the backtest on the 97 calls):
  * single: one listed wallet buys / receives >= MIN_SOL of a coin (one record per coin per 24 h; later wallets join it)
  * strong: >= 2 wallet families on the same coin (the vipstrong alert)
Each record keeps the tier of the wallet (main = >= 2 winning coins in history, watch = 1, none), the USD price
when it was seen (Jupiter price, polled every ~30 s), the best multiple within 15 min / 1 h / 4 h / 24 h, the dip
before the peak, and a $500 paper position run by fixed exit rules (expert note, section 4):
  take half at 2x (the rest's stop moves to break-even), stop -35 %, trail 30 % from the peak after the half,
  out after 30 min if never 1.5x, out after 60 min in any case, out when a signal wallet sold half, out on a crash.
Prices cost SLIP each way (no quote for a size: the pools are thin, 3 % is the expert's default for < $50k).
Polling every 30 s misses spikes between two polls: the multiples are lower bounds of what happened."""
from __future__ import annotations

import json
import os
import statistics
import threading
import time
import urllib.request
from collections import Counter

USD = float(os.environ.get("INSIDER_SHADOW_USD") or 500)
SLIP = float(os.environ.get("INSIDER_SHADOW_SLIP") or 0.03)
EVERY_S = int(os.environ.get("INSIDER_SHADOW_EVERY_S") or 30)
WINDOWS = {"15m": 900, "1h": 3600, "4h": 14400, "24h": 86400}
SINGLE_DEDUP_S = 86400
NO_PRICE_GIVEUP_S = 900
MAX_SIGNALS = 1500
TP_MULT, STOP_MULT, TRAIL, TIME_SOFT_S, SOFT_MULT, TIME_HARD_S, CRASH = 2.0, 0.65, 0.30, 1800, 1.5, 3600, 0.4


def _jup(ids: list[str]) -> dict:
    req = urllib.request.Request("https://lite-api.jup.ag/price/v3?ids=" + ",".join(ids),
                                 headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=20).read()) or {}


def wilson(k: int, n: int, z: float = 1.96) -> list | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    m = z * ((p * (1 - p) + z * z / (4 * n)) / n) ** 0.5
    return [round((c - m) / d, 2), round((c + m) / d, 2)]


class Shadow:
    def __init__(self, state: dict, prices=_jup, now=time.time, sold_half=None, meta=None, lock=None):
        self.s = state.setdefault("shadow", {"signals": [], "n": 0})
        self.prices, self.now = prices, now
        self.sold_half = sold_half or (lambda mint, wallet: False)     # ledger: has this wallet sold half of it?
        self.meta = meta or (lambda mint: {})
        self.lock = lock or threading.Lock()               # the watcher's lock: its state is saved under it
        self.stat = {"ticks": 0, "error": None, "last": None}

    # ---------------------------------------------------------------- recording
    def signal(self, kind: str, mint: str, wallet: str, ts: float | None, sol: float, tier: str,
               family: str | None = None) -> dict | None:
        now = self.now()
        ts = ts or now
        info = self.meta(mint) or {}                         # outside the lock (may fetch)
        with self.lock:
            sigs = self.s["signals"]
            if kind == "single":
                for x in sigs:
                    if x["kind"] == "single" and x["mint"] == mint and ts - x["ts"] < SINGLE_DEDUP_S:
                        if wallet not in {w["wallet"] for w in x["wallets"]}:
                            x["wallets"].append({"wallet": wallet, "tier": tier, "sol": sol, "ts": ts})
                        return None
            else:
                if any(x["kind"] == kind and x["mint"] == mint and ts - x["ts"] < SINGLE_DEDUP_S for x in sigs):
                    return None
            self.s["n"] = self.s.get("n", 0) + 1
            rec = {"id": self.s["n"], "kind": kind, "mint": mint, "ts": ts, "seen": now, "tier": tier,
                   "symbol": info.get("symbol"), "name": info.get("name"),
                   "wallets": [{"wallet": wallet, "tier": tier, "sol": sol, "ts": ts, "family": family}],
                   "entry": None, "entry_ts": None, "last": None, "last_ts": None,
                   "max": {k: None for k in WINDOWS}, "peak": None, "peak_ts": None, "dip": None,
                   "status": "wait_price", "paper": None}
            sigs.insert(0, rec)
            del sigs[MAX_SIGNALS:]
        self.tick([rec])                                     # the entry price as soon as possible
        return rec

    # ---------------------------------------------------------------- price updates
    def _open(self) -> list[dict]:
        now = self.now()
        return [x for x in self.s["signals"] if x["status"] in ("wait_price", "live")
                and now - (x["entry_ts"] or x["seen"]) <= WINDOWS["24h"] + EVERY_S]

    def tick(self, only: list | None = None) -> None:
        recs = only if only is not None else self._open()
        if not recs:
            return
        mints = sorted({x["mint"] for x in recs})
        px = {}
        for i in range(0, len(mints), 50):
            try:
                d = self.prices(mints[i:i + 50]) or {}
            except Exception as e:                           # one failed batch: the next tick tries again
                self.stat["error"] = f"price: {type(e).__name__}"
                continue
            for m in mints[i:i + 50]:
                p = (d.get(m) or {}).get("usdPrice")
                if p:
                    px[m] = float(p)
        now = self.now()
        with self.lock:
            for x in recs:
                self._apply(x, px.get(x["mint"]), now)
        self.stat.update(ticks=self.stat["ticks"] + 1, last=now)

    def _apply(self, x: dict, p: float | None, now: float) -> None:
        if x["status"] == "wait_price":
            if not p:
                if now - x["seen"] > NO_PRICE_GIVEUP_S:
                    x["status"] = "no_price"
                return
            x.update(entry=p, entry_ts=now, status="live", peak=p, peak_ts=now, dip=1.0, low=1.0,
                     paper={"status": "open", "qty": USD / (p * (1 + SLIP)), "left": 1.0, "cash": 0.0,
                            "half": False, "peak": p, "exit": None, "pnl_usd": None, "closed_ts": None})
        age = now - x["entry_ts"]
        if p:
            prev, prev_ts = x.get("last"), x.get("last_ts")
            x["last"], x["last_ts"] = p, now
            m = p / x["entry"]
            for k, s in WINDOWS.items():
                if age <= s:
                    x["max"][k] = round(max(x["max"][k] or 0, m), 4)
            x["low"] = round(min(x.get("low", 1.0), m), 4)
            if p > x["peak"]:
                x["peak"], x["peak_ts"] = p, now
                x["dip"] = x["low"]                          # the lowest point before the (current) peak
            self._paper(x, p, now, age, prev, prev_ts)
        if age > WINDOWS["24h"]:
            x["status"] = "done"
            if x["paper"] and x["paper"]["status"] == "open":
                self._close(x, x["last"] or 0, now, "end")

    # ---------------------------------------------------------------- the $500 paper position
    def _sell(self, x: dict, frac: float, p: float) -> None:
        pp = x["paper"]
        part = min(frac, pp["left"])
        pp["cash"] += pp["qty"] * part * p * (1 - SLIP)
        pp["left"] -= part

    def _close(self, x: dict, p: float, now: float, why: str) -> None:
        pp = x["paper"]
        self._sell(x, pp["left"], p)
        pp.update(status="closed", exit=why, closed_ts=now, pnl_usd=round(pp["cash"] - USD, 2),
                  hold_s=round(now - x["entry_ts"]))

    def _paper(self, x: dict, p: float, now: float, age: float, prev: float | None, prev_ts: float | None) -> None:
        pp = x["paper"]
        if not pp or pp["status"] != "open":
            return
        pp["peak"] = max(pp["peak"], p)
        m = p / x["entry"]
        if prev and prev_ts and now - prev_ts <= 120 and p <= prev * CRASH:
            return self._close(x, p, now, "crash")
        if not pp["half"] and m >= TP_MULT:
            self._sell(x, 0.5, p)
            pp["half"] = True
        if not pp["half"] and m <= STOP_MULT:
            return self._close(x, p, now, "stop")
        if pp["half"] and (p <= pp["peak"] * (1 - TRAIL) or p <= x["entry"]):
            return self._close(x, p, now, "trail")
        if age >= TIME_SOFT_S and pp["peak"] / x["entry"] < SOFT_MULT:
            return self._close(x, p, now, "time30")
        if age >= TIME_HARD_S:
            return self._close(x, p, now, "time60")
        if any(self.sold_half(x["mint"], w["wallet"]) for w in x["wallets"]):
            return self._close(x, p, now, "wallet_exit")

    # ---------------------------------------------------------------- report
    def summary(self) -> list[dict]:
        out = []
        sigs = [x for x in self.s["signals"] if x["status"] in ("live", "done")]
        groups = {}
        for x in sigs:
            groups.setdefault((x["kind"], x["tier"]), []).append(x)
            groups.setdefault((x["kind"], "all"), []).append(x)
        for (kind, tier), xs in sorted(groups.items()):
            row = {"kind": kind, "tier": tier, "n": len(xs)}
            for k in ("1h", "24h"):
                full = [x for x in xs if self.now() - x["entry_ts"] >= WINDOWS[k] and x["max"][k]]
                hit = sum(x["max"][k] >= 2 for x in full)
                row[f"n_{k}"] = len(full)
                row[f"hit2x_{k}"] = hit
                row[f"ci_{k}"] = wilson(hit, len(full))
                row[f"med_{k}"] = round(statistics.median(x["max"][k] for x in full), 2) if full else None
            closed = [x["paper"] for x in xs if x["paper"] and x["paper"]["status"] == "closed"]
            pn = sorted(c["pnl_usd"] for c in closed)
            row.update(paper_n=len(closed), paper_win=sum(v > 0 for v in pn), paper_usd=round(sum(pn), 2),
                       paper_usd_wo_best=round(sum(pn[:-1]), 2) if pn else 0.0,
                       exits=dict(Counter(c["exit"] for c in closed)),
                       paper_open=sum(1 for x in xs if x["paper"] and x["paper"]["status"] == "open"))
            out.append(row)
        return out

    def rows(self, limit: int = 60) -> list[dict]:
        return [{k: x.get(k) for k in ("id", "kind", "mint", "symbol", "name", "ts", "tier", "entry", "last",
                                       "max", "dip", "status")}
                | {"wallets": x["wallets"][:6], "n_wallets": len(x["wallets"]),
                   "paper": {k: (x["paper"] or {}).get(k) for k in ("status", "exit", "pnl_usd", "half", "hold_s")}}
                for x in self.s["signals"][:limit]]

    def view(self) -> dict:
        return {"summary": self.summary(), "rows": self.rows(), "stat": self.stat, "usd": USD, "slip": SLIP,
                "rules": {"tp": TP_MULT, "stop": STOP_MULT, "trail": TRAIL, "soft_s": TIME_SOFT_S,
                          "soft_mult": SOFT_MULT, "hard_s": TIME_HARD_S}}

    def run_forever(self, stop: threading.Event, save=None) -> None:
        while not stop.is_set():
            try:
                self.tick()
                if save:
                    save()
            except Exception as e:                            # the loop never dies
                self.stat["error"] = f"{type(e).__name__}: {str(e)[:60]}"
            stop.wait(EVERY_S)
