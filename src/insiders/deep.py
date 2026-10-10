"""Deep SOL-flow trace (3-4 hops back and forward) from every called token's insider wallets.

Seeds: the deployer and the traced early wallets of each token (sniper bots that buy every new token are left out:
they connect to everything). From each seed:
  back    who funded it, who funded that funder, ... (largest + first funders, >= MIN_SOL)
  forward where its SOL went after the buy, then where that went after it arrived, ... (time-ordered)
Every address reached carries the set of tokens whose seeds lead to it. Expansion STOPS at exchanges, bridges,
routers, fee / tip receivers and busy wallets: going through them would link everybody. An address that forwards to
a known exchange is marked "pays into <exchange>" (likely that person's deposit address) and is not expanded.
Output: addresses ranked by the number of tokens they connect, each with one money path per token as evidence."""
from __future__ import annotations

import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

from insiders.scan import (FEE_AVG_SOL, KNOWN_LABELS, MIN_TRANSFER_LAMPORTS, Chain, ok, on_curve, sol_transfers)

MAX_HOPS = 4
FAN = 2                           # counterparties followed per node and direction (largest first; + first funder)
MIN_SOL_LAMPORTS = 50_000_000     # 0.05 SOL
BUSY_TXS_PER_HOUR = 60            # a first page of 100 tx inside < 100 min: exchange / bot, not a person
WORKERS = 4
MIN_SOL_PER_TOKEN = 2.0           # less SOL than this per linked coin: fees / tips / dust, not an operator
SERVICE_SOL = 50_000.0            # an address moving more than this through our graph is a service (exchange, MM)


def _label(addr: str):
    return KNOWN_LABELS.get(addr, (None, None))


def flows(chain: Chain, addr: str, after_ts: int | None) -> dict:
    """Funders (from the start of the history) and sinks (from `after_ts`) of one address, with first times."""
    first = chain.history(addr, gte=0)
    txs = first.get("data") or []
    times = [t.get("blockTime") for t in txs if t.get("blockTime")]
    busy = len(txs) >= 100 and times and (max(times) - min(times)) < 100 * 3600 / BUSY_TXS_PER_HOUR
    fund: dict[str, list] = {}
    order = []
    for tx in txs:
        if not ok(tx):
            continue
        for src, dst, lam in sol_transfers(tx):
            if dst == addr and src and src != addr and lam >= MIN_TRANSFER_LAMPORTS and on_curve(src):
                if src not in fund:
                    fund[src] = [0, tx.get("blockTime")]
                    order.append(src)
                fund[src][0] += lam
    if after_ts is not None and first.get("paginationToken") and times and max(times) < after_ts:
        txs = chain.history(addr, gte=after_ts).get("data") or []
    sink: dict[str, list] = {}
    for tx in txs:
        if not ok(tx) or (after_ts is not None and (tx.get("blockTime") or 0) < after_ts):
            continue
        for src, dst, lam in sol_transfers(tx):
            if src == addr and dst and dst != addr and lam >= MIN_TRANSFER_LAMPORTS and on_curve(dst):
                if dst not in sink:
                    sink[dst] = [0, tx.get("blockTime")]
                sink[dst][0] += lam
    return {"busy": bool(busy), "first_funder": order[0] if order else None,
            "funders": {a: tuple(v) for a, v in fund.items()}, "sinks": {a: tuple(v) for a, v in sink.items()}}


def _pick(d: dict, fan: int, also: str | None = None) -> list[str]:
    big = [a for a, (lam, _) in sorted(d.items(), key=lambda x: -x[1][0]) if lam >= MIN_SOL_LAMPORTS][:fan]
    if also and also in d and also not in big:
        big.append(also)                                   # the FIRST funder of a fresh wallet is the key link
    return big


def deep_trace(chain: Chain, seeds: dict[str, dict], max_hops: int = MAX_HOPS, fan: int = FAN,
               progress=lambda m: None, workers: int = WORKERS) -> dict:
    """seeds: wallet -> {"tokens": set(mint), "ts": first time it got the token}."""
    nodes: dict[str, dict] = {}
    lock = threading.Lock()

    def node(a):
        if a not in nodes:
            # tokens / seeds / via are kept PER DIRECTION: a chain is followed one way only, so an address that
            # funded insider A and was paid by insider B is linked to both, but nothing is carried back and forth
            nodes[a] = {"tok": {"back": set(), "fwd": set()}, "sd": {"back": set(), "fwd": set()},
                        "back": None, "fwd": None, "via": {"back": {}, "fwd": {}}, "busy": False, "pays_into": None,
                        "sol_in": 0, "sol_out": 0}
        return nodes[a]

    for w, s in seeds.items():
        n = node(w)
        for d in ("back", "fwd"):
            n["tok"][d] |= set(s["tokens"])
            n["sd"][d].add(w)
        n["back"] = n["fwd"] = 0
    frontier = [(w, "back", None) for w in seeds] + [(w, "fwd", seeds[w].get("ts")) for w in seeds]
    expanded: set[tuple] = set()
    stopped_budget = False
    for hop in range(1, max_hops + 1):
        todo = []
        for a, d, ts in frontier:
            if (a, d) in expanded or _label(a)[1] in ("exchange", "fee", "deposit") or nodes[a]["busy"]                     or nodes[a]["pays_into"]:
                continue
            expanded.add((a, d))
            todo.append((a, d, ts))
        todo.sort(key=lambda x: -len(nodes[x[0]]["tok"][x[1]]))
        nxt = []

        def work(item):
            a, d, ts = item
            try:
                return item, flows(chain, a, ts if d == "fwd" else None)
            except RuntimeError as e:                       # budget reached / request failed
                return item, {"error": str(e)}

        with ThreadPoolExecutor(max_workers=workers) as ex:
            for (a, d, ts), f in ex.map(work, todo):
                if "error" in f:
                    stopped_budget = stopped_budget or "budget" in f["error"]
                    continue
                with lock:
                    me = nodes[a]
                    vol = sum(v[0] for v in f["funders"].values()) + sum(v[0] for v in f["sinks"].values())
                    me["busy"] = me["busy"] or f["busy"] or vol / 1e9 > SERVICE_SOL
                    if me["busy"] and hop > 1:
                        continue                            # an exchange / bot / big service: do not go through
                    side = f["funders"] if d == "back" else f["sinks"]
                    picks = _pick(side, fan, f["first_funder"] if d == "back" else None)
                    for b in picks:
                        lam, t = side[b]
                        nb = node(b)
                        nb["tok"][d] |= me["tok"][d]
                        nb["sd"][d] |= me["sd"][d]
                        if d == "back":
                            nb["back"] = hop if nb["back"] is None else min(nb["back"], hop)
                            nb["sol_out"] += lam
                        else:
                            nb["fwd"] = hop if nb["fwd"] is None else min(nb["fwd"], hop)
                            nb["sol_in"] += lam
                        for m in me["tok"][d]:
                            nb["via"][d].setdefault(m, (a, lam))
                        kind = _label(b)[1]
                        if d == "fwd" and kind == "exchange":
                            me["pays_into"] = _label(b)[0]  # `a` deposits into an exchange: likely its own account
                        nxt.append((b, d, t if d == "fwd" else None))
        progress(f"deep hop {hop}/{max_hops}: expanded {len(todo)}, reached {len(nodes)} addresses, "
                 f"rpc {chain.calls}")
        frontier = nxt
        if stopped_budget:
            progress("call budget reached: deeper hops not expanded")
            break
    return {"nodes": nodes, "stopped_budget": stopped_budget, "hops": max_hops}


def path_to_seed(nodes: dict, addr: str, mint: str, limit: int = 8) -> list[dict]:
    """addr -> ... -> a seed of `mint` in ONE direction (back first), following the first discovery links."""
    for d in ("back", "fwd"):
        out, cur = [], addr
        for _ in range(limit):
            v = nodes.get(cur, {}).get("via", {}).get(d, {}).get(mint)
            if not v:
                break
            prev, lam = v
            out.append({"from": prev, "to": cur, "dir": d, "sol": round(lam / 1e9, 3)})
            cur = prev
        if out:
            return out
    return []


def summarize(deep: dict, seeds: dict, tickers: dict, top: int = 120) -> dict:
    nodes = deep["nodes"]
    rows = []
    for a, n in nodes.items():
        n["tokens"] = n["tok"]["back"] | n["tok"]["fwd"]
        n["seeds"] = n["sd"]["back"] | n["sd"]["fwd"]
        if a in seeds and len(n["tokens"]) < 2:
            continue
        label, kind = _label(a)
        vol = (n["sol_in"] + n["sol_out"]) / 1e9
        if kind is None and a not in seeds and vol / max(1, len(n["tokens"])) < MIN_SOL_PER_TOKEN:
            label, kind = "small amounts only (fees / tips / dust)", "fee"
        elif kind is None and vol > SERVICE_SOL:
            label, kind = f"large service ({vol:,.0f} SOL moved)", "exchange"
        if n.get("active_hub"):
            label, kind = label or f"busy now ({n['active_hub']} tx/min)", kind or "exchange"
        hub = kind in ("exchange", "fee") or (n["busy"] and kind not in ("person", "deposit"))
        rows.append({"address": a, "n_tokens": len(n["tokens"]), "tokens": sorted(n["tokens"]),
                     "n_seeds": len(n["seeds"]), "hop_back": n["back"], "hop_fwd": n["fwd"],
                     "sol_out": round(n["sol_out"] / 1e9, 3), "sol_in": round(n["sol_in"] / 1e9, 3),
                     "is_seed": a in seeds, "label": label, "kind": kind, "hub": hub, "pays_into": n["pays_into"],
                     "paths": [{"token": m, "path": path_to_seed(nodes, a, m)} for m in sorted(n["tokens"])[:6]]})
    rows.sort(key=lambda r: (r["hub"], -r["n_tokens"], -r["n_seeds"]))
    return {"hops": deep["hops"], "stopped_budget": deep["stopped_budget"], "reached": len(nodes),
            "top": [r for r in rows if not r["hub"] and r["n_tokens"] >= 2][:top],
            "hubs": [r for r in rows if r["hub"] and r["n_tokens"] >= 2][:40]}


def seeds_from(result: dict, per_token: int = 8) -> dict[str, dict]:
    """Deployer + earliest wallets (before the call first) of every scanned token, without sniper bots."""
    bots = {w["wallet"] for w in result.get("repeat_wallets", []) if w.get("bot_like")}
    seeds: dict[str, dict] = {}
    for t in result.get("tokens", []):
        if t.get("status") != "ok":
            continue
        picks = ([{"wallet": t["deployer"], "ts": t.get("created_ts")}] if t.get("deployer") else []) +             sorted(t.get("buyers") or [], key=lambda b: (not b.get("pre_call"), b.get("rank", 0)))[:per_token - 1]
        for b in picks:
            w = b["wallet"]
            if w in bots:
                continue
            s = seeds.setdefault(w, {"tokens": set(), "ts": b.get("ts")})
            s["tokens"].add(t["mint"])
            if b.get("ts") and (s["ts"] is None or b["ts"] < s["ts"]):
                s["ts"] = b["ts"]
    return seeds


def check_activity(chain: Chain, deep: dict, seeds: dict, top: int = 60) -> int:
    """Current transaction rate of the best-ranked unlabelled addresses (exchanges / bots that the oldest page hid)."""
    from insiders.scan import activity
    nodes = deep["nodes"]
    size = {a: len(n["tok"]["back"] | n["tok"]["fwd"]) for a, n in nodes.items()}
    cand = sorted((a for a in nodes if a not in seeds and size[a] >= 2 and _label(a)[1] is None),
                  key=lambda a: -size[a])[:top]
    n_hub = 0
    for a in cand:
        try:
            act = activity(chain, a)
        except RuntimeError:
            continue
        if act.get("hub"):
            nodes[a]["active_hub"] = act["per_min"]
            n_hub += 1
    return n_hub
