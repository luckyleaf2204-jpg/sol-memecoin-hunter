"""Run the insider scan (src/insiders/scan.py) on the call channel CSV and write the result JSON.

usage: python tools/insider_scan.py [--csv path] [--out src/insiders/result.json] [--trace-per-token 8]
Env: HELIUS_API_KEY (never printed), INSIDERS_CACHE (SQLite cache of immutable RPC pages)."""
import argparse
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from insiders import scan as S  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--csv", default=str(S.CALLS_CSV))
    ap.add_argument("--out", default=str(S.RESULT_JSON))
    ap.add_argument("--trace-per-token", type=int, default=S.TRACE_PER_TOKEN)
    ap.add_argument("--budget", type=int, default=4000)
    ap.add_argument("--deep", type=int, default=0, help="also trace N hops back / forward (3-4); 0 = off")
    ap.add_argument("--deep-budget", type=int, default=8000)
    ap.add_argument("--reuse", action="store_true", help="deep trace only, on the existing result file")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        sys.exit("HELIUS_API_KEY not set")
    chain = S.Chain(key, S.default_cache(), budget_calls=a.budget)
    if a.reuse:
        r = json.loads(Path(a.out).read_text(encoding="utf-8"))
    else:
        r = S.run(chain, S.load_calls(Path(a.csv)), progress=print, trace_per_token=a.trace_per_token)
    if a.deep:
        from insiders import deep as D
        chain.budget = chain.calls + a.deep_budget
        seeds = D.seeds_from(r, a.trace_per_token)
        print(f"deep trace: {len(seeds)} seed wallets, {a.deep} hops, budget {a.deep_budget} calls")
        d = D.deep_trace(chain, seeds, max_hops=a.deep, progress=print)
        print(f"activity check: {D.check_activity(chain, d, seeds)} busy addresses flagged")
        r["deep"] = D.summarize(d, seeds, r.get("tickers", {}))
        r["deep"]["rpc_calls"] = chain.calls
    Path(a.out).write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"done: rpc calls this run {chain.calls}, cache hits {chain.cached} -> {a.out}")


if __name__ == "__main__":
    main()
