"""Smart-money research: record pump.fun bonding-curve trades live (docs/smart_money_plan.md). Runs until stopped
(Ctrl+C) or until --days have passed since the first trade in the database. Read-only listener.

usage: python tools/sm_record.py [--db data/smartmoney/trades.db] [--days 21]"""
import argparse
import asyncio
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from smartmoney.recorder import Store, record  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=str(ROOT / "data" / "smartmoney" / "trades.db"))
    ap.add_argument("--days", type=float, default=21.0)
    a = ap.parse_args()
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)
    Path(a.db).parent.mkdir(parents=True, exist_ok=True)
    store = Store(Path(a.db))
    first = store.db.execute("SELECT MIN(ts) FROM trades").fetchone()[0]
    store.db.execute("INSERT OR IGNORE INTO meta VALUES ('started_at', ?)", (str(time.time()),))
    store.db.commit()
    stop = asyncio.Event()

    async def run():
        async def watchdog():
            while not stop.is_set():
                f = store.db.execute("SELECT MIN(ts) FROM trades").fetchone()[0] or first
                if f and time.time() - f >= a.days * 86400:
                    print(f"[sm] {a.days} days recorded: stopping", flush=True)
                    stop.set()
                await asyncio.sleep(60)
        await asyncio.gather(record(store, stop=stop, log=lambda m: print(m, flush=True)), watchdog())
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        store.flush()
        print("[sm] stopped", flush=True)


if __name__ == "__main__":
    main()
