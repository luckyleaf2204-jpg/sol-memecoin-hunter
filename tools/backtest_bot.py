"""Backtest the PAPER trading bot on the local snapshot database.

usage: python tools/backtest_bot.py [--hours 24] [--db path]
Prints NET P&L stats, every closed trade and the assumptions (live-only checks are not stored in snapshots).
"""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from core.config import DB_PATH  # noqa: E402
from database.db import Database  # noqa: E402
from trading.backtest import backtest_db  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--hours", type=float, default=24)
ap.add_argument("--db", default=str(DB_PATH))
a = ap.parse_args()
res = backtest_db(Database(a.db), since=time.time() - a.hours * 3600)
print(json.dumps(res["stats"], indent=2))
print(f"config {res['config']} (gated lifecycle = production)")
print(f"frames {res['frames']} · executions {res['executions']} · closed {len(res['trades'])} · open {len(res['open'])}")
for t in res["trades"]:
    print(f"  {t['symbol']:<12} net {t['net']:+9.2f}  exit {t['exit']}")
print("ASSUMPTIONS:", "; ".join(res["assumptions"]))
