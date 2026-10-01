"""Benchmark of the scanner's full evaluation of 400 tokens — the exact code path of
tests/test_stress.py::test_full_evaluation_of_400_tokens_fits_the_budget (same fixture, same timed loop).
Runs it N times on fresh engines and prints min / median / p95 / max.   usage: python tools/bench_eval.py [--runs 10]"""
import argparse
import random
import statistics
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "tests")]

from conftest import SOL_USD, default_info, dex_pair, good_dev, good_holders  # noqa: E402
from core.config import ApiKeys, Settings  # noqa: E402
from core.models import TokenState  # noqa: E402
from database.db import Database  # noqa: E402
from dex.dexscreener import parse_pair  # noqa: E402
from scanner.engine import ScannerEngine  # noqa: E402
from scanner.pipeline import ingest_market  # noqa: E402
from validation.identity import record_claim  # noqa: E402

N = 400


def one() -> float:
    eng = ScannerEngine(Settings(), Database(Path(tempfile.mkdtemp()) / "b.db"), keys=ApiKeys(), on_log=lambda m: None)
    rnd = random.Random(1)
    now = time.time()
    for i in range(N):
        mint = f"Stress{i:036d}"
        st = TokenState(info=default_info(mint, age_s=rnd.uniform(0.5, 30) * 60))
        st.holders, st.dev, st.holder_status = good_holders(), good_dev(), "ok"
        record_claim(st.identity, "dexscreener", "TKN", "")
        h = eng.history.get(mint)
        mc = rnd.uniform(5_000, 300_000)
        for k in range(8):
            mc *= rnd.uniform(0.9, 1.25)
            p = dex_pair(mint=mint, mc=mc, fdv=mc, price=f"{mc / 1e9:.12f}", vol=(1_000 * (k + 1), 2_000 * (k + 1)),
                         m5=(10 * (k + 1), 6 * (k + 1)), liq=rnd.uniform(3_000, 80_000))
            ingest_market(st, parse_pair(p), {}, SOL_USD, h, now - 20 * (8 - k))
        eng.tracked[mint] = st
    t0 = time.perf_counter()
    for st in eng.tracked.values():
        eng._evaluate(st, now)
    return time.perf_counter() - t0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=10)
    a = ap.parse_args()
    xs = sorted(one() for _ in range(a.runs))
    p95 = xs[min(len(xs) - 1, round(0.95 * (len(xs) - 1)))]
    print(f"runs {len(xs)}  min {xs[0]:.3f}s  median {statistics.median(xs):.3f}s  p95 {p95:.3f}s  max {xs[-1]:.3f}s"
          f"  budget 1.5s  over {sum(1 for x in xs if x >= 1.5)}/{len(xs)}")
