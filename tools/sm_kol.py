"""KOL copy research: run the pre-registered test on the recorded database (docs/kol_plan.md).
Use ONLY after the recording window ended (21 days). Writes docs/kol_report.md.

usage: python tools/sm_kol.py [--db data/smartmoney/trades.db] [--roster docs/kol_wallets_20261006.json] [--days 21]"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney.kol import run  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "smartmoney" / "trades.db"))
    ap.add_argument("--roster", default=str(ROOT / "docs" / "kol_wallets_20261006.json"))
    ap.add_argument("--days", type=float, default=21.0)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    import sqlite3
    from smartmoney.analysis import analysis_allowed
    db = sqlite3.connect(f"file:{a.db}?mode=ro", uri=True)
    ok, end = analysis_allowed(db, a.days)
    db.close()
    if not ok:                                   # amendment 6: no peeking, no early stop
        sys.exit(f"refused: the pre-registered window ends {datetime.fromtimestamp(end, timezone.utc):%Y-%m-%d %H:%M} "
                 "UTC; B / C are analysed only after that (docs/smart_money_plan.md amendment 6)")
    r = run(a.db, a.roster, a.days)
    f = lambda t: datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d %H:%M")  # noqa: E731
    w = r["window"]
    L = ["# KOL copy test — result", "", "Plan: [docs/kol_plan.md](kol_plan.md). Research only.", "",
         f"Window {f(w['start'])} -> {f(w['end'])} UTC (whole window = test).", "",
         f"## Verdict: **{r['verdict']}**", ""]
    L += [f"- {'✅' if v else '❌'} {k}" for k, v in r["checks"].items()]
    L += ["", f"Roster {r['roster']} wallets, seen in data {r['kols_seen']}, eligible {r['kols_eligible']}.", "",
          "```", json.dumps(r["test"], indent=1), "```", "", f"Random baseline: {r['random_baseline']}", "",
          f"KOL own return on the same tokens (report only): {r['kol_own']}", "",
          f"Delay 10 s (not decisive): {r['delay_10s']}", ""]
    text = "\n".join(L)
    (ROOT / "docs" / "kol_report.md").write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
