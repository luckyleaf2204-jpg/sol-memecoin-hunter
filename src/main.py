"""SOL MEMECOIN HUNTER — Memecoin Potential Intelligence Engine (research only: no wallet, no keys, no trading).

  python src/main.py                     desktop dashboard (default)
  python src/main.py --check <MINT>      full report for one token (all metrics with source/age/confidence)
  python src/main.py --headless          console scanner
  python src/main.py --backtest [early]  backtest stored Opportunity (or Early Signal) signals
  python src/main.py --web               web server + iPhone PWA (needs APP_ACCESS_CODE; PORT env or --port)
  add --lang en|vi to any command
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if sys.stdout is not None and hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")


def cmd_check(mint: str) -> None:
    from alerts.report import text_report
    from core.config import DB_PATH, Settings
    from database.db import Database
    from scanner.engine import ScannerEngine

    async def go():
        eng = ScannerEngine(Settings.load(), Database(DB_PATH), on_log=lambda m: None)
        try:
            st = await eng.analyze_one(mint)
            print(text_report(st))
            print("\nSource health:")
            for name, s in eng.http.health.sources.items():
                print(f"  {name:12s} {'OK ' if s.ok else 'ERR'} requests={s.requests} errors={s.errors} {s.last_error}")
        finally:
            await eng.http.aclose()

    asyncio.run(go())


def cmd_headless() -> None:
    from alerts.report import age_str, usd
    from core.config import DB_PATH, Settings
    from database.db import Database
    from scanner.engine import ScannerEngine
    from scoring.ranking import rank_early, rank_opportunities

    def show(states):
        dq = {k: sum(1 for s in states if s.dq_status == k) for k in ("VALID", "PARTIAL", "INVALID")}
        early = [s for s in rank_early(states) if s.early.is_early]
        print(f"  data quality {dq} | early signals: {', '.join('$' + s.info.symbol for s in early[:8]) or '-'}")
        for s in rank_opportunities(states)[:8]:
            m = s.market
            es = s.early.strength if s.early and s.early.strength is not None else "-"
            print(f"  opp {s.score.total:3d} risk {s.risk.score:3d} early {es!s:>3} DQ {s.quality.score:3d} "
                  f"{s.lifecycle:15s} ${s.info.symbol[:10]:10s} age {age_str(s.age_minutes):>5s} "
                  f"MC {usd(m.market_cap):>9s} vol5m {usd(m.vol_5m):>9s}")

    eng = ScannerEngine(Settings.load(), Database(DB_PATH), on_update=show,
                        on_events=lambda evs: [print(f"  EVENT {e.type} ${e.symbol} {e.params}") for e in evs])
    try:
        asyncio.run(eng.run())
    except KeyboardInterrupt:
        pass


def cmd_backtest(column: str) -> None:
    from analytics.backtest import TARGETS, run_backtest
    from core.config import DB_PATH
    from database.db import Database

    db = Database(DB_PATH)
    print(f"DB: {db.stats()}  signal column: {column}")
    print(f"{'>=':>4} {'window':>6} {'signals':>7} {'eval':>5} " + " ".join(f"{k:>6}" for k in TARGETS) + "  drop50  medianMax")
    for r in run_backtest(db.conn, column=column):
        med = f"{r.median_max_gain_pct:.0f}%" if r.median_max_gain_pct is not None else "-"
        print(f"{r.threshold:>4} {r.window:>6} {r.signals:>7} {r.evaluated:>5} "
              + " ".join(f"{r.hits[k]:>5.0f}%" for k in TARGETS) + f"  {r.drop50_pct:>5.0f}%  {med:>8}")


def cmd_web(host: str, port: int) -> None:
    import uvicorn
    uvicorn.run("web.app:app", host=host, port=port, proxy_headers=True, forwarded_allow_ips="*",
                log_level="info")


def main() -> None:
    ap = argparse.ArgumentParser(description="SOL Memecoin Hunter — research scanner")
    ap.add_argument("--check", metavar="MINT", help="print a full report for one token")
    ap.add_argument("--headless", action="store_true", help="run the scanner in the console")
    ap.add_argument("--backtest", nargs="?", const="score", choices=["score", "early"],
                    help="backtest stored signals (score | early)")
    ap.add_argument("--lang", choices=["vi", "en"], help="language for this run")
    ap.add_argument("--web", action="store_true", help="run the web server + PWA")
    ap.add_argument("--host", default=os.getenv("HOST", "0.0.0.0"))
    ap.add_argument("--port", type=int, default=int(os.getenv("PORT", "8000")))
    a = ap.parse_args()
    from core.config import Settings
    from i18n import set_language
    set_language(a.lang or Settings.load().language)
    if a.web:
        cmd_web(a.host, a.port)
    elif a.check:
        cmd_check(a.check)
    elif a.headless:
        cmd_headless()
    elif a.backtest:
        cmd_backtest("early_signal" if a.backtest == "early" else "score")
    else:
        from ui.app import run_gui
        run_gui()


if __name__ == "__main__":
    main()
