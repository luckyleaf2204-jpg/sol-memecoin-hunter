"""Fill the PumpSwap outcome data of amendment 5 (src/smartmoney/pumpswap.py) into the recorder database.

  --plan   count the completed mints / wallets to fetch (no price, no outcome); allowed at any time
  (none)   fetch; refused before the pre-registered window ends (amendment 6), because every range must have passed

A mint that fails (page error, pool not found / ambiguous, page cap, consistency check) is NOT marked complete: its
copies stay UNRESOLVED and the analysis verdict stays BLOCKED_MIGRATION_DATA. Stops at --max-credits (the rest stays
unresolved). The Helius key comes from HELIUS_API_KEY in the environment or dist/.env and is never printed.

usage: python tools/sm_pumpswap.py [--plan] [--db data/smartmoney/trades.db] [--days 21] [--max-credits 1000000]"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney import pumpswap as P  # noqa: E402
from smartmoney.analysis import analysis_allowed, window  # noqa: E402


def helius_key() -> str:
    k = os.environ.get("HELIUS_API_KEY")
    if not k:
        from dotenv import dotenv_values
        k = dotenv_values(ROOT / "dist" / ".env").get("HELIUS_API_KEY")
    if not k:
        sys.exit("HELIUS_API_KEY not set")
    return k


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "smartmoney" / "trades.db"))
    ap.add_argument("--days", type=float, default=21.0)
    ap.add_argument("--plan", action="store_true")
    ap.add_argument("--max-credits", type=int, default=1_000_000)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    db = sqlite3.connect(a.db, timeout=60)
    w = window(db, a.days)
    if a.plan:
        items = P.plan(db, w.start, w.end)
        print(f"completed mints with a passed range: {len(items)}; wallet marks: "
              f"{sum(len(i['wallets']) for i in items)}")
        return
    ok, end = analysis_allowed(db, a.days)
    if not ok:
        sys.exit(f"refused: the window ends {datetime.fromtimestamp(end, timezone.utc):%Y-%m-%d %H:%M} UTC "
                 "(amendment 6); fetch after it")
    done = {m for m, in db.execute("SELECT mint_id FROM amm_fetch")} if P._has_table(db, "amm_fetch") else set()
    items = [i for i in P.plan(db, w.start, w.end) if i["mint_id"] not in done]
    rpc, credits, n_ok, n_fail = P.helius_rpc(helius_key()), 0, 0, 0
    for k, item in enumerate(items, 1):
        if credits >= a.max_credits:
            print(f"credit budget reached: {len(items) - k + 1} mints left UNRESOLVED (verdict stays BLOCKED)")
            break
        r = P.fetch_mint(db, item, rpc)
        credits += r["pages"] * P.CREDITS_PER_PAGE
        n_ok += r["status"] == "complete"
        n_fail += r["status"] != "complete"
        print(f"[{k}/{len(items)}] {r['mint']} {r['status']} pages {r['pages']} "
              f"{r.get('swaps', '')}{r.get('error', '')}")
    print(f"complete {n_ok}, failed {n_fail}, ~credits {credits:,}")


if __name__ == "__main__":
    main()
