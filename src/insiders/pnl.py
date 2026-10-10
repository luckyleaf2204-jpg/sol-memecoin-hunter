"""Realised profit of a wallet on one called coin (research only, read-only chain data).

For (wallet, mint) the wallet's associated token account (ATA) history holds every buy, sell and transfer of that
coin by the wallet. Each trade is priced on the POOL side, not on the wallet's balance: bundler / bot programs
("SellAndDispersePump") send sale proceeds to their own vaults, so the wallet's SOL balance would show nothing.
  buy / sell : the counterparty holding the coin is a program account (bonding curve or PumpSwap pool); the SOL it
               received / paid in that transaction (pool WSOL change, else the curve's own SOL change) is shared
               between the wallets in the transaction by their token amounts
  transfer   : the counterparty is a normal wallet -> tokens moved in / out, no SOL (insider distribution)
  net = SOL from sells - SOL into buys (gross: fees, tips and bot fees not included); tokens left are not valued.
Limits: a wallet trading through a non-ATA token account is not seen ("no_ata_history")."""
from __future__ import annotations

import hashlib
from collections import defaultdict

from insiders.scan import Chain, b58decode, ok, on_curve

ATA_PROGRAM = "ATokenGPvbdGVxr1b2hvZbsiqW5xWH25efTNsLJA8knL"
TOKEN = "TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA"
MAX_PAGES = 15
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(b: bytes) -> str:
    n = int.from_bytes(b, "big")
    s = ""
    while n:
        n, r = divmod(n, 58)
        s = _B58[r] + s
    return "1" * (len(b) - len(b.lstrip(b"\0"))) + s


def find_pda(seeds: list[bytes], program: str) -> str:
    pid = b58decode(program)
    for bump in range(255, -1, -1):
        h = hashlib.sha256(b"".join(seeds) + bytes([bump]) + pid + b"ProgramDerivedAddress").digest()
        addr = b58encode(h)
        if not on_curve(addr):
            return addr
    raise ValueError("no PDA")


def ata(wallet: str, mint: str, token_program: str = TOKEN) -> str:
    return find_pda([b58decode(wallet), b58decode(token_program), b58decode(mint)], ATA_PROGRAM)


def mint_program(chain: Chain, mint: str) -> str:
    v = (chain.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}]).get("result") or {}).get("value") or {}
    return v.get("owner") or TOKEN


def _sol_change(tx: dict, wallet: str) -> int:
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    keys = [k.get("pubkey") if isinstance(k, dict) else k for k in keys]
    meta = tx.get("meta") or {}
    if wallet not in keys:
        return 0
    i = keys.index(wallet)
    return int(meta["postBalances"][i]) - int(meta["preBalances"][i])


def _all_tok(tx: dict, mint: str) -> dict[str, int]:
    meta, out = tx.get("meta") or {}, defaultdict(int)
    for sign, rows in ((-1, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in rows or []:
            if b.get("mint") == mint and b.get("owner"):
                out[b["owner"]] += sign * int(b["uiTokenAmount"]["amount"])
    return out


def _tok_change(tx: dict, wallet: str, mint: str) -> int:
    meta, d = tx.get("meta") or {}, 0
    for sign, rows in ((-1, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in rows or []:
            if b.get("mint") == mint and b.get("owner") == wallet:
                d += sign * int(b["uiTokenAmount"]["amount"])
    return d


WSOL = "So11111111111111111111111111111111111111112"


def _pool_sol(tx: dict, owner: str) -> int:
    """SOL change of a program account in the transaction: its WSOL token balance, else its own lamports."""
    meta, d, seen = tx.get("meta") or {}, 0, False
    for sign, rows in ((-1, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in rows or []:
            if b.get("mint") == WSOL and b.get("owner") == owner:
                d += sign * int(b["uiTokenAmount"]["amount"])
                seen = True
    return d if seen else _sol_change(tx, owner)


def pair_pnl(chain: Chain, wallet: str, mint: str, token_program: str = TOKEN, max_pages: int = MAX_PAGES) -> dict:
    acct = ata(wallet, mint, token_program)
    spent = received = bought = sold = 0
    n_buy = n_sell = 0
    t_in = t_out = 0
    trades: list[list] = []                                 # [ts, side, sol, tokens]
    to_wallets: dict[str, int] = defaultdict(int)
    first = last = None
    token, pages, complete = None, 0, False
    while pages < max_pages:
        page = chain.history(acct, gte=0, token=token)
        pages += 1
        for tx in page.get("data") or []:
            if not ok(tx):
                continue
            dt = _tok_change(tx, wallet, mint)
            if not dt:
                continue
            ts = tx.get("blockTime")
            first = first or ts
            last = ts
            deltas = {o: d for o, d in _all_tok(tx, mint).items() if o != wallet and d}
            # the curve / pool side: a program account whose SOL went UP on a buy (DOWN on a sell). Its token change
            # cannot be used for the share (in a create transaction the curve is minted the whole supply), so the
            # SOL is shared by the wallets' own token amounts in the same direction
            pools = {o: _pool_sol(tx, o) for o in deltas if not on_curve(o)}
            pools = {o: v for o, v in pools.items() if (v > 0) == (dt > 0) and v}
            if pools:                                       # a trade against the curve / pool
                o, pv = max(pools.items(), key=lambda x: abs(x[1]))
                same = abs(dt) + sum(abs(d) for q, d in deltas.items() if on_curve(q) and (d > 0) == (dt > 0))
                sol = abs(pv) * abs(dt) / max(1, same)
                trades.append([ts, "buy" if dt > 0 else "sell", round(sol / 1e9, 4), abs(dt)])
                if dt > 0:
                    bought += dt
                    n_buy += 1
                    spent += sol
                else:
                    sold += -dt
                    n_sell += 1
                    received += sol
            elif dt > 0:
                t_in += dt                                  # received from another wallet (or minted to it)
                trades.append([ts, "in", 0, dt])
            else:
                t_out += -dt
                trades.append([ts, "out", 0, -dt])
                for o, d in deltas.items():
                    if d > 0 and on_curve(o):
                        to_wallets[o] += d
        token = page.get("paginationToken")
        if not token or not page.get("data"):
            complete = True
            break
    status = "ok" if complete else "incomplete (page cap)"
    if complete and not (n_buy or n_sell):
        status = "no_ata_history"
    return {"wallet": wallet, "mint": mint, "status": status, "spent_sol": round(spent / 1e9, 4),
            "received_sol": round(received / 1e9, 4), "net_sol": round((received - spent) / 1e9, 4),
            "n_buy": n_buy, "n_sell": n_sell, "tokens_in": t_in, "tokens_out": t_out,
            "tokens_left": max(0, bought + t_in - sold - t_out), "sent_to": sorted(to_wallets, key=lambda a: -to_wallets[a])[:5],
            "first_ts": first, "last_ts": last, "trades": trades[:400]}


def pick_pairs(result: dict, top_repeat: int = 30) -> tuple[dict, dict]:
    """(wallet, mint) pairs to price and the cluster each wallet belongs to (by its shared funder / sink)."""
    pairs: dict[tuple, set] = defaultdict(set)              # (wallet, mint) -> reasons
    tokens = [t for t in result.get("tokens", []) if t.get("status") == "ok"]
    in_tok = defaultdict(set)
    for t in tokens:
        if t.get("deployer"):
            pairs[(t["deployer"], t["mint"])].add("deployer")
            in_tok[t["deployer"]].add(t["mint"])
        for b in t.get("buyers") or []:
            in_tok[b["wallet"]].add(t["mint"])
    for w in result.get("repeat_wallets", [])[:top_repeat]:
        for m in w["tokens"]:
            pairs[(w["wallet"], m)].add("repeat")
    clusters: dict[str, str] = {}
    for e in (result.get("entities") or []):
        if e.get("hub"):
            continue
        name = e.get("label") or e["address"]
        for ev in e.get("evidence") or []:
            for r in ev.get("roles") or []:
                pairs[(ev["wallet"], r[0])].add("cluster")
            clusters.setdefault(ev["wallet"], e["address"])
    return pairs, clusters


def run_pnl(chain: Chain, result: dict, progress=lambda m: None, top_repeat: int = 30) -> dict:
    pairs, clusters = pick_pairs(result, top_repeat)
    progs: dict[str, str] = {}
    rows = []
    for k, ((w, m), why) in enumerate(sorted(pairs.items()), 1):
        try:
            if m not in progs:
                progs[m] = mint_program(chain, m)
            r = pair_pnl(chain, w, m, progs[m])
        except Exception as e:                              # one pair never stops the run
            r = {"wallet": w, "mint": m, "status": f"error: {str(e)[:80]}", "net_sol": None}
        r["why"] = sorted(why)
        rows.append(r)
        if k % 50 == 0:
            progress(f"pnl {k}/{len(pairs)} pairs, rpc {chain.calls}")
    per_wallet: dict[str, dict] = defaultdict(lambda: {"net_sol": 0.0, "spent_sol": 0.0, "received_sol": 0.0,
                                                       "coins": 0, "wins": 0, "unknown": 0, "why": set()})
    for r in rows:
        p = per_wallet[r["wallet"]]
        p["why"] |= set(r.get("why") or [])
        if r.get("status") != "ok":
            p["unknown"] += 1
            continue
        p["coins"] += 1
        p["wins"] += r["net_sol"] > 0
        for f in ("net_sol", "spent_sol", "received_sol"):
            p[f] += r[f]
    wallets = sorted(({"wallet": w, **{k: (round(v, 3) if isinstance(v, float) else v) for k, v in p.items()
                                      if k != "why"}, "why": sorted(p["why"]), "cluster": clusters.get(w)}
                      for w, p in per_wallet.items()), key=lambda x: -x["net_sol"])
    per_cluster: dict[str, dict] = defaultdict(lambda: {"net_sol": 0.0, "wallets": 0, "coins": 0})
    for x in wallets:
        if x["cluster"]:
            c = per_cluster[x["cluster"]]
            c["net_sol"] += x["net_sol"]
            c["wallets"] += 1
            c["coins"] += x["coins"]
    return {"pairs": len(rows), "rpc_calls": chain.calls,
            "status": dict(sorted(defaultdict(int, {s: sum(1 for r in rows if (r.get("status") or "").split(":")[0] == s)
                                                   for s in {(r.get("status") or "").split(":")[0] for r in rows}}).items())),
            "wallets": wallets[:300],
            "clusters": sorted(({"address": a, **{k: round(v, 3) if isinstance(v, float) else v for k, v in c.items()}}
                                for a, c in per_cluster.items()), key=lambda x: -x["net_sol"]),
            "rows": rows}


def group_pnl(chain: Chain, result: dict, rows: list[dict], depth: int = 2, fan: int = 6, progress=lambda m: None,
              programs: dict | None = None) -> list[dict]:
    """Per coin: the deployer's own trades PLUS every wallet it sent the coin to (and theirs, `depth` levels).
    Insiders often buy with one wallet and sell from others; tokens sent to an exchange are sold off-chain
    (proceeds unknown) and reported apart."""
    from insiders.scan import KNOWN_LABELS
    programs = programs or {}
    cache = {(r["wallet"], r["mint"]): r for r in rows if r.get("status") == "ok"}
    out = []
    for t in result.get("tokens", []):
        dep, mint = t.get("deployer"), t["mint"]
        if t.get("status") != "ok" or not dep or (dep, mint) not in cache:
            continue
        members, frontier, to_cex = {dep: 0}, [dep], defaultdict(int)
        for level in range(1, depth + 1):
            nxt = []
            for w in frontier:
                r = cache.get((w, mint))
                if not r:
                    continue
                for to in (r.get("sent_to") or [])[:fan]:
                    kind = KNOWN_LABELS.get(to, (None, None))[1]
                    if kind in ("exchange", "deposit"):
                        to_cex[KNOWN_LABELS[to][0]] += 1
                        continue
                    if to in members:
                        continue
                    members[to] = level
                    if (to, mint) not in cache:
                        try:
                            if mint not in programs:
                                programs[mint] = mint_program(chain, mint)
                            pr = pair_pnl(chain, to, mint, programs[mint])
                        except Exception as e:
                            pr = {"status": f"error: {str(e)[:60]}"}
                        if pr.get("status") == "ok":
                            cache[(to, mint)] = pr
                    nxt.append(to)
            frontier = nxt
        rs = [cache[(w, mint)] for w in members if (w, mint) in cache]
        out.append({"mint": mint, "deployer": dep, "wallets": len(members), "priced": len(rs),
                    "spent_sol": round(sum(r["spent_sol"] for r in rs), 3),
                    "received_sol": round(sum(r["received_sol"] for r in rs), 3),
                    "net_sol": round(sum(r["net_sol"] for r in rs), 3),
                    "deployer_net_sol": cache[(dep, mint)]["net_sol"], "sent_to_exchange": dict(to_cex),
                    "members": [{"wallet": w, "level": lv, "net_sol": cache.get((w, mint), {}).get("net_sol")}
                                for w, lv in sorted(members.items(), key=lambda x: x[1])][:12]})
        if len(out) % 20 == 0:
            progress(f"deployer groups {len(out)}, rpc {chain.calls}")
    return sorted(out, key=lambda x: -x["net_sol"])
