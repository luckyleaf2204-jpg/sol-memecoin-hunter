"""Real-time watch of the special wallets (research only, read-only): the 45 wallets that HELD the channel's coins
>= 10 minutes and the personal wallets they moved SOL / tokens to.

One WebSocket to the public Solana RPC with a logsSubscribe (mentions = the wallet) per wallet: a transaction is
pushed within seconds, fetched with getTransaction and analysed. Every trade or transfer >= MIN_SOL (buy, sell,
tokens sent / received valued at a Jupiter quote, SOL sent or received) is an alert of level 'vip', pushed at once
(Telegram when configured). A wallet one of them sends >= MIN_SOL to (a person, not an exchange) joins the list
for 7 days and is subscribed live: the group switching to a new wallet stays in view."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time
from collections import deque

from insiders.scan import KNOWN_LABELS, on_curve
from insiders.watch import MIN_SOL, Rpc, analyse

WS_URL = os.environ.get("INSIDER_WS") or "wss://api.mainnet-beta.solana.com"
CHILD_TTL_S = 7 * 86400
MAX_WALLETS = 300


def vip_list(result: dict) -> dict[str, str]:
    v = result.get("vip") or {}
    out = {}
    for h in v.get("holders", []):
        out[h["wallet"]] = f"held >= 10 min · {h.get('profit_sol', 0):+.0f} SOL on {', '.join(h.get('coins', [])[:3])}"
    for s in v.get("successors", []):
        if s.get("kind") == "person" and KNOWN_LABELS.get(s["wallet"], (None, None))[1] not in ("exchange", "fee", "deposit"):
            out.setdefault(s["wallet"], f"received {s.get('sol', 0):.0f} SOL / {s.get('token_tx', 0)} token tx from "
                                        f"{', '.join(f[:6] for f in s.get('from', [])[:3])}")
    return out


def sol_in(tx: dict, wallet: str) -> tuple[float, str | None]:
    """SOL the wallet received in this transaction from a normal wallet (amount, largest sender)."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    ins = list(msg.get("instructions") or [])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        ins += inner.get("instructions") or []
    got, best = 0, (0, None)
    for i in ins:
        p = i.get("parsed")
        if isinstance(p, dict) and p.get("type") in ("transfer", "createAccount"):
            info = p.get("info") or {}
            if (info.get("destination") or info.get("newAccount")) == wallet and info.get("source") != wallet:
                lam = int(info.get("lamports", 0))
                got += lam
                if lam > best[0]:
                    best = (lam, info.get("source"))
    return got / 1e9, best[1]


class VipStream:
    def __init__(self, watcher, result: dict, url: str = WS_URL, rpc=None, connect=None, now=time.time):
        self.w, self.url, self.now = watcher, url, now
        self.rpc = rpc or Rpc(gap_s=0.2)
        self.connect = connect
        self.state = watcher.state.setdefault("vip", {"children": {}, "events": 0})
        self.base = vip_list(result)
        self.seen = deque(maxlen=5000)
        self.stat = {"connected": False, "subscribed": 0, "last_msg": None, "reconnects": 0, "error": None,
                     "pushed": 0, "handled": 0, "missed": 0}
        self.retry_sleep = True
        self.queue: asyncio.Queue | None = None
        self.ws = None
        self.subs: dict[int, str] = {}

    def wallets(self) -> dict[str, str]:
        now = self.now()
        kids = {a: f"new wallet of {v['parent'][:6]}… ({v['sol']} SOL)" for a, v in self.state["children"].items()
                if now - v["since"] < CHILD_TTL_S}
        return dict(list({**self.base, **kids}.items())[:MAX_WALLETS])

    # ---------------------------------------------------------------- analysis of one pushed transaction
    def handle(self, wallet: str, sig: str) -> list[dict]:
        if sig in self.seen:
            return []
        self.seen.append(sig)
        tx = None
        for k in range(4):                    # just after confirmation the node may not serve it yet / be busy
            try:
                tx = self.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                       "maxSupportedTransactionVersion": 0}])
            except RuntimeError:
                tx = None
            if tx:
                break
            time.sleep(1.5 * (k + 1)) if self.retry_sleep else None
        self.stat["handled" if tx else "missed"] = self.stat.get("handled" if tx else "missed", 0) + 1
        if not tx:
            return []
        why = self.wallets().get(wallet, "special wallet")
        self.state.setdefault("last_seen", {})[wallet] = tx.get("blockTime")
        out = []
        for ev in analyse(tx, wallet):
            with self.w.lock:
                self.w._ledger(ev, why)
            sol = ev.get("sol") or 0
            if ev["kind"] in ("RECEIVE", "SEND") and ev.get("mint") and not sol:
                v = self.w._value_sol(ev["mint"], ev.get("tokens") or 0)
                sol = round(v, 3) if v else 0
            if sol < MIN_SOL:
                continue
            a = {**ev, "sol": sol, "why": why, "level": "vip"}
            if ev.get("mint"):
                info = self.w._meta(ev["mint"])
                a.update({k: info.get(k) for k in ("name", "symbol", "mcap", "chart")})
                if info.get("created_ts"):
                    a["age_h"] = round(max(0.0, self.now() - info["created_ts"]) / 3600, 2)
            if ev["kind"] == "FUND":
                self._adopt(ev["to"], wallet, sol)
            out.append(a)
            if ev["kind"] in ("BUY", "RECEIVE", "CREATE") and ev.get("mint"):
                with self.w.lock:
                    self.w._qualify(a)                       # the coin gets its tab
                strong = self._together(ev["mint"], wallet, a)
                if strong:
                    out.append(strong)
        got, frm = sol_in(tx, wallet)
        if got >= MIN_SOL:
            out.append({"ts": tx.get("blockTime"), "sig": sig, "wallet": wallet, "kind": "SOL_IN", "from": frm,
                        "sol": round(got, 3), "why": why, "level": "vip"})
        if out:
            with self.w.lock:
                self.w.state["alerts"] = (out[::-1] + self.w.state["alerts"])[:600]
                self.state["events"] = self.state.get("events", 0) + len(out)
            self.w.save()
            self.w._send(out)
        return out

    def _together(self, mint: str, wallet: str, a: dict) -> dict | None:
        """>= 2 different special wallets bought / received the same coin (>= MIN_SOL each) within 24 h: one
        'vipstrong' alert per coin (Telegram + the paper trade)."""
        now = self.now()
        by = self.state.setdefault("by_mint", {})
        seen = by.setdefault(mint, {})
        seen[wallet] = now
        for m in list(by):
            by[m] = {w: t for w, t in by[m].items() if now - t < 86400}
            if not by[m]:
                del by[m]
        done = self.state.setdefault("strong_done", {})
        if len(by.get(mint, {})) < 2 or mint in done:
            return None
        done[mint] = now
        s = {**a, "kind": "VIPSTRONG", "level": "vipstrong", "n_wallets": len(by[mint]),
             "holders": sorted(by[mint])[:10], "why": "≥ 2 special wallets on this coin"}
        paper = getattr(self.w, "paper", None)
        if paper:
            p = paper.open(s)
            if p:
                s["paper"] = p["status"]
        return s

    def _adopt(self, to: str, parent: str, sol: float) -> None:
        kind = KNOWN_LABELS.get(to, (None, None))[1]
        if kind in ("exchange", "fee", "deposit") or not on_curve(to) or to in self.base:
            return
        if to not in self.state["children"]:
            self.state["children"][to] = {"parent": parent, "since": self.now(), "sol": sol}
            if self.ws is not None and self.queue is not None:
                self.queue.put_nowait(("subscribe", to))

    # ---------------------------------------------------------------- websocket loop
    async def _subscribe(self, ws, addr: str, req_id: int) -> None:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "logsSubscribe",
                                  "params": [{"mentions": [addr]}, {"commitment": "confirmed"}]}))

    async def run(self, stop: threading.Event) -> None:
        import websockets
        connect = self.connect or (lambda: websockets.connect(self.url, max_size=2 ** 22, ping_interval=20,
                                                               ping_timeout=30))
        backoff = 1.0
        while not stop.is_set():
            pending: dict[int, str] = {}
            try:
                async with connect() as ws:
                    self.ws, self.queue, self.subs = ws, asyncio.Queue(), {}
                    for k, addr in enumerate(self.wallets(), 1):
                        pending[k] = addr
                        await self._subscribe(ws, addr, k)
                    self.stat.update(connected=True, error=None)
                    backoff, nxt = 1.0, len(pending) + 1
                    while not stop.is_set():
                        while not self.queue.empty():
                            _, addr = self.queue.get_nowait()
                            pending[nxt] = addr
                            await self._subscribe(ws, addr, nxt)
                            nxt += 1
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 5)
                        except asyncio.TimeoutError:
                            continue
                        msg = json.loads(raw)
                        self.stat["last_msg"] = self.now()
                        if "id" in msg and msg.get("id") in pending:          # subscription confirmed
                            self.subs[msg["result"]] = pending.pop(msg["id"])
                            self.stat["subscribed"] = len(self.subs)
                            continue
                        prm = msg.get("params") or {}
                        val = ((prm.get("result") or {}).get("value") or {})
                        addr = self.subs.get(prm.get("subscription"))
                        if addr and val.get("signature") and val.get("err") is None:
                            self.stat["pushed"] += 1
                            try:
                                await asyncio.to_thread(self.handle, addr, val["signature"])
                            except Exception as e:                   # one bad transaction never stops the stream
                                self.stat["error"] = f"handle: {type(e).__name__}"
            except Exception as e:                                   # dropped: reconnect and resubscribe
                self.stat.update(connected=False, error=f"{type(e).__name__}: {str(e)[:80]}")
                self.stat["reconnects"] += 1
                self.ws = None
                await asyncio.sleep(backoff)
                backoff = min(60.0, backoff * 2)
        self.stat["connected"] = False

    def status(self) -> dict:
        ws = self.wallets()
        return {**self.stat, "wallets": len(ws), "base": len(self.base), "last_seen": self.state.get("last_seen", {}),
                "kids": {a: v["parent"] for a, v in self.state["children"].items()},
                "children": len(self.state["children"]), "events": self.state.get("events", 0), "min_sol": MIN_SOL,
                "list": [{"wallet": a, "why": why} for a, why in list(ws.items())[:120]]}

    def start(self, stop: threading.Event) -> threading.Thread:
        th = threading.Thread(target=lambda: asyncio.run(self.run(stop)), daemon=True)
        th.start()
        return th
