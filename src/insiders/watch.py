"""Deployer watch (research only, read-only): every 5 minutes look at the wallets behind the channel's coins and
raise an alert when one of them
  * creates a coin                              (CREATE)
  * buys / receives a coin that is < 24 h old   (BUY_NEW / RECEIVE_NEW) and is not one of the channel's old coins
  * sends >= 0.5 SOL to a brand-new wallet      (FUND_NEW_WALLET) -> that wallet is watched for 72 h too
A coin touched by >= 2 watched wallets within 24 h is a STRONG alert (the group is loading it before a call).

Polling uses the public Solana RPC (getSignaturesForAddress + getTransaction: no Helius credits). The coin age
lookup uses the public RPC too (the oldest of its first 1000 signatures). State (last seen signature per wallet,
alerts, temporary wallets) is kept in a JSON file; the first poll of a wallet only records where it is."""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from pathlib import Path

from insiders.scan import KNOWN_LABELS, on_curve

PUBLIC_RPC = "https://api.mainnet-beta.solana.com"
PUMP = "6EF8rrecthR5Dkzon8Nwu78hRvfCKubJ14M5uBEwF6P"
WSOL = "So11111111111111111111111111111111111111112"
STABLES = {"EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v", "Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB"}
EVERY_S = 300
NEW_COIN_S = 24 * 3600
FUND_MIN_LAMPORTS = 500_000_000
CHILD_TTL_S = 72 * 3600
MAX_NEW_TX = 25                  # per wallet per poll (a busy wallet is summarised, not replayed)
MAX_ALERTS = 600
B_SLICES = 6                     # tier B is checked one sixth per poll: every wallet about every 30 minutes
BOT_NEW_COINS = 4                # a wallet touching >= 4 different new coins in 24 h is a sniper bot: muted


class Rpc:
    def __init__(self, url: str = PUBLIC_RPC, gap_s: float = 0.25, sleep=time.sleep):
        self.url, self.gap_s, self.sleep, self.calls, self.errors = url, gap_s, sleep, 0, 0
        self._last = 0.0
        self._lock = threading.Lock()

    def __call__(self, method: str, params: list):
        with self._lock:                                     # requests start >= gap_s apart (public RPC limits)
            wait = self.gap_s - (time.time() - self._last)
            if wait > 0:
                self.sleep(wait)
            self._last = time.time()
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        for k in range(4):
            try:
                req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
                res = json.loads(urllib.request.urlopen(req, timeout=40).read())
                self.calls += 1
                if res.get("error"):
                    raise RuntimeError(str(res["error"])[:120])
                return res.get("result")
            except (urllib.error.URLError, TimeoutError, ConnectionError, RuntimeError) as e:
                if k == 3:
                    self.errors += 1
                    raise RuntimeError(f"{type(e).__name__}") from None
                self.sleep(2 * 2 ** k)


def watch_tiers(result: dict) -> tuple[dict[str, str], dict[str, str]]:
    """(tier A every poll, tier B in rotation). A: cluster funders, wallets linked to the extra coins, the extra coins'
    deployers + non-bot early wallets, wallets that sold for deployers, profitable wallets. B: the old coins'
    deployers and non-bot early wallets."""
    a = watch_list(result)
    tick = result.get("tickers", {})
    ex = result.get("extra") or {}
    for c in ex.get("coins", []):
        if c.get("deployer"):
            a.setdefault(c["deployer"], f"deployer {c['ticker']} (new list)")
        for b in c.get("buyers", [])[:40]:
            if not b.get("bot"):
                a.setdefault(b["wallet"], f"early #{b['rank']} {c['ticker']} (new list)")
    for l in ex.get("links", []):
        if l.get("kind") not in ("exchange", "fee", "deposit"):
            a.setdefault(l["address"], f"linked to {', '.join(l['new_coins'][:3])}")
    bots = {w["wallet"] for w in result.get("repeat_wallets", []) if w.get("bot_like")}
    b: dict[str, str] = {}
    for t in result.get("tokens", []):
        if t.get("status") != "ok":
            continue
        for x in sorted(t.get("buyers") or [], key=lambda x: (not x.get("pre_call"), x.get("rank", 0)))[:15]:
            if x["wallet"] not in bots and x["wallet"] not in a:
                b.setdefault(x["wallet"], f"early #{x['rank']} {tick.get(t['mint']) or t['mint'][:6]}")
    ok = lambda w: KNOWN_LABELS.get(w, (None, None))[1] not in ("exchange", "fee") and on_curve(w)  # noqa: E731
    return {w: v for w, v in a.items() if ok(w)}, {w: v for w, v in b.items() if ok(w) and w not in a}


def watch_list(result: dict, top_profit: int = 40) -> dict[str, str]:
    """address -> why it is watched (deployers, wallets that sold for a deployer, the cluster funders)."""
    tick = result.get("tickers", {})
    out: dict[str, str] = {}
    for t in result.get("tokens", []):
        if t.get("status") == "ok" and t.get("deployer"):
            out.setdefault(t["deployer"], f"deployer {tick.get(t['mint']) or t['mint'][:6]}")
    for g in (result.get("pnl") or {}).get("deployer_groups", []):
        for m in g.get("members", []):
            if m.get("level") and (m.get("net_sol") or 0) > 1:
                out.setdefault(m["wallet"], f"sold for deployer of {tick.get(g['mint']) or g['mint'][:6]}")
    for e in (result.get("deep") or {}).get("top", [])[:10]:
        if not e.get("hub") and e.get("kind") not in ("exchange", "fee", "deposit"):
            out.setdefault(e["address"], f"cluster funder ({e['n_tokens']} coins)")
    for w in (result.get("pnl") or {}).get("wallets", [])[:top_profit]:
        if w["net_sol"] > 10:
            out.setdefault(w["wallet"], f"profit +{w['net_sol']:.0f} SOL on the channel's coins")
    return {a: why for a, why in out.items() if KNOWN_LABELS.get(a, (None, None))[1] not in ("exchange", "fee")}


def _keys(tx):
    ks = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    return [k.get("pubkey") if isinstance(k, dict) else k for k in ks]


def analyse(tx: dict, wallet: str) -> list[dict]:
    """Events of `wallet` in one transaction."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return []
    keys, ts, ev = _keys(tx), tx.get("blockTime"), []
    sig = ((tx.get("transaction") or {}).get("signatures") or [None])[0]
    logs = " ".join(meta.get("logMessages") or [])
    pre = defaultdict(int)
    post = defaultdict(int)
    for rows, d in ((meta.get("preTokenBalances"), pre), (meta.get("postTokenBalances"), post)):
        for b in rows or []:
            if b.get("owner") == wallet:
                d[b["mint"]] += int(b["uiTokenAmount"]["amount"])
    sol = 0
    if wallet in keys:
        i = keys.index(wallet)
        sol = int(meta["postBalances"][i]) - int(meta["preBalances"][i])
    created = PUMP in keys and "Instruction: Create" in logs and keys and keys[0] == wallet
    for mint in set(pre) | set(post):
        if mint in (WSOL, *STABLES) or post[mint] <= pre[mint]:
            continue
        kind = "CREATE" if created and pre[mint] == 0 else ("BUY" if sol < -10_000_000 else "RECEIVE")
        ev.append({"ts": ts, "sig": sig, "wallet": wallet, "kind": kind, "mint": mint,
                   "sol": round(-sol / 1e9, 3) if sol < 0 else 0, "tokens": post[mint] - pre[mint]})
    if created and not ev:
        mints = [b["mint"] for b in meta.get("postTokenBalances") or [] if b.get("mint") not in (WSOL, *STABLES)]
        if mints:
            ev.append({"ts": ts, "sig": sig, "wallet": wallet, "kind": "CREATE", "mint": mints[0], "sol": 0,
                       "tokens": 0})
    msg = (tx.get("transaction") or {}).get("message") or {}
    ins = list(msg.get("instructions") or [])
    for inner in meta.get("innerInstructions") or []:
        ins += inner.get("instructions") or []
    for i in ins:
        p = i.get("parsed")
        if isinstance(p, dict) and p.get("type") in ("transfer", "createAccount"):
            info = p.get("info") or {}
            dst = info.get("destination") or info.get("newAccount")
            if info.get("source") == wallet and dst and on_curve(dst) and int(info.get("lamports", 0)) >= FUND_MIN_LAMPORTS:
                ev.append({"ts": ts, "sig": sig, "wallet": wallet, "kind": "FUND", "to": dst,
                           "sol": round(int(info["lamports"]) / 1e9, 3)})
    return ev


class Watcher:
    def __init__(self, result: dict, state_path: Path, rpc=None, now=time.time, old_mints: set | None = None):
        self.base, self.tier_b = watch_tiers(result)
        self.old = old_mints if old_mints is not None else (
            {t["mint"] for t in result.get("tokens", [])} | {c["mint"] for c in (result.get("extra") or {}).get("coins", [])})
        self.tickers = result.get("tickers", {})
        self.path, self.rpc, self.now = Path(state_path), rpc or Rpc(), now
        self.lock = threading.Lock()
        self.state = {"last_sig": {}, "children": {}, "alerts": [], "mint_age": {}, "polls": 0, "last_poll": None,
                      "last_error": None, "muted": {}}
        try:
            self.state.update(json.loads(self.path.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            pass

    def wallets(self) -> dict[str, str]:
        now = self.now()
        kids = {a: f"new wallet funded by {v['parent'][:6]}… ({v['why']})" for a, v in self.state["children"].items()
                if now - v["since"] < CHILD_TTL_S}
        return {**self.base, **kids}

    def _mint_age(self, mint: str):
        """Seconds since the coin's first transaction, or None when it has >= 1000 (old / busy)."""
        if mint in self.state["mint_age"]:
            created = self.state["mint_age"][mint]
            return None if created is None else self.now() - created
        sigs = self.rpc("getSignaturesForAddress", [mint, {"limit": 1000}]) or []
        created = sigs[-1].get("blockTime") if sigs and len(sigs) < 1000 else None
        self.state["mint_age"][mint] = created
        return None if created is None else self.now() - created

    def _fresh(self, addr: str) -> bool:
        return len(self.rpc("getSignaturesForAddress", [addr, {"limit": 5}]) or []) <= 2

    def poll_once(self, progress=lambda m: None) -> list[dict]:
        new_alerts = []
        watched = self.wallets()
        keys_b = sorted(self.tier_b)
        k = self.state["polls"] % B_SLICES
        watched.update({w: self.tier_b[w] for w in keys_b[k::B_SLICES] if w not in self.state.get("muted", {})})

        def one(item):
            w, why = item
            found = []
            try:
                last = self.state["last_sig"].get(w)
                opts = {"limit": MAX_NEW_TX}
                if last:
                    opts["until"] = last
                sigs = self.rpc("getSignaturesForAddress", [w, opts]) or []
                if sigs:
                    self.state["last_sig"][w] = sigs[0]["signature"]
                if not last:
                    return found                             # first look: remember the position only
                for s in reversed(sigs):
                    if s.get("err"):
                        continue
                    tx = self.rpc("getTransaction", [s["signature"], {"encoding": "jsonParsed", "commitment": "confirmed",
                                                                      "maxSupportedTransactionVersion": 0}])
                    if not tx:
                        continue
                    for ev in analyse(tx, w):
                        with self.lock:
                            a = self._alert(ev, why)
                        if a:
                            found.append(a)
            except RuntimeError as e:
                self.state["last_error"] = f"{w[:6]}: {e}"
            return found

        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=4) as ex:
            for found in ex.map(one, list(watched.items())):
                new_alerts += found
        self._strong(new_alerts)
        with self.lock:
            self.state["alerts"] = (new_alerts[::-1] + self.state["alerts"])[:MAX_ALERTS]
            self.state["polls"] += 1
            self.state["last_poll"] = self.now()
            self.state["watched"] = len(watched)
            self.state["rpc_calls"] = getattr(self.rpc, "calls", None)
        self.save()
        progress(f"[insider-watch] poll {self.state['polls']}: {len(watched)} wallets, {len(new_alerts)} alerts")
        return new_alerts

    def _alert(self, ev: dict, why: str) -> dict | None:
        ev = {**ev, "why": why}
        if ev["kind"] == "FUND":
            if ev["to"] in self.base or ev["to"] in self.state["children"] or not self._fresh(ev["to"]):
                return None
            self.state["children"][ev["to"]] = {"parent": ev["wallet"], "since": self.now(), "why": why[:40]}
            return {**ev, "level": "watch", "note": "now watched for 72 h"}
        mint = ev["mint"]
        if ev["wallet"] in self.state.setdefault("muted", {}):
            return None
        if mint in self.old:
            return None if ev["kind"] != "CREATE" else {**ev, "level": "info", "ticker": self.tickers.get(mint)}
        age = self._mint_age(mint)
        if ev["kind"] != "CREATE" and (age is None or age > NEW_COIN_S):
            return None                                       # an old / established coin: not a launch
        if ev["kind"] != "CREATE" and self._is_bot(ev["wallet"], mint):
            return None
        return {**ev, "age_h": None if age is None else round(age / 3600, 2),
                "level": "high" if ev["kind"] == "CREATE" else "medium"}

    def _is_bot(self, wallet: str, mint: str) -> bool:
        """A wallet buying >= BOT_NEW_COINS different new coins within 24 h snipes everything: mute it."""
        now = self.now()
        seen = self.state.setdefault("new_by_wallet", {}).setdefault(wallet, {})
        seen[mint] = now
        for m in [m for m, t in seen.items() if now - t > NEW_COIN_S]:
            del seen[m]
        if len(seen) >= BOT_NEW_COINS:
            self.state["muted"][wallet] = now
            return True
        return False

    def _strong(self, new_alerts: list[dict]) -> None:
        """>= 2 watched wallets on the same new coin within 24 h -> STRONG."""
        now = self.now()
        muted = self.state.get("muted", {})
        recent = [a for a in new_alerts + self.state["alerts"] if a.get("mint") and now - (a.get("ts") or now) < NEW_COIN_S
                  and a["wallet"] not in muted]
        by_mint = defaultdict(set)
        for a in recent:
            if a["kind"] in ("CREATE", "BUY", "RECEIVE"):
                by_mint[a["mint"]].add(a["wallet"])
        for a in new_alerts:
            n = len(by_mint.get(a.get("mint"), ()))
            if n >= 2:
                a["level"], a["n_wallets"] = "strong", n

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.state), encoding="utf-8")
        except OSError:
            pass

    def status(self) -> dict:
        with self.lock:
            return {"polls": self.state["polls"], "last_poll": self.state["last_poll"],
                    "watched": self.state.get("watched", len(self.base)), "every_s": EVERY_S,
                    "tier_a": len(self.base), "tier_b": len(self.tier_b), "muted": len(self.state.get("muted", {})),
                    "children": len(self.state["children"]), "last_error": self.state["last_error"],
                    "rpc_calls": self.state.get("rpc_calls"), "last_poll_s": self.state.get("last_poll_s"),
                    "alerts": self.state["alerts"][:200]}

    def run_forever(self, stop: threading.Event, progress=print, every_s: int = EVERY_S) -> None:
        """A poll STARTS every `every_s`; a poll longer than that is followed at once by the next."""
        while not stop.is_set():
            t0 = time.time()
            try:
                self.poll_once(progress)
            except Exception as e:                            # never let the watcher die
                self.state["last_error"] = f"{type(e).__name__}: {str(e)[:120]}"
            self.state["last_poll_s"] = round(time.time() - t0)
            stop.wait(max(0.0, every_s - (time.time() - t0)))
