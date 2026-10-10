"""PumpSwap outcome fetcher (amendment 5): mocked GeckoTerminal / Helius only, synthetic database, no network."""
import urllib.error

import pytest

from smartmoney import analysis as A
from smartmoney import pumpswap as P
from smartmoney.recorder import Store

SOL = A.LAMPORTS
MINT, POOL = "M", "POOL"
W_DAYS = 200_000 / 86_400


def put(s, ts, mint, wallet, buy, sol, tok):
    s.buf.append((ts, None, s._id("mint", mint), s._id("wallet", wallet), int(buy), int(sol), int(tok), None))


def migrating(tmp_path):
    """Same scenario as the amendment tests: W buys at 100 100, entry 1.0, last curve price 3.0, completion 100 950."""
    s = Store(tmp_path / "t.db")
    put(s, 0, "Z", "Z", True, 0.01 * SOL, 1)
    put(s, 200_000, "Z", "Z", True, 0.01 * SOL, 1)
    put(s, 100_100, MINT, "W", True, 1 * SOL, 1 * SOL)
    put(s, 100_104, MINT, "o", True, 0.1 * SOL, 0.1 * SOL)
    put(s, 100_900, MINT, "o", True, 0.3 * SOL, 0.1 * SOL)
    s.done.append((s._id("mint", MINT), 100_950, None))
    s.flush()
    return s


def bal(owner, mint, amount):
    return {"owner": owner, "mint": mint, "uiTokenAmount": {"amount": str(int(amount))}}


def tx(ts, payer, pre, post, sig, err=None):
    """pre / post = (base, quote) held by the pool."""
    return {"blockTime": ts, "slot": ts, "meta": {"err": err,
            "preTokenBalances": [bal(POOL, MINT, pre[0]), bal(POOL, P.WSOL, pre[1])] if pre else [],
            "postTokenBalances": [bal(POOL, MINT, post[0]), bal(POOL, P.WSOL, post[1])]},
            "transaction": {"signatures": [sig], "message": {"accountKeys": [{"pubkey": payer}]}}}


R0 = (100 * SOL, 300 * SOL)                     # initial reserves: price 3.0 = the last curve price


def history():
    """Creation, a buy at ~1.2 ... the wallet's sell, a later sell at 0.7, a failed tx, a liquidity add."""
    return [tx(100_950, "MIGRATOR", None, R0, "c"),
            tx(101_000, "x", (100 * SOL, 300 * SOL), (99.9 * SOL, 300.12 * SOL), "s1"),          # buy 0.1 @ 1.2
            tx(101_500, "x", (99.9 * SOL, 300.12 * SOL), (99.9 * SOL, 300.12 * SOL), "f", err={"x": 1}),
            tx(102_000, "W", (99.9 * SOL, 300.12 * SOL), (100.9 * SOL, 299.32 * SOL), "s2"),       # W sells 1 @ 0.8
            tx(102_004, "y", (100.9 * SOL, 299.32 * SOL), (101 * SOL, 299.25 * SOL), "s3"),        # sell 0.1 @ 0.7
            tx(103_000, "lp", (101 * SOL, 299.25 * SOL), (102 * SOL, 302 * SOL), "lp1")]           # liquidity: skipped


def gt(created=100_950, extra=()):
    def pool(addr, dex="pumpswap", base=MINT, quote=P.WSOL, ts=created):
        iso = __import__("datetime").datetime.fromtimestamp(ts, __import__("datetime").timezone.utc).isoformat()
        return {"attributes": {"address": addr, "pool_created_at": iso},
                "relationships": {"dex": {"data": {"id": dex}}, "base_token": {"data": {"id": f"solana_{base}"}},
                                  "quote_token": {"data": {"id": f"solana_{quote}"}}}}
    data = [pool(POOL), pool("RAY", dex="raydium"), pool("PUMPQ", quote="pumpCm")] + [pool(*e) for e in extra]
    return lambda url: {"data": data}


def paged(txs, per_page=2, fail_page=None, dup=False, calls=None):
    def rpc(method, params):
        assert method == "getTransactionsForAddress" and params[0] == POOL
        o = params[1]
        assert o["sortOrder"] == "asc" and o["transactionDetails"] == "full"
        lo, hi = o["filters"]["blockTime"]["gte"], o["filters"]["blockTime"]["lte"]
        rows = [t for t in txs if lo <= t["blockTime"] <= hi]
        i = int(o.get("paginationToken") or 0)
        if calls is not None:
            calls.append(i)
        if fail_page is not None and i // per_page == fail_page:
            return {"error": {"code": -32603, "message": "upstream unavailable"}}
        page = rows[i:i + per_page]
        if dup and i:
            page = [rows[i - 1]] + page                  # the previous page's last tx again
        nxt = i + per_page
        return {"result": {"data": page, "paginationToken": str(nxt) if nxt < len(rows) else None}}
    return rpc


def item(s, to_ts):
    return {**P.plan(s.db, 0, 200_000, now=10 ** 9)[0], "to_ts": to_ts}


def status(s):
    w = A.window(s.db, W_DAYS)
    return {r["mint"]: r for r in A.copies_with_status(s.db, w, [s._id("wallet", "W")], [])}[s._id("mint", MINT)]


nosleep = lambda s: None  # noqa: E731


# ---------------------------------------------------------------- parsing
def test_parse_swap_sides_failed_and_liquidity():
    h = history()
    assert P.parse_swap(h[0], POOL, MINT) is None                          # creation
    b = P.parse_swap(h[1], POOL, MINT)
    assert (b["is_buy"], b["sol"], b["token"], b["wallet"], b["ts"]) == (True, 0.12 * SOL, 0.1 * SOL, "x", 101_000)
    assert P.parse_swap(h[2], POOL, MINT) is None                          # failed tx
    sw = P.parse_swap(h[3], POOL, MINT)
    assert (sw["is_buy"], sw["wallet"], sw["sol"] / sw["token"]) == (False, "W", pytest.approx(0.8))
    assert P.parse_swap(h[5], POOL, MINT) is None                          # both reserves up: liquidity
    assert P.pool_init_price(h[0], POOL, MINT) == 3.0 and P.pool_init_price(h[1], POOL, MINT) is None


def test_pool_lookup_only_the_canonical_pumpswap_sol_pool():
    assert P.find_pool(MINT, 100_950, gt()) == POOL
    with pytest.raises(P.FetchError, match="candidates: 0"):
        P.find_pool(MINT, 100_950 + 2 * P.POOL_CREATED_TOL_S, gt())         # created far from the completion
    with pytest.raises(P.FetchError, match="candidates: 2"):
        P.find_pool(MINT, 100_950, gt(extra=[("POOL2",)]))                 # ambiguous: never guessed


# ---------------------------------------------------------------- pagination / retry
def test_pagination_reads_to_the_end_and_dedups_a_repeated_tx():
    swaps, pages, init = P.fetch_pool_swaps(POOL, MINT, 100_950, 200_000, paged(history(), dup=True), sleep=nosleep)
    assert [s["sig"] for s in swaps] == ["s1", "s2", "s3"] and pages == 3 and init == 3.0


def test_range_end_is_respected():
    swaps, _, _ = P.fetch_pool_swaps(POOL, MINT, 100_950, 101_999, paged(history()), sleep=nosleep)
    assert [s["sig"] for s in swaps] == ["s1"]


def test_transient_errors_are_retried_and_request_errors_are_not():
    n = {"k": 0}

    def flaky():
        n["k"] += 1
        if n["k"] < 3:
            raise urllib.error.HTTPError("u", 429, "rate", {}, None)
        return {"result": 1}
    assert P.with_retry(flaky, sleep=nosleep) == {"result": 1} and n["k"] == 3
    m = {"k": 0}

    def bad():
        m["k"] += 1
        return {"error": {"code": -32015, "message": "Transaction version (1) is not supported"}}
    with pytest.raises(P.FetchError):
        P.with_retry(bad, sleep=nosleep)
    assert m["k"] == 1


def test_out_of_order_block_time_fails():
    h = history()
    h[3]["blockTime"] = 100_990                                            # older than the previous tx
    with pytest.raises(P.FetchError, match="inconsistent block time"):
        P.fetch_pool_swaps(POOL, MINT, 100_950, 200_000, lambda m, p: {"result": {"data": h}}, sleep=nosleep)


# ---------------------------------------------------------------- fetch -> analysis status
def test_complete_fetch_resolves_the_copy_on_pumpswap_prices_not_the_curve_top(tmp_path):
    s = migrating(tmp_path)
    assert status(s)["status"] == "unresolved"
    r = P.fetch_mint(s.db, item(s, 110_000), paged(history()), gt(), sleep=nosleep)
    assert r["status"] == "complete" and r["swaps"] == 3
    row = status(s)
    assert row["status"] == "ok" and row["kind"] == "wallet_sold+migrated"
    assert row["gross_pct"] == pytest.approx(-30.0)                        # exit 0.7 on PumpSwap, not 3.0 (+200 %)
    assert row["exit_ts"] == 102_004
    assert s.db.execute("SELECT from_ts, to_ts FROM amm_fetch").fetchone() == (100_950, 110_000)
    assert s.db.execute("SELECT COUNT(*) FROM amm_wallet_fetch WHERE wallet_id=?", (s._id("wallet", "W"),)) \
        .fetchone()[0] == 1


def test_refetch_is_idempotent(tmp_path):
    s = migrating(tmp_path)
    for _ in range(2):
        assert P.fetch_mint(s.db, item(s, 110_000), paged(history(), dup=True), gt(), sleep=nosleep)["status"] \
            == "complete"
    assert s.db.execute("SELECT COUNT(*), COUNT(DISTINCT sig) FROM amm_trades").fetchone() == (3, 3)


@pytest.mark.parametrize("case", ["page_error", "no_pool", "page_cap", "no_creation", "unit_error"])
def test_any_failure_marks_nothing_and_the_run_stays_blocked(tmp_path, monkeypatch, case):
    s = migrating(tmp_path)
    h, get, kw = history(), gt(), {}
    rpc = paged(h)
    if case == "page_error":
        rpc = paged(h, fail_page=1)
    elif case == "no_pool":
        get = gt(created=50_000)
    elif case == "page_cap":
        kw["max_pages"] = 1
    elif case == "no_creation":
        rpc = paged(h[1:])
    elif case == "unit_error":                                             # e.g. decimals mixed up: 1000x
        h[0] = tx(100_950, "MIGRATOR", None, (100 * SOL, 300_000 * SOL), "c")
        rpc = paged(h)
    r = P.fetch_mint(s.db, item(s, 110_000), rpc, get, sleep=nosleep, **kw)
    assert r["status"] == "failed"
    assert s.db.execute("SELECT COUNT(*) FROM amm_fetch").fetchone()[0] == 0
    assert s.db.execute("SELECT COUNT(*) FROM amm_wallet_fetch").fetchone()[0] == 0
    assert s.db.execute("SELECT COUNT(*) FROM amm_trades").fetchone()[0] == 0          # no partial rows
    assert s.db.execute("SELECT status FROM amm_fetch_log").fetchone()[0] == "failed"
    assert status(s)["status"] == "unresolved"
    w = s._id("wallet", "W")
    monkeypatch.setattr(A, "select_wallets", lambda db, win: {"eligible": [w], "selected": [w], "profit_sol": {w: 1}})
    assert A.run(str(tmp_path / "t.db"), days=W_DAYS, draws=10)["verdict"] == "BLOCKED_MIGRATION_DATA"


def test_failed_fetch_still_counts_its_pages(tmp_path):
    s = migrating(tmp_path)
    r = P.fetch_mint(s.db, item(s, 110_000), paged(history(), fail_page=1), gt(), sleep=nosleep)
    assert r["status"] == "failed" and r["pages"] == 1


def test_fetch_range_too_short_for_the_copy_stays_unresolved(tmp_path):
    s = migrating(tmp_path)
    P.fetch_mint(s.db, item(s, 101_200), paged(history()), gt(), sleep=nosleep)   # complete only to 101 200
    assert status(s)["status"] == "unresolved"


# ---------------------------------------------------------------- plan
def test_plan_ranges_wallets_and_unpassed_ranges(tmp_path):
    s = migrating(tmp_path)
    it = P.plan(s.db, 0, 200_000, now=10 ** 9)
    assert len(it) == 1 and it[0]["completion"] == 100_950
    assert it[0]["to_ts"] == min(100_950 + A.MAX_HOLD_S, 200_000)
    assert sorted(it[0]["wallets"]) == sorted([s._id("wallet", "W"), s._id("wallet", "o")])   # >= 0.05 SOL buyers
    assert P.plan(s.db, 0, 200_000, now=100_950 + 100) == []                          # range not passed yet


# ---------------------------------------------------------------- live (real GeckoTerminal + Helius; --live only)
@pytest.mark.live
def test_live_fetch_of_a_real_migrated_pool(tmp_path):
    """An external token (not from the recorded data): its PumpSwap pool from creation, 2 minutes."""
    import os
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        from pathlib import Path
        from dotenv import dotenv_values
        key = dotenv_values(Path(__file__).resolve().parents[1] / "dist" / ".env").get("HELIUS_API_KEY")
    if not key:
        pytest.skip("HELIUS_API_KEY not exported")
    s = Store(tmp_path / "live.db")
    mint, created = "5YLCp1XSRpGevfKBoSJvs7fSv6fSgbTDe3XB3dLfpump", 1_791_355_019
    it = {"mint_id": s._id("mint", mint), "mint": mint, "completion": created, "to_ts": created + 120, "wallets": []}
    s.db.commit()
    r = P.fetch_mint(s.db, it, P.helius_rpc(key))
    assert r["status"] == "complete" and r["pool"] == "3TXrbeYcuPNYt98xJ4W74fuwmPrFb9boANt9oM1trkzZ" and r["swaps"] > 0
    lo, hi, n, nd = s.db.execute("SELECT MIN(ts), MAX(ts), COUNT(*), COUNT(DISTINCT sig) FROM amm_trades").fetchone()
    assert created <= lo <= hi <= created + 120 and n == nd
