"""Insider scan of a call channel's tokens (research only, read-only chain data; nothing is signed or sent).

For every called token:
  1. the deployer (fee payer of the token's first transaction) and the first N wallets that RECEIVED the token
     (early buyers / bundle wallets), each marked before / after the call time;
  2. wallets seen in several tokens;
  3. the SOL flow of those wallets 1-2 hops back / forward: who funded them first, where their SOL went after the
     buy (cash-out), so one funder / sink shared by many tokens shows even when a fresh wallet is used per token.

Data: Helius `getTransactionsForAddress` (full transactions in block-time order) and `getAccountInfo`. The Helius key
stays on the server (env HELIUS_API_KEY); it is never returned by the API. Program-owned accounts (bonding curves,
pools, vault authorities) are excluded by the ed25519 on-curve test: only real wallets are counted.
High-activity addresses (exchanges, routers, bots: >= HUB_TX_PER_MIN transactions per minute) are reported apart as
hubs and never as "the wallet behind" a cluster."""
from __future__ import annotations

import csv
import json
import os
import re
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

N_BUYERS = 40
MAX_TOKEN_PAGES = 5
TRACE_PER_TOKEN = 8               # deployer + earliest buyers traced per token (pre-call first)
BOT_TOKENS_PER_100TX = 20         # a wallet whose last 100 tx touch >= 20 tokens buys everything new (sniper bot)
HUB_TX_PER_MIN = 1.0              # >= 1 440 tx/day: exchange hot wallet / router / bot, not an operator
MIN_TRANSFER_LAMPORTS = 10_000_000   # 0.01 SOL: dust / rent is not a funding link
FEE_AVG_SOL = 0.25                # a receiver getting < 0.25 SOL per wallet on average: platform fee / tip account
SYSTEM = "11111111111111111111111111111111"
TOKEN_PROGRAMS = {"TokenkegQfeZyiNwAJbNbGKPFXCWuBvf9Ss623VQ5DA", "TokenzQdBNbLqP5VEhdkAS6EPFLC1PHnBqCXEpPxuEb"}
CALL_TZ = timezone(timedelta(hours=7))
# Public names shown by Solscan (checked by hand 2026-10-10). Exchange HOT wallets serve everyone -> hub, never "the
# wallet behind". An exchange DEPOSIT address belongs to ONE exchange account -> kept as a lead (not a hub).
KNOWN_LABELS = {
    "5tzFkiKscXHK5ZXCGbXZxdw7gTjjD1mBwuoFbhUvuAi9": ("Binance 2", "exchange"),
    "is6MTRHEgyFLNTfYcuV4QBWLjrZBfmhVNYR6ccgr8KV": ("OKX Hot Wallet 1", "exchange"),
    "iGdFcQoyR2MwbXMHQskhmNsqddZ6rinsipHc4TNSdwu": ("Bybit Wallet 10", "exchange"),
    "AC5RDfQFmDS1deWZos921JfqscXdByf8BKHs5ACWjtW2": ("Bybit Hot Wallet", "exchange"),
    "ASTyfSima4LLAdDgoFGkgqoKowG1LZFDr9fAQrg7iaJZ": ("MEXC", "exchange"),
    "BmFdpraQhkiDQE6SnfG5omcA1VwzqfXrwtNYBwWTymy6": ("KuCoin Hot Wallet", "exchange"),
    "A77HErqtfN1hLLpvZ9pCtu66FEtM8BveoaKbbMoZ4RiR": ("Bitget Exchange", "exchange"),
    "6LY1JzAFVZsP2a2xKrtU6znQMQ5h4i7tocWdgrkZzkzF": ("Kraken Hot Wallet", "exchange"),
    "21wG4F3ZR8gwGC47CkpD6ySBUgH9AABtYMBWFiYdTTgv": ("Binance deposit address (one account)", "deposit"),
    "u6PJ8DtQuPFnfmwHbGFULQ4u4EgjDiyYKjVEsynXq2w": ("pump.fun user bygonekraken600", "person"),
    "FWznbcNXWQuHTawe9RxvQ2LdCENssh12dsznf4RiouN5": ("Kraken Hot Wallet", "exchange"),
    "5g7yNHyGLJ7fiQ9SN9mf47opDnMjc585kqXWt6d7aBWs": ("Coinbase Hot Wallet", "exchange"),
    "5ndLnEYqSFiA5yUFHo6LVZ1eWc6Rhh11K5CfJNkoHEPs": ("FixedFloat Exchange", "exchange"),
    "2snHHreXbpJ7UwZxPe37gnUNf7Wx7wv6UKDSR2JckKuS": ("deBridge Bridge Vault", "exchange"),
    "F7p3dFrjRTbtRp8FRF6qHLomXbKRBzpvBLjtQcfcgmNe": ("Relay Solver (bridge)", "exchange"),
    "D2L6yPZ2FmmmTKPgzaMKdhu6EWZcTpLy1Vhx8uvZe7NZ": ("Helius Tipping Account 2", "fee"),
    "88xTWZMeKfiTgbfEmPLdsUCQcZinwUfk25EBQZ21XMAZ": ("Huobi", "exchange"),
    "BY4StcU9Y2BpgH8quZzorg31EGE4L1rjomN8FNsCBEcx": ("HTX Hot Wallet", "exchange"),
    "12unoFRA4pZ1UBgjwNranXdqJLEy9vgDwXhqBgpevEPZ": ("Binance deposit address (one account)", "deposit"),
    "AKbotZVF3zr9i4Cz2Lv34s6SydytjdidruoGTnvwPxFh": ("akbot (trading bot)", "fee"),
}
_B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_B58_RE = re.compile(r"^[1-9A-HJ-NP-Za-km-z]{32,44}$")
CALLS_CSV = Path(__file__).with_name("sol_pump_insiders_calls.csv")
RESULT_JSON = Path(__file__).with_name("result.json")


# ---------------------------------------------------------------- addresses
def b58decode(s: str) -> bytes:
    n = 0
    for ch in s:
        n = n * 58 + _B58.index(ch)
    raw = n.to_bytes((n.bit_length() + 7) // 8, "big") if n else b""
    return b"\0" * (len(s) - len(s.lstrip("1"))) + raw


_P = 2 ** 255 - 19
_D = (-121665 * pow(121666, _P - 2, _P)) % _P


def on_curve(addr: str) -> bool:
    """True for a normal wallet (an ed25519 public key); False for a program-derived address (PDA)."""
    try:
        b = b58decode(addr)
    except ValueError:
        return False
    if len(b) != 32:
        return False
    y = int.from_bytes(b, "little") & ((1 << 255) - 1)
    if y >= _P:
        return False
    u, v = (y * y - 1) % _P, (_D * y * y + 1) % _P
    x2 = u * pow(v, _P - 2, _P) % _P
    return x2 == 0 or pow(x2, (_P - 1) // 2, _P) == 1


def is_solana_address(s: str) -> bool:
    return bool(_B58_RE.match(s or "")) and len(b58decode(s)) == 32


# ---------------------------------------------------------------- calls file
def load_calls(path: Path = CALLS_CSV) -> list[dict]:
    out, seen = [], set()
    with open(path, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            ca = (r.get("ca") or "").strip()
            if ca in seen:
                continue
            seen.add(ca)
            ts = int(datetime.strptime(r["call_time_utc7"].strip(), "%d.%m.%Y %H:%M:%S")
                     .replace(tzinfo=CALL_TZ).timestamp())
            out.append({"mint": ca, "call_ts": ts, "ticker": (r.get("ticker") or "").strip(),
                        "text": (r.get("text") or "")[:120], "dex_status": r.get("status") or ""})
    return out


# ---------------------------------------------------------------- RPC with cache
class Chain:
    """Helius JSON-RPC with retries and a SQLite cache of immutable answers (historical ascending pages)."""

    def __init__(self, api_key: str, cache_path: str | Path, budget_calls: int = 5000, sleep=time.sleep):
        self.url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"
        self.db = sqlite3.connect(str(cache_path), check_same_thread=False)
        self.db.execute("CREATE TABLE IF NOT EXISTS cache (k TEXT PRIMARY KEY, v TEXT NOT NULL)")
        self.lock = threading.Lock()
        self.calls = self.cached = 0
        self.budget, self.sleep = budget_calls, sleep

    def rpc(self, method: str, params: list, cache: bool = True) -> dict:
        key = json.dumps([method, params], sort_keys=True)
        if cache:
            with self.lock:
                r = self.db.execute("SELECT v FROM cache WHERE k=?", (key,)).fetchone()
            if r:
                self.cached += 1
                return json.loads(r[0])
        if self.calls >= self.budget:
            raise RuntimeError(f"call budget {self.budget} reached")
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
        last = None
        for k in range(4):
            try:
                req = urllib.request.Request(self.url, data=body, headers={"Content-Type": "application/json"})
                res = json.loads(urllib.request.urlopen(req, timeout=90).read())
                self.calls += 1
                if res.get("error"):
                    msg = str(res["error"])[:200]
                    if re.search(r"rate|limit|timeout|busy|unavailable|429", msg, re.I) and k < 3:
                        last = msg
                        self.sleep(2 * 2 ** k)
                        continue
                    raise RuntimeError(f"rpc error: {msg}")
                if cache:
                    with self.lock:
                        self.db.execute("INSERT OR REPLACE INTO cache VALUES (?, ?)", (key, json.dumps(res)))
                        self.db.commit()
                return res
            except urllib.error.HTTPError as e:            # never echo the URL (it holds the key)
                last = f"HTTP {e.code}"
                if e.code not in (429, 500, 502, 503, 504):
                    break
            except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
                last = type(e).__name__
            self.sleep(2 * 2 ** k)
        raise RuntimeError(f"request failed: {last}")

    def history(self, address: str, gte: int | None = None, token: str | None = None, order: str = "asc",
                limit: int = 100) -> dict:
        opts = {"transactionDetails": "full", "sortOrder": order, "limit": limit, "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1}
        if gte is not None:
            opts["filters"] = {"blockTime": {"gte": int(gte)}}
        if token:
            opts["paginationToken"] = token
        # ascending pages that start in the past are immutable; descending (latest) pages are not cached
        return self.rpc("getTransactionsForAddress", [address, opts], cache=(order == "asc")).get("result") or {}


# ---------------------------------------------------------------- transaction parsing
def _keys(tx: dict) -> list[str]:
    ks = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    return [k.get("pubkey") if isinstance(k, dict) else k for k in ks]


def fee_payer(tx: dict) -> str | None:
    k = _keys(tx)
    return k[0] if k else None


def token_deltas(tx: dict, mint: str) -> dict[str, int]:
    """Net change of `mint` per OWNER in this transaction."""
    meta = tx.get("meta") or {}
    out: dict[str, int] = defaultdict(int)
    for sign, rows in ((-1, meta.get("preTokenBalances")), (1, meta.get("postTokenBalances"))):
        for b in rows or []:
            if b.get("mint") == mint and b.get("owner"):
                out[b["owner"]] += sign * int(b["uiTokenAmount"]["amount"])
    return {o: d for o, d in out.items() if d}


def sol_transfers(tx: dict) -> list[tuple[str, str, int]]:
    """System-program SOL transfers (outer and inner): (source, destination, lamports)."""
    msg = (tx.get("transaction") or {}).get("message") or {}
    ins = list(msg.get("instructions") or [])
    for inner in (tx.get("meta") or {}).get("innerInstructions") or []:
        ins += inner.get("instructions") or []
    out = []
    for i in ins:
        p = i.get("parsed")
        if i.get("programId") != SYSTEM or not isinstance(p, dict):
            continue
        info = p.get("info") or {}
        if p.get("type") in ("transfer", "transferWithSeed") and info.get("lamports"):
            out.append((info.get("source"), info.get("destination"), int(info["lamports"])))
        elif p.get("type") == "createAccount" and info.get("lamports"):
            out.append((info.get("source"), info.get("newAccount"), int(info["lamports"])))
    return out


def ok(tx: dict) -> bool:
    return (tx.get("meta") or {}).get("err") is None


# ---------------------------------------------------------------- step 1: deployer + early receivers
def scan_token(chain: Chain, call: dict, n_buyers: int = N_BUYERS, max_pages: int = MAX_TOKEN_PAGES) -> dict:
    mint, call_ts = call["mint"], call["call_ts"]
    res = {**call, "status": "ok", "deployer": None, "created_ts": None, "buyers": [], "pages": 0}
    if not is_solana_address(mint):
        return {**res, "status": "not_solana"}
    info = (chain.rpc("getAccountInfo", [mint, {"encoding": "jsonParsed"}], cache=False).get("result") or {}) \
        .get("value")
    if not info or info.get("owner") not in TOKEN_PROGRAMS or \
            ((info.get("data") or {}).get("parsed") or {}).get("type") != "mint":
        return {**res, "status": "not_a_mint"}
    seen, token = {}, None
    while res["pages"] < max_pages and len(seen) < n_buyers:
        page = chain.history(mint, gte=0, token=token)
        res["pages"] += 1
        for tx in page.get("data") or []:
            if res["created_ts"] is None:
                res["created_ts"], res["deployer"] = tx.get("blockTime"), fee_payer(tx)
            if not ok(tx):
                continue
            ts = tx.get("blockTime")
            for owner, d in token_deltas(tx, mint).items():
                if d <= 0 or owner in seen or owner == res["deployer"] or not on_curve(owner):
                    continue
                seen[owner] = {"wallet": owner, "ts": ts, "tokens": d, "pre_call": ts is not None and ts < call_ts,
                               "rank": len(seen) + 1, "payer": fee_payer(tx),
                               "sig": ((tx.get("transaction") or {}).get("signatures") or [None])[0]}
                if len(seen) >= n_buyers:
                    break
            if len(seen) >= n_buyers:
                break
        token = page.get("paginationToken")
        if not token or not page.get("data"):
            break
    res["buyers"] = list(seen.values())
    if res["created_ts"] is None:
        res["status"] = "no_history"
    return res


# ---------------------------------------------------------------- step 3: SOL flow of a wallet
def trace_wallet(chain: Chain, wallet: str, after_ts: int | None) -> dict:
    """First funders (from the start of its history) and SOL sinks after `after_ts` (its buy: the cash-out)."""
    first = chain.history(wallet, gte=0)
    txs = first.get("data") or []
    funders: dict[str, int] = defaultdict(int)
    first_funder = None
    for tx in txs:
        if not ok(tx):
            continue
        for src, dst, lam in sol_transfers(tx):
            if dst == wallet and src and src != wallet and lam >= MIN_TRANSFER_LAMPORTS and on_curve(src):
                funders[src] += lam
                if first_funder is None:
                    first_funder = {"address": src, "lamports": lam, "ts": tx.get("blockTime")}
    if after_ts is not None and first.get("paginationToken") and txs and (txs[-1].get("blockTime") or 0) < after_ts:
        txs = chain.history(wallet, gte=after_ts).get("data") or []
    sinks: dict[str, int] = defaultdict(int)
    for tx in txs:
        if not ok(tx) or (after_ts is not None and (tx.get("blockTime") or 0) < after_ts):
            continue
        for src, dst, lam in sol_transfers(tx):
            if src == wallet and dst and dst != wallet and lam >= MIN_TRANSFER_LAMPORTS and on_curve(dst):
                sinks[dst] += lam
    return {"wallet": wallet, "first_funder": first_funder,
            "funders": dict(sorted(funders.items(), key=lambda x: -x[1])[:5]),
            "sinks": dict(sorted(sinks.items(), key=lambda x: -x[1])[:5]),
            "history_txs_first_page": len(first.get("data") or [])}


def activity(chain: Chain, address: str) -> dict:
    """Recent transaction rate: a hub (exchange, router, bot) does many per minute."""
    page = chain.history(address, order="desc", limit=100)
    data = page.get("data") or []
    ts = [t.get("blockTime") for t in data if t.get("blockTime")]
    mints = {b.get("mint") for t in data for b in ((t.get("meta") or {}).get("postTokenBalances") or [])
             if b.get("owner") == address} - {"So11111111111111111111111111111111111111112"}
    if len(ts) < 2:
        return {"txs": len(ts), "per_min": 0.0, "hub": False, "tokens_traded": len(mints), "span_h": 0.0}
    span = max(1, max(ts) - min(ts))
    per_min = len(ts) / (span / 60)
    return {"txs": len(ts), "per_min": round(per_min, 2), "hub": len(ts) >= 100 and per_min >= HUB_TX_PER_MIN,
            "tokens_traded": len(mints), "span_h": round(span / 3600, 1)}


# ---------------------------------------------------------------- whole run
def run(chain: Chain, calls: list[dict], progress=lambda m: None, n_buyers: int = N_BUYERS,
        trace_per_token: int = TRACE_PER_TOKEN, hop2_top: int = 40) -> dict:
    t0 = time.time()
    tokens = []
    for k, c in enumerate(calls, 1):
        try:
            tokens.append(scan_token(chain, c, n_buyers))
        except Exception as e:                              # one bad token never stops the run
            tokens.append({**c, "status": f"error: {str(e)[:120]}", "buyers": [], "deployer": None})
        progress(f"token {k}/{len(calls)} {c.get('ticker') or c['mint'][:6]}: {tokens[-1]['status']}")
    good = [t for t in tokens if t["status"] == "ok"]

    # step 2: wallets across tokens
    roles: dict[str, dict] = defaultdict(lambda: {"tokens": set(), "pre_call": set(), "deployer": set()})
    for t in good:
        if t["deployer"]:
            roles[t["deployer"]]["tokens"].add(t["mint"])
            roles[t["deployer"]]["deployer"].add(t["mint"])
        for b in t["buyers"]:
            roles[b["wallet"]]["tokens"].add(t["mint"])
            if b["pre_call"]:
                roles[b["wallet"]]["pre_call"].add(t["mint"])
    repeat = sorted(((w, r) for w, r in roles.items() if len(r["tokens"]) >= 2),
                    key=lambda x: (-len(x[1]["tokens"]), -len(x[1]["pre_call"])))

    # step 3: who funds / receives from each token's insiders
    to_trace: dict[str, tuple[str, int | None]] = {}
    for t in good:
        picks = ([{"wallet": t["deployer"], "ts": t["created_ts"]}] if t["deployer"] else []) + \
            sorted(t["buyers"], key=lambda b: (not b["pre_call"], b["rank"]))[:trace_per_token - 1]
        for b in picks:
            to_trace.setdefault(b["wallet"], (t["mint"], b["ts"]))
    for w, _ in repeat[:50]:
        to_trace.setdefault(w, (next(iter(roles[w]["tokens"])), None))
    traces = {}
    for k, (w, (_, ts)) in enumerate(to_trace.items(), 1):
        try:
            traces[w] = trace_wallet(chain, w, ts)
        except Exception as e:
            traces[w] = {"wallet": w, "error": str(e)[:120], "funders": {}, "sinks": {}, "first_funder": None}
        if k % 25 == 0:
            progress(f"traced {k}/{len(to_trace)} wallets")

    rep_act = {}
    for w, _ in repeat[:30]:
        try:
            rep_act[w] = activity(chain, w)
        except Exception as e:
            rep_act[w] = {"error": str(e)[:120]}

    def link_table(field: str) -> dict[str, dict]:
        tab: dict[str, dict] = defaultdict(lambda: {"tokens": set(), "wallets": set(), "lamports": 0})
        for w, tr in traces.items():
            for x, lam in (tr.get(field) or {}).items():
                tab[x]["wallets"].add(w)
                tab[x]["lamports"] += lam
                tab[x]["tokens"] |= roles[w]["tokens"] if w in roles else set()
        return tab
    funders, sinks = link_table("funders"), link_table("sinks")

    # hop 2: funders of the most shared hop-1 counterparties, and hub check
    def n_wallets(x):
        return len((funders.get(x) or {"wallets": set()})["wallets"] | (sinks.get(x) or {"wallets": set()})["wallets"])
    def fee_like(x):
        s_ = sinks.get(x)
        return x not in funders and s_ and s_["lamports"] / 1e9 / max(1, len(s_["wallets"])) < FEE_AVG_SOL
    shared = sorted((x for x in set(funders) | set(sinks) if n_wallets(x) >= 2 and not fee_like(x)
                     and KNOWN_LABELS.get(x, (None, None))[1] not in ("exchange", "fee")),
                    key=lambda x: -n_wallets(x))[:hop2_top]
    hop2, acts = {}, {}
    for x in shared:
        try:
            acts[x] = activity(chain, x)
            if not acts[x]["hub"] and KNOWN_LABELS.get(x, (None, None))[1] != "exchange":
                hop2[x] = trace_wallet(chain, x, None)
        except Exception as e:
            acts[x] = {"error": str(e)[:120], "hub": False}
    parents: dict[str, dict] = defaultdict(lambda: {"children": set(), "tokens": set()})
    for x, tr in hop2.items():
        for p in tr.get("funders") or {}:
            parents[p]["children"].add(x)
            parents[p]["tokens"] |= (funders.get(x) or {"tokens": set()})["tokens"] | \
                (sinks.get(x) or {"tokens": set()})["tokens"]

    def entity_rows():
        rows = []
        for x in set(funders) | set(sinks):
            f, s = funders.get(x), sinks.get(x)
            toks = (f["tokens"] if f else set()) | (s["tokens"] if s else set())
            n_w = len((f["wallets"] if f else set()) | (s["wallets"] if s else set()))
            label, kind = KNOWN_LABELS.get(x, (None, None))
            if kind is None and not f and s and s["lamports"] / 1e9 / max(1, len(s["wallets"])) < FEE_AVG_SOL:
                label, kind = "small fees / tips (platform, bot)", "fee"
            rows.append({"address": x, "n_tokens": len(toks), "tokens": sorted(toks), "n_wallets": n_w,
                         "wallets": sorted((f["wallets"] if f else set()) | (s["wallets"] if s else set()))[:40],
                         "funded_wallets": len(f["wallets"]) if f else 0,
                         "funded_sol": round((f["lamports"] if f else 0) / 1e9, 3),
                         "received_from_wallets": len(s["wallets"]) if s else 0,
                         "received_sol": round((s["lamports"] if s else 0) / 1e9, 3),
                         "is_insider_wallet": x in roles,
                         "label": label, "kind": kind, "activity": acts.get(x),
                         # a labelled person / deposit address is a lead even when it is busy
                         "hub": kind in ("exchange", "fee") or (kind not in ("person", "deposit") and
                                                                 bool((acts.get(x) or {}).get("hub")))})
        rows.sort(key=lambda r: (-r["n_wallets"], -r["n_tokens"]))
        return rows
    entities = entity_rows()
    where: dict[str, list] = defaultdict(list)            # wallet -> its roles: [mint, "deployer" | rank, pre_call]
    for t in good:
        if t["deployer"]:
            where[t["deployer"]].append([t["mint"], "deployer", True])
        for b in t["buyers"]:
            where[b["wallet"]].append([t["mint"], b["rank"], b["pre_call"]])
    for e in entities:
        e["evidence"] = [{"wallet": w, "roles": where.get(w, [])[:4],
                          "in_sol": round((traces.get(w, {}).get("sinks") or {}).get(e["address"], 0) / 1e9, 3),
                          "from_sol": round((traces.get(w, {}).get("funders") or {}).get(e["address"], 0) / 1e9, 3)}
                         for w in e["wallets"]]
    ticker = {t["mint"]: t.get("ticker") or t["mint"][:6] for t in tokens}
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "seconds": round(time.time() - t0, 1), "rpc_calls": chain.calls, "cache_hits": chain.cached,
        "params": {"n_buyers": n_buyers, "trace_per_token": trace_per_token, "hub_tx_per_min": HUB_TX_PER_MIN,
                   "min_transfer_sol": MIN_TRANSFER_LAMPORTS / 1e9},
        "tickers": ticker,
        "tokens": [{k: v for k, v in t.items() if k != "text"} for t in tokens],
        "repeat_wallets": [{"wallet": w, "n_tokens": len(r["tokens"]), "n_pre_call": len(r["pre_call"]),
                            "deployer_of": sorted(r["deployer"]), "tokens": sorted(r["tokens"]),
                            "activity": rep_act.get(w),
                            "bot_like": (rep_act.get(w) or {}).get("tokens_traded", 0) >= BOT_TOKENS_PER_100TX}
                           for w, r in repeat[:200]],
        # >= 2 distinct traced wallets: one prolific wallet alone cannot make its counterparties look shared
        "entities": [e for e in entities if e["n_tokens"] >= 2 and e["n_wallets"] >= 2][:200],
        "hop2_parents": sorted(({"address": p, "n_children": len(v["children"]), "children": sorted(v["children"]),
                                 "n_tokens": len(v["tokens"]), "tokens": sorted(v["tokens"])}
                                for p, v in parents.items() if len(v["tokens"]) >= 2 and len(v["children"]) >= 2),
                               key=lambda r: -r["n_tokens"])[:50],
        "traced_wallets": len(traces),
    }


def default_cache() -> Path:
    return Path(os.environ.get("INSIDERS_CACHE") or (Path(os.environ.get("TMPDIR", "/tmp")) / "insiders_cache.db"))
