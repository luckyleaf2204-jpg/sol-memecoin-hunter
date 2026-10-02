"""TP-before-SL replay of Trade Candidates vs a random-token baseline, walk-forward 60/40 (read-only).

usage: python tools/replay_tp_sl.py --db data/research.db [--horizon 1h] [--split 0.6] [--bought-only] [--out r.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research.replay import replay  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--horizon", default="1h", help="forward_returns horizon: 30s 1m 2m 5m 10m 30m 1h 6h 24h")
    ap.add_argument("--split", type=float, default=0.6)
    ap.add_argument("--bought-only", action="store_true")
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--holdout-log", default="", help="JSON log: one parameter hash per out-of-sample holdout")
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    res = replay(a.db, a.horizon, a.split, a.bought_only, a.seed, a.holdout_log or None)
    text = json.dumps(res, indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
