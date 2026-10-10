"""PAPER trades on the watch's STRONG alerts (research only: nothing is signed or sent).

On a STRONG alert for a coin (once per coin) a $500 (INSIDER_PAPER_USD) paper buy is priced with a real Jupiter
quote USDC -> coin (price impact for that size included). The position is then valued with real quotes
coin -> USDC: every 10 minutes (peak / trough) and at fixed marks +15 min, +1 h, +6 h, +24 h; it is closed at 24 h.
Not included: network / priority fees (FEE_USD per round trip is deducted), the delay between the quote and a real
fill, and failed transactions. No exit rule is optimised: the marks only show what holding that long would give."""
from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request

USDC = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"
QUOTE = "https://lite-api.jup.ag/swap/v1/quote?inputMint={i}&outputMint={o}&amount={a}&slippageBps=500"
MARKS = (("15m", 900), ("1h", 3600), ("6h", 6 * 3600), ("24h", 24 * 3600))
SAMPLE_S = 600
FEE_USD = 1.0


def jupiter_quote(inp: str, out: str, amount: int, get=None) -> int | None:
    """Expected output amount (raw) of a Jupiter swap quote, or None (no route / error)."""
    url = QUOTE.format(i=inp, o=out, a=int(amount))
    try:
        if get:
            d = get(url)
        else:
            req = urllib.request.Request(url, headers={"Accept": "application/json"})
            d = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return int(d["outAmount"]) if d and d.get("outAmount") else None
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError):
        return None


class PaperBook:
    """Positions live in the watcher's state dict (state["paper"]), saved with it."""

    def __init__(self, state: dict, usd: float | None = None, quote=jupiter_quote, now=time.time,
                 levels: tuple = ("strong",)):
        self.book = state.setdefault("paper", {"positions": {}})
        self.usd = float(usd if usd is not None else (os.environ.get("INSIDER_PAPER_USD") or 500))
        self.quote, self.now, self.levels = quote, now, set(levels)

    def open(self, alert: dict) -> dict | None:
        mint = alert.get("mint")
        if not mint or alert.get("level") not in self.levels or mint in self.book["positions"]:
            return None
        t = self.now()
        tokens = self.quote(USDC, mint, int(self.usd * 1e6))
        pos = {"mint": mint, "opened": t, "alert_ts": alert.get("ts"), "level": alert.get("level"),
               "n_wallets": alert.get("n_wallets"), "wallet": alert.get("wallet"), "usd": self.usd,
               "tokens": tokens, "marks": {}, "samples": [], "status": "open" if tokens else "no_route"}
        if tokens:
            v = self._value(pos)
            pos["entry_value"] = v                            # selling at once: the round-trip cost of this size
            pos["samples"].append([0, v])
        self.book["positions"][mint] = pos
        return pos

    def _value(self, pos: dict) -> float | None:
        back = self.quote(pos["mint"], USDC, pos["tokens"]) if pos.get("tokens") else None
        return None if back is None else round(back / 1e6 - FEE_USD, 2)

    def update(self) -> None:
        t = self.now()
        for pos in self.book["positions"].values():
            if pos["status"] != "open":
                continue
            age = t - pos["opened"]
            last = pos["samples"][-1][0] if pos["samples"] else -SAMPLE_S
            due = [k for k, s in MARKS if age >= s and k not in pos["marks"]]
            if not due and age - last < SAMPLE_S:
                continue
            v = self._value(pos)
            pos["samples"].append([round(age), v])
            pos["samples"] = pos["samples"][-200:]
            for k in due:
                pos["marks"][k] = v                           # None = no route at that time (cannot sell)
            if age >= MARKS[-1][1]:
                pos["status"] = "closed"

    def summary(self) -> dict:
        ps = [p for p in self.book["positions"].values() if p["status"] != "no_route"]
        out = {"usd": self.usd, "positions": len(ps), "open": sum(p["status"] == "open" for p in ps),
               "no_route": sum(p["status"] == "no_route" for p in self.book["positions"].values()), "marks": {}}
        for k, _ in MARKS:
            vals = [p["marks"][k] for p in ps if p["marks"].get(k) is not None]
            if vals:
                rets = [100 * (v / self.usd - 1) for v in vals]
                out["marks"][k] = {"n": len(vals), "avg_pct": round(sum(rets) / len(rets), 1),
                                   "wins": sum(r > 0 for r in rets), "pnl_usd": round(sum(vals) - self.usd * len(vals), 2)}
        return out

    def rows(self, limit: int = 100) -> list[dict]:
        out = []
        for p in sorted(self.book["positions"].values(), key=lambda p: -p["opened"])[:limit]:
            vals = [v for _, v in p["samples"] if v is not None]
            out.append({**{k: p.get(k) for k in ("mint", "opened", "level", "n_wallets", "wallet", "usd", "status",
                                                 "entry_value")},
                        "marks": p["marks"], "peak": max(vals) if vals else None, "trough": min(vals) if vals else None,
                        "now": vals[-1] if vals else None})
        return out
