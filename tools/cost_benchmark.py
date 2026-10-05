"""Execution-cost benchmark of the PAPER model for one position size (read-only, no strategy change).

Off-curve markets (D1 blocks curve entries), random liquidity and 5-minute move. The production BUY / SELL use a real
Jupiter quote for the price impact; offline the liquidity model stands in for it (same formula family), so impact
here is a model estimate. Reports mean / P50 / P75 / P90 / P95 / P99 / max per component, in % of the position.

usage: python tools/cost_benchmark.py [--usd 50] [--n 5000] [--sol 150]
"""
import argparse
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

LIQ = (5_000, 10_000, 20_000, 50_000, 100_000)
PC5 = (2.0, 10.0, 25.0, 50.0, -30.0)          # -30: selling into a dump (dump tail)


def pct(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * (len(v) - 1) + 0.5))]


def run(usd=50.0, n=5000, sol=150.0, seed=1):
    from test_bot_v2 import good
    from trading.config import TradingConfig
    from trading.execution import NETWORK_FEE_SOL, PaperExecutor
    cfg = TradingConfig()
    rng = random.Random(seed)
    comp = {k: [] for k in ("entry_impact", "exit_impact", "network_fee", "priority_fee", "route_fee",
                            "latency_slippage", "total_round_trip")}
    fails = attempts = 0
    for i in range(n):
        ex = PaperExecutor(seed=seed * 100_000 + i, max_slippage_pct=cfg.max_slippage_pct)
        ex.priority_fee_sol = cfg.priority_fee_sol
        st = good()
        st.market.dex_id = "pumpswap"
        st.market.liquidity_usd, st.market.price_change_5m = rng.choice(LIQ), rng.choice(PC5)
        attempts += 1
        b = ex.buy(st, usd, sol)
        if b.status != "FILLED":
            fails += 1
            continue
        s = ex.sell(st, b.tokens, st.market.price_usd, sol, "x", force=True)
        attempts += 1
        if s.status != "FILLED":
            fails += 1
            continue
        comp["entry_impact"].append(b.price_impact_pct)
        comp["exit_impact"].append(s.price_impact_pct)
        comp["network_fee"].append(100 * 2 * NETWORK_FEE_SOL * sol / usd)
        comp["priority_fee"].append(100 * 2 * cfg.priority_fee_sol * sol / usd)
        comp["route_fee"].append(100 * (b.fee_usd + s.fee_usd) / usd)
        comp["latency_slippage"].append(b.slippage_pct + s.slippage_pct)
        comp["total_round_trip"].append(100 * (usd + b.network_fee_usd + s.network_fee_usd - s.usd_in) / usd)
    out = {k: {"mean": sum(v) / len(v), "p50": pct(v, .5), "p75": pct(v, .75), "p90": pct(v, .9), "p95": pct(v, .95),
               "p99": pct(v, .99), "max": max(v)} for k, v in comp.items()}
    out["_meta"] = {"usd": usd, "n_round_trips": len(comp["total_round_trip"]), "tx_attempts": attempts,
                    "failed_tx_pct": 100 * fails / attempts}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--usd", type=float, default=50.0)
    ap.add_argument("--n", type=int, default=5000)
    ap.add_argument("--sol", type=float, default=150.0)
    a = ap.parse_args()
    r = run(a.usd, a.n, a.sol)
    m = r.pop("_meta")
    print(f"size ${m['usd']:.0f} · {m['n_round_trips']} round trips · failed tx {m['failed_tx_pct']:.1f}% "
          "(each failed tx still pays network + priority fee)")
    print(f"{'% of position':<18}{'mean':>7}{'p50':>7}{'p75':>7}{'p90':>7}{'p95':>7}{'p99':>7}{'max':>7}")
    for k, s in r.items():
        print(f"{k:<18}" + "".join(f"{s[q]:7.2f}" for q in ("mean", "p50", "p75", "p90", "p95", "p99", "max")))


if __name__ == "__main__":
    main()
