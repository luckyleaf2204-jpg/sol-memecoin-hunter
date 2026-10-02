"""No-chasing gate report on research.db: block rate, blocked vs entered forward returns vs a matched random baseline.

usage: python tools/gate_report.py --db data/research.db [--out gate.json]
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from research.gate_eval import evaluate  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", required=True)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default="")
    a = ap.parse_args()
    text = json.dumps(evaluate(a.db, a.seed), indent=1)
    print(text)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
