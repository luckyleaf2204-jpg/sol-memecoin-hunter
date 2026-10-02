"""TP-before-SL replay of Trade Candidates vs a random-token baseline, walk-forward 60/40 (read-only).

usage: python tools/replay_tp_sl.py --db data/research.db [--horizon 1h] [--split 0.6] [--out r.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research.replay import lock_holdout, replay  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--horizon", default="1h", help="forward_returns horizon: 30s 1m 2m 5m 10m 30m 1h 6h 24h")
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--holdout-lock", default="", help="JSON lock file: parameter hash + lock time + time cut")
    ap.add_argument("--lock-holdout", action="store_true", help="freeze the parameters on the in-sample part")
    ap.add_argument("--min-n", type=int, default=30)
    ap.add_argument("--engine", default="lifecycle", help="candidates of this engine only ('any' = all)")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    if a.lock_holdout:
        if not a.holdout_lock:
            sys.exit("--lock-holdout needs --holdout-lock PATH")
        res = lock_holdout(a.db, a.holdout_lock, a.horizon, a.split, a.seed, min_n=a.min_n,
                           engine=None if a.engine == "any" else a.engine)
        print(f"HOLDOUT {res['status']}" + (f": {res['reason']}" if res.get("reason") else ""))
    else:
        res = replay(a.db, a.horizon, a.split, a.seed, a.holdout_lock or None, a.min_n,
                     None if a.engine == "any" else a.engine)
        print(f"REPLAY n_in_sample={res['n_in_sample']} n_holdout={res['n_holdout']} · {res['verdict']}")
    text = json.dumps(res, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
