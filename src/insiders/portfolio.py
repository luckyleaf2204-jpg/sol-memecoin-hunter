"""Current balances of the special wallets (research only, read-only): SOL balance and every token account with a
price, refreshed in a loop. Public RPC for balances (getBalance, getTokenAccountsByOwner for both token programs),
Jupiter price API in batches of 50 for prices, DexScreener for symbols / chart links. Tokens without a price (dead
coins, spam) are remembered for 6 hours and not asked again; holdings under MIN_USD are only counted."""
from __future__ import annotations

import json
import os
import threading
import time
import urllib.request
from collections import defaultdict

TOKEN_PROGRAMS = ("TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb")
SOL = "So11111111111111111111111111111111111111112"
STABLE = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v": "USDC", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB": "USDT"}
STABLE_SYMBOLS = {"USDC", "USDT", "USD1", "EURC", "PYUSD", "USDS", "USDE", "FDUSD", "DAI", "USDG", "USDH"}
EVERY_S = int(os.environ.get("INSIDER_PORTFOLIO_S") or 180)
MIN_SOL_VALUE = float(os.environ.get("INSIDER_HOLD_MIN_SOL") or 1.0)   # only holdings worth more than this
DEAD_TTL_S = 6 * 3600


def _get(url: str):
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "Mozilla/5.0"})
    return json.loads(urllib.request.urlopen(req, timeout=25).read())


def holdings(rpc, wallet: str) -> tuple[float, list[tuple[str, float]]]:
    """(SOL balance, [(mint, ui amount)] of non-empty token accounts)."""
    sol = (rpc("getBalance", [wallet]) or {}).get("value", 0) / 1e9
    toks: dict[str, float] = defaultdict(float)
    for prog in TOKEN_PROGRAMS:
        res = (rpc("getTokenAccountsByOwner", [wallet, {"programId": prog}, {"encoding": "jsonParsed"}]) or {})
        for a in res.get("value") or []:
            info = ((a.get("account") or {}).get("data") or {}).get("parsed", {}).get("info") or {}
            amt = info.get("tokenAmount") or {}
            if info.get("mint") and int(amt.get("amount") or 0) > 0:
                toks[info["mint"]] += float(amt.get("uiAmountString") or 0)
    return sol, list(toks.items())


class Prices:
    """USD prices (Jupiter), symbols (DexScreener), with a memory of priceless mints."""

    def __init__(self, get=_get, now=time.time):
        self.get, self.now = get, now
        self.usd: dict[str, float] = {}
        self.dead: dict[str, float] = {}
        self.meta: dict[str, dict] = {}

    def refresh(self, mints: set[str]) -> None:
        now = self.now()
        ask = [m for m in mints if m not in self.dead or now - self.dead[m] > DEAD_TTL_S]
        for i in range(0, len(ask), 50):
            batch = ask[i:i + 50]
            try:
                d = self.get("https://lite-api.jup.ag/price/v3?ids=" + ",".join(batch)) or {}
            except Exception:
                continue
            for m in batch:
                p = (d.get(m) or {}).get("usdPrice")
                if p:
                    self.usd[m] = float(p)
                    self.dead.pop(m, None)
                else:
                    self.usd.pop(m, None)
                    self.dead[m] = now
        need = [m for m in mints if m in self.usd and m not in self.meta and m not in STABLE and m != SOL]
        for i in range(0, len(need), 30):
            try:
                for p in self.get("https://api.dexscreener.com/tokens/v1/solana/" + ",".join(need[i:i + 30])) or []:
                    bt = p.get("baseToken") or {}
                    if bt.get("address") and bt["address"] not in self.meta:
                        self.meta[bt["address"]] = {"symbol": bt.get("symbol"), "name": bt.get("name"),
                                                    "chart": p.get("url")}
            except Exception:
                continue


def build(wallets: dict[str, str], raw: dict[str, tuple], prices: Prices, sol_usd: float | None,
          min_sol: float = MIN_SOL_VALUE) -> dict:
    """Per-wallet and per-coin views from raw holdings {wallet: (sol, [(mint, amount)])}."""
    rows, coins = [], defaultdict(lambda: {"holders": [], "usd": 0.0, "amount": 0.0})
    for w, why in wallets.items():
        if w not in raw:
            continue
        sol, toks = raw[w]
        held, dust, tok_usd, stable_usd = [], 0, 0.0, 0.0
        for m, amt in toks:
            if m == SOL:                                      # wrapped SOL is SOL
                sol += amt
                continue
            sym0 = STABLE.get(m) or (prices.meta.get(m) or {}).get("symbol") or ""
            if sym0.upper().strip() in STABLE_SYMBOLS:        # stablecoins are cash, not a coin position
                stable_usd += amt * (prices.usd.get(m) or 1.0)
                continue
            p = prices.usd.get(m)
            if m in STABLE:
                p = p or 1.0
            usd = amt * p if p else None
            if usd is None or not sol_usd or usd < min_sol * sol_usd:   # junk / dead / worth <= min_sol SOL
                dust += 1
                continue
            meta = prices.meta.get(m, {})
            sym = STABLE.get(m) or meta.get("symbol")
            held.append({"mint": m, "symbol": sym, "name": meta.get("name"), "amount": amt, "usd": round(usd, 2),
                         "sol": round(usd / sol_usd, 3), "chart": meta.get("chart")})
            tok_usd += usd
            c = coins[m]
            c["holders"].append({"wallet": w, "usd": round(usd, 2), "amount": amt})
            c["usd"] += usd
            c["amount"] += amt
            c.update({"symbol": sym, "name": meta.get("name"), "chart": meta.get("chart")})
        held.sort(key=lambda x: -x["usd"])
        rows.append({"wallet": w, "why": why, "sol": round(sol, 4), "stable_usd": round(stable_usd, 2),
                     "sol_usd": round(sol * sol_usd, 2) if sol_usd else None, "tokens_usd": round(tok_usd, 2),
                     "total_usd": round(tok_usd + (sol * sol_usd if sol_usd else 0), 2), "tokens": held, "dust": dust})
    rows.sort(key=lambda r: -r["total_usd"])
    clist = sorted(({"mint": m, **{k: v for k, v in c.items() if k != "holders"}, "usd": round(c["usd"], 2),
                     "n_holders": len(c["holders"]), "holders": sorted(c["holders"], key=lambda h: -h["usd"])}
                    for m, c in coins.items()), key=lambda c: (-c["n_holders"], -c["usd"]))
    return {"wallets": rows, "coins": clist, "sol_usd": sol_usd, "min_sol": min_sol,
            "total_sol": round(sum(r["sol"] for r in rows), 3), "total_tokens_usd": round(sum(r["tokens_usd"] for r in rows), 2),
            "total_stable_usd": round(sum(r["stable_usd"] for r in rows), 2)}


class Portfolio:
    def __init__(self, wallets_fn, rpc, get=_get, now=time.time):
        self.wallets_fn, self.rpc, self.now = wallets_fn, rpc, now
        self.prices = Prices(get=get, now=now)
        self.view: dict | None = None
        self.updated = None
        self.error = None
        self.seconds = None

    def refresh(self) -> dict:
        t0 = time.time()
        wallets = self.wallets_fn()
        raw = {}

        def one(w):
            try:
                return w, holdings(self.rpc, w)
            except Exception as e:                            # one wallet failing keeps the others
                self.error = f"{w[:6]}: {type(e).__name__}"
                return w, None
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=1) as ex:      # the public RPC answers 429 to parallel bursts
            for w, h in ex.map(one, list(wallets)):
                if h is not None:
                    raw[w] = h
        mints = {m for _, toks in raw.values() for m, _ in toks} | {SOL}
        self.prices.refresh(mints)
        self.view = build(wallets, raw, self.prices, self.prices.usd.get(SOL))
        self.updated, self.seconds = self.now(), round(time.time() - t0)
        return self.view

    def status(self) -> dict:
        return {**(self.view or {}), "updated": self.updated, "seconds": self.seconds, "error": self.error,
                "every_s": EVERY_S}

    def run_forever(self, stop: threading.Event, every_s: int = EVERY_S) -> None:
        while not stop.is_set():
            t0 = time.time()
            try:
                self.refresh()
            except Exception as e:
                self.error = f"{type(e).__name__}: {str(e)[:80]}"
            stop.wait(max(5.0, every_s - (time.time() - t0)))
