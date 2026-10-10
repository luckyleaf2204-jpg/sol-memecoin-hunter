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
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    key = os.environ.get("HELIUS_API_KEY")
    if not key:
        sys.exit("HELIUS_API_KEY not set")
    chain = S.Chain(key, S.default_cache(), budget_calls=a.budget)
    r = S.run(chain, S.load_calls(Path(a.csv)), progress=print, trace_per_token=a.trace_per_token)
    Path(a.out).write_text(json.dumps(r, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"done in {r['seconds']} s, rpc calls {r['rpc_calls']}, cache hits {r['cache_hits']} -> {a.out}")


if __name__ == "__main__":
    main()
