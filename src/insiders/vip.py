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

from insiders.pairs import tiers as pair_tiers, win_counts
from insiders.scan import KNOWN_LABELS, on_curve
from insiders.watch import MIN_SOL, STABLES, WSOL, Rpc, analyse

WS_URL = os.environ.get("INSIDER_WS") or "wss://api.mainnet-beta.solana.com"
CHILD_TTL_S = 7 * 86400
STRONG_WINDOW_S = int(os.environ.get("INSIDER_STRONG_WINDOW_S") or 86400)   # >= 2 families inside this (blockTime)
STRONG_REPEAT_S = 86400          # a coin can be STRONG again a day later
WORKERS = 3
QUEUE_MAX = 2000
SILENT_S = 120                   # no message at all for this long: the socket is dead, reconnect
SWEEP_S = 600                    # expired children are unsubscribed this often
SUB_GAP_S = float(os.environ.get("INSIDER_SUB_GAP_S") or 0.3)   # the public RPC refuses bursts of subscriptions (1013)
LIMITED_BACKOFF_S = 45.0
ADOPT_MIN_SOL = float(os.environ.get("INSIDER_ADOPT_MIN_SOL") or 0.5)   # a new wallet funded with this joins
MAX_WALLETS = int(os.environ.get("INSIDER_MAX_WALLETS") or 300)  # followed in all (base list + linked wallets)
WS_MAX = int(os.environ.get("INSIDER_WS_MAX") or 90)            # live on the socket (the public RPC drops ~100); the rest polled
POLL_GAP_S = float(os.environ.get("INSIDER_POLL_GAP_S") or 0.35)  # between two polled wallets
POLL_LIMIT = 20                                                  # signatures read per wallet per round
FLOW_MIN_SOL = float(os.environ.get("INSIDER_FLOW_MIN_SOL") or 0.5)          # a SOL move worth recording
ADOPT_IN_MIN_SOL = float(os.environ.get("INSIDER_ADOPT_IN_MIN_SOL") or 1.0)  # a wallet paying a listed one joins too
MAX_FLOWS = 2000


def vip_list(result: dict) -> dict[str, str]:
    v = result.get("vip") or {}
    out = {}
    for h in v.get("holders", []):
        src = " · last-20 scan" if h.get("source") == "last20" else ""
        held = f"held {h['max_hold_min']:g} min" if h.get("max_hold_min") else "held >= 10 min"
        out[h["wallet"]] = (f"{held} · {h.get('profit_sol', 0):+.0f} SOL on {', '.join(h.get('coins', [])[:3])}{src}"
                            + (f" · {h['note']}" if h.get("note") else ""))
    for x in v.get("external", []):                       # other projects' wallets, given by hand
        out.setdefault(x["wallet"], f"{x.get('group')} · {x.get('role')}" + (f" · {x['note']}" if x.get("note") else ""))
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


def token_counterparty(tx: dict, wallet: str, mint: str, dt: int) -> str | None:
    """The other owner whose balance of `mint` moved the opposite way in this transaction (a token transfer)."""
    meta = tx.get("meta") or {}
    bal: dict[str, int] = {}
    for rows, sign in ((meta.get("preTokenBalances"), -1), (meta.get("postTokenBalances"), 1)):
        for b in rows or []:
            if b.get("mint") == mint and b.get("owner") and b["owner"] != wallet:
                bal[b["owner"]] = bal.get(b["owner"], 0) + sign * int((b.get("uiTokenAmount") or {}).get("amount") or 0)
    best = max(((o, -d if dt > 0 else d) for o, d in bal.items()), key=lambda x: x[1], default=(None, 0))
    return best[0] if best[1] > 0 and on_curve(best[0]) else None


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
        self.result = result
        try:                                                  # main / watch tier of each wallet (signal quality)
            self.tiers = pair_tiers(result, now(), set(KNOWN_LABELS))
            self.wins = dict(win_counts(result, now(), set(KNOWN_LABELS)))
        except Exception:
            self.tiers, self.wins = {}, {}
        v = result.get("vip") or {}
        self.external = {x["wallet"]: x for x in v.get("external", [])}
        self.ws_refused: set[str] = set()
        self.pstat = {"rounds": 0, "wallets": 0, "round_s": None, "new": 0, "errors": 0, "last": None}
        self.holders = {h["wallet"] for h in v.get("holders", [])}
        self.succ_from = {s["wallet"]: s.get("from", []) for s in v.get("successors", [])}

    def wallets(self) -> dict[str, str]:
        """The base list always first and always whole; live children (newest kept) fill the rest up to MAX_WALLETS."""
        now = self.now()
        kids = sorted(((a, v) for a, v in self.state["children"].items() if now - v["since"] < CHILD_TTL_S),
                      key=lambda x: -x[1]["since"])[:max(0, MAX_WALLETS - len(self.base))]
        return {**self.base, **{a: f"new wallet of {v['parent'][:6]}… ({v['sol']} SOL)" for a, v in kids}}

    def family(self, wallet: str) -> str:
        """One vote per person: a child counts as its root parent, a successor as the holder that funded it, and every
        wallet of one external project (DEPTH, VALENTINO ...) as that project."""
        seen = set()
        kids = self.state.get("children", {})
        while wallet in kids and wallet not in seen:
            seen.add(wallet)
            wallet = kids[wallet]["parent"]
        if wallet in self.external:
            return "group:" + (self.external[wallet].get("group") or "?")
        for s in (self.result.get("vip") or {}).get("successors", []):
            if s["wallet"] == wallet and s.get("from"):
                return sorted(s["from"])[0]
        return wallet

    def ws_set(self) -> list[str]:
        """Wallets on the socket: the most recently active first (base list, then linked wallets), at most WS_MAX;
        every other followed wallet is polled."""
        seen = self.state.get("last_seen", {})
        ws = self.wallets()
        base = sorted((a for a in ws if a in self.base), key=lambda a: -(seen.get(a) or 0))
        kids = [a for a in ws if a not in self.base]
        return [a for a in base + kids if a not in self.ws_refused][:WS_MAX]

    def group(self, wallet: str) -> str:
        if wallet in self.external:
            return self.external[wallet].get("group") or "KHÁC"
        if wallet in self.base:
            return "INSIDERS"
        kid = self.state.get("children", {}).get(wallet)
        return f"LIÊN KẾT {self.group(kid['parent'])}" if kid and kid.get("parent") != wallet else "KHÁC"

    def role(self, wallet: str) -> str:
        if wallet in self.external:
            return "external"
        if wallet in self.holders:
            return "holder"
        if wallet in self.succ_from:
            return "successor"
        return "child" if wallet in self.state.get("children", {}) else "other"

    def info(self, wallet: str) -> dict:
        """Display facts of one listed wallet: role, tier, winning coins, family root and direct parent."""
        kid = self.state.get("children", {}).get(wallet) or {}
        ext = self.external.get(wallet) or {}
        return {"role": self.role(wallet), "group": self.group(wallet), "role_note": ext.get("role"), "dir": kid.get("dir"),
                "tier": self.tier(wallet), "wins": self.wins.get(wallet, 0),
                "family": self.family(wallet), "parent": kid.get("parent") or (sorted(self.succ_from.get(wallet) or [None])[0]),
                "funded_sol": kid.get("sol"), "since": kid.get("since")}

    def tier(self, wallet: str) -> str:
        """The wallet's own tier, else its family's (a child / successor inherits from where its money came)."""
        return self.tiers.get(wallet) or self.tiers.get(self.family(wallet)) or "none"

    def prune_children(self) -> list[str]:
        """Drop children older than CHILD_TTL_S from the state; returns them (to unsubscribe)."""
        now = self.now()
        old = [a for a, v in self.state["children"].items() if now - v["since"] >= CHILD_TTL_S]
        for a in old:
            del self.state["children"][a]
        return old

    # ---------------------------------------------------------------- analysis of one pushed transaction
    def handle(self, wallet: str, sig: str) -> list[dict]:
        if sig in self.seen:
            return []
        self.seen.append(sig)
        tx = None
        for k in range(4):                    # just after confirmation the node may not serve it yet / be busy
            try:
                tx = self.rpc("getTransaction", [sig, {"encoding": "jsonParsed", "commitment": "confirmed",
                                                       "maxSupportedTransactionVersion": 1}])
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
        evs = analyse(tx, wallet)
        for ev in evs:
            with self.w.lock:
                self.w._ledger(ev, why)
            if ev.get("mint") and ev["kind"] != "FUND":
                self._coin_facts(ev["mint"])
            if ev["kind"] == "FUND" and (ev.get("sol") or 0) >= ADOPT_MIN_SOL:
                self._adopt(ev["to"], wallet, ev["sol"])          # followed from 0.5 SOL, alerted from MIN_SOL
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
            out.append(a)
            if ev["kind"] in ("BUY", "RECEIVE", "CREATE") and ev.get("mint"):
                with self.w.lock:
                    self.w._qualify(a)                       # the coin gets its tab
                strong = self._together(ev["mint"], wallet, a, tx.get("blockTime"))
                sh = getattr(self.w, "shadow", None)
                if sh is not None and ev["mint"] not in STABLES and ev["mint"] != WSOL:
                    sh.signal("single", ev["mint"], wallet, tx.get("blockTime"), sol, self.tier(wallet), self.family(wallet))
                    if strong:
                        best = min((self.tier(x) for x in strong.get("holders", [])), key=["main", "watch", "none"].index)
                        sh.signal("strong", ev["mint"], wallet, tx.get("blockTime"), sol, best, self.family(wallet))
                if strong:
                    out.append(strong)
        got, frm = sol_in(tx, wallet)
        moved = any(e["kind"] in ("SELL", "SEND", "FUND") for e in evs)   # the SOL is a sale's proceeds: said already
        self._flows(tx, wallet, evs, got, frm, moved)
        if got >= MIN_SOL and not moved and frm not in self.wallets():
            out.append({"ts": tx.get("blockTime"), "sig": sig, "wallet": wallet, "kind": "SOL_IN", "from": frm,
                        "sol": round(got, 3), "why": why, "level": "vip"})
        if got >= ADOPT_IN_MIN_SOL and not moved and frm and frm not in self.wallets():
            self._adopt(frm, wallet, round(got, 3), direction="in")         # whoever pays a listed wallet is followed too
        if out:
            with self.w.lock:
                self.w.state["alerts"] = (out[::-1] + self.w.state["alerts"])[:600]
                self.state["events"] = self.state.get("events", 0) + len(out)
            self.w.save()
            self.w._send(out)
        return out

    def _flows(self, tx: dict, wallet: str, evs: list, got: float, frm: str | None, moved: bool) -> None:
        """Money and tokens between a listed wallet and other wallets (who paid it, whom it paid), for the trace."""
        ts, sig = tx.get("blockTime"), ((tx.get("transaction") or {}).get("signatures") or [None])[0]
        rows = []
        for ev in evs:
            if ev["kind"] == "FUND" and (ev.get("sol") or 0) >= FLOW_MIN_SOL:
                rows.append({"dir": "out", "other": ev.get("to"), "sol": ev.get("sol"), "mint": None, "tokens": None})
            elif ev["kind"] in ("SEND", "RECEIVE") and ev.get("mint"):
                dt = ev.get("tokens") or 0
                other = token_counterparty(tx, wallet, ev["mint"], dt if ev["kind"] == "RECEIVE" else -dt)
                if other:                                     # tokens via a pool / route are trades, not transfers
                    rows.append({"dir": "in" if ev["kind"] == "RECEIVE" else "out", "other": other, "sol": None,
                                 "mint": ev["mint"], "tokens": dt})
        if got >= FLOW_MIN_SOL and not moved and frm and on_curve(frm):
            rows.append({"dir": "in", "other": frm, "sol": round(got, 3), "mint": None, "tokens": None})
        if not rows:
            return
        listed = self.wallets()
        for r in rows:
            r.update(ts=ts, sig=sig, wallet=wallet, listed_other=bool(r["other"] and r["other"] in listed),
                     label=KNOWN_LABELS.get(r["other"] or "", (None, None))[0])
        with self.w.lock:
            fl = self.w.state.setdefault("flows", [])
            known = {(f["sig"], f["wallet"], f["dir"], f.get("mint")) for f in fl[:200]}
            new = [r for r in rows if (r["sig"], r["wallet"], r["dir"], r.get("mint")) not in known]
            self.w.state["flows"] = (new[::-1] + fl)[:MAX_FLOWS]

    def _together(self, mint: str, wallet: str, a: dict, ts: float | None = None) -> dict | None:
        """>= 2 different special-wallet FAMILIES bought / received the same coin (>= MIN_SOL each) within
        STRONG_WINDOW_S of chain time (blockTime): a 'vipstrong' alert (Telegram + the paper trade). A parent and its
        children, or a holder and its successors, are one vote. The same coin can be STRONG again after a day."""
        ts = ts or self.now()
        by = self.state.setdefault("by_mint", {})
        seen = by.setdefault(mint, {})
        seen[wallet] = max(ts, seen.get(wallet, 0))
        for m in list(by):
            by[m] = {w: t for w, t in by[m].items() if ts - t < STRONG_WINDOW_S}
            if not by[m]:
                del by[m]
        done = self.state.setdefault("strong_done", {})
        for m in [m for m, t in done.items() if ts - t >= STRONG_REPEAT_S]:
            del done[m]
        fams = {self.family(w) for w in by.get(mint, {})}
        if len(fams) < 2 or mint in done:
            return None
        done[mint] = ts
        s = {**a, "kind": "VIPSTRONG", "level": "vipstrong", "n_wallets": len(by[mint]), "n_families": len(fams),
             "holders": sorted(by[mint])[:10], "why": "≥ 2 special wallet families on this coin"}
        paper = getattr(self.w, "paper", None)
        if paper:
            p = paper.open(s)
            if p:
                s["paper"] = p["status"]
        return s

    def _coin_facts(self, mint: str) -> None:
        """Supply / decimals (for the market cap) and name / creation (for the age) of a coin, once."""
        sup = self.w.state.setdefault("supply", {})
        if mint not in sup:
            try:
                v = (self.rpc("getTokenSupply", [mint]) or {}).get("value") or {}
                sup[mint] = {"ui": float(v.get("uiAmountString") or 0), "decimals": int(v.get("decimals") or 0)}
            except Exception:                                 # facts are a nicety: never block the alert
                pass
        if mint not in self.w.state.get("meta", {}):
            self.w._meta(mint)

    def _adopt(self, to: str, parent: str, sol: float, direction: str = "out") -> None:
        kind = KNOWN_LABELS.get(to, (None, None))[1]
        if kind in ("exchange", "fee", "deposit") or not on_curve(to) or to in self.base:
            return
        if to not in self.state["children"]:
            self.prune_children()
            kids = self.state["children"]
            if len(self.base) + len(kids) >= MAX_WALLETS and kids:     # full: the oldest child makes room
                oldest = min(kids, key=lambda a: kids[a]["since"])
                del kids[oldest]
                if self.ws is not None and self.queue is not None:
                    self.queue.put_nowait(("unsubscribe", oldest))
            self.state["children"][to] = {"parent": parent, "since": self.now(), "sol": sol, "dir": direction}
            if self.ws is not None and self.queue is not None and len(self.subs) < WS_MAX:
                self.queue.put_nowait(("subscribe", to))                  # else the poller picks it up

    # ---------------------------------------------------------------- websocket loop
    async def _subscribe(self, ws, addr: str, req_id: int) -> None:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": req_id, "method": "logsSubscribe",
                                  "params": [{"mentions": [addr]}, {"commitment": "confirmed"}]}))

    async def run(self, stop: threading.Event) -> None:
        import websockets
        connect = self.connect or (lambda: websockets.connect(self.url, max_size=2 ** 22, ping_interval=20,
                                                               ping_timeout=30))
        backoff = 1.0
        work: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_MAX)      # (wallet, signature) for the workers
        queued: set[str] = set()

        async def worker():
            while True:
                addr, sig = await work.get()
                try:
                    await asyncio.to_thread(self.handle, addr, sig)
                except Exception as e:                               # one bad transaction never stops the stream
                    self.stat["error"] = f"handle: {type(e).__name__}"
                finally:
                    queued.discard(sig)
                    work.task_done()

        workers = [asyncio.create_task(worker()) for _ in range(WORKERS)]
        while not stop.is_set():
            pending: dict[int, str] = {}
            try:
                async with connect() as ws:
                    self.ws, self.queue, self.subs = ws, asyncio.Queue(), {}
                    for k, addr in enumerate(self.ws_set(), 1):
                        pending[k] = addr
                        await self._subscribe(ws, addr, k)
                        await asyncio.sleep(SUB_GAP_S)                       # paced: no "too many subscriptions"
                    self.stat.update(connected=True, error=None)
                    backoff, nxt = 1.0, len(pending) + 1
                    heard, swept = time.monotonic(), time.monotonic()
                    while not stop.is_set():
                        while not self.queue.empty():
                            op, addr = self.queue.get_nowait()
                            if op == "unsubscribe":
                                sid = next((s for s, a in self.subs.items() if a == addr), None)
                                if sid is not None:
                                    await ws.send(json.dumps({"jsonrpc": "2.0", "id": nxt, "method": "logsUnsubscribe",
                                                              "params": [sid]}))
                                    self.subs.pop(sid, None)
                            else:
                                pending[nxt] = addr
                                await self._subscribe(ws, addr, nxt)
                                await asyncio.sleep(SUB_GAP_S)
                            nxt += 1
                        if time.monotonic() - swept > SWEEP_S:              # expired children leave the socket
                            swept = time.monotonic()
                            for addr in self.prune_children():
                                self.queue.put_nowait(("unsubscribe", addr))
                        try:
                            raw = await asyncio.wait_for(ws.recv(), 5)
                        except asyncio.TimeoutError:
                            if time.monotonic() - heard > SILENT_S:
                                raise ConnectionError(f"silent for {SILENT_S} s") from None
                            continue
                        heard = time.monotonic()
                        self.stat["last_msg"] = self.now()
                        try:
                            msg = json.loads(raw)
                        except ValueError:
                            self.stat["bad_json"] = self.stat.get("bad_json", 0) + 1
                            continue
                        if not isinstance(msg, dict):
                            continue
                        if "error" in msg:                                   # a refused subscribe: poll that wallet
                            addr = pending.pop(msg.get("id"), None)
                            self.stat["sub_errors"] = self.stat.get("sub_errors", 0) + 1
                            if addr:
                                self.ws_refused.add(addr)
                            continue
                        if msg.get("id") in pending:                         # subscription confirmed
                            addr = pending.pop(msg["id"])
                            if msg.get("result") is not None:
                                self.subs[msg["result"]] = addr
                            self.stat["subscribed"] = len(self.subs)
                            continue
                        prm = msg.get("params") or {}
                        val = ((prm.get("result") or {}).get("value") or {})
                        addr = self.subs.get(prm.get("subscription"))
                        sig = val.get("signature")
                        if addr and sig and val.get("err") is None and sig not in queued and sig not in self.seen:
                            self.stat["pushed"] += 1
                            try:
                                work.put_nowait((addr, sig))                 # never wait on a slow fetch here
                                queued.add(sig)
                            except asyncio.QueueFull:
                                self.stat["missed"] += 1
            except Exception as e:                                   # dropped: reconnect and resubscribe
                self.stat.update(connected=False, error=f"{type(e).__name__}: {str(e)[:80]}")
                self.stat["reconnects"] += 1
                self.ws = None
                if "1013" in str(e) or "Rate limit" in str(e):              # refused for too many attempts: cool down
                    backoff = max(backoff, LIMITED_BACKOFF_S)
                await asyncio.sleep(backoff)
                backoff = min(120.0, backoff * 2)
        for t in workers:
            t.cancel()
        self.stat["connected"] = False

    def poll_once(self, rpc=None) -> int:
        """One round over the followed wallets that are not live on the socket: new signatures since the last round
        are analysed like pushed ones (a wallet's first round only records where it is). Returns new transactions."""
        rpc = rpc or self.rpc
        live = set(self.subs.values())
        last = self.state.setdefault("poll_last", {})
        todo = [a for a in self.wallets() if a not in live]
        t0, new = time.monotonic(), 0
        for a in todo:
            try:
                sigs = rpc("getSignaturesForAddress", [a, {"limit": POLL_LIMIT}]) or []
            except RuntimeError:
                self.pstat["errors"] += 1
                continue
            if not sigs:
                continue
            prev = last.get(a)
            last[a] = sigs[0]["signature"]
            if prev is None:
                continue                                           # first look: remember the position only
            fresh = []
            for x in sigs:
                if x["signature"] == prev:
                    break
                if x.get("err") is None:
                    fresh.append(x["signature"])
            for sig in reversed(fresh):                            # oldest first
                if sig not in self.seen:
                    new += 1
                    try:
                        self.handle(a, sig)
                    except Exception as e:                         # one bad transaction never stops the round
                        self.stat["error"] = f"poll: {type(e).__name__}"
        self.pstat.update(rounds=self.pstat["rounds"] + 1, wallets=len(todo), new=self.pstat["new"] + new,
                          round_s=round(time.monotonic() - t0, 1), last=self.now())
        return new

    def poll_forever(self, stop: threading.Event) -> None:
        rpc = Rpc(gap_s=POLL_GAP_S)
        while not stop.is_set():
            try:
                self.poll_once(rpc)
                self.w.save()
            except Exception as e:                                 # the loop never dies
                self.stat["error"] = f"poll: {type(e).__name__}: {str(e)[:60]}"
            stop.wait(5)

    def status(self) -> dict:
        ws = self.wallets()
        return {**self.stat, "wallets": len(ws), "base": len(self.base), "last_seen": self.state.get("last_seen", {}),
                "kids": {a: v["parent"] for a, v in self.state["children"].items()},
                "children": len(self.state["children"]), "events": self.state.get("events", 0), "min_sol": MIN_SOL,
                "list": [{"wallet": a, "why": why, **self.info(a)} for a, why in list(ws.items())[:400]],
                "poll": self.pstat, "ws_max": WS_MAX, "refused": len(self.ws_refused),
                "max_wallets": MAX_WALLETS, "adopt_min_sol": ADOPT_MIN_SOL, "strong_window_s": STRONG_WINDOW_S}

    def start(self, stop: threading.Event) -> threading.Thread:
        th = threading.Thread(target=lambda: asyncio.run(self.run(stop)), daemon=True)
        th.start()
        threading.Thread(target=self.poll_forever, args=(stop,), daemon=True).start()
        return th
