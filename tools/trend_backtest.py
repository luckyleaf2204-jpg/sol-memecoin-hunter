"""Trend research (direction A): download the pre-registered universe and run rule T1 (docs/trend_plan.md).

usage:
  python tools/trend_backtest.py --fetch          # select pools + download 180 days of hourly bars (cached)
  python tools/trend_backtest.py --run            # T1 at the primary size -> docs/trend_report.md
Research only: no order, no wallet."""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from trend.backtest import run  # noqa: E402
from trend.data import build_dataset, load_csv  # noqa: E402

DATA = ROOT / "data" / "trend"


def fmt_ts(t):
    return datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")


def report(uni: dict, main: dict, alt: dict) -> str:
    L = ["# Trend research — rule T1 result", "",
         "Pre-registered plan: [docs/trend_plan.md](trend_plan.md). Research only (paper numbers, no order).", "",
         f"Universe built {uni['built_at'][:16]} UTC. Window {fmt_ts(main['window']['start'])} -> "
         f"{fmt_ts(main['window']['end'])}, holdout from {fmt_ts(main['window']['cut'])}.", "",
         f"## Verdict: **{main['verdict']}**", ""]
    for k, v in main["checks"].items():
        L.append(f"- {'✅' if v else '❌'} {k}")
    L += ["", "## Results (primary: $100 size, priority fee 0.005 SOL)", "",
          "| period | n | tokens | mean net % | 95 % CI per token | median % | mean gross % | win % | avg win | avg loss | PF | hold h |",
          "|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for name in ("in_sample", "holdout"):
        s = main[name]
        if not s.get("n"):
            L.append(f"| {name} | 0 | | | | | | | | | | |")
            continue
        L.append(f"| {name} | {s['n']} | {s['tokens']} | {s['mean_net_pct']} | {s['ci95_cluster']} | {s['median_net_pct']} | "
                 f"{s['mean_gross_pct']} | {s['win_rate_pct']} | {s['avg_win_pct']} | {s['avg_loss_pct']} | "
                 f"{s['profit_factor']} | {s['mean_hold_h']} |")
    b = main["random_baseline_holdout"]
    if b:
        L += ["", f"Random baseline (holdout, {b['iters']} draws, same trades per token and holding times): mean of "
              f"random means {b['mean_of_random_means']} %, p(random >= observed) = {b['p_random_ge_observed']}"]
    L += ["", "## Sensitivity (not decisive)", ""]
    for name, r in alt.items():
        h = r["holdout"]
        L.append(f"- {name}: holdout n {h.get('n')}, mean net {h.get('mean_net_pct')} %, CI {h.get('ci95_cluster')}, "
                 f"verdict {r['verdict']}")
    L += ["", "## Universe", "", "| token | pool | dex | quote | reserve $ | round trip cost % | buy & hold holdout net % |",
          "|---|---|---|---|---|---|---|"]
    for t in uni["tokens"]:
        if t.get("dropped"):
            L.append(f"| {t['symbol']} | dropped: {t['dropped']} | | | | | |")
        else:
            L.append(f"| {t['symbol']} | {t['pool'][:8]}… | {t['dex']} | {t['quote']} | {t['reserve_usd']:,.0f} | "
                     f"{main['round_trip_cost_pct'].get(t['symbol'])} | {main['buy_hold_holdout_net_pct'].get(t['symbol'])} |")
    L += ["", "## Known limits", "",
          "- 180 days only (public API): one market regime.",
          "- Survivorship: today's well-known tokens and today's pool reserve -> results biased UPWARD.",
          "- Price impact uses today's reserve; fills at the next hourly open (gaps included, intrabar path unknown)."]
    return "\n".join(L) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--run", action="store_true")
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if a.fetch:
        build_dataset(DATA, log=lambda m: print(m, flush=True))
    if a.run:
        uni = json.loads((DATA / "universe.json").read_text(encoding="utf-8"))
        toks = [t for t in uni["tokens"] if not t.get("dropped") and (DATA / f"{t['symbol']}.csv").exists()]
        data = {t["symbol"]: load_csv(DATA / f"{t['symbol']}.csv") for t in toks}
        data = {k: v for k, v in data.items() if len(v) > 300}
        res = {t["symbol"]: t["reserve_usd"] for t in toks}
        main_r = run(data, res, 100.0, 0.005)
        alt = {"$50, 0.005 SOL": run(data, res, 50.0, 0.005, 500), "$100, 0.001 SOL": run(data, res, 100.0, 0.001, 500)}
        text = report(uni, main_r, alt)
        (ROOT / "docs" / "trend_report.md").write_text(text, encoding="utf-8")
        (DATA / "t1_result.json").write_text(json.dumps(main_r, indent=1), encoding="utf-8")
        print(text)


if __name__ == "__main__":
    main()
