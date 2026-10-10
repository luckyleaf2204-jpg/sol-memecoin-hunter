"""PumpSwap outcome fetcher (docs/smart_money_plan.md amendment 5). After a pump.fun curve completes, the token
trades on its PumpSwap pool, which the live recorder does not see. This fills, for each completed mint:

  amm_trades        every swap of the token's canonical PumpSwap pool (base = the mint, quote = WSOL) in
                    [completion, to_ts]: block time, fee payer = wallet, side, SOL (lamports), tokens (raw)
  amm_fetch         (mint, from_ts, to_ts): the price path is COMPLETE over that range
  amm_wallet_fetch  (mint, wallet, from_ts, to_ts) for every wallet that bought the mint on the curve: the pool
                    history contains every wallet, so that wallet's PumpSwap activity on the pool is complete too

A completeness row is written ONLY after every page of [from_ts, to_ts] was read without an error and the
consistency checks passed; otherwise nothing is marked complete, the copies stay UNRESOLVED and the analysis verdict
stays BLOCKED_MIGRATION_DATA. Nothing is interpolated, filled or guessed. No outcome is computed here.

Price = the pool's own vault changes in the transaction (quote WSOL delta / base token delta), the same units as the
curve TradeEvent (lamports per raw token). Limits: the vault delta includes the pool's LP fee (~0.2 %); the wallet is
the fee payer (a relayer-paid swap is attributed to the relayer); swaps on any other pool / DEX are not seen.

Sources: GeckoTerminal (pool lookup, no key) and Helius `getTransactionsForAddress` (pool history in block-time
order). The Helius key is read from the environment by the caller and never logged."""
from __future__ import annotations

import json
import re
import sqlite3
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

from smartmoney.analysis import AMM_SCHEMA, BIG, MAX_HOLD_S

WSOL = "So11111111111111111111111111111111111111112"
GT_POOLS = "https://api.geckoterminal.com/api/v2/networks/solana/tokens/{mint}/pools?page=1"
POOL_CREATED_TOL_S = 600         # the migration pool is created at the completion; a pool far from it is not it
PRICE_JUMP_MAX = 5.0             # pool's initial reserve price vs last curve price: only a unit / wrong-pool error
                                 # trips it (NOT the first swap: a migration-second snipe can legitimately move the
                                 # price 30x+, seen live)
PAGE_LIMIT = 100
MAX_PAGES = 400                  # per mint; more pages -> not complete (stays UNRESOLVED), never a partial result
RETRIES = 4
CREDITS_PER_PAGE = 100

FETCH_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS amm_fetch_log (at REAL NOT NULL, mint TEXT NOT NULL, pool TEXT, from_ts INTEGER,
                                          to_ts INTEGER, pages INTEGER, swaps INTEGER, status TEXT NOT NULL,
                                          detail TEXT);
"""


class FetchError(RuntimeError):
    pass


class _Final(Exception):
    pass


TRANSIENT = re.compile(r"rate|limit|timeout|timed out|busy|unavailable|overload|429", re.I)


def http_json(url: str, body: dict | None = None, timeout: float = 60.0) -> dict:
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    return json.loads(urllib.request.urlopen(req, timeout=timeout).read())


def with_retry(fn, retries: int = RETRIES, sleep=time.sleep, base_s: float = 2.0):
    """Retry transient failures (HTTP 429 / 5xx, network, JSON-RPC error); the last failure is raised."""
    for k in range(retries):
        try:
            r = fn()
            if isinstance(r, dict) and r.get("error"):
                msg = str(r["error"])[:160]
                if not TRANSIENT.search(msg) or k == retries - 1:   # a request error is not retried
                    raise _Final(f"rpc error: {msg}")
                raise FetchError(f"rpc error: {msg}")
            return r
        except _Final as e:
            raise FetchError(str(e)) from None
        except urllib.error.HTTPError as e:
            if e.code not in (429, 500, 502, 503, 504) or k == retries - 1:
                raise FetchError(f"HTTP {e.code}") from None
        except (urllib.error.URLError, TimeoutError, ConnectionError, FetchError, json.JSONDecodeError) as e:
            if k == retries - 1:
                raise FetchError(f"{type(e).__name__}: {str(e)[:160]}") from None
        sleep(base_s * 2 ** k)


def _iso_ts(s: str | None) -> int | None:
    if not s:
        return None
    return int(datetime.fromisoformat(s.replace("Z", "+00:00")).astimezone(timezone.utc).timestamp())


def find_pool(mint: str, completion_ts: int, get=http_json) -> str:
    """The canonical PumpSwap pool of a migrated mint: dex pumpswap, base = mint, quote = WSOL, created within
    POOL_CREATED_TOL_S of the completion. None found or more than one candidate -> FetchError (no guessing)."""
    d = with_retry(lambda: get(GT_POOLS.format(mint=mint)))
    cands = []
    for p in d.get("data") or []:
        a, r = p.get("attributes") or {}, p.get("relationships") or {}
        dex = ((r.get("dex") or {}).get("data") or {}).get("id")
        base = ((r.get("base_token") or {}).get("data") or {}).get("id", "")
        quote = ((r.get("quote_token") or {}).get("data") or {}).get("id", "")
        created = _iso_ts(a.get("pool_created_at"))
        if dex == "pumpswap" and base == f"solana_{mint}" and quote == f"solana_{WSOL}" and created is not None \
                and abs(created - completion_ts) <= POOL_CREATED_TOL_S:
            cands.append(a["address"])
    if len(cands) != 1:
        raise FetchError(f"pumpswap pool candidates: {len(cands)}")
    return cands[0]


def parse_swap(tx: dict, pool: str, mint: str) -> dict | None:
    """One pool transaction -> a swap from the pool's vault changes, or None (failed tx, liquidity change, other)."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None

    def bal(rows):
        out = {}
        for b in rows or []:
            if b.get("owner") == pool and b.get("mint") in (mint, WSOL):
                out[b["mint"]] = out.get(b["mint"], 0) + int(b["uiTokenAmount"]["amount"])
        return out
    pre, post = bal(meta.get("preTokenBalances")), bal(meta.get("postTokenBalances"))
    d_base = post.get(mint, 0) - pre.get(mint, 0)
    d_quote = post.get(WSOL, 0) - pre.get(WSOL, 0)
    if d_base < 0 < d_quote:
        is_buy = True                                   # tokens left the pool, SOL came in
    elif d_quote < 0 < d_base:
        is_buy = False
    else:
        return None
    keys = ((tx.get("transaction") or {}).get("message") or {}).get("accountKeys") or []
    payer = keys[0].get("pubkey") if keys and isinstance(keys[0], dict) else (keys[0] if keys else None)
    sigs = (tx.get("transaction") or {}).get("signatures") or [None]
    if tx.get("blockTime") is None or not payer:
        raise FetchError("swap without block time or fee payer")
    return {"ts": int(tx["blockTime"]), "wallet": payer, "is_buy": is_buy, "sol": abs(d_quote),
            "token": abs(d_base), "sig": sigs[0], "slot": tx.get("slot")}


def pool_init_price(tx: dict, pool: str, mint: str) -> float | None:
    """The pool-creation (migration) transaction: the pool holds no base / quote before and both after. Returns the
    initial reserve price (lamports per raw token) or None for any other transaction."""
    meta = tx.get("meta") or {}
    if meta.get("err") is not None:
        return None

    def held(rows):
        return {b["mint"]: int(b["uiTokenAmount"]["amount"]) for b in rows or []
                if b.get("owner") == pool and b.get("mint") in (mint, WSOL)}
    pre, post = held(meta.get("preTokenBalances")), held(meta.get("postTokenBalances"))
    if any(pre.values()) or not (post.get(mint, 0) > 0 and post.get(WSOL, 0) > 0):
        return None
    return post[WSOL] / post[mint]


def fetch_pool_swaps(pool: str, mint: str, from_ts: int, to_ts: int, rpc, max_pages: int = MAX_PAGES,
                     sleep=time.sleep, spent: dict | None = None) -> tuple[list[dict], int, float | None]:
    """Every swap of `pool` with block time in [from_ts, to_ts], oldest first, read to the end of the range, plus the
    pool's initial reserve price if its creation is in the range. Raises FetchError when a page fails after retries,
    the order / times are inconsistent, or max_pages is hit. `spent["pages"]` counts every page read (credits),
    also when it raises."""
    spent = spent if spent is not None else {}
    token, pages, out, seen, last_ts, init = None, 0, [], set(), from_ts, None
    while True:
        if pages >= max_pages:
            raise FetchError(f"page cap {max_pages} reached before {to_ts}")
        opts = {"transactionDetails": "full", "sortOrder": "asc", "limit": PAGE_LIMIT, "encoding": "jsonParsed",
                "maxSupportedTransactionVersion": 1, "commitment": "finalized",
                "filters": {"blockTime": {"gte": int(from_ts), "lte": int(to_ts)}}}
        if token:
            opts["paginationToken"] = token
        res = with_retry(lambda: rpc("getTransactionsForAddress", [pool, opts]), sleep=sleep).get("result")
        if not isinstance(res, dict):
            raise FetchError("malformed page")
        pages += 1
        spent["pages"] = spent.get("pages", 0) + 1
        for tx in res.get("data") or []:
            bt = tx.get("blockTime")
            if bt is None or bt < from_ts or bt > to_ts or bt < last_ts:
                raise FetchError(f"inconsistent block time {bt} (range {from_ts}-{to_ts}, previous {last_ts})")
            last_ts = bt
            if init is None and not out:
                init = pool_init_price(tx, pool, mint)
            sw = parse_swap(tx, pool, mint)
            if sw is None:
                continue
            if sw["sig"] in seen:                        # the same transaction on two pages: kept once
                continue
            seen.add(sw["sig"])
            out.append(sw)
        token = res.get("paginationToken")
        if not token or not res.get("data"):
            return out, pages, init


def plan(db: sqlite3.Connection, start: int, end: int, now: float | None = None) -> list[dict]:
    """Mints whose curve completed in [start, end]; range [completion, min(completion + MAX_HOLD_S, end)] covers every
    copy open at the completion (a copy's span never exceeds its trigger + MAX_HOLD_S, and trigger <= completion).
    Wallets = every wallet with a >= BIG curve buy of the mint up to the completion (all possible copiers). Mints
    whose range has not fully passed (`now` - 600 s) are left out: they cannot be complete yet."""
    now = time.time() if now is None else now
    names = dict(db.execute("SELECT id, key FROM names WHERE kind='mint'"))
    out = []
    for mid, comp in db.execute("SELECT mint_id, ts FROM completes WHERE ts>=? AND ts<=? ORDER BY ts", (start, end)):
        to_ts = min(comp + MAX_HOLD_S, end)
        if to_ts > now - 600:
            continue
        wallets = [w for w, in db.execute("SELECT DISTINCT wallet_id FROM trades WHERE mint_id=? AND is_buy=1 "
                                          "AND sol_lamports>=? AND ts<=?", (mid, BIG, comp))]
        out.append({"mint_id": mid, "mint": names[mid], "completion": comp, "to_ts": to_ts, "wallets": wallets})
    return out


def _has_table(db, name: str) -> bool:
    return db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone() is not None


def _last_curve_price(db, mint_id: int, t: int):
    r = db.execute("SELECT sol_lamports * 1.0 / token_raw FROM trades WHERE mint_id=? AND ts<=? ORDER BY ts DESC "
                   "LIMIT 1", (mint_id, t)).fetchone()
    return r[0] if r else None


def _wallet_id(db, cache: dict, key: str) -> int:
    i = cache.get(key)
    if i is None:
        r = db.execute("SELECT id FROM names WHERE kind='wallet' AND key=?", (key,)).fetchone()
        i = r[0] if r else db.execute("INSERT INTO names (kind, key) VALUES ('wallet', ?)", (key,)).lastrowid
        cache[key] = i
    return i


def fetch_mint(db: sqlite3.Connection, item: dict, rpc, get=http_json, sleep=time.sleep,
               max_pages: int = MAX_PAGES) -> dict:
    """Fetch one planned mint. All-or-nothing: rows + completeness marks are committed together only on success."""
    db.executescript(AMM_SCHEMA + FETCH_LOG_SCHEMA)
    mint, mid, comp, to_ts = item["mint"], item["mint_id"], item["completion"], item["to_ts"]
    pool, pages, swaps, spent = None, 0, [], {}
    try:
        pool = find_pool(mint, comp, get)
        swaps, pages, init = fetch_pool_swaps(pool, mint, comp, to_ts, rpc, max_pages, sleep, spent)
        if init is None:                                  # the history must start at the pool's creation
            raise FetchError("pool creation not found at the start of the range")
        ref = _last_curve_price(db, mid, comp)
        if ref is not None and not (1 / PRICE_JUMP_MAX <= init / ref <= PRICE_JUMP_MAX):
            raise FetchError(f"pool initial price {init:.6g} vs last curve price {ref:.6g}: unit / pool error")
        source = f"pumpswap:{pool}"
        cache: dict = {}
        with db:                                          # one transaction: rows and completeness marks together
            db.execute("DELETE FROM amm_trades WHERE mint_id=? AND source=? AND ts>=? AND ts<=?",
                       (mid, source, comp, to_ts))
            db.executemany("INSERT OR IGNORE INTO amm_trades (ts, mint_id, wallet_id, is_buy, sol_lamports, token_raw, "
                           "source, sig) VALUES (?,?,?,?,?,?,?,?)",
                           [(s["ts"], mid, _wallet_id(db, cache, s["wallet"]), int(s["is_buy"]), s["sol"], s["token"],
                             source, s["sig"]) for s in swaps])
            db.execute("INSERT OR REPLACE INTO amm_fetch VALUES (?,?,?,?,?)", (mid, comp, to_ts, source, time.time()))
            db.executemany("INSERT OR REPLACE INTO amm_wallet_fetch VALUES (?,?,?,?,?)",
                           [(mid, w, comp, to_ts, source) for w in item["wallets"]])
            db.execute("INSERT INTO amm_fetch_log VALUES (?,?,?,?,?,?,?,?,?)",
                       (time.time(), mint, pool, comp, to_ts, pages, len(swaps), "complete",
                        f"init {init:.6g}; last curve {ref:.6g}" if ref is not None else f"init {init:.6g}; no curve"))
        return {"mint": mint, "status": "complete", "pool": pool, "pages": pages, "swaps": len(swaps)}
    except Exception as e:                                # any failure: nothing marked complete
        e = FetchError(f"{type(e).__name__}: {str(e)[:180]}") if not isinstance(e, FetchError) else e
        db.rollback()
        db.executescript(FETCH_LOG_SCHEMA)
        with db:
            db.execute("INSERT INTO amm_fetch_log VALUES (?,?,?,?,?,?,?,?,?)",
                       (time.time(), mint, pool, comp, to_ts, spent.get("pages", 0), len(swaps), "failed", str(e)[:200]))
        return {"mint": mint, "status": "failed", "pool": pool, "pages": spent.get("pages", 0), "error": str(e)}


def helius_rpc(api_key: str):
    url = f"https://mainnet.helius-rpc.com/?api-key={api_key}"

    def rpc(method: str, params: list) -> dict:
        return http_json(url, {"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
    return rpc
